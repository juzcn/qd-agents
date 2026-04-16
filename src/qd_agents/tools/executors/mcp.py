"""
MCP 工具执行器

处理 Model Context Protocol 工具的执行器。
"""
from __future__ import annotations

import logging
from typing import Any

from .base import ToolExecutor
from qd_agents.registry import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType


logger = logging.getLogger(__name__)


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