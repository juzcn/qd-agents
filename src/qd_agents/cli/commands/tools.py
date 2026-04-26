"""
工具管理命令

负责列出和初始化工具。
"""

import sys
from pathlib import Path
from typing import Optional, List

from rich.console import Console
from rich.table import Table

from qd_agents.config import load_config, load_runtime_config, save_runtime_config
from qd_agents.registry import ToolRegistry, Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType
from qd_agents.tools.builtins import echo


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
    table.add_column("分类", style="magenta")
    table.add_column("ID", style="dim")

    for tool in tools:
        tool_type = tool.execution.type.value.lower() if tool.execution.type else "unknown"
        table.add_row(
            tool.name,
            tool_type,
            tool.description,
            tool.metadata.category,
            tool.id,
        )

    console.print(table)


def init_tools(
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> None:
    """
    初始化工具

    Args:
        console: Rich 控制台对象
        base_dir: 基础目录
        config_file: 配置文件路径
    """
    config = load_config(base_dir=base_dir, config_file=config_file)

    # 确保数据目录存在
    if config.storage:
        config.storage.data_dir.mkdir(parents=True, exist_ok=True)

    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)

    # 清空现有工具（确保初始化前数据库干净）
    registry.clear_all()

    registered_tools: List[str] = []

    # ==================== 元工具 ====================
    # 注意：元工具已移除，使用标准的 OpenAI Tool Calling 格式



    # ==================== 搜索工具 ====================

    # search.serper
    serper_tool = Tool(
        id="search.serper",
        name="serper_search",
        description="使用 Serper API 进行网络搜索，获取网页摘要和链接",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词或问题"},
                "num": {"type": "integer", "description": "返回结果数量，默认 10", "default": 10},
            },
            "required": ["query"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.tools.builtin_search",
            function="serper_search",
        ),
        metadata=ToolMetadata(
            category="search",
            tags=["web", "search", "serper"],
        ),
    )
    registry.register(serper_tool)
    registered_tools.append(serper_tool.name)

    # search.tavily
    tavily_tool = Tool(
        id="search.tavily",
        name="tavily_search",
        description="使用 Tavily API 进行 AI 增强的网络搜索，支持深度搜索和答案提取",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词或问题"},
                "search_depth": {
                    "type": "string",
                    "enum": ["basic", "advanced"],
                    "description": "搜索深度，默认 basic",
                    "default": "basic",
                },
                "include_answer": {
                    "type": "boolean",
                    "description": "是否包含 AI 生成的答案",
                    "default": True,
                },
                "max_results": {
                    "type": "integer",
                    "description": "返回结果数量，默认 5",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.tools.builtin_search",
            function="tavily_search",
        ),
        metadata=ToolMetadata(
            category="search",
            tags=["web", "search", "tavily", "ai"],
        ),
    )
    registry.register(tavily_tool)
    registered_tools.append(tavily_tool.name)



    # ==================== 实用工具 ====================

    # util.echo
    echo_tool = Tool(
        id="util.echo",
        name="echo",
        description="回显输入的消息",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "要回显的消息"}
            },
            "required": ["message"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.tools.builtins",
            function="echo",
        ),
        metadata=ToolMetadata(
            category="utilities",
            tags=["echo", "utility"],
        ),
    )
    registry.register(echo_tool)
    registered_tools.append(echo_tool.name)


    # ==================== Bash 工具 ====================
    from qd_agents.tools.executors import create_bash_tool

    # 通用bash执行工具
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
        category="shell",
        tags=["bash", "shell", "command"],
    )
    registry.register(bash_tool)
    registered_tools.append(bash_tool.name)


    # 获取所有工具对象并显示类型
    all_tools = registry.list_all()
    console.print(f"[green]已注册内置工具 ({len(all_tools)} 个):[/]")
    for tool in all_tools:
        # 获取工具类型并转换为小写字符串
        tool_type = tool.execution.type.value.lower() if tool.execution.type else "unknown"
        console.print(f"  - {tool.name}({tool_type})")


def add_tool(
    console: Console,
    name: str,
    command: str,
    description: Optional[str] = None,
    category: str = "cli",
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
        metadata=ToolMetadata(
            category=category,
            tags=["cli", name, "learned"],
        ),
    )

    registry.register(tool)
    console.print(f"[green][OK][/] 已注册工具: {name} ({tool_id})")
    console.print(f"  命令模板: {command}")
    console.print(f"  分类: {category}")


def remove_tools(
    console: Console,
    tool_identifier: str,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
    keep_credentials: bool = False,
) -> None:
    """移除已注册的工具（支持所有类型：function/cli/http/skill/mcp/bash）

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
    from qd_agents.cli.commands.skills import _env_var_to_tool_name

    runtime_config = load_runtime_config(base_dir=base_dir)
    removed = []
    for env_var in tool.execution.env:
        tool_name = _env_var_to_tool_name(env_var)
        if runtime_config.tools_credentials.tools and tool_name in runtime_config.tools_credentials.tools:
            del runtime_config.tools_credentials.tools[tool_name]
            removed.append(f"{env_var} (tools_credentials.{tool_name})")

    if removed:
        save_runtime_config(runtime_config, base_dir=base_dir)
        for item in removed:
            console.print(f"  [dim]已清理凭证配置: {item}[/]")