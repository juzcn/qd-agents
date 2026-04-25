"""
Evolve Agent — 包装 EvolveMetaAgent 的 Agent

只包含一个 MetaAgent: EvolveMetaAgent，用于路由判断。
"""
from __future__ import annotations

import logging
import time
import uuid

from ..llm import LLMClient
from ..registry import ToolRegistry
from ..context import ContextManager
from ..models import EvolveResult
from .base import Agent, AgentResult, MetaAgentInput
from .evolve_meta import EvolveMetaAgent

logger = logging.getLogger(__name__)


class EvolveAgent(Agent):
    """Evolve Agent — 单阶段路由判断模式

    只编排 EvolveMetaAgent，输出路由判断结果。
    """

    name = "evolve"
    description = "进化路由判断Agent，分析用户问题并决定处理路径"

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        context_manager: ContextManager,
        expanded_tools_cache: list | None = None,
    ):
        self.llm = llm_client
        self.registry = tool_registry
        self.context = context_manager
        self._expanded_tools = expanded_tools_cache or []

        self._evolve = EvolveMetaAgent(
            llm_client=llm_client,
            context_manager=context_manager,
        )

    async def execute(self, user_input: str, history: list[dict], **kwargs) -> AgentResult:
        """执行路由判断"""
        trace_id = kwargs.get("trace_id", str(uuid.uuid4()))
        start_time = time.perf_counter()

        evolve_input = MetaAgentInput(
            user_message=user_input,
            history=history,
            context={
                "tools": self._expanded_tools,
            },
        )

        evolve_output = await self._evolve.run(evolve_input)
        evolve_result: EvolveResult = evolve_output.output

        logger.info(
            f"Evolve result: route={evolve_result.route}, "
            f"reasoning={evolve_result.reasoning}, "
            f"tools={evolve_result.tool_list}"
        )

        # 直接返回路由判断结果作为最终答案
        final_answer = (
            evolve_result.direct_answer
            if evolve_result.route == "direct" and evolve_result.direct_answer
            else f"路由: {evolve_result.route}\n理由: {evolve_result.reasoning}\n工具: {', '.join(evolve_result.tool_list) or '无'}"
        )

        total_duration_ms = int((time.perf_counter() - start_time) * 1000)

        return AgentResult(
            final_answer=final_answer,
            success=True,
            meta_traces=[evolve_output],
            trace_id=trace_id,
            total_duration_ms=total_duration_ms,
        )
