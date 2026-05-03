"""Use-Tool Agent — 工具执行子循环

接收 Chat 的 Job 输出，在主循环上下文中迭代调用工具完成任务。
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
from ..models.job import Job
from ..models.tool import Tool
from ..registry import ToolRegistry
from ..tools import ToolExecutorRegistry
from .base import MetaAgent, AgentResult, StepCallback
from .tool_execution import ensure_bash_available

logger = logging.getLogger(__name__)


class UseToolAgent(MetaAgent):
    """Use-Tool Agent — 工具执行子循环

    在主循环上下文中执行，共享系统提示词。
    任务信息通过 tool message 注入，完成后截断中间消息。
    """

    name = "use-tool"
    description = "工具执行子循环，按编排逻辑迭代调用工具完成任务"

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        context_manager: ContextManager,
        executor_registry: ToolExecutorRegistry | None = None,
        max_iterations: int = 15,
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

    async def execute(self, job: Job, messages: list[dict], **kwargs) -> AgentResult:
        """执行工具调用子循环

        在主循环 messages 上操作：注入 task message，执行工具循环，完成后截断。

        Args:
            job: Chat 输出的 Job 对象
            messages: 主循环的消息列表（就地修改）
            **kwargs: on_step, cancel_event, memory_context 等
        """
        on_step = kwargs.get("on_step")
        cancel_event = kwargs.get("cancel_event")
        if on_step:
            self._on_step = on_step
        if cancel_event:
            self._cancel_event = cancel_event

        trace_id = kwargs.get("trace_id", str(uuid.uuid4()))
        start_time = time.perf_counter()

        # 1. 从 job.tool_list 解析出 Tool 对象
        tools = self._resolve_tools(job.tool_list)
        if not tools:
            logger.warning("No tools resolved from job.tool_list: %s", job.tool_list)
            return AgentResult(
                final_answer="无法解析任务所需的工具，请检查工具列表。",
                success=False,
                trace_id=trace_id,
                total_duration_ms=int((time.perf_counter() - start_time) * 1000),
            )

        # 2. 构建 openai_tools（完整 schema）和 tool_map
        openai_tools = [t.to_openai_function() for t in tools]
        tool_map = {t.name: t for t in tools}

        # 3. 添加 delegate 工具（Use-Tool 可委派到 Coding Agent 处理复杂编排）
        delegate_tool = self.registry.get("delegate") or self.registry.get_by_name("delegate")
        if delegate_tool and "delegate" not in tool_map:
            openai_tools.append(delegate_tool.to_openai_function())
            tool_map["delegate"] = delegate_tool

        # 4. 确保 execute_bash 可用（SKILL 工具需要）
        ensure_bash_available(self.registry, self.executor_registry)
        bash_tool = self.registry.get("execute_bash")
        if bash_tool and "execute_bash" not in tool_map:
            openai_tools.append(bash_tool.to_openai_function())
            tool_map["execute_bash"] = bash_tool

        # 5. 注入 task message
        start_idx = len(messages)
        task_message = self.context.build_use_tool_task_message(job=job)
        messages.append({"role": "user", "content": task_message})

        # 5. 设置 LLM 日志标识
        self.llm.meta_agent_name = self.name
        self.llm.reset_log_count(messages)

        # 6. 运行 MetaAgent 标准工具调用循环
        result = await self.run_loop(
            messages=messages,
            openai_tools=openai_tools,
            tool_map=tool_map,
            temperature=0.3,
        )

        # 7. 截断中间消息，只保留 final answer
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

    # --- 内部方法 ---

    def _resolve_tools(self, tool_names: list[str]) -> list[Tool]:
        """从工具名列表解析出 Tool 对象

        查找顺序：expanded_tool_map（含 MCP subtools）→ registry（DB 存储）
        """
        tools = []
        for name in tool_names:
            tool = (
                self._expanded_tool_map.get(name)
                or self.registry.get(name)
                or self.registry.get_by_name(name)
            )
            if tool:
                tools.append(tool)
            else:
                logger.warning("Tool not found: %s (checked expanded_tool_map and registry)", name)
        return tools

    async def _handle_delegate(
        self,
        tool_call: Any,
        tool_input: dict,
        tool_map: dict[str, Tool],
        iteration: int,
        messages: list[dict],
    ) -> str:
        """Use-Tool Agent 的 delegate 处理：可委派到 Coding Agent"""
        agent_name = tool_input.get("agent", "Unknown")
        task = tool_input.get("task", "")

        self._emit_step(iteration, event="delegate_call", tool_name=agent_name, detail=task[:100])

        if agent_name == "Coding":
            result = json.dumps({
                "success": False,
                "error": "Coding Agent 尚未实现",
                "message": "请使用 execute_bash 执行 Python 脚本来完成复杂编排。",
            }, ensure_ascii=False)
        else:
            result = json.dumps({
                "success": False,
                "error": f"Use-Tool Agent 不支持委派到 {agent_name}",
            }, ensure_ascii=False)

        self._emit_step(iteration, event="delegate_result", tool_name=agent_name, result_summary=result[:100])
        return result