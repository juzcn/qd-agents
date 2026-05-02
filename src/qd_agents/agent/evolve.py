"""Evolve Agent — 路由决策器

主循环：分析用户请求，输出 Job JSON 决定路由方向。
不再执行工具，只做路由决策。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from ..llm import LLMClient
from ..context import ContextManager
from ..models.job import Job
from ..models.evolve import AskUserInfo, DelegateInfo, EvolveResult
from ..models.tool import Tool
from ..registry import ToolRegistry
from ..utils.parsing import extract_json_from_llm_output
from .base import Agent, AgentResult, StepCallback

if TYPE_CHECKING:
    from ..memory.service import MemoryService

logger = logging.getLogger(__name__)


class EvolveAgent(Agent):
    """Evolve Agent — 路由决策器

    分析用户请求和可用工具，输出 Job JSON 决定路由方向。
    不执行工具，只做决策。
    """

    name = "evolve"
    description = "路由决策器，分析用户请求并决定路由方向"

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        context_manager: ContextManager,
        expanded_tools_cache: list | None = None,
        max_iterations: int | None = None,
        on_step: StepCallback | None = None,
        memory_service: MemoryService | None = None,
        session_id: str = "",
    ):
        self.llm = llm_client
        self.registry = tool_registry
        self.context = context_manager
        self._expanded_tools = expanded_tools_cache or []
        self._max_iterations = max_iterations or 3
        self._on_step = on_step
        self._cancel_event: asyncio.Event | None = None
        self._memory_service = memory_service
        self._session_id = session_id

    async def execute(self, user_input: str, history: list[dict], **kwargs) -> AgentResult:
        """执行路由决策

        输出 Job JSON，由 QDAgent 解析并路由到子循环。
        """
        on_step = kwargs.get("on_step")
        cancel_event = kwargs.get("cancel_event")
        if on_step:
            self._on_step = on_step
        if cancel_event:
            self._cancel_event = cancel_event

        trace_id = kwargs.get("trace_id", str(uuid.uuid4()))
        start_time = time.perf_counter()

        # 检查取消信号
        if self._cancel_event and self._cancel_event.is_set():
            return AgentResult(
                final_answer="已取消",
                success=False,
                trace_id=trace_id,
                total_duration_ms=0,
            )

        # 1. 可选：记忆召回预步骤
        memory_context = ""
        if self._memory_service:
            memory_context = await self._recall_memory_context(user_input)

        # 2. 构建 messages
        messages = self.context.build_evolve_messages(
            user_input=user_input,
            tools=self._expanded_tools,
            history=history,
        )

        # 如果有记忆召回结果，注入到 user_input 之前
        if memory_context:
            memory_message = {
                "role": "user",
                "content": f"## 相关历史记忆\n\n{memory_context}\n\n请结合以上历史记忆进行路由决策。",
            }
            messages.insert(-1, memory_message)

        # 3. 调用 LLM（不传 tools 参数，输出纯文本 Job JSON）
        self.llm.meta_agent_name = self.name
        self.llm.reset_log_count(messages)

        self._emit_step(1, event="route_decision")

        response = await self.llm.chat(
            messages=messages,
            temperature=0.1,
        )

        choice = response.choices[0]
        content = choice.message.content or ""

        total_tokens = response.usage.total_tokens if hasattr(response, "usage") and response.usage else 0
        last_prompt_tokens = response.usage.prompt_tokens if hasattr(response, "usage") and response.usage else 0

        # 4. 解析 Job JSON
        job = self._parse_job(content)

        total_duration_ms = int((time.perf_counter() - start_time) * 1000)

        if job is None:
            # 解析失败，fallback 为直接回答
            logger.warning("Failed to parse Job JSON from Evolve response, treating as direct answer")
            final_answer = content.strip() if content.strip() else "抱歉，无法理解您的请求。"
            return AgentResult(
                final_answer=final_answer,
                success=True,
                total_tokens=total_tokens,
                last_prompt_tokens=last_prompt_tokens,
                trace_id=trace_id,
                total_duration_ms=total_duration_ms,
            )

        # 5. 按 route 路由
        self._emit_step(1, event="route_result", detail=job.route)

        if job.route == "direct-answer":
            final_answer = job.direct_answer or content.strip()
            return AgentResult(
                final_answer=final_answer,
                success=True,
                total_tokens=total_tokens,
                last_prompt_tokens=last_prompt_tokens,
                trace_id=trace_id,
                total_duration_ms=total_duration_ms,
            )

        elif job.route == "ask_user":
            final_answer = self._format_ask_user(job)
            return AgentResult(
                final_answer=final_answer,
                success=True,
                total_tokens=total_tokens,
                last_prompt_tokens=last_prompt_tokens,
                trace_id=trace_id,
                total_duration_ms=total_duration_ms,
            )

        elif job.route == "delegate":
            final_answer = self._format_delegate(job)
            return AgentResult(
                final_answer=final_answer,
                success=True,
                total_tokens=total_tokens,
                last_prompt_tokens=last_prompt_tokens,
                trace_id=trace_id,
                total_duration_ms=total_duration_ms,
            )

        elif job.route in ("use-tool", "find-tools"):
            # 返回 Job 给 QDAgent，由 QDAgent 协调子循环
            return AgentResult(
                final_answer="",  # 子循环会产生最终答案
                success=True,
                working_memory={"job": job},
                total_tokens=total_tokens,
                last_prompt_tokens=last_prompt_tokens,
                trace_id=trace_id,
                total_duration_ms=total_duration_ms,
            )

        else:
            # 未知路由，fallback
            logger.warning("Unknown route: %s, treating as direct answer", job.route)
            final_answer = content.strip() if content.strip() else "抱歉，无法处理您的请求。"
            return AgentResult(
                final_answer=final_answer,
                success=True,
                total_tokens=total_tokens,
                last_prompt_tokens=last_prompt_tokens,
                trace_id=trace_id,
                total_duration_ms=total_duration_ms,
            )

    # --- 内部方法 ---

    async def _recall_memory_context(self, user_input: str) -> str:
        """记忆召回预步骤：在路由决策前召回相关记忆"""
        if not self._memory_service:
            return ""
        try:
            records = self._memory_service.recall(
                query=user_input,
                exclude_session=self._session_id,
            )
            return self._memory_service.format_recall_result(records)
        except Exception:
            logger.exception("Memory recall pre-step failed")
            return ""

    def _parse_job(self, content: str) -> Job | None:
        """解析 LLM 输出为 Job 对象"""
        try:
            json_str = extract_json_from_llm_output(content)
            result_dict = json.loads(json_str)

            # 兼容：如果 LLM 输出 "action" 而非 "route"
            if "action" in result_dict and "route" not in result_dict:
                result_dict["route"] = result_dict.pop("action")

            job = Job(**result_dict)
            logger.info("Parsed Job: route=%s, tool_list=%s", job.route, job.tool_list)
            return job
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Failed to parse Job JSON: %s", e)
            return None

    @staticmethod
    def _format_ask_user(job: Job) -> str:
        """格式化向用户提问"""
        parts = ["**需要你的输入**\n"]
        if job.ask_user:
            parts.append(job.ask_user.question)
            if job.ask_user.options:
                parts.append("\n选项：")
                for i, opt in enumerate(job.ask_user.options, 1):
                    parts.append(f"  {i}. {opt}")
            if job.ask_user.reason:
                parts.append(f"\n原因：{job.ask_user.reason}")
        if job.reflection:
            parts.append(f"\n\n*反思：{job.reflection}*")
        return "\n".join(parts)

    @staticmethod
    def _format_delegate(job: Job) -> str:
        """格式化委托用户执行"""
        parts = ["**需要你来执行**\n"]
        if job.delegate:
            parts.append(f"任务：{job.delegate.task}")
            parts.append(f"\n操作指南：\n{job.delegate.guide}")
            parts.append(f"\n原因：{job.delegate.reason}")
        if job.reflection:
            parts.append(f"\n\n*反思：{job.reflection}*")
        return "\n".join(parts)

    def _emit_step(
        self,
        iteration: int,
        event: str,
        tool_name: str = "",
        command: str = "",
        result_summary: str = "",
        detail: str = "",
    ) -> None:
        """触发步骤回调"""
        if self._on_step:
            self._on_step({
                "iteration": iteration,
                "max_iterations": self._max_iterations,
                "event": event,
                "tool_name": tool_name,
                "command": command,
                "result_summary": result_summary,
                "detail": detail,
                "loop": "evolve",
            })
