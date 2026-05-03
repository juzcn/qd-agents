"""Find-Tools Agent — 工具发现子循环

接收 EvolveAgent 的委派，在主循环上下文中搜索和发现工具。
完成后截断中间消息，只保留 final answer。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from ..llm import LLMClient
from ..context import ContextManager
from ..models.tool import Tool
from ..registry import ToolRegistry
from ..tools import ToolExecutorRegistry
from .base import MetaAgent, AgentResult, StepCallback
from .tool_execution import ensure_bash_available

logger = logging.getLogger(__name__)


class FindToolsAgent(MetaAgent):
    """Find-Tools Agent — 工具发现子循环

    在主循环上下文中执行，共享系统提示词。
    任务信息通过 tool message 注入，完成后截断中间消息。
    """

    name = "find-tools"
    description = "工具发现子循环，搜索和发现可用工具"

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        context_manager: ContextManager,
        executor_registry: ToolExecutorRegistry | None = None,
        max_iterations: int = 10,
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
        """执行工具发现子循环

        在主循环 messages 上操作：注入 task message，执行工具循环，完成后截断。

        Args:
            task_background: 任务背景上下文
            task_description: 任务具体描述
            messages: 主循环的消息列表（就地修改）
            **kwargs: on_step, cancel_event, memory_context 等
        """
        on_step = kwargs.get("on_step")
        cancel_event = kwargs.get("cancel_event")
        task_background: str = kwargs.get("task_background", "")
        task_description: str = kwargs.get("task_description", "")
        messages: list[dict] = kwargs.get("messages", [])
        if on_step:
            self._on_step = on_step
        if cancel_event:
            self._cancel_event = cancel_event

        trace_id = kwargs.get("trace_id", str(uuid.uuid4()))
        start_time = time.perf_counter()

        # 1. 获取所有 builtin 工具
        all_tools = self.registry.list_all()
        builtin_tools = [t for t in all_tools if t.name not in ("delegate", "ask_user", "context_summarizer")]

        # 2. 构建 openai_tools 和 tool_map
        openai_tools = [t.to_openai_function() for t in builtin_tools]
        tool_map = {t.name: t for t in builtin_tools}

        # 3. 添加 delegate 工具
        delegate_tool = self.registry.get("delegate") or self.registry.get_by_name("delegate")
        if delegate_tool and "delegate" not in tool_map:
            openai_tools.append(delegate_tool.to_openai_function())
            tool_map["delegate"] = delegate_tool

        # 4. 确保 execute_bash 可用
        ensure_bash_available(self.registry, self.executor_registry)
        bash_tool = self.registry.get("execute_bash")
        if bash_tool and "execute_bash" not in tool_map:
            openai_tools.append(bash_tool.to_openai_function())
            tool_map["execute_bash"] = bash_tool

        # 5. 注入 task message
        start_idx = len(messages)
        task_message = self.context.build_find_tools_task_message(
            task_background=task_background,
            task_description=task_description,
            builtin_tools=builtin_tools,
        )
        messages.append({"role": "user", "content": task_message})

        # 6. 设置 LLM 日志标识
        self.llm.meta_agent_name = self.name
        self.llm.reset_log_count(messages)

        # 7. 运行 MetaAgent 标准工具调用循环
        result = await self.run_loop(
            messages=messages,
            openai_tools=openai_tools,
            tool_map=tool_map,
            temperature=0.3,
        )

        # 8. 截断中间消息，只保留 final answer
        del messages[start_idx:]
        messages.append({"role": "assistant", "content": result.final_answer})

        return AgentResult(
            final_answer=result.final_answer,
            success=result.success,
            working_memory=result.working_memory,
            total_tokens=result.total_tokens,
            last_prompt_tokens=result.last_prompt_tokens,
            trace_id=trace_id,
            total_duration_ms=int((time.perf_counter() - start_time) * 1000),
        )