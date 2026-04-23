"""
MCP 工具管理器

负责 MCP 服务器的连接、工具预加载和缓存。
"""
from __future__ import annotations

import logging
from typing import Any

from ..registry import ToolRegistry

logger = logging.getLogger(__name__)


class MCPToolManager:
    """
    MCP 工具管理器

    管理 MCP 服务器的连接、工具发现和注册。
    """

    def __init__(self, tool_registry: ToolRegistry):
        self._tool_registry = tool_registry
        self._mcp_clients: dict[str, Any] = {}
        self._mcp_tools_cache: dict[str, list[dict[str, Any]]] = {}

    async def preload_mcp_tools(self, mcp_servers: dict[str, Any]) -> None:
        """
        预加载所有 MCP 服务器的工具

        Args:
            mcp_servers: MCP 服务器配置字典
        """
        if not mcp_servers:
            return

        logger.info("Preloading MCP tools from %d servers", len(mcp_servers))

        for server_name, server_config in mcp_servers.items():
            try:
                tools = await self._load_server_tools(server_name, server_config)
                self._mcp_tools_cache[server_name] = tools
                logger.info("Loaded %d tools from MCP server '%s'", len(tools), server_name)
            except Exception as e:
                logger.error("Failed to load tools from MCP server '%s': %s", server_name, e)

    async def _load_server_tools(
        self,
        server_name: str,
        server_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        加载单个 MCP 服务器的工具

        Args:
            server_name: 服务器名称
            server_config: 服务器配置

        Returns:
            工具列表
        """
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            server_params = StdioServerParameters(
                command=server_config.get("command", ""),
                args=server_config.get("args", []),
                env=server_config.get("env"),
            )

            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()

                    tools = []
                    for tool in result.tools:
                        tool_info = {
                            "name": tool.name,
                            "description": tool.description or "",
                            "inputSchema": tool.inputSchema,
                            "server_name": server_name,
                        }
                        tools.append(tool_info)

                        # 注册到工具注册表
                        self._tool_registry.register(
                            name=tool.name,
                            description=tool.description or "",
                            input_schema=tool.inputSchema,
                        )

                    return tools

        except ImportError:
            logger.warning("MCP SDK not installed, skipping MCP tool loading")
            return []
        except Exception as e:
            logger.error("Error loading MCP tools from '%s': %s", server_name, e)
            return []

    def get_cached_tools(self, server_name: str | None = None) -> list[dict[str, Any]]:
        """
        获取缓存的 MCP 工具

        Args:
            server_name: 服务器名称，None 则返回所有

        Returns:
            工具列表
        """
        if server_name:
            return self._mcp_tools_cache.get(server_name, [])
        all_tools: list[dict[str, Any]] = []
        for tools in self._mcp_tools_cache.values():
            all_tools.extend(tools)
        return all_tools

    def get_all_tool_names(self) -> list[str]:
        """获取所有已加载的 MCP 工具名称"""
        names: list[str] = []
        for tools in self._mcp_tools_cache.values():
            for tool in tools:
                names.append(tool["name"])
        return names

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """
        调用 MCP 工具

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            工具执行结果
        """
        # 找到工具所在的服务器
        for server_name, tools in self._mcp_tools_cache.items():
            for tool in tools:
                if tool["name"] == tool_name:
                    return await self._call_server_tool(
                        server_name=server_name,
                        mcp_servers={},  # will be passed from caller
                        tool_name=tool_name,
                        arguments=arguments,
                    )

        raise ValueError(f"MCP tool '{tool_name}' not found in any server")

    async def _call_server_tool(
        self,
        server_name: str,
        mcp_servers: dict[str, Any],
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """调用指定服务器的工具"""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            server_config = mcp_servers.get(server_name, {})
            server_params = StdioServerParameters(
                command=server_config.get("command", ""),
                args=server_config.get("args", []),
                env=server_config.get("env"),
            )

            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    return result

        except ImportError:
            raise RuntimeError("MCP SDK not installed")
        except Exception as e:
            raise RuntimeError(f"Error calling MCP tool '{tool_name}': {e}") from e

    async def close(self) -> None:
        """关闭所有 MCP 客户端连接"""
        for name, client in self._mcp_clients.items():
            try:
                if hasattr(client, "close"):
                    await client.close()
            except Exception as e:
                logger.error("Error closing MCP client '%s': %s", name, e)
        self._mcp_clients.clear()
        self._mcp_tools_cache.clear()
