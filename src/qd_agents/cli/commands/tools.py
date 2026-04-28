"""
工具管理命令

负责列出和初始化工具。
"""

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from rich.console import Console
from rich.table import Table

from qd_agents.config import load_config, load_runtime_config, save_runtime_config
from qd_agents.registry import ToolRegistry
from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType
from qd_agents.tools.executors import create_bash_tool, create_mcp_tool, extract_mcp_servers_config
from qd_agents.cli.utils.credentials import env_var_to_tool_name
from qd_agents.cli.commands.mcp import _detect_package_version
from qd_agents.models.url_analyze import UrlAnalyzeResult


# 默认 MCP 工具名列表（用于迁移去重）
DEFAULT_MCP_NAMES = {"filesystem", "fetch", "serper-search"}

# 内置工具名列表（用于迁移去重）
BUILTIN_TOOL_NAMES = {"execute_bash", "memory_list", "memory_recall"}


def list_tools(
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
    type_filter: Optional[List[str]] = None,
) -> None:
    """
    列出工具

    Args:
        console: Rich 控制台对象
        base_dir: 基础目录
        config_file: 配置文件路径
        type_filter: 工具类型过滤列表（如 ["mcp", "skill", "function"]）
    """
    config = load_config(base_dir=base_dir, config_file=config_file)

    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)

    tools = registry.list_all()

    # 按类型过滤
    if type_filter:
        tools = [t for t in tools if t.execution.type.value.lower() in type_filter]

    if not tools:
        if type_filter:
            console.print(f"[yellow]未找到类型为 {', '.join(type_filter)} 的工具[/]")
        else:
            console.print("[yellow]未找到已注册的工具[/]")
        return

    table = Table(title=f"已注册工具 ({len(tools)} 个)")
    table.add_column("名称", style="cyan")
    table.add_column("类型", style="green")
    table.add_column("描述", style="dim", max_width=50)
    table.add_column("属性", style="magenta")
    table.add_column("版本", style="dim")
    table.add_column("ID", style="dim")

    for tool in tools:
        tool_type = tool.execution.type.value.lower() if tool.execution.type else "unknown"
        version = tool.metadata.version or "-"
        table.add_row(
            tool.name,
            tool_type,
            tool.description,
            tool.scope,
            version,
            tool.id,
        )

    console.print(table)


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

    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)

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
    )
    memory_recall_tool.id = "builtin.memory_recall"
    registry.register(memory_recall_tool)
    registered_tools.append(memory_recall_tool.name)

    # ==================== 默认 MCP 工具 (default) ====================
    # 从包内 defaults/ 目录读取配置，注册为 default 类别

    defaults_dir = Path(__file__).parent.parent.parent / "tools" / "defaults"

    for json_file in sorted(defaults_dir.glob("*.json")):
        server_name = json_file.stem  # filesystem, fetch, serper-search
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                json_config = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            console.print(f"[red][ERROR][/] 读取默认 MCP 配置失败: {json_file.name}: {e}")
            continue

        servers, config_server_name = extract_mcp_servers_config(json_config)
        if not servers or not config_server_name:
            console.print(f"[yellow][WARN][/] 跳过无效配置: {json_file.name}[/]")
            continue

        server_config = servers[config_server_name]
        final_command = server_config.get("command")
        final_args = server_config.get("args", [])
        final_env = server_config.get("env") or {}
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


def add_tool(
    console: Console,
    name: str,
    command: str,
    description: Optional[str] = None,
    scope: str = "user",
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> None:
    """注册 CLI/Bash 工具到工具箱

    Args:
        console: Rich 控制台对象
        name: 工具名称
        command: 命令模板（可用 {args} 作为参数占位符）
        description: 工具描述
        category: 工具分类
        base_dir: 基础目录
        config_file: 配置文件路径
    """
    config = load_config(base_dir=base_dir, config_file=config_file)
    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)

    # 检查是否已存在
    existing = registry.get_by_name(name)
    if existing:
        console.print(f"[yellow]工具 {name} 已存在 (ID: {existing.id})，将更新[/]")

    tool_desc = description or f"CLI tool: {name}"
    tool_id = f"cli.{name}"

    tool = Tool(
        id=tool_id,
        name=name,
        description=tool_desc,
        parameters={
            "type": "object",
            "properties": {
                "args": {"type": "string", "description": f"传递给 {name} 的参数"},
            },
            "required": [],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.BASH,
            shell_command=command,
            shell="bash",
        ),
        scope=scope,
        metadata=ToolMetadata(
            tags=["cli", name, "learned"],
        ),
    )

    registry.register(tool)
    console.print(f"[green][OK][/] 已注册工具: {name} ({tool_id})")
    console.print(f"  命令模板: {command}")
    console.print(f"  属性: {scope}")


