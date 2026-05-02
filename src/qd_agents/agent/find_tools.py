"""Find-Tools Agent — 工具发现子循环

根据任务需求，搜索、评估、安装并注册合适的工具。
完成后将工具列表交给 Use-Tool 子循环执行。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import TYPE_CHECKING, Any

from ..llm import LLMClient
from ..context import ContextManager
from ..models.job import Job
from ..models.tool import Tool
from ..registry import ToolRegistry
from ..tools import ToolExecutorRegistry
from .base import Agent, AgentResult, StepCallback
from .tool_execution import execute_tool, ensure_bash_available

if TYPE_CHECKING:
    from ..memory.service import MemoryService

logger = logging.getLogger(__name__)


class FindToolsAgent(Agent):
    """Find-Tools Agent — 工具发现子循环

    搜索、安装、注册工具，返回新注册的工具名列表。
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
    ):
        self.llm = llm_client
        self.registry = tool_registry
        self.context = context_manager
        self.executor_registry = executor_registry
        self._max_iterations = max_iterations
        self._on_step = on_step
        self._cancel_event: asyncio.Event | None = None

    async def execute(self, job: Job, **kwargs) -> AgentResult:
        """执行工具发现子循环

        Args:
            job: Chat 输出的 Job 对象
            **kwargs: on_step, cancel_event 等
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

        # 3. 构建消息
        messages = self.context.build_find_tools_messages(
            job=job, builtin_tools=builtin_tools,
        )

        # 4. 设置 LLM 日志标识
        self.llm.meta_agent_name = self.name
        self.llm.reset_log_count(messages)

        # 5. 工具执行循环
        iteration = 0
        total_tokens = 0
        last_prompt_tokens = 0

        while iteration < self._max_iterations:
            if self._cancel_event and self._cancel_event.is_set():
                logger.info("Find-tools loop cancelled by user")
                latency_ms = int((time.perf_counter() - start_time) * 1000)
                return AgentResult(
                    final_answer="已取消",
                    success=False,
                    total_tokens=total_tokens,
                    last_prompt_tokens=last_prompt_tokens,
                    trace_id=trace_id,
                    total_duration_ms=latency_ms,
                )

            iteration += 1

            response = await self.llm.chat(
                messages=messages,
                tools=openai_tools,
                tool_choice="auto",
                temperature=0.3,
            )

            choice = response.choices[0]
            assistant_message = choice.message

            if hasattr(response, "usage") and response.usage:
                total_tokens += response.usage.total_tokens
                last_prompt_tokens = response.usage.prompt_tokens

            # 追加 assistant 消息
            assistant_dict: dict[str, Any] = {
                "role": "assistant",
                "content": assistant_message.content or "",
            }
            if hasattr(assistant_message, "tool_calls") and assistant_message.tool_calls:
                assistant_dict["tool_calls"] = assistant_message.tool_calls
            messages.append(assistant_dict)

            # 终止条件：LLM 不返回 tool_calls
            if not assistant_message.tool_calls:
                content = assistant_message.content or ""
                # 从最终消息中提取注册的工具名
                found_tool_names = self._extract_registered_tools(content)
                total_duration_ms = int((time.perf_counter() - start_time) * 1000)

                return AgentResult(
                    final_answer=content.strip(),
                    success=True,
                    working_memory={
                        "found_tool_names": found_tool_names,
                    },
                    total_tokens=total_tokens,
                    last_prompt_tokens=last_prompt_tokens,
                    trace_id=trace_id,
                    total_duration_ms=total_duration_ms,
                )

            # 执行工具调用
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_input = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_input = {"raw": tool_call.function.arguments}

                # 记录工具调用
                if tool_name == "execute_bash":
                    command = tool_input.get("command", tool_input.get("code", ""))
                    logger.info("Find-tools: LLM generated command [%s]: %s", tool_name, command)
                    self._emit_step(iteration, event="tool_call", tool_name=tool_name, command=command)
                else:
                    logger.info("Find-tools: LLM generated tool call [%s]: %s", tool_name, json.dumps(tool_input, ensure_ascii=False))
                    self._emit_step(iteration, event="tool_call", tool_name=tool_name)

                # 执行工具
                tool_result = await execute_tool(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_map=tool_map,
                    registry=self.registry,
                    executor_registry=self.executor_registry,
                    expanded_tools=fixed_tools,
                )

                # 回调：工具执行结果
                result_summary = tool_result[:200] if len(tool_result) > 200 else tool_result
                self._emit_step(iteration, event="tool_result", tool_name=tool_name, result_summary=result_summary)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                })

        # 达到最大迭代次数
        total_duration_ms = int((time.perf_counter() - start_time) * 1000)
        return AgentResult(
            final_answer="达到最大工具发现迭代次数，工具搜索可能未完成。",
            success=False,
            working_memory={"found_tool_names": []},
            total_tokens=total_tokens,
            last_prompt_tokens=last_prompt_tokens,
            trace_id=trace_id,
            total_duration_ms=total_duration_ms,
        )

    # --- 内部方法 ---

    def _build_fixed_tools(self) -> list[Tool]:
        """构建 Find-Tools 循环的固定工具集

        包含：搜索工具 + 注册工具 + execute_bash
        """
        tool_names = [
            "serper_search", "tavily_search",
            "tool_register_cli", "tool_register_mcp",
            "tool_register_skill", "tool_register_http",
            "execute_bash",
        ]
        tools = []
        for name in tool_names:
            tool = self.registry.get(name) or self.registry.get_by_name(name)
            if tool:
                tools.append(tool)
            else:
                logger.warning("Fixed tool not found in registry: %s", name)
        return tools

    @staticmethod
    def _extract_registered_tools(content: str) -> list[str]:
        """从 LLM 最终消息中提取注册的工具名列表

        支持格式：
        - "已注册工具: tool1, tool2, tool3"
        - JSON 数组 ["tool1", "tool2"]
        """
        # 尝试匹配 "已注册工具: tool1, tool2" 格式
        match = re.search(r"已注册工具[：:]\s*(.+)", content)
        if match:
            names = [n.strip() for n in match.group(1).split(",") if n.strip()]
            return names

        # 尝试匹配 JSON 数组
        json_match = re.search(r"\[([^\]]+)\]", content)
        if json_match:
            try:
                parsed = json.loads(f"[{json_match.group(1)}]")
                if isinstance(parsed, list):
                    return [str(item) for item in parsed if isinstance(item, str)]
            except json.JSONDecodeError:
                pass

        return []

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
                "loop": "find-tools",
            })
