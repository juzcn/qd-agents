"""
工具管理命令

负责列出和初始化工具。
"""

import sys
from pathlib import Path
from typing import Optional, List

from rich.console import Console

from qd_agents.config import load_config
from qd_agents.registry import ToolRegistry, Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType
from qd_agents.agent.builtins import echo


def list_tools(
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> None:
    """
    列出工具

    Args:
        console: Rich 控制台对象
        base_dir: 基础目录
        config_file: 配置文件路径
    """
    config = load_config(base_dir=base_dir, config_file=config_file)

    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)


    tools = registry.list_all()

    console.print(f"[bold]已注册工具 ({len(tools)} 个):[/]\n")
    for tool in tools:
        # 获取工具类型
        tool_type = tool.execution.type.value.lower() if tool.execution.type else "unknown"
        console.print(f"  - [cyan]{tool.name}[/]({tool_type}) ({tool.id})")
        console.print(f"    描述: {tool.description}", style="dim")
        console.print(f"    分类: {tool.metadata.category}", style="dim")
        console.print()


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
            module="qd_agents.agent.builtin_tools",
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
            module="qd_agents.agent.builtin_tools",
            function="tavily_search",
        ),
        metadata=ToolMetadata(
            category="search",
            tags=["web", "search", "tavily", "ai"],
        ),
    )
    registry.register(tavily_tool)
    registered_tools.append(tavily_tool.name)

    # search.baidu
    baidu_tool = Tool(
        id="search.baidu",
        name="baidu_search",
        description="使用百度搜索 API 进行中文网络搜索",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词或问题"},
                "count": {"type": "integer", "description": "返回结果数量，默认 10 (1-50)", "default": 10},
            },
            "required": ["query"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.agent.builtin_tools",
            function="baidu_search",
        ),
        metadata=ToolMetadata(
            category="search",
            tags=["web", "search", "baidu", "chinese"],
        ),
    )
    registry.register(baidu_tool)
    registered_tools.append(baidu_tool.name)


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
            module="qd_agents.agent.builtins",
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