def remove_tools(
    console: Console,
    tool_identifier: str,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
    keep_credentials: bool = False,
) -> None:
    """移除已注册的工具（支持所有类型：function/cli/http/skill/mcp/bash）

    builtin 和 default 类别的工具受保护，不可删除。

    Args:
        console: Rich 控制台对象
        tool_identifier: 工具名称或 ID
        base_dir: 基础目录
        config_file: 配置文件路径
        keep_credentials: 是否保留工具凭证配置
    """
    config = load_config(base_dir=base_dir, config_file=config_file)
    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)

    # 查找工具：先按 ID 查找，再按名称查找
    tool = registry.get(tool_identifier) or registry.get_by_name(tool_identifier)
    if not tool:
        console.print(f"[red][ERROR][/] 未找到工具: {tool_identifier}")
        return

    # 删除保护：builtin 和 default 不可删除
    scope = tool.scope
    if scope in ("builtin", "default"):
        console.print(f"[red][ERROR][/] 工具 {tool.name} 属于 [cyan]{scope}[/] 属性，受保护不可删除")
        if scope == "default":
            console.print("[dim]提示：默认工具可通过 `tools update` 更新版本[/]")
        return

    tool_type = tool.execution.type.value
    console.print(f"即将移除工具: [cyan]{tool.name}[/] (ID: {tool.id}, 类型: {tool_type})")

    # 从注册表删除
    success = registry.delete(tool.id)
    if not success:
        console.print(f"[red][ERROR][/] 移除工具失败: {tool.name}")
        return

    console.print(f"[green][OK][/] 已移除工具: {tool.name}")

    # 清理 runtime.json 中的对应配置
    if not keep_credentials and tool.execution.env:
        _cleanup_credentials(console, tool, base_dir)


def _cleanup_credentials(
    console: Console,
    tool: Tool,
    base_dir: Optional[Path],
) -> None:
    """清理工具对应的 credentials 配置（runtime.json）"""
    runtime_config = load_runtime_config(base_dir=base_dir)
    removed = []
    for env_var in tool.execution.env:
        tool_name = env_var_to_tool_name(env_var)
        if runtime_config.tools_credentials.tools and tool_name in runtime_config.tools_credentials.tools:
            del runtime_config.tools_credentials.tools[tool_name]
            removed.append(f"{env_var} (tools_credentials.{tool_name})")

    if removed:
        save_runtime_config(runtime_config, base_dir=base_dir)
        for item in removed:
            console.print(f"  [dim]已清理凭证配置: {item}[/]")


