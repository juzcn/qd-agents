"""
Evolve Agent — 自主进化智能体

EvolveMetaAgent 是一个真正的自主 agent，通过 function calling 直接调用工具。
EvolveAgent 只做外层控制：构建初始上下文、处理 ask_user/delegate 等特殊输出。
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from ..llm import LLMClient
from ..registry import ToolRegistry
from ..context import ContextManager, ContextCompressor
from ..models import EvolveResult
from ..tools import ToolExecutorRegistry
from .base import Agent, AgentResult, MetaAgentInput, StepCallback
from .evolve_meta import EvolveMetaAgent

logger = logging.getLogger(__name__)


class EvolveAgent(Agent):
    """Evolve Agent — 自主进化智能体

    EvolveMetaAgent 自己持有完整上下文，通过 function calling 直接调用工具。
    EvolveAgent 只做外层包装：处理 ask_user/delegate 等特殊输出格式。
    """

    name = "evolve"
    description = "自主进化Agent，能自主思考、决策、行动，并在需要时向用户求助或请求协作"

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        context_manager: ContextManager,
        executor_registry: ToolExecutorRegistry | None = None,
        expanded_tools_cache: list | None = None,
        openai_tools_cache: list[dict[str, Any]] | None = None,
        tool_map_cache: dict[str, Any] | None = None,
        compressor: ContextCompressor | None = None,
        on_step: StepCallback | None = None,
    ):
        self.llm = llm_client
        self.registry = tool_registry
        self.context = context_manager
        self.executor_registry = executor_registry
        self._expanded_tools = expanded_tools_cache or []
        self._openai_tools = openai_tools_cache or []
        self._tool_map = tool_map_cache or {}
        self._on_step = on_step
        self._compressor = compressor

        self._evolve = EvolveMetaAgent(
            llm_client=llm_client,
            context_manager=context_manager,
            executor_registry=executor_registry,
            tool_registry=tool_registry,
            openai_tools=openai_tools_cache,
            tool_map=tool_map_cache,
            expanded_tools=expanded_tools_cache,
            on_step=on_step,
            compressor=compressor,
        )

    async def execute(self, user_input: str, history: list[dict], **kwargs) -> AgentResult:
        """执行自主进化"""
        # 动态注入 on_step 回调和 cancel_event（构造时可能为 None，运行时从 kwargs 传入）
        on_step = kwargs.get("on_step")
        cancel_event = kwargs.get("cancel_event")
        compressor = kwargs.get("compressor")
        if on_step:
            self._on_step = on_step
            self._evolve._on_step = on_step
        if cancel_event:
            self._evolve._cancel_event = cancel_event
        if compressor:
            self._compressor = compressor
            self._evolve._compressor = compressor

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

        # 处理特殊输出格式
        if evolve_output.output_type == "evolve_result":
            evolve_result: EvolveResult = evolve_output.output
            if evolve_result.action == "ask_user":
                final_answer = self._format_ask_user(evolve_result)
            elif evolve_result.action == "delegate":
                final_answer = self._format_delegate(evolve_result)
            else:
                final_answer = evolve_result.direct_answer or str(evolve_result)
        else:
            final_answer = str(evolve_output.output)

        total_duration_ms = int((time.perf_counter() - start_time) * 1000)

        return AgentResult(
            final_answer=final_answer,
            success=evolve_output.success,
            meta_traces=[evolve_output],
            total_tokens=evolve_output.total_tokens,
            last_prompt_tokens=evolve_output.last_prompt_tokens,
            trace_id=trace_id,
            total_duration_ms=total_duration_ms,
        )

    @staticmethod
    def _format_ask_user(result: EvolveResult) -> str:
        """格式化向用户提问"""
        parts = ["**需要你的输入**\n"]
        if result.ask_user:
            parts.append(result.ask_user.question)
            if result.ask_user.options:
                parts.append("\n选项：")
                for i, opt in enumerate(result.ask_user.options, 1):
                    parts.append(f"  {i}. {opt}")
            if result.ask_user.reason:
                parts.append(f"\n原因：{result.ask_user.reason}")
        if result.reflection:
            parts.append(f"\n\n*反思：{result.reflection}*")
        return "\n".join(parts)

    @staticmethod
    def _format_delegate(result: EvolveResult) -> str:
        """格式化委托用户执行"""
        parts = ["**需要你来执行**\n"]
        if result.delegate:
            parts.append(f"任务：{result.delegate.task}")
            parts.append(f"\n操作指南：\n{result.delegate.guide}")
            parts.append(f"\n原因：{result.delegate.reason}")
        if result.reflection:
            parts.append(f"\n\n*反思：{result.reflection}*")
        return "\n".join(parts)