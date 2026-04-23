"""
工具模块

包含工具执行器、内置工具函数和工具注册表。
"""
from .executors import (
    ToolExecutor,
    ToolExecutorRegistry,
    create_executor,
    create_http_tool,
    create_cli_tool,
    create_function_tool,
    create_bash_tool,
    HTTPToolExecutor,
    CLIToolExecutor,
    BashToolExecutor,
    FunctionToolExecutor,
)
from .builtins import echo
from .builtin_search import serper_search, tavily_search
from .mcp_manager import MCPToolManager

__all__ = [
    "ToolExecutor",
    "ToolExecutorRegistry",
    "create_executor",
    "create_http_tool",
    "create_cli_tool",
    "create_function_tool",
    "create_bash_tool",
    "HTTPToolExecutor",
    "CLIToolExecutor",
    "BashToolExecutor",
    "FunctionToolExecutor",
    # 内置搜索工具
    "echo",
    "serper_search",
    "tavily_search",
    # MCP 管理
    "MCPToolManager",
]
