"""
执行器工厂和注册表

包含执行器创建工厂函数和工具执行器注册表。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from .base import ToolExecutor
from .http import HTTPToolExecutor, create_http_tool
from .cli import CLIToolExecutor, BashToolExecutor, create_cli_tool, create_bash_tool
from .function import FunctionToolExecutor, create_function_tool
from .mcp import MCPToolExecutor, create_mcp_tool
from qd_agents.registry import Tool, ToolExecutionType


logger = logging.getLogger(__name__)


def create_executor(tool: Tool) -> ToolExecutor:
    """
    根据工具定义创建执行器

    Args:
        tool: 工具定义

    Returns:
        工具执行器
    """
    exec_config = tool.execution

    if exec_config.type == ToolExecutionType.HTTP:
        if not exec_config.endpoint:
            raise ValueError("HTTP tool requires endpoint")
        return HTTPToolExecutor(
            endpoint=exec_config.endpoint,
            method=exec_config.method or "POST",
            headers=exec_config.headers,
            timeout=exec_config.timeout,
        )

    elif exec_config.type == ToolExecutionType.CLI:
        if not exec_config.command:
            raise ValueError("CLI tool requires command")
        return CLIToolExecutor(
            command=exec_config.command,
            args=exec_config.args,
            timeout=exec_config.timeout,
        )

    elif exec_config.type == ToolExecutionType.BASH:
        if not exec_config.shell_command:
            raise ValueError("BASH tool requires shell_command")
        return BashToolExecutor(
            shell_command=exec_config.shell_command,
            shell=exec_config.shell or "bash",
            timeout=exec_config.timeout,
            env=exec_config.env,
        )

    elif exec_config.type == ToolExecutionType.FUNCTION:
        # 函数工具需要单独注册
        raise NotImplementedError(
            "Function tools must be registered with ToolExecutorRegistry"
        )

    elif exec_config.type == ToolExecutionType.MCP:
        if not exec_config.server:
            raise ValueError("MCP tool requires server configuration")
        return MCPToolExecutor(
            server=exec_config.server,
            transport=exec_config.transport or "stdio",
            command=exec_config.command,
            args=exec_config.args,
            url=exec_config.endpoint,  # 复用 endpoint 作为 URL
            headers=exec_config.headers,
            timeout=exec_config.timeout,
            tool_name=exec_config.tool,  # 如果指定了具体工具名
        )

    elif exec_config.type == ToolExecutionType.SKILL:
        if not exec_config.shell_command:
            raise ValueError("SKILL tool requires shell_command")
        return BashToolExecutor(
            shell_command=exec_config.shell_command,
            shell=exec_config.shell or "bash",
            timeout=exec_config.timeout,
            env=exec_config.env,
        )

    else:
        raise ValueError(f"Unknown tool type: {exec_config.type}")


class ToolExecutorRegistry:
    """工具执行器注册表"""

    def __init__(self):
        self._executors: dict[str, ToolExecutor] = {}
        self._functions: dict[str, Callable] = {}

    def register_function(self, name: str, func: Callable) -> None:
        """注册 Python 函数"""
        self._functions[name] = func

    def register_executor(self, tool_id: str, executor: ToolExecutor) -> None:
        """注册执行器"""
        self._executors[tool_id] = executor

    def get_executor(self, tool: Tool) -> ToolExecutor:
        """获取工具执行器"""
        if tool.id in self._executors:
            return self._executors[tool.id]

        if tool.execution.type == ToolExecutionType.FUNCTION:
            func_name = tool.execution.function
            if func_name and func_name in self._functions:
                return FunctionToolExecutor(self._functions[func_name])

        return create_executor(tool)