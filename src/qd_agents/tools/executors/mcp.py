"""
MCP 工具执行器

处理 MCP (Model Context Protocol) 服务器的工具执行器。
支持 stdio, SSE, streamable-http 传输模式。
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from .base import ToolExecutor
from qd_agents.registry import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType


logger = logging.getLogger(__name__)


class MCPToolExecutor(ToolExecutor):
    """MCP 工具执行器"""

    def __init__(
        self,
        server: str,
        transport: str = "stdio",
        command: str | None = None,
        args: list[str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 30,
    ):
        """
        初始化 MCP 工具执行器

        Args:
            server: MCP服务器标识
            transport: 传输模式 ("stdio", "sse", "streamable-http")
            command: stdio 模式下的命令（如 "npx"）
            args: stdio 模式下的参数（如 ["-y", "@modelcontextprotocol/server-weather"]）
            url: SSE 或 streamable-http 模式的 URL
            headers: HTTP 请求头
            timeout: 超时时间（秒）
        """
        self.server = server
        self.transport = transport
        self.command = command
        self.args = args or []
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout

        # 缓存客户端会话
        self._session: ClientSession | None = None
        self._tools_cache: dict[str, dict] = {}

    async def _ensure_connected(self) -> None:
        """确保连接到 MCP 服务器"""
        if self._session is not None and self._client is not None:
            return

        logger.info(f"Connecting to MCP server via {self.transport}: {self.server}")

        if self.transport == "stdio":
            if not self.command:
                raise ValueError("stdio transport requires command")

            server_params = StdioServerParameters(
                command=self.command,
                args=self.args,
                env=None,
            )
            stdio_transport = await stdio_client(server_params)
            self._session = ClientSession(*stdio_transport)

        elif self.transport == "sse":
            if not self.url:
                raise ValueError("sse transport requires url")

            async with sse_client(
                url=self.url,
                headers=self.headers,
            ) as sse_transport:
                self._session = ClientSession(*sse_transport)

        elif self.transport == "streamable-http":
            if not self.url:
                raise ValueError("streamable-http transport requires url")

            async with streamable_http_client(
                url=self.url,
                headers=self.headers,
            ) as http_transport:
                self._session = ClientSession(*http_transport)

        else:
            raise ValueError(f"Unsupported transport: {self.transport}")

        # 初始化会话
        await self._session.__aenter__()

        # 获取可用工具
        tools_result = await self._session.list_tools()
        for tool in tools_result.tools:
            self._tools_cache[tool.name] = tool

    async def execute(self, **kwargs: Any) -> Any:
        """执行 MCP 工具"""
        await self._ensure_connected()

        if not self._session:
            raise RuntimeError("MCP client not initialized")

        # 从工具名获取实际的工具名称
        # 假设第一个参数是 tool_name，或者从上下文中获取
        tool_name = kwargs.pop("tool_name", None)
        if not tool_name:
            # 如果没有指定 tool_name，尝试从参数中推断
            # 这里需要根据具体实现调整
            raise ValueError("tool_name is required for MCP execution")

        if tool_name not in self._tools_cache:
            raise ValueError(f"Tool not found: {tool_name}. Available tools: {list(self._tools_cache.keys())}")

        logger.info(f"Executing MCP tool: {tool_name}")

        try:
            result = await self._session.call_tool(tool_name, arguments=kwargs)
            return result.content
        except Exception as e:
            logger.error(f"Error executing MCP tool {tool_name}: {e}")
            raise

    async def close(self) -> None:
        """关闭连接"""
        if self._session:
            await self._session.__aexit__(None, None, None)
            self._session = None

    async def __aenter__(self):
        await self._ensure_connected()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


def create_mcp_tool(
    name: str,
    description: str,
    server: str,
    transport: str = "stdio",
    command: str | None = None,
    args: list[str] | None = None,
    url: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    parameters: dict[str, Any] | None = None,
) -> Tool:
    """创建 MCP 工具"""
    return Tool(
        id=f"mcp.{name}",
        name=name,
        description=description,
        parameters=parameters or {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "要执行的 MCP 工具名称"},
            },
            "required": ["tool_name"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.MCP,
            server=server,
            transport=transport,
            command=command,
            args=args or [],
            endpoint=url,  # 复用 endpoint 字段用于 URL
            headers=headers or {},
            timeout=timeout,
        ),
        metadata=ToolMetadata(
            category="mcp",
            tags=["mcp", server],
        ),
    )