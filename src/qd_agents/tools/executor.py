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

        # 始终返回包含 stdout、stderr 和 returncode 的结构化结果
        # 这样可以保持与 OpenAI tool calling 标准的兼容性
        result = {
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
            "returncode": proc.returncode,
            "success": proc.returncode == 0
        }

        # 如果输出是 JSON，也提供解析后的版本
        try:
            result["json"] = json.loads(stdout.decode())
        except json.JSONDecodeError:
            pass

        return result


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

    def __init__(
        self,
        server: str,
        tool: str,
        transport: str = "stdio",
        endpoint: str | None = None,
    ):
        """
        初始化 MCP 工具执行器

        Args:
            server: MCP 服务器标识符（如 "weather"）
            tool: 工具名称（如 "get_current_weather"）
            transport: 传输模式，支持 "stdio", "sse", "streamable-http"
            endpoint: HTTP 端点（仅用于 SSE 或 Streamable HTTP 模式）
        """
        self.server = server
        self.tool = tool
        self.transport = transport
        self.endpoint = endpoint

    async def execute(self, **kwargs: Any) -> Any:
        logger.info("Executing MCP tool: %s/%s via %s", self.server, self.tool, self.transport)

        if self.transport in ["sse", "streamable-http"]:
            try:
                return await self._execute_http(**kwargs)
            except Exception as e:
                logger.warning("HTTP mode failed for MCP tool %s/%s: %s", self.server, self.tool, e)
                logger.warning("Falling back to simplified mode for demonstration")
                # 降级到简化模式，返回模拟数据
                return await self._execute_simplified(**kwargs)
        else:
            # stdio 模式需要完整的 MCP 客户端实现
            # 这里提供一个简化的 HTTP 模拟实现作为示例
            return await self._execute_simplified(**kwargs)

    async def _execute_http(self, **kwargs: Any) -> Any:
        """通过 HTTP 执行 MCP 工具"""
        if not self.endpoint:
            raise ValueError(f"HTTP endpoint required for {self.transport} transport")

        import httpx

        # MCP HTTP 协议可能使用不同的端点
        # 尝试常见的 MCP 端点
        endpoints_to_try = [
            self.endpoint,  # 原始端点
            f"{self.endpoint}/sse",  # SSE 专用端点
            f"{self.endpoint}/message",  # MCP 消息端点
            f"{self.endpoint}/tools/{self.tool}/call",  # 直接工具调用端点
        ]

        # 根据 mcp-weather-server 的 HTTP API 格式调用
        # 注意：实际实现需要根据具体的 MCP HTTP 协议实现
        async with httpx.AsyncClient(timeout=30) as client:
            last_error = None
            for endpoint in endpoints_to_try:
                try:
                    logger.info("Trying MCP endpoint: %s", endpoint)
                    if self.transport == "sse":
                        # SSE 模式：建立 Server-Sent Events 连接
                        # 这里简化实现，实际需要处理 SSE 流
                        response = await client.post(
                            endpoint,
                            json={
                                "method": f"tools/{self.tool}/call",
                                "params": kwargs,
                            }
                        )
                    else:  # streamable-http
                        # Streamable HTTP 模式
                        response = await client.post(
                            endpoint,
                            json={
                                "method": f"tools/{self.tool}/call",
                                "params": kwargs,
                            }
                        )

                    response.raise_for_status()
                    result = response.json()
                    logger.info("MCP tool %s/%s executed successfully via %s (endpoint: %s)",
                               self.server, self.tool, self.transport, endpoint)
                    return result.get("result", result)
                except httpx.HTTPStatusError as e:
                    last_error = e
                    logger.warning("MCP endpoint %s failed with status %s: %s",
                                 endpoint, e.response.status_code, e.response.text[:200])
                    if e.response.status_code == 404:
                        continue  # 尝试下一个端点
                    else:
                        raise
                except Exception as e:
                    last_error = e
                    logger.warning("MCP endpoint %s failed: %s", endpoint, e)
                    continue

            # 所有端点都失败
            if last_error:
                raise last_error
            else:
                raise ValueError(f"All MCP endpoints failed for tool {self.server}/{self.tool}")

    async def _execute_simplified(self, **kwargs: Any) -> Any:
        """简化的 MCP 工具执行（用于演示）"""
        # 在实际项目中，这里应该使用 mcp 库的客户端
        # 与 MCP 服务器进行 stdio 通信

        # 对于天气工具，我们返回一个模拟响应
        if self.server == "weather":
            if self.tool == "get_current_weather":
                return {
                    "temperature": 20.5,
                    "humidity": 65,
                    "description": "晴朗",
                    "city": kwargs.get("city", "未知城市"),
                    "timestamp": "2026-04-16T10:30:00Z"
                }
            elif self.tool == "get_air_quality":
                return {
                    "pm2_5": 35,
                    "pm10": 50,
                    "aqi": 45,
                    "health_advice": "空气质量良好",
                    "city": kwargs.get("city", "未知城市")
                }

        raise NotImplementedError(
            f"MCP tool {self.server}/{self.tool} not implemented. "
            f"Transport mode: {self.transport}"
        )


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
            transport=exec_config.transport,
            endpoint=exec_config.endpoint,
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


def create_mcp_tool(
    name: str,
    description: str,
    server: str,
    tool_name: str,
    parameters: dict[str, Any] | None = None,
    transport: str = "stdio",
    endpoint: str | None = None,
    timeout: int = 30,
    category: str = "mcp",
    tags: list[str] | None = None,
) -> Tool:
    """创建 MCP 工具"""
    from ..registry import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType

    if tags is None:
        tags = ["mcp", server]

    return Tool(
        id=f"{server}.{tool_name}",
        name=name,
        description=description,
        parameters=parameters or {"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.MCP,
            server=server,
            tool=tool_name,
            transport=transport,
            endpoint=endpoint,
            timeout=timeout,
        ),
        metadata=ToolMetadata(
            category=category,
            tags=tags,
        ),
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
