"""
Tool Use Agent — 包装 ToolCallingMetaAgent 的简单 Agent

Agent 具备完整功能，元Agent 只是 LLM 调用的封装。
ToolUseAgent 持有工具缓存、MCP 连接等资源，构建 MetaAgentInput 后委托给 ToolCallingMetaAgent 执行。
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from ..llm import LLMClient
from ..registry import ToolRegistry, Tool
from ..context import ContextManager
from ..tools import ToolExecutorRegistry
from .base import Agent, AgentResult, MetaAgentInput
from .tool_calling_meta import ToolCallingMetaAgent

logger = logging.getLogger(__name__)


class ToolUseAgent(Agent):
    """Tool Use Agent — 单阶段工具调用模式

    包装 ToolCallingMetaAgent，提供完整的 Agent 接口。
    负责管理工具缓存、MCP 资源，为 MetaAgent 构建上下文。
    """

    name = "tool-use"
    description = "单阶段工具调用模式（OpenAI Tool Calling）"

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        context_manager: ContextManager,
        executor_registry: ToolExecutorRegistry,
        expanded_tools_cache: list[Tool] | None = None,
        openai_tools_cache: list[dict[str, Any]] | None = None,
        tool_map_cache: dict[str, Tool] | None = None,
    ):
        self.llm = llm_client
        self.registry = tool_registry
        self.context = context_manager
        self.executor_registry = executor_registry
        self._expanded_tools_cache = expanded_tools_cache
        self._openai_tools_cache = openai_tools_cache
        self._tool_map_cache = tool_map_cache or {}

        self.meta = ToolCallingMetaAgent(
            llm_client=llm_client,
            context_manager=context_manager,
            executor_registry=executor_registry,
            tool_registry=tool_registry,
        )

    def set_tools_cache(
        self,
        expanded_tools: list[Tool],
        openai_tools: list[dict[str, Any]],
        tool_map: dict[str, Tool],
    ) -> None:
        """设置工具缓存（由 QDAgent 统一管理后注入）"""
        self._expanded_tools_cache = expanded_tools
        self._openai_tools_cache = openai_tools
        self._tool_map_cache = tool_map

    async def execute(self, user_input: str, history: list[dict], **kwargs) -> AgentResult:
        """
        执行 Tool Use Agent。

        1. 构建 MetaAgentInput（包含工具缓存等上下文）
        2. 委托给 ToolCallingMetaAgent 执行
        3. 将 MetaAgentOutput 转换为 AgentResult
        """
        trace_id = kwargs.get("trace_id", str(uuid.uuid4()))
        start_time = time.perf_counter()

        if self._expanded_tools_cache is None or self._openai_tools_cache is None:
            logger.warning("Tools cache is empty, cannot execute ToolUseAgent")
            return AgentResult(
                final_answer="工具未初始化，请先初始化 Agent",
                success=False,
                trace_id=trace_id,
            )

        search_web_available = any(t.id == "search.web" for t in self._expanded_tools_cache)

        meta_input = MetaAgentInput(
            user_message=user_input,
            history=history,
            context={
                "expanded_tools": self._expanded_tools_cache,
                "openai_tools": self._openai_tools_cache,
                "tool_map": self._tool_map_cache,
                "search_web_available": search_web_available,
            },
        )

        meta_output = await self.meta.run(meta_input)

        total_duration_ms = int((time.perf_counter() - start_time) * 1000)

        return AgentResult(
            final_answer=meta_output.output,
            success=meta_output.success,
            meta_traces=[meta_output],
            trace_id=trace_id,
            total_duration_ms=total_duration_ms,
        )
