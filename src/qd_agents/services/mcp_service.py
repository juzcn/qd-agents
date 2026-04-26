"""
MCP 服务管理

负责 MCP 服务器的预加载、连接、工具展开和资源清理。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..models.tool import Tool, ToolExecutionConfig, ToolExecutionType, ToolMetadata
from ..tools.executors.mcp import MCPToolExecutor

logger = logging.getLogger(__name__)


class MCPService:
    """MCP 服务管理器"""

    def __init__(self) -> None:
        self._tools_cache: dict[str, list[Tool]] = {}
        self._executors_cache: dict[str, Any] = {}

    @property
    def tools_cache(self) -> dict[str, list[Tool]]:
        return self._tools_cache

    @property
    def executors_cache(self) -> dict[str, Any]:
        return self._executors_cache

    async def preload(
        self,
        mcp_tools: list[Tool],
        executor_registry: Any,
    ) -> None:
        """预加载 MCP 工具（会话开始时连接 MCP 服务器）"""
        logger.info("Preloading MCP tools...")

        if not mcp_tools:
            logger.info("No MCP tools found to preload")
            return

        server_configs = self._build_server_configs(mcp_tools)
        if not server_configs:
            logger.warning("No valid MCP server configurations found")
            return

        tasks = [
            self._connect_server(server_key, config, executor_registry)
            for server_key, config in server_configs.items()
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _build_server_configs(self, mcp_tools: list[Tool]) -> dict[str, dict]:
        """从 MCP 工具列表构建服务器配置"""
        server_configs: dict[str, dict] = {}
        for tool in mcp_tools:
            exec_config = tool.execution
            server_key = exec_config.server or ""
            if not server_key:
                logger.warning(f"MCP tool {tool.id} has no server configuration, skipping")
                continue
            if server_key in server_configs:
                continue

            server_configs[server_key] = {
                'type': ToolExecutionType.MCP,
                'server': server_key,
                'transport': exec_config.transport or "stdio",
                'command': exec_config.command,
                'args': exec_config.args,
                'endpoint': exec_config.endpoint,
                'headers': exec_config.headers,
                'env': exec_config.env or {},
                'timeout': exec_config.timeout,
                'tool': None,
            }
        return server_configs

    async def _connect_server(
        self,
        server_key: str,
        config: dict,
        executor_registry: Any,
    ) -> None:
        """连接单个 MCP 服务器"""
        try:
            subtools, executor = await self.get_server_tools(config)
            if subtools:
                logger.info(f"Preloaded MCP server '{server_key}' with {len(subtools)} tools")
                # 注册子工具执行器
                for subtool in subtools:
                    executor_registry.register_executor(subtool.id, executor)
            else:
                logger.warning(f"Failed to preload MCP server '{server_key}'")
        except Exception as e:
            logger.error(f"Error preloading MCP server '{server_key}': {e}")

    async def get_server_tools(self, server_config: dict) -> tuple[list[Tool], Any]:
        """获取 MCP 服务器的工具列表和执行器"""
        server_key = server_config.get('server', '')
        if not server_key:
            return [], None

        if server_key in self._tools_cache:
            logger.debug(f"Using cached MCP tools for server: {server_key}")
            return self._tools_cache[server_key], self._executors_cache.get(server_key)

        try:
            executor = MCPToolExecutor(
                server=server_key,
                transport=server_config.get('transport', 'stdio'),
                command=server_config.get('command'),
                args=server_config.get('args', []),
                url=server_config.get('endpoint'),
                headers=server_config.get('headers', {}),
                env=server_config.get('env', {}),
                timeout=server_config.get('timeout', 30),
            )

            # 连接超时控制
            default_connect_timeout = 5
            if "filesystem" in server_key.lower() or server_config.get('command') in ["npx", "node"]:
                default_connect_timeout = 15

            connect_timeout = server_config.get('timeout', default_connect_timeout)
            try:
                await asyncio.wait_for(executor._ensure_connected(), timeout=connect_timeout)
            except asyncio.TimeoutError:
                logger.error(f"MCP server {server_key} connection timeout after {connect_timeout}s")
                await self._safe_close_executor(executor)
                return [], None
            except asyncio.CancelledError:
                logger.warning(f"MCP server {server_key} connection cancelled")
                await self._safe_close_executor(executor)
                return [], None
            except Exception as e:
                logger.error(f"MCP server {server_key} connection failed: {e}")
                await self._safe_close_executor(executor)
                return [], None

            mcp_tools = executor.get_cached_tools()
            if not mcp_tools:
                logger.warning(f"MCP server {server_key} returned no tools")
                await self._safe_close_executor(executor)
                return [], None

            # 构建子工具列表
            subtools = self._build_subtools(server_key, server_config, mcp_tools)

            self._tools_cache[server_key] = subtools
            self._executors_cache[server_key] = executor

            logger.info(f"Connected to MCP server {server_key} and cached {len(subtools)} subtools")
            return subtools, executor

        except Exception as e:
            logger.error(f"Failed to connect to MCP server {server_key}: {e}")
            return [], None

    def _build_subtools(
        self,
        server_key: str,
        server_config: dict,
        mcp_tools: dict,
    ) -> list[Tool]:
        """从 MCP 服务器工具构建子工具列表"""
        subtools = []
        for mcp_tool_name, mcp_tool in mcp_tools.items():
            subtool_id = f"mcp.{server_key}.{mcp_tool_name}"
            parameters = _extract_mcp_tool_parameters(mcp_tool)

            exec_config = ToolExecutionConfig(
                type=ToolExecutionType.MCP,
                server=server_key,
                transport=server_config.get('transport', 'stdio'),
                command=server_config.get('command'),
                args=server_config.get('args') or [],
                endpoint=server_config.get('endpoint'),
                headers=server_config.get('headers') or {},
                env=server_config.get('env') or {},
                timeout=server_config.get('timeout', 30),
                tool=mcp_tool_name,
            )

            subtool = Tool(
                id=subtool_id,
                name=mcp_tool_name,
                description=mcp_tool.description if hasattr(mcp_tool, 'description') else f"MCP tool: {mcp_tool_name}",
                parameters=parameters,
                execution=exec_config,
                metadata=ToolMetadata(
                    category="mcp",
                    tags=[mcp_tool_name, 'mcp-subtool', server_key],
                ),
            )
            subtools.append(subtool)
        return subtools

    async def close(self) -> None:
        """关闭所有 MCP 连接"""
        logger.info("Closing MCP connections...")

        for server_key, executor in self._executors_cache.items():
            try:
                await executor.close()
                logger.info(f"Closed MCP connection to server: {server_key}")
            except (RuntimeError, GeneratorExit, asyncio.CancelledError, BaseException) as e:
                logger.debug(f"Ignoring error closing MCP connection to server {server_key}: {type(e).__name__}: {e}")
            except Exception as e:
                logger.error(f"Error closing MCP connection to server {server_key}: {e}")

        self._tools_cache.clear()
        self._executors_cache.clear()
        logger.info("MCP connections closed")

    async def _safe_close_executor(self, executor: Any) -> None:
        """安全关闭执行器"""
        try:
            await executor.close()
        except Exception:
            pass


def _extract_mcp_tool_parameters(mcp_tool: Any) -> dict[str, Any]:
    """从 mcp.Tool 对象提取参数 schema"""
    for attr in ("inputSchema", "input_schema"):
        schema = getattr(mcp_tool, attr, None)
        if isinstance(schema, dict) and schema.get("properties"):
            return schema

    return {
        "type": "object",
        "properties": {},
        "required": [],
    }