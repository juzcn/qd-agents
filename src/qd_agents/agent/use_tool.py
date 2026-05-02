"""Use-Tool Agent — 工具执行子循环

接收 Evolve 的 Job 输出，迭代调用工具完成任务。
核心逻辑从 EvolveAgent 的工具执行循环迁移而来。
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
from ..models.tool import Tool, ToolExecutionType
from ..registry import ToolRegistry
from ..tools import ToolExecutorRegistry
from .base import Agent, AgentResult, StepCallback
from .tool_execution import (
    execute_tool,
    find_skill_tool,
    ensure_bash_available,
    inject_skill_into_system_prompt,
    format_tool_result,
)

if TYPE_CHECKING:
    from ..memory.service import MemoryService

logger = logging.getLogger(__name__)


class UseToolAgent(Agent):
    """Use-Tool Agent — 工具执行子循环

    接收 Job 对象，按编排逻辑迭代调用工具，返回最终答案。
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
    ):
        self.llm = llm_client
        self.registry = tool_registry
        self.context = context_manager
        self.executor_registry = executor_registry
        self._max_iterations = max_iterations
        self._on_step = on_step
        self._cancel_event: asyncio.Event | None = None
        self._disclosed_tools: set[str] = set()

    async def execute(self, job: Job, **kwargs) -> AgentResult:
        """执行工具调用子循环

        Args:
            job: Evolve 输出的 Job 对象
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

        # 3. 确保 execute_bash 可用（SKILL 工具需要）
        openai_tools, tool_map = ensure_bash_available(
            openai_tools, tool_map, self.registry
        )

        # 4. 构建消息
        messages = self.context.build_use_tool_messages(job=job, tools=tools)

        # 5. 设置 LLM 日志标识
        self.llm.meta_agent_name = self.name
        self.llm.reset_log_count(messages)

        # 6. 工具执行循环
        iteration = 0
        total_tokens = 0
        last_prompt_tokens = 0
        tools_used: list[str] = []

        while iteration < self._max_iterations:
            if self._cancel_event and self._cancel_event.is_set():
                logger.info("Use-tool loop cancelled by user")
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
                final_answer = content.strip() if content.strip() else "任务执行完成，但未生成明确答案。"
                total_duration_ms = int((time.perf_counter() - start_time) * 1000)

                return AgentResult(
                    final_answer=final_answer,
                    success=True,
                    working_memory={"tools_used": tools_used},
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

                # SKILL 工具渐进式披露
                skill_result = self._handle_skill_disclosure(
                    tool_call, tool_input, tool_name, tool_map, iteration, messages,
                )
                if skill_result is not None:
                    replacement_msgs, append_msgs = skill_result
                    if replacement_msgs is not None:
                        messages = replacement_msgs
                    messages.extend(append_msgs)
                    continue

                # 渐进式 schema 披露（非 SKILL 工具）
                schema_result = self._handle_schema_disclosure(
                    tool_call, tool_name, tool_map, openai_tools, iteration,
                )
                if schema_result is not None:
                    messages.extend(schema_result)
                    continue

                # 记录工具调用
                if tool_name == "execute_bash":
                    command = tool_input.get("command", tool_input.get("code", ""))
                    logger.info("LLM generated command [%s]: %s", tool_name, command)
                    self._emit_step(iteration, event="tool_call", tool_name=tool_name, command=command)
                else:
                    logger.info("LLM generated tool call [%s]: %s", tool_name, json.dumps(tool_input, ensure_ascii=False))
                    self._emit_step(iteration, event="tool_call", tool_name=tool_name)

                # 执行工具
                tool_result = await execute_tool(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_map=tool_map,
                    registry=self.registry,
                    executor_registry=self.executor_registry,
                    expanded_tools=tools,
                )

                # 记录使用的工具
                if tool_name not in tools_used:
                    tools_used.append(tool_name)

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
            final_answer="达到最大工具调用迭代次数，任务可能未完成。",
            success=False,
            working_memory={"tools_used": tools_used},
            total_tokens=total_tokens,
            last_prompt_tokens=last_prompt_tokens,
            trace_id=trace_id,
            total_duration_ms=total_duration_ms,
        )

    # --- 内部方法 ---

    def _resolve_tools(self, tool_names: list[str]) -> list[Tool]:
        """从工具名列表解析出 Tool 对象"""
        tools = []
        for name in tool_names:
            tool = self.registry.get(name) or self.registry.get_by_name(name)
            if tool:
                tools.append(tool)
            else:
                logger.warning("Tool not found in registry: %s", name)
        return tools

    def _handle_skill_disclosure(
        self,
        tool_call: Any,
        tool_input: dict,
        tool_name: str,
        tool_map: dict,
        iteration: int,
        messages: list[dict],
    ) -> tuple[list[dict] | None, list[dict]] | None:
        """SKILL 工具渐进式披露

        prompt 类型：注入系统提示词（已在 build_use_tool_messages 中预注入，
        此处处理运行时动态调用的情况）。
        tool_manual 类型：注入 tool result。
        """
        skill_tool = find_skill_tool(tool_name, tool_map, self.registry)
        if not skill_tool:
            return None

        skill_type = skill_tool.dependencies.get("skill_type", "tool_manual")
        skill_md = self.context._load_skill_md(
            skill_tool.local_path or skill_tool.name
        ) or ""
        self._emit_step(iteration, event="skill_load", tool_name=tool_name, detail=tool_name)

        if skill_md:
            if skill_type == "prompt":
                logger.info("Injecting SKILL.md into system prompt (prompt type): %s", tool_name)
                new_messages = inject_skill_into_system_prompt(messages, tool_name, skill_md)
                append = [{
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"已加载技能 {tool_name} 的行为指南到系统提示词。请在后续所有决策中遵循该指南。",
                }]
                return (new_messages, append)
            else:
                logger.info("Injecting SKILL.md into tool result (tool_manual type): %s", tool_name)
                return (None, [{
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"已加载技能指南，请按照以下说明使用 execute_bash 执行：\n\n{skill_md}",
                }])
        else:
            return (None, [{
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": f"技能 {tool_name} 的 SKILL.md 未找到。",
            }])

    def _handle_schema_disclosure(
        self,
        tool_call: Any,
        tool_name: str,
        tool_map: dict[str, Tool],
        openai_tools: list[dict[str, Any]],
        iteration: int,
    ) -> list[dict[str, Any]] | None:
        """渐进式 schema 披露：首次调用时通过 tool result 返回完整参数定义"""
        if tool_name in self._disclosed_tools or tool_name not in tool_map:
            return None

        tool_obj = tool_map[tool_name]
        if tool_obj.execution.type == ToolExecutionType.SKILL:
            return None

        self._disclosed_tools.add(tool_name)
        logger.info("Disclosing tool schema via tool result: %s (progressive disclosure)", tool_name)
        self._emit_step(iteration, event="schema_load", tool_name=tool_name, detail=tool_name)

        self._replace_tool_in_list(openai_tools, tool_obj.to_openai_function())

        schema_str = json.dumps(tool_obj.parameters, ensure_ascii=False, indent=2)
        msgs = [{
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": (
                f"工具 {tool_name} 的参数定义如下，请严格按照参数定义重新调用：\n\n"
                f"描述: {tool_obj.description}\n\n"
                f"参数 Schema:\n```json\n{schema_str}\n```"
            ),
        }]
        return msgs

    @staticmethod
    def _replace_tool_in_list(
        tools: list[dict[str, Any]],
        full_tool: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """替换工具列表中对应工具为完整 schema 版本"""
        target_name = full_tool.get("function", {}).get("name", "")
        for i, t in enumerate(tools):
            if t.get("function", {}).get("name") == target_name:
                tools[i] = full_tool
                break
        return tools

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
                "loop": "use-tool",
            })
