"""
工具执行器 - 支持多种执行类型
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from abc import ABC, abstractmethod
from typing import Any, Callable

import httpx

from ..registry import Tool, ToolExecutionType


logger = logging.getLogger(__name__)


class ToolExecutor(ABC):
    """工具执行器基类"""

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """执行工具"""
        pass


class HTTPToolExecutor(ToolExecutor):
    """HTTP 工具执行器"""

    def __init__(
        self,
        endpoint: str,
        method: str = "POST",
        headers: dict[str, str] | None = None,
        timeout: int = 30,
    ):
        self.endpoint = endpoint
        self.method = method.upper()
        self.headers = headers or {}
        self.timeout = timeout

    async def execute(self, **kwargs: Any) -> Any:
        logger.info("Executing HTTP tool: %s %s", self.method, self.endpoint)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            request_kwargs: dict[str, Any] = {
                "headers": self.headers,
            }

            if self.method in ["POST", "PUT", "PATCH"]:
                request_kwargs["json"] = kwargs
            else:
                request_kwargs["params"] = kwargs

            response = await client.request(
                method=self.method,
                url=self.endpoint,
                **request_kwargs
            )

            response.raise_for_status()
            return response.json()


class CLIToolExecutor(ToolExecutor):
    """CLI 工具执行器"""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        timeout: int = 30,
    ):
        self.command = command
        self.args = args or []
        self.timeout = timeout

    def _format_arg(self, arg: str, **kwargs: Any) -> str:
        """格式化参数，替换占位符"""
        result = arg
        for key, value in kwargs.items():
            placeholder = f"{{{key}}}"
            if placeholder in result:
                result = result.replace(placeholder, str(value))
        return result

    async def execute(self, **kwargs: Any) -> Any:
        import shlex

        # 构建命令
        cmd_parts = [self.command]
        cmd_parts.extend(self._format_arg(arg, **kwargs) for arg in self.args)

        cmd_str = " ".join(shlex.quote(p) for p in cmd_parts)
        logger.info("Executing CLI tool: %s", cmd_str)

        # 执行命令
        proc = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"Command timed out after {self.timeout}s")

        if proc.returncode != 0:
            raise RuntimeError(
                f"Command failed with code {proc.returncode}: "
                f"{stderr.decode()}"
            )

        try:
            return json.loads(stdout.decode())
        except json.JSONDecodeError:
            return {"output": stdout.decode()}


class FunctionToolExecutor(ToolExecutor):
    """Python 函数执行器"""

    def __init__(self, func: Callable):
        self.func = func

    async def execute(self, **kwargs: Any) -> Any:
        logger.info("Executing function tool: %s", self.func.__name__)

        if asyncio.iscoroutinefunction(self.func):
            return await self.func(**kwargs)
        else:
            return self.func(**kwargs)


class SkillToolExecutor(ToolExecutor):
    """Skill 工具执行器（工作流）"""

    def __init__(self, skill_id: str):
        self.skill_id = skill_id

    async def execute(self, **kwargs: Any) -> Any:
        logger.info("Executing skill tool: %s", self.skill_id)
        # Skill 执行在后续版本实现
        raise NotImplementedError("Skill execution not implemented yet")


class MCPToolExecutor(ToolExecutor):
    """MCP 工具执行器"""

    def __init__(self, server: str, tool: str):
        self.server = server
        self.tool = tool

    async def execute(self, **kwargs: Any) -> Any:
        logger.info("Executing MCP tool: %s/%s", self.server, self.tool)
        # MCP 执行在后续版本实现
        raise NotImplementedError("MCP execution not implemented yet")


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

    elif exec_config.type == ToolExecutionType.FUNCTION:
        # 函数工具需要单独注册
        raise NotImplementedError(
            "Function tools must be registered with ToolExecutorRegistry"
        )

    elif exec_config.type == ToolExecutionType.SKILL:
        if not exec_config.skill_id:
            raise ValueError("Skill tool requires skill_id")
        return SkillToolExecutor(skill_id=exec_config.skill_id)

    elif exec_config.type == ToolExecutionType.MCP:
        if not exec_config.server or not exec_config.tool:
            raise ValueError("MCP tool requires server and tool")
        return MCPToolExecutor(
            server=exec_config.server,
            tool=exec_config.tool,
        )

    else:
        raise ValueError(f"Unknown tool type: {exec_config.type}")


def create_http_tool(
    name: str,
    description: str,
    endpoint: str,
    method: str = "POST",
    parameters: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> Tool:
    """创建 HTTP 工具"""
    from ..registry import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType

    return Tool(
        id=name,
        name=name,
        description=description,
        parameters=parameters or {"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.HTTP,
            endpoint=endpoint,
            method=method,
            headers=headers or {},
            timeout=timeout,
        ),
        metadata=ToolMetadata(),
    )


def create_cli_tool(
    name: str,
    description: str,
    command: str,
    args: list[str] | None = None,
    parameters: dict[str, Any] | None = None,
    timeout: int = 30,
) -> Tool:
    """创建 CLI 工具"""
    from ..registry import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType

    return Tool(
        id=name,
        name=name,
        description=description,
        parameters=parameters or {"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.CLI,
            command=command,
            args=args or [],
            timeout=timeout,
        ),
        metadata=ToolMetadata(),
    )


def create_function_tool(
    name: str,
    description: str,
    func: Callable,
    parameters: dict[str, Any] | None = None,
) -> Tool:
    """创建 Python 函数工具"""
    from ..registry import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType

    return Tool(
        id=name,
        name=name,
        description=description,
        parameters=parameters or {"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module=func.__module__,
            function=func.__name__,
        ),
        metadata=ToolMetadata(),
    )


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
