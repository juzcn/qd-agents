"""
工具执行器模块

按类型拆分的工具执行器模块，通过此文件统一导出。
"""

from .base import ToolExecutor
from .http import HTTPToolExecutor, create_http_tool
from .cli import CLIToolExecutor, BashToolExecutor, create_cli_tool, create_bash_tool
from .function import FunctionToolExecutor, create_function_tool
from .mcp import MCPToolExecutor, create_mcp_tool, extract_mcp_servers_config
from .factories import create_executor, ToolExecutorRegistry

# 重新导出所有公共API
__all__ = [
    # 基类
    "ToolExecutor",

    # 执行器类
    "HTTPToolExecutor",
    "CLIToolExecutor",
    "BashToolExecutor",
    "FunctionToolExecutor",
    "MCPToolExecutor",

    # 工厂函数
    "create_executor",

    # 工具创建函数
    "create_http_tool",
    "create_cli_tool",
    "create_bash_tool",
    "create_function_tool",
    "create_mcp_tool",
    "extract_mcp_servers_config",

    # 注册表
    "ToolExecutorRegistry",
]