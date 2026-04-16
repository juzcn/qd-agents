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
from qd_agents.tools.executor import create_mcp_tool
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
        console.print(f"  - [cyan]{tool.name}[/] ({tool.id})")
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

    registered_tools: List[str] = []

    # ==================== 元工具 ====================

    # meta.direct
    direct_tool = Tool(
        id="meta.direct",
        name="direct",
        description="直接生成自然语言回复，不调用任何工具",
        parameters={
            "type": "object",
            "properties": {
                "response": {"type": "string", "description": "生成的回复内容"}
            },
            "required": ["response"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.agent.builtin_tools",
            function="meta_direct",
        ),
        metadata=ToolMetadata(
            category="meta",
            tags=["meta", "direct"],
        ),
    )
    registry.register(direct_tool)
    registered_tools.append(direct_tool.name)

    # meta.find_tools
    find_tools_tool = Tool(
        id="meta.find_tools",
        name="find_tools",
        description="根据用户需求检索相关工具",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "用于检索工具的关键词或描述"}
            },
            "required": ["query"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.agent.builtin_tools",
            function="meta_find_tools",
        ),
        metadata=ToolMetadata(
            category="meta",
            tags=["meta", "search", "tools"],
        ),
    )
    registry.register(find_tools_tool)
    registered_tools.append(find_tools_tool.name)

    # meta.coding_tool_use
    coding_tool = Tool(
        id="meta.coding_tool_use",
        name="coding_tool_use",
        description="生成Python代码来编排多个工具的执行（支持条件、循环等复杂逻辑）。优先使用已注册工具，也可使用Python标准库进行数据处理。",
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python代码字符串，可调用已注册工具，也可使用Python标准库"}
            },
            "required": ["code"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.agent.builtin_tools",
            function="meta_coding_tool_use",
        ),
        metadata=ToolMetadata(
            category="meta",
            tags=["meta", "coding", "workflow"],
        ),
    )
    registry.register(coding_tool)
    registered_tools.append(coding_tool.name)

    # meta.step_down
    step_down_tool = Tool(
        id="meta.step_down",
        name="step_down",
        description="当无法通过工具完成任务时，降级为人工友好的回复",
        parameters={
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": ["no_matching_tools", "too_complex", "safety_concern", "user_confirmation_required"],
                    "description": "降级原因",
                },
                "message": {"type": "string", "description": "给用户的友好提示信息"},
            },
            "required": ["reason", "message"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.agent.builtin_tools",
            function="meta_step_down",
        ),
        metadata=ToolMetadata(
            category="meta",
            tags=["meta", "fallback"],
        ),
    )
    registry.register(step_down_tool)
    registered_tools.append(step_down_tool.name)

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

    # search.web (统一接口)
    web_search_tool = Tool(
        id="search.web",
        name="web_search",
        description="通用网络搜索工具，自动选择合适的搜索引擎进行搜索",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词或问题"},
                "num_results": {
                    "type": "integer",
                    "description": "返回结果数量，默认 5",
                    "default": 5,
                },
                "engine": {
                    "type": "string",
                    "enum": ["auto", "serper", "tavily", "baidu"],
                    "description": "指定搜索引擎，auto 表示自动选择",
                    "default": "auto",
                },
                "language": {
                    "type": "string",
                    "description": "搜索结果语言偏好，例如 zh-CN、en-US",
                    "default": "zh-CN",
                },
            },
            "required": ["query"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.agent.builtin_tools",
            function="web_search",
        ),
        metadata=ToolMetadata(
            category="search",
            tags=["web", "search", "unified"],
        ),
    )
    registry.register(web_search_tool)
    registered_tools.append(web_search_tool.name)

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

    # ==================== MCP 天气工具 ====================

    # 当前天气工具
    current_weather_tool = create_mcp_tool(
        name="get_current_weather",
        description="获取指定城市的当前天气信息，包括温度、湿度、风速、天气描述等",
        server="weather",
        tool_name="get_current_weather",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名称，如 'Beijing' 或 '上海'"},
                "country": {"type": "string", "description": "国家代码，如 'CN'，可选"},
                "latitude": {"type": "number", "description": "纬度，可选"},
                "longitude": {"type": "number", "description": "经度，可选"},
            },
            "required": ["city"],
        },
        transport="sse",
        endpoint="http://localhost:8000",
        category="weather",
        tags=["weather", "mcp", "current"],
    )
    registry.register(current_weather_tool)
    registered_tools.append(current_weather_tool.name)

    # 空气质量工具
    air_quality_tool = create_mcp_tool(
        name="get_air_quality",
        description="获取指定城市的空气质量信息，包括 PM2.5、PM10、臭氧、NO₂、CO 等级和健康建议",
        server="weather",
        tool_name="get_air_quality",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名称"},
                "country": {"type": "string", "description": "国家代码，可选"},
            },
            "required": ["city"],
        },
        transport="sse",
        endpoint="http://localhost:8000",
        category="air-quality",
        tags=["air-quality", "mcp", "pollution"],
    )
    registry.register(air_quality_tool)
    registered_tools.append(air_quality_tool.name)

    console.print(f"[green]已注册内置工具 ({len(registered_tools)} 个):[/]")
    for tool_name in registered_tools:
        console.print(f"  - {tool_name}")