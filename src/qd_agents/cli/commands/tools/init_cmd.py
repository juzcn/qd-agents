"""工具初始化命令 — 注册内置工具 + 默认 MCP 工具"""

import json
import os
from pathlib import Path
from typing import Optional, List

from rich.console import Console

from qd_agents.config import load_config, load_runtime_config
from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType
from qd_agents.tools.executors import create_bash_tool, create_mcp_tool, extract_mcp_servers_config
from qd_agents.cli.utils.credentials import env_var_to_tool_name
from qd_agents.cli.utils.registry import get_tool_registry
from qd_agents import __version__

from .update_cmd import _detect_package_version


# 默认 MCP 工具名列表（用于迁移去重）
DEFAULT_MCP_NAMES = {"filesystem", "fetch", "serper-search", "github-api"}

# 内置工具名列表（用于迁移去重）
BUILTIN_TOOL_NAMES = {"execute_bash", "memory_list", "memory_recall"}


def init_tools(
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> None:
    """
    初始化工具箱：注册内置工具 + 默认 MCP 工具

    - builtin 类别：核心工具（execute_bash, memory_list, memory_recall），不可删除不可更新
    - default 类别：预装 MCP 工具（filesystem, fetch, serper-search），不可删除但可更新
    - 迁移去重：已存在的同名默认 MCP 工具先删除再重新注册
    """
    config = load_config(base_dir=base_dir, config_file=config_file)

    # 确保数据目录存在
    if config.storage:
        config.storage.data_dir.mkdir(parents=True, exist_ok=True)

    registry = get_tool_registry(config)

    # 1. 只清除 builtin + default 类别的工具（保留用户添加的工具）
    deleted = registry.delete_by_scopes(["builtin", "default"])
    if deleted > 0:
        console.print(f"[dim]已清除 {deleted} 个内置/默认工具（准备重新注册）[/]")

    # 2. 迁移去重：删除与内置/默认工具同名的旧工具（旧版可能注册为其他类别）
    migrate_names = DEFAULT_MCP_NAMES | BUILTIN_TOOL_NAMES
    for name in migrate_names:
        existing = registry.get_by_name(name)
        if existing:
            registry.delete(existing.id)
            console.print(f"[dim]迁移去重：移除旧版工具 {name} (ID: {existing.id}, 属性: {existing.scope})[/]")

    registered_tools: List[str] = []

    # ==================== 内置工具 (builtin) ====================

    # execute_bash — 核心元工具，唯一始终给完整 schema 的工具
    bash_tool = create_bash_tool(
        name="execute_bash",
        description="执行bash/shell命令，支持管道、重定向等shell特性",
        shell_command="{command}",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的bash/shell命令"},
            },
            "required": ["command"],
        },
        scope="builtin",
        tags=["bash", "shell", "command", "core"],
        version=__version__,
    )
    bash_tool.id = "builtin.execute_bash"
    registry.register(bash_tool)
    registered_tools.append(bash_tool.name)

    # memory_list — 列出长期记忆
    memory_list_tool = create_bash_tool(
        name="memory_list",
        description="列出长期记忆，支持时间区间、session 筛选",
        shell_command="qd-agents memory list {args}",
        parameters={
            "type": "object",
            "properties": {
                "args": {
                    "type": "string",
                    "description": "可选参数：--asc（正序）、--interval（时间区间如 1d/today/04-25~04-27）、--session（session ID）",
                },
            },
            "required": [],
        },
        scope="builtin",
        tags=["memory", "list", "cli"],
        version=__version__,
    )
    memory_list_tool.id = "builtin.memory_list"
    registry.register(memory_list_tool)
    registered_tools.append(memory_list_tool.name)

    # memory_recall — 语义召回长期记忆
    memory_recall_tool = create_bash_tool(
        name="memory_recall",
        description="语义召回长期记忆，输入查询语句返回相关历史对话",
        shell_command="qd-agents memory recall {query}",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "用于语义检索的查询语句"},
            },
            "required": ["query"],
        },
        scope="builtin",
        tags=["memory", "recall", "cli"],
        version=__version__,
    )
    memory_recall_tool.id = "builtin.memory_recall"
    registry.register(memory_recall_tool)
    registered_tools.append(memory_recall_tool.name)

    # ==================== 默认 MCP 工具 (default) ====================
    # 从包内 defaults/ 目录读取配置，注册为 default 类别

    runtime_config = load_runtime_config(base_dir=base_dir)
    defaults_dir = Path(__file__).parent.parent.parent / "tools" / "defaults"

    for json_file in sorted(defaults_dir.glob("*.json")):
        server_name = json_file.stem  # filesystem, fetch, serper-search, github-api
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                json_config = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            console.print(f"[red][ERROR][/] 读取默认配置失败: {json_file.name}: {e}")
            continue

        # 跳过非 MCP 格式的配置（如 HTTP 工具）
        if "mcpServers" not in json_config:
            continue

        servers, config_server_name = extract_mcp_servers_config(json_config)
        if not servers or not config_server_name:
            console.print(f"[yellow][WARN][/] 跳过无效 MCP 配置: {json_file.name}")
            continue

        server_config = servers[config_server_name]
        final_command = server_config.get("command")
        final_args = server_config.get("args", [])

        # 构建 env：合并 JSON 配置中的 env dict + 从 runtime.json 加载凭证
        json_env = server_config.get("env") or {}
        # 如果 env 是列表格式（如 ["SERPER_API_KEY"]），转换为 dict 并从 runtime.json 加载值
        if isinstance(json_env, list):
            final_env: dict[str, str] = {}
            for env_var in json_env:
                tool_name = env_var_to_tool_name(env_var)
                api_key = runtime_config.tools_credentials.get_api_key(tool_name)
                if api_key:
                    final_env[env_var] = api_key
                    console.print(f"  [dim]{env_var}[/]: 从 runtime.json 加载")
                else:
                    final_env[env_var] = os.environ.get(env_var, "")
                    if final_env[env_var]:
                        console.print(f"  [dim]{env_var}[/]: 从环境变量加载")
                    else:
                        console.print(f"  [yellow]{env_var}[/]: 未配置，工具可能无法启动")
        else:
            final_env = dict(json_env)

        final_env["__mcp_config__"] = json.dumps(json_config, ensure_ascii=False)

        # 检测版本和安装源
        parsed_default_args = final_args if isinstance(final_args, list) else [final_args]
        version, install_source = _detect_package_version(final_command, parsed_default_args)

        tool = create_mcp_tool(
            name=server_name,
            description=f"MCP server: {config_server_name}",
            server=config_server_name,
            transport="stdio",
            command=final_command,
            args=parsed_default_args,
            env=final_env,
            parameters={
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "要执行的 MCP 工具名称"},
                    "arguments": {"type": "object", "description": "工具参数", "additionalProperties": True},
                },
                "required": ["tool_name", "arguments"],
            },
            source_path=str(json_file),
            version=version,
            install_source=install_source,
        )
        # 覆盖 category 为 default
        tool.scope = "default"
        tool.metadata.tags = ["mcp", config_server_name, "default"]
        # 覆盖 id 为 default 前缀
        tool.id = f"default.{server_name}"

        registry.register(tool)
        registered_tools.append(tool.name)
        console.print(f"[dim]  注册默认 MCP: {server_name} ({config_server_name})[/]")

    # ==================== 默认 HTTP 工具 (default) ====================

    # github-api — GitHub REST API 客户端
    github_token = runtime_config.tools_credentials.get_api_key("github") or os.environ.get("GITHUB_TOKEN", "")
    github_headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if github_token:
        github_headers["Authorization"] = f"Bearer {github_token}"
    github_env: dict[str, str] = {}
    if github_token:
        github_env["GITHUB_TOKEN"] = github_token

    github_api_tool = Tool(
        id="default.github-api",
        name="github-api",
        description="GitHub REST API 客户端，访问仓库内容、搜索代码等",
        parameters={
            "type": "object",
            "properties": {
                "endpoint": {"type": "string", "description": "API 端点路径，如 /repos/{owner}/{repo}/contents/{path}"},
                "method": {"type": "string", "description": "HTTP 方法", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "GET"},
                "params": {"type": "object", "description": "查询参数"},
                "body": {"type": "object", "description": "请求体 (JSON)"},
            },
            "required": ["endpoint"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.HTTP,
            endpoint="https://api.github.com",
            method="GET",
            headers=github_headers,
            env=github_env,
            timeout=30,
        ),
        scope="default",
        metadata=ToolMetadata(
            tags=["github", "api", "default"],
        ),
    )
    registry.register(github_api_tool)
    registered_tools.append(github_api_tool.name)
    if github_token:
        console.print("[dim]  注册默认 HTTP: github-api (token 已配置)[/dim]")
    else:
        console.print("[dim]  注册默认 HTTP: github-api (未配置 token，API 限额受限)[/dim]")

    # ==================== 显示结果 ====================
    all_tools = registry.list_all()
    builtin_count = sum(1 for t in all_tools if t.scope == "builtin")
    default_count = sum(1 for t in all_tools if t.scope == "default")
    user_count = len(all_tools) - builtin_count - default_count

    console.print(f"\n[green]工具箱初始化完成 ({len(all_tools)} 个工具):[/]")
    console.print(f"  [cyan]内置工具[/] (builtin): {builtin_count} 个 — 不可删除、不可更新")
    console.print(f"  [green]默认工具[/] (default): {default_count} 个 — 不可删除、可更新")
    console.print(f"  [yellow]用户工具[/] (user): {user_count} 个 — 可删除、可更新")

    for tool in all_tools:
        tool_type = tool.execution.type.value.lower() if tool.execution.type else "unknown"
        scope = tool.scope
        style = {"builtin": "cyan", "default": "green"}.get(scope, "yellow")
        console.print(f"  - [{style}]{tool.name}[/]({tool_type}, {scope})")
