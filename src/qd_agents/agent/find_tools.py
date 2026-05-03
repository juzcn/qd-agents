"""Find-Tools Agent — 工具发现子循环

根据任务需求，搜索、评估、安装并注册合适的工具。
在主循环上下文中执行，完成后截断中间消息。
"""
from __future__ import annotations

import json
import logging
import re
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


class FindToolsAgent(MetaAgent):
    """Find-Tools Agent — 工具发现子循环

    在主循环上下文中执行，共享系统提示词。
    任务信息通过 tool message 注入，完成后截断中间消息。
    """

    name = "find-tools"
    description = "工具发现子循环，搜索、安装并注册缺失的工具"

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        context_manager: ContextManager,
        executor_registry: ToolExecutorRegistry | None = None,
        max_iterations: int = 10,
        on_step: StepCallback | None = None,
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

    async def execute(self, job: Job, messages: list[dict], **kwargs) -> AgentResult:
        """执行工具发现子循环

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

        # 1. 构建固定工具集（搜索 + 注册 + bash）
        fixed_tools = self._build_fixed_tools()
        openai_tools = [t.to_openai_function() for t in fixed_tools]
        tool_map = {t.name: t for t in fixed_tools}

        # 确保 execute_bash 可用
        openai_tools, tool_map = ensure_bash_available(
            openai_tools, tool_map, self.registry
        )

        # 2. 获取所有 builtin 工具（展示给 LLM，避免重复注册）
        builtin_tools = self.registry.list_all()

        # 3. 记录当前 messages 长度，注入 task message
        start_idx = len(messages)
        task_message = self.context.build_find_tools_task_message(
            job=job, builtin_tools=builtin_tools,
        )
        messages.append({"role": "user", "content": task_message})

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

        # 6. 提取注册的工具名
        found_tool_names = self._extract_registered_tools(result.final_answer)

        # 7. 截断中间消息（find-tools 是中间步骤，不保留到历史）
        del messages[start_idx:]

        return AgentResult(
            final_answer=result.final_answer,
            success=result.success,
            working_memory={"found_tool_names": found_tool_names},
            total_tokens=result.total_tokens,
            last_prompt_tokens=result.last_prompt_tokens,
            trace_id=trace_id,
            total_duration_ms=int((time.perf_counter() - start_time) * 1000),
        )

    # --- 内部方法 ---

    def _build_fixed_tools(self) -> list[Tool]:
        """构建 Find-Tools 循环的工具集

        从注册表中动态获取所有 builtin 工具（搜索、注册、fetch 等），
        不硬编码工具名称列表，确保随工具箱进化自动更新。
        """
        all_tools = self.registry.list_all()
        # 取所有 builtin scope 的工具 + execute_bash
        tools = [t for t in all_tools if t.scope == "builtin"]
        # 确保 execute_bash 在列表中
        bash_tool = self.registry.get("execute_bash")
        if bash_tool and bash_tool not in tools:
            tools.append(bash_tool)
        return tools

    @staticmethod
    def _extract_registered_tools(content: str) -> list[str]:
        """从 LLM 最终消息中提取注册的工具名列表"""
        match = re.search(r"已注册工具[：:]\s*(.+)", content)
        if match:
            names = [n.strip() for n in match.group(1).split(",") if n.strip()]
            return names

        json_match = re.search(r"\[([^\]]+)\]", content)
        if json_match:
            try:
                parsed = json.loads(f"[{json_match.group(1)}]")
                if isinstance(parsed, list):
                    return [str(item) for item in parsed if isinstance(item, str)]
            except json.JSONDecodeError:
                pass

        return []