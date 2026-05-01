"""
工具服务

负责内置工具注册、工具缓存构建和执行引擎注册。
"""
from __future__ import annotations

import logging
from typing import Any

from ..models.tool import Tool, ToolExecutionType
from ..registry import ToolRegistry
from ..tools import ToolExecutorRegistry
from ..services.mcp_service import MCPService

logger = logging.getLogger(__name__)


class ToolService:
    """工具服务管理器

    提供内置工具注册、工具缓存构建等辅助功能。
    QDAgent 通过组合持有 ToolService 实例。
    """

    def __init__(self) -> None:
        pass

    async def load_expanded_tools(
        self,
        registry: ToolRegistry,
        executor_registry: ToolExecutorRegistry | None = None,
    ) -> list[Tool]:
        """加载展开后的工具列表（含 MCP subtools）。

        启动 MCP 服务、获取 subtools、展开工具列表、关闭 MCP 服务。

        Args:
            registry: 工具注册表
            executor_registry: 执行器注册表（可选，用于注册 MCP subtool 执行器）

        Returns:
            展开后的工具列表（MCP 工具被替换为 subtools）
        """
        mcp_service = MCPService()
        try:
            all_tools = registry.list_all()
            mcp_tools = [t for t in all_tools if t.execution.type == ToolExecutionType.MCP]
            if mcp_tools:
                await mcp_service.preload(
                    mcp_tools=mcp_tools,
                    executor_registry=executor_registry or ToolExecutorRegistry(),
                )

            expanded_tools = []
            for tool in all_tools:
                if tool.execution.type != ToolExecutionType.MCP:
                    expanded_tools.append(tool)
                    continue

                server_key = tool.execution.server or ""
                cached_tools = mcp_service.tools_cache.get(server_key)
                if cached_tools:
                    expanded_tools.extend(cached_tools)
                else:
                    logger.warning("MCP server '%s' not in cache, keeping original tool", server_key)
                    expanded_tools.append(tool)

            logger.info("Loaded %d expanded tools (from %d DB tools)", len(expanded_tools), len(all_tools))
            return expanded_tools
        finally:
            await mcp_service.close()

    def build_expanded_tools(
        self,
        registry: ToolRegistry,
        mcp_tools_cache: dict[str, list[Tool]],
    ) -> tuple[list[Tool], list[dict[str, Any]], dict[str, Tool]]:
        """构建展开后的工具列表、OpenAI 格式工具列表和工具映射。

        Returns:
            (expanded_tools, openai_tools, tool_map)
        """
        all_tools = registry.list_all()

        expanded_tools = []
        for tool in all_tools:
            if tool.execution.type != ToolExecutionType.MCP:
                expanded_tools.append(tool)
                continue

            exec_config = tool.execution
            server_key = exec_config.server or ""
            cached_tools = mcp_tools_cache.get(server_key)
            if cached_tools:
                expanded_tools.extend(cached_tools)
            else:
                logger.warning(f"MCP server '{server_key}' not in cache, keeping original tool")
                expanded_tools.append(tool)

        # search.web 优先
        openai_tools = []
        search_web = registry.get("search.web")
        if search_web:
            openai_tools.append(search_web.to_openai_function())
            expanded_tools = [t for t in expanded_tools if t.id != "search.web"]

        # SKILL 工具也加入 openai_tools（参数 schema 为空，触发渐进式披露）
        openai_tools.extend([t.to_openai_function() for t in expanded_tools])

        tool_map = {t.name: t for t in expanded_tools}

        logger.info(f"Built {len(expanded_tools)} expanded tools and {len(openai_tools)} OpenAI tools")
        return expanded_tools, openai_tools, tool_map