def _get_latest_version(command: str, install_source: str) -> str | None:
    """查询包的最新版本"""
    try:
        if command in ("npx", "npm"):
            result = subprocess.run(
                ["npm", "view", install_source, "version"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        elif command in ("uvx", "pip"):
            result = subprocess.run(
                ["pip", "index", "versions", install_source],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                # 输出格式: pip index versions package → available versions: 1.0, 0.9
                for line in result.stdout.splitlines():
                    if "available versions" in line.lower() or "LATEST" in line:
                        # 取第一个（最新）版本
                        versions = line.split(":")[-1].strip().split(",")
                        if versions:
                            return versions[0].strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def update_check(
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> None:
    """检查 default 类别 MCP 工具是否有新版本"""
    config = load_config(base_dir=base_dir, config_file=config_file)
    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)

    # 只检查 default 类别的 MCP 工具
    default_tools = [t for t in registry.list_all() if t.scope == "default"]
    if not default_tools:
        console.print("[yellow]没有默认 MCP 工具需要检查[/]")
        return

    console.print(f"检查 {len(default_tools)} 个默认 MCP 工具的版本更新...\n")

    has_update = False
    for tool in default_tools:
        install_source = tool.metadata.install_source
        current_version = tool.metadata.version
        command = tool.execution.command

        if not install_source or not command:
            console.print(f"  [dim]{tool.name}: 无安装源信息，跳过[/]")
            continue

        latest = _get_latest_version(command, install_source)
        if latest and latest != current_version:
            has_update = True
            console.print(
                f"  [yellow]{tool.name}[/]: {current_version or '未知'} → [green]{latest}[/] (可更新)"
            )
        elif latest:
            console.print(f"  [green]{tool.name}[/]: {current_version or '未知'} (已是最新)")
        else:
            console.print(f"  [dim]{tool.name}: 无法查询远程版本[/]")

    if not has_update:
        console.print("\n[green]所有默认工具均为最新版本[/]")


def update_tools(
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> None:
    """更新 default 类别 MCP 工具到最新版本"""
    config = load_config(base_dir=base_dir, config_file=config_file)
    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)

    default_tools = [t for t in registry.list_all() if t.scope == "default"]
    if not default_tools:
        console.print("[yellow]没有默认 MCP 工具需要更新[/]")
        return

    updated_count = 0
    for tool in default_tools:
        install_source = tool.metadata.install_source
        command = tool.execution.command

        if not install_source or not command:
            console.print(f"  [dim]{tool.name}: 无安装源信息，跳过[/]")
            continue

        # 先检查是否有更新
        latest = _get_latest_version(command, install_source)
        current_version = tool.metadata.version
        if latest and latest == current_version:
            console.print(f"  [green]{tool.name}[/]: 已是最新 ({current_version})")
            continue

        # 执行更新
        console.print(f"  更新 {tool.name} ({install_source})...")
        try:
            if command in ("npx", "npm"):
                result = subprocess.run(
                    ["npm", "install", "-g", install_source],
                    capture_output=True, text=True, timeout=120,
                )
            elif command in ("uvx", "pip"):
                result = subprocess.run(
                    ["pip", "install", "--upgrade", install_source],
                    capture_output=True, text=True, timeout=120,
                )
            else:
                console.print(f"  [yellow]{tool.name}: 不支持的包管理器 {command}[/]")
                continue

            if result.returncode != 0:
                console.print(f"  [red]{tool.name}: 更新失败 — {result.stderr[:200]}[/]")
                continue

            # 重新检测版本并更新注册表
            new_version, _ = _detect_package_version(command, tool.execution.args)
            tool.metadata.version = new_version or latest
            tool.updated_at = datetime.utcnow()
            registry.register(tool)
            updated_count += 1
            console.print(f"  [green]{tool.name}[/]: 更新成功 → {new_version or latest}")
        except subprocess.TimeoutExpired:
            console.print(f"  [red]{tool.name}: 更新超时[/]")
        except Exception as e:
            console.print(f"  [red]{tool.name}: 更新失败 — {e}[/]")

    console.print(f"\n[green]更新完成: {updated_count} 个工具已更新[/]")


def add_tool_from_url(
    console: Console,
    url: str,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> None:
    """从 URL 自动安装工具

    支持 pypi 包、MCP 服务器、单个 skill、skillset（多 skill 集合）。
    用 LLM 分析 URL 内容，自动判断类型并注册。
    """
    import httpx

    try:
        from html2text import HTML2Text
    except ImportError:
        console.print("[red][ERROR][/] 需要 html2text 库，请运行: uv add html2text")
        return

    from qd_agents.cli.managers import LLMClientManager, setup_configuration
    from qd_agents.agent.url_analyzer import UrlAnalyzer
    from qd_agents.context import ContextManager
    from qd_agents.prompts import PromptLoader

    config = setup_configuration(console, base_dir=base_dir, config_file=config_file)
    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)

    # 1. 获取 URL 内容
    console.print(f"正在获取 URL 内容: {url}")
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=30)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        console.print(f"[red][ERROR][/] 获取 URL 失败: {e}")
        return

    # HTML → markdown
    h2t = HTML2Text()
    h2t.ignore_links = False
    h2t.ignore_images = True
    h2t.body_width = 0
    content = h2t.handle(resp.text)

    if not content.strip():
        console.print("[red][ERROR][/] URL 内容为空")
        return

    console.print(f"已获取内容 ({len(content)} 字符)，正在分析...")

    # 2. 用 LLM 分析（通过 LLMClientManager 完整初始化）
    prompt_loader = None
    if config.prompts and config.prompts.template_dir:
        prompt_loader = PromptLoader(template_dir=Path(config.prompts.template_dir))

    context_manager = ContextManager(prompt_loader=prompt_loader, base_dir=base_dir)
    provider_name = config.llm.default_provider
    llm_manager = LLMClientManager(console, config, registry, prompt_loader, context_manager)

    async def _analyze_and_register():
        if not await llm_manager.initialize(provider_name):
            console.print("[red][ERROR][/] LLM 初始化失败")
            return None

        try:
            analyzer = UrlAnalyzer(llm_client=llm_manager.llm_client)
            result = await analyzer.analyze(url=url, content=content)

            if not result.success:
                console.print(f"[red][ERROR][/] 分析失败: {result.failure_reason}")
                return None

            console.print(f"分析结果: [cyan]{result.type}[/] — {result.name}: {result.description}")

            # 3. 执行前置安装
            if result.prereqs:
                console.print(f"\n[yellow]前置安装步骤 ({len(result.prereqs)} 个):[/]")
                for prereq in result.prereqs:
                    console.print(f"  执行: {prereq}")
                    try:
                        proc = subprocess.run(
                            prereq, shell=True, capture_output=True, text=True, timeout=120,
                        )
                        if proc.returncode != 0:
                            console.print(f"  [red]失败: {proc.stderr[:200]}[/]")
                        else:
                            console.print(f"  [green]成功[/]")
                    except subprocess.TimeoutExpired:
                        console.print(f"  [red]超时[/]")

            return result
        except Exception as e:
            console.print(f"[red][ERROR][/] 分析失败: {e}")
            return None
        finally:
            await llm_manager.close()

    # 2. 用 LLM 分析
    try:
        result = asyncio.run(_analyze_and_register())
    except Exception as e:
        console.print(f"[red][ERROR][/] 安装失败: {e}")
        return

    if not result:
        return

    # 4. 写入 env_vars 到 runtime.json（在 asyncio 之外，避免 input() 阻塞事件循环）
    if result.env_vars:
        _save_env_vars_to_runtime(console, result.env_vars, base_dir)

    # 5. 按类型注册（重新初始化 LLM 客户端用于 AddSkillAnalyzer）
    async def _register():
        if not await llm_manager.initialize(provider_name):
            console.print("[red][ERROR][/] LLM 初始化失败")
            return
        try:
            if result.type == "mcp":
                _register_mcp_from_url(console, registry, result, url)
            elif result.type == "skill":
                await _register_skill_from_url(
                    console, registry, result, url, base_dir, config_file,
                    llm_manager=llm_manager, all_tools=registry.list_all(),
                )
            elif result.type == "skillset":
                await _register_skillset_from_url(
                    console, registry, result, url, base_dir, config_file,
                    llm_manager=llm_manager,
                )
            elif result.type == "pypi":
                _register_pypi_from_url(console, registry, result, url)
            else:
                console.print(f"[red][ERROR][/] 未知类型: {result.type}")

            console.print(f"\n[green]安装完成[/]")
        finally:
            await llm_manager.close()

    try:
        asyncio.run(_register())
    except Exception as e:
        console.print(f"[red][ERROR][/] 注册失败: {e}")


def _save_env_vars_to_runtime(
    console: Console,
    env_vars: dict[str, str],
    base_dir: Optional[Path],
) -> None:
    """将 env_vars 写入 runtime.json 的 tools_credentials

    env_vars: key=环境变量名, value=获取说明
    如果 runtime.json 中已有该 key 的值则保留，否则提示用户输入。
    """
    import sys

    runtime_config = load_runtime_config(base_dir=base_dir)
    runtime_changed = False

    for env_var, description in env_vars.items():
        tool_name = env_var_to_tool_name(env_var)
        existing = runtime_config.tools_credentials.get_api_key(tool_name)
        if existing:
            console.print(f"  [dim]{env_var}[/]: 从 runtime.json (tools_credentials.{tool_name}) 加载")
        else:
            console.print(f"  [yellow]{env_var}[/] 未配置 — {description}")
            # 检查是否在交互式终端中
            if sys.stdin.isatty():
                api_key_input = input(f"  {env_var}=").strip()
            else:
                # 非交互模式：检查环境变量
                api_key_input = os.environ.get(env_var, "")
                if api_key_input:
                    console.print(f"  [dim]从环境变量 {env_var} 获取[/]")
                else:
                    console.print(f"  [yellow]非交互模式，跳过输入。请稍后手动配置 runtime.json[/]")
                    continue

            if api_key_input:
                runtime_config.tools_credentials.set_api_key(tool_name, api_key_input)
                runtime_changed = True
                console.print(f"  [green]已将 {env_var} 写入 runtime.json (tools_credentials.{tool_name})[/]")
            else:
                console.print(f"  [yellow]警告: {env_var} 未设置，工具执行时可能失败[/]")

    if runtime_changed:
        save_runtime_config(runtime_config, base_dir=base_dir)
        console.print("  [dim]runtime.json 已更新[/]")


def _register_mcp_from_url(
    console: Console,
    registry: ToolRegistry,
    result: "UrlAnalyzeResult",
    url: str,
) -> None:
    """注册 MCP 工具"""
    # 合并 mcp_env 和 env_vars 到 execution.env
    env = dict(result.mcp_env or {})
    for env_var in result.env_vars:
        if env_var not in env:
            env[env_var] = ""

    tool = create_mcp_tool(
        name=result.name,
        description=result.description or f"MCP server: {result.name}",
        server=result.name,
        transport=result.mcp_transport,
        command=result.mcp_command or None,
        args=result.mcp_args,
        env=env,
        source_path=url,
        version=result.version or None,
        install_source=result.install_source or None,
    )
    registry.register(tool)
    console.print(f"[green][OK][/] 已注册 MCP 工具: {result.name}")
    if result.env_vars:
        console.print(f"  所需环境变量: {', '.join(result.env_vars.keys())}")


async def _register_skill_from_url(
    console: Console,
    registry: ToolRegistry,
    result: "UrlAnalyzeResult",
    url: str,
    base_dir: Optional[Path],
    config_file: Optional[Path],
    *,
    llm_manager: "LLMClientManager",
    all_tools: list | None = None,
) -> None:
    """注册单个 skill 工具

    1. 保存 SKILL.md 到 tools/skills/<name>/SKILL.md
    2. 用 AddSkillAnalyzer 分析 SKILL.md 内容
    3. 注册为 SKILL 类型工具
    """
    from qd_agents.agent.add_skill import AddSkillAnalyzer

    skill_md = result.skill_md_content
    if not skill_md:
        console.print(f"[red][ERROR][/] skill {result.name} 没有 SKILL.md 内容")
        return

    # 保存 SKILL.md 到 tools/skills/<name>/
    skills_dir = Path("tools/skills") / result.name
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skills_dir / "SKILL.md"
    skill_file.write_text(skill_md, encoding="utf-8")
    console.print(f"  已保存 SKILL.md → {skill_file}")

    # 用 AddSkillAnalyzer 分析（复用 llm_manager 的 LLM 客户端）
    skill_result = None
    try:
        analyzer = AddSkillAnalyzer(
            llm_client=llm_manager.llm_client,
            context_manager=llm_manager.context_manager,
        )
        skill_result = await analyzer.analyze(
            skill_md=skill_md,
            tools=all_tools or [],
        )
    except Exception as e:
        console.print(f"  [yellow]Skill 分析失败，使用默认配置: {e}[/]")

    # 构建 env：合并 UrlAnalyzeResult.env_vars + AddSkillResult 的 env
    env: dict[str, str] = {}
    for env_var in result.env_vars:
        env[env_var] = ""

    skill_type = "tool_manual"
    if skill_result and skill_result.success:
        skill_type = skill_result.skill_type

    tool = Tool(
        id=f"skill.{result.name}",
        name=result.name,
        description=result.description or skill_md[:200],
        parameters={"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.SKILL,
            env=env,
        ),
        scope="user",
        metadata=ToolMetadata(
            tags=["skill", result.name],
            version=result.version or None,
            install_source=result.install_source or None,
        ),
        dependencies={"skill_type": skill_type},
        source_path=url,
    )
    registry.register(tool)
    console.print(f"[green][OK][/] 已注册 skill: {result.name} (type={skill_type})")
    if result.env_vars:
        console.print(f"  所需环境变量: {', '.join(result.env_vars.keys())}")


async def _register_skillset_from_url(
    console: Console,
    registry: ToolRegistry,
    result: "UrlAnalyzeResult",
    url: str,
    base_dir: Optional[Path],
    config_file: Optional[Path],
    *,
    llm_manager: "LLMClientManager",
) -> None:
    """注册 skillset 中的所有 skill"""
    import httpx

    if not result.skills:
        console.print("[red][ERROR][/] skillset 中没有找到子 skill")
        return

    all_tools = registry.list_all()
    console.print(f"\n发现 {len(result.skills)} 个 skill，逐个安装:\n")

    for skill_info in result.skills:
        console.print(f"  安装 skill: [cyan]{skill_info.name}[/]")

        # 构建单个 skill 的 UrlAnalyzeResult（继承 skillset 的 env_vars 和版本信息）
        skill_result = UrlAnalyzeResult(
            type="skill",
            name=skill_info.name,
            description=skill_info.description,
            skill_md_content="",  # 需要下载
            env_vars=result.env_vars,  # 子 skill 继承 skillset 的环境变量
            version=result.version,
            install_source=result.install_source,
        )

        # 下载 SKILL.md
        if skill_info.skill_md_url:
            try:
                resp = httpx.get(skill_info.skill_md_url, follow_redirects=True, timeout=30)
                resp.raise_for_status()
                skill_result.skill_md_content = resp.text
            except httpx.HTTPError as e:
                console.print(f"    [red]下载 SKILL.md 失败: {e}[/]")
                continue
        else:
            console.print(f"    [yellow]没有 SKILL.md URL，跳过[/]")
            continue

        await _register_skill_from_url(
            console, registry, skill_result, url, base_dir, config_file,
            llm_manager=llm_manager, all_tools=all_tools,
        )

    console.print(f"\n[green]skillset 安装完成: {len(result.skills)} 个 skill[/]")


def _register_pypi_from_url(
    console: Console,
    registry: ToolRegistry,
    result: "UrlAnalyzeResult",
    url: str,
) -> None:
    """注册 pypi 包工具"""
    if result.install_command:
        console.print(f"  执行安装: {result.install_command}")
        try:
            proc = subprocess.run(
                result.install_command, shell=True, capture_output=True, text=True, timeout=120,
            )
            if proc.returncode != 0:
                console.print(f"  [red]安装失败: {proc.stderr[:200]}[/]")
                return
            console.print(f"  [green]安装成功[/]")
        except subprocess.TimeoutExpired:
            console.print(f"  [red]安装超时[/]")
            return

    # 合并 env_vars
    env = {}
    for env_var in result.env_vars:
        env[env_var] = ""

    tool = Tool(
        id=f"pypi.{result.name}",
        name=result.name,
        description=result.description or f"PyPI package: {result.package_name or result.name}",
        parameters={"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module=result.package_name or result.name,
            env=env,
        ),
        scope="user",
        metadata=ToolMetadata(
            tags=["pypi", result.name],
            version=result.version or None,
            install_source=result.install_source or None,
        ),
        source_path=url,
    )
    registry.register(tool)
    console.print(f"[green][OK][/] 已注册 pypi 工具: {result.name}")
    if result.env_vars:
        console.print(f"  所需环境变量: {', '.join(result.env_vars.keys())}")