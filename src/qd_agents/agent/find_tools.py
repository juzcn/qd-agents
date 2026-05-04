"""Find-Tools Agent — 工具发现子循环

接收 EvolveAgent 的委派，在独立上下文中搜索和发现工具。
完成后返回 final answer，不污染主循环上下文。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from ..llm import LLMClient
from ..context import ContextManager
from ..context.manager import format_tools_markdown
from ..models.tool import Tool, ToolExecutionType
from ..registry import ToolRegistry
from ..tools import ToolExecutorRegistry
from .base import MetaAgent, AgentResult, StepCallback
from .tool_execution import ensure_bash_available, resolve_tool_map, build_tools_detail_section

logger = logging.getLogger(__name__)


class FindToolsAgent(MetaAgent):
    """Find-Tools Agent — 工具发现子循环

    在独立上下文中执行，不共享主循环的 messages。
    任务信息通过 task_background/task_description 传入，完成后返回 final answer。
    """

    name = "find-tools"
    description = "工具发现子循环，搜索和发现可用工具"

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        context_manager: ContextManager,
        executor_registry: ToolExecutorRegistry | None = None,
        max_iterations: int = 20,
        on_step: StepCallback | None = None,
        expanded_tool_map: dict[str, Tool] | None = None,
        context_window_size: int = 0,
        context_summarizer_threshold: float = 0.75,
        task_background: str = "",
        task_requirements: str = "",
    ):
        super().__init__(
            llm_client=llm_client,
            tool_registry=tool_registry,
            executor_registry=executor_registry,
            max_iterations=max_iterations,
            on_step=on_step,
            context_window_size=context_window_size,
            context_summarizer_threshold=context_summarizer_threshold,
            task_background=task_background,
            task_requirements=task_requirements,
        )
        self.context = context_manager
        self._expanded_tool_map = expanded_tool_map or {}

    async def execute(self, **kwargs) -> AgentResult:
        """执行工具发现子循环（独立上下文）

        构建独立的 messages，不共享主循环上下文。
        只依赖传入的 task_background 和 task_description。

        Args:
            task_background: 任务背景上下文
            task_description: 任务具体描述
            tool_list: 需要使用的工具名列表
            **kwargs: on_step, cancel_event 等
        """
        on_step = kwargs.get("on_step")
        cancel_event = kwargs.get("cancel_event")
        task_background: str = kwargs.get("task_background", "")
        task_description: str = kwargs.get("task_description", "")
        tool_list: list[str] | None = kwargs.get("tool_list")
        if on_step:
            self._on_step = on_step
        if cancel_event:
            self._cancel_event = cancel_event

        trace_id = kwargs.get("trace_id", str(uuid.uuid4()))
        start_time = time.perf_counter()

        # 1. 获取所有已注册工具（用于工具箱概览）
        if self._expanded_tool_map:
            all_tools = list(self._expanded_tool_map.values())
        else:
            all_tools = list(self.registry.list_all())

        # 2. 从 tool_list 解析出 tool_map（含壳工具→subtool 展开）
        tool_map = resolve_tool_map(tool_list or [], self._expanded_tool_map, self.registry)
        if not tool_map:
            logger.warning("No tools resolved from tool_list: %s", tool_list)
            return AgentResult(
                final_answer="无法解析任务所需的工具，请检查工具列表。",
                success=False,
                trace_id=trace_id,
                total_duration_ms=int((time.perf_counter() - start_time) * 1000),
            )

        # 确保 execute_bash 和 ask_user 可用
        ensure_bash_available(self.registry, self.executor_registry)
        bash_tool = self.registry.get("execute_bash")
        if bash_tool and "execute_bash" not in tool_map:
            tool_map["execute_bash"] = bash_tool
        ask_user_tool = self.registry.get("ask_user")
        if ask_user_tool and "ask_user" not in tool_map:
            tool_map["ask_user"] = ask_user_tool

        openai_tools = [t.to_openai_function() for t in tool_map.values()]
        detail_tools = list(tool_map.values())

        # 3. 构建独立上下文：system_prompt + task_message（含 SKILL.md）
        tools_detail_section = build_tools_detail_section(detail_tools, self.context)
        task_message = self.context.build_find_tools_task_message(
            task_background=task_background,
            task_description=task_description,
            detail_tools=detail_tools,
            all_tools=all_tools,
            tools_detail_section=tools_detail_section,
        )
        messages: list[dict] = [
            {"role": "system", "content": task_message},
        ]

        # 4. 设置 LLM 日志标识
        self.llm.meta_agent_name = self.name
        self.llm.reset_log_count(messages)

        # 5. 运行 MetaAgent 标准工具调用循环
        result = await self.run_loop(
            messages=messages,
            openai_tools=openai_tools,
            tool_map=tool_map,
            temperature=0.3,
        )

        return AgentResult(
            final_answer=result.final_answer,
            success=result.success,
            working_memory=result.working_memory,
            total_tokens=result.total_tokens,
            last_prompt_tokens=result.last_prompt_tokens,
            trace_id=trace_id,
            total_duration_ms=int((time.perf_counter() - start_time) * 1000),
        )