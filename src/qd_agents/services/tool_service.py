"""工具服务

负责工具缓存构建和执行引擎注册。
"""
from __future__ import annotations

import logging
from typing import Any

from ..models.tool import Tool, ToolExecutionType
from ..registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolService:
    """工具服务管理器

    提供工具缓存构建等辅助功能。
    QDAgent 通过组合持有 ToolService 实例。
    """

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
                logger.warning("MCP server '%s' not in cache, keeping original tool", server_key)
                expanded_tools.append(tool)

        openai_tools = [t.to_openai_function() for t in expanded_tools]
        tool_map = {t.name: t for t in expanded_tools}

        logger.info("Built %d expanded tools and %d OpenAI tools", len(expanded_tools), len(openai_tools))
        return expanded_tools, openai_tools, tool_map