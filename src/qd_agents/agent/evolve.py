"""
Evolve Agent — 自主进化智能体

核心循环：思考 → 调用工具 → 观察结果 → 继续或完成。
工具执行逻辑在 tool_execution.py 中。
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
from ..models import EvolveResult
from ..models.tool import Tool, ToolExecutionType
from ..registry import ToolRegistry
from ..tools import ToolExecutorRegistry
from ..utils.parsing import extract_json_from_llm_output
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

RECALL_MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "recall_memory",
        "description": (
            "从长期记忆中召回与当前问题相关的历史对话记录。"
            "当你需要引用之前会话中讨论过的方案、配置、决策等信息时调用。"
            "如果当前问题不依赖历史信息，不要调用此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "用于检索历史记忆的查询语句",
                }
            },
            "required": ["query"],
        },
    },
}


class EvolveAgent(Agent):
    """Evolve Agent — 自主进化智能体

    通过 function calling 直接调用工具，持有完整对话上下文。
    自主循环：思考 → 调用工具 → 观察结果 → 继续或完成。
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
        max_iterations: int | None = None,
        on_step: StepCallback | None = None,
        memory_service: MemoryService | None = None,
        session_id: str = "",
    ):
        self.llm = llm_client
        self.registry = tool_registry
        self.context = context_manager
        self.executor_registry = executor_registry
        self._expanded_tools = expanded_tools_cache or []
        self._openai_tools = openai_tools_cache or []
        self._tool_map = tool_map_cache or {}
        self._max_iterations = max_iterations or 10
        self._on_step = on_step
        self._cancel_event: asyncio.Event | None = None
        self._memory_service = memory_service
        self._session_id = session_id
        self._disclosed_tools: set[str] = set()  # 已披露 schema 的工具名

    async def execute(self, user_input: str, history: list[dict], **kwargs) -> AgentResult:
        """执行自主进化"""
        on_step = kwargs.get("on_step")
        cancel_event = kwargs.get("cancel_event")
        if on_step:
            self._on_step = on_step
        if cancel_event:
            self._cancel_event = cancel_event

        trace_id = kwargs.get("trace_id", str(uuid.uuid4()))
        start_time = time.perf_counter()

        # 确保 bash 工具可用（SKILL 工具需要）
        openai_tools, tool_map = ensure_bash_available(
            self._openai_tools, self._tool_map, self.registry
        )

        # 注入 recall_memory 工具（当记忆服务可用时）
        if self._memory_service:
            existing_names = {t.get("function", {}).get("name") for t in openai_tools if "function" in t}
            if "recall_memory" not in existing_names:
                openai_tools.append(RECALL_MEMORY_TOOL)

        # 渐进式 schema 披露：初始只给 name + description（空 parameters）
        # execute_bash 和 recall_memory 作为元工具，始终给完整 schema
        minimal_tools = self._build_minimal_openai_tools(openai_tools, tool_map)

        # 构建初始消息
        messages = self.context.build_evolve_messages(
            user_input=user_input,
            tools=self._expanded_tools,
            history=history,
        )

        # 重置增量日志计数：当前 messages 全部已构建，后续只输出增量
        self.llm.meta_agent_name = self.name
        self.llm.reset_log_count(messages)

        iteration = 0
        total_tokens = 0
        last_prompt_tokens = 0

        while iteration < self._max_iterations:
            # 检查取消信号
            if self._cancel_event and self._cancel_event.is_set():
                logger.info("Evolve loop cancelled by user")
                latency_ms = int((time.perf_counter() - start_time) * 1000)
                final_answer = "已取消"
                return AgentResult(
                    final_answer=final_answer,
                    success=False,
                    total_tokens=total_tokens,
                    last_prompt_tokens=last_prompt_tokens,
                    trace_id=trace_id,
                    total_duration_ms=latency_ms,
                )

            iteration += 1

            response = await self.llm.chat(
                messages=messages,
                tools=minimal_tools,
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

                # 尝试解析为 EvolveResult（ask_user/delegate 等特殊输出）
                evolve_result = self._try_parse_evolve_result(content)

                if evolve_result and evolve_result.action in ("ask_user", "delegate"):
                    if evolve_result.action == "ask_user":
                        final_answer = self._format_ask_user(evolve_result)
                    elif evolve_result.action == "delegate":
                        final_answer = self._format_delegate(evolve_result)
                    else:
                        final_answer = evolve_result.direct_answer or str(evolve_result)
                else:
                    final_answer = content.strip() if content.strip() else "抱歉，无法生成回答"

                total_duration_ms = int((time.perf_counter() - start_time) * 1000)

                # QA 自动写入长期记忆
                self._auto_save_memory(user_input, final_answer, total_tokens)

                return AgentResult(
                    final_answer=final_answer,
                    success=True,
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

                # recall_memory 工具：拦截并调用记忆服务
                handled = self._handle_recall_memory(
                    tool_call, tool_input, tool_name, iteration,
                )
                if handled is not None:
                    messages.extend(handled)
                    continue

                # SKILL 工具渐进式披露
                skill_result = self._handle_skill_disclosure(
                    tool_call, tool_input, tool_name, tool_map, iteration,
                )
                if skill_result is not None:
                    replacement_msgs, append_msgs = skill_result
                    if replacement_msgs is not None:
                        messages = replacement_msgs
                    messages.extend(append_msgs)
                    continue

                # 渐进式 schema 披露
                schema_result = self._handle_schema_disclosure(
                    tool_call, tool_name, tool_map, iteration,
                )
                if schema_result is not None:
                    minimal_tools, schema_msgs = schema_result
                    messages.extend(schema_msgs)
                    continue

                # 记录 LLM 生成的工具调用命令
                if tool_name == "execute_bash":
                    command = tool_input.get("command", tool_input.get("code", ""))
                    logger.info("LLM generated command [%s]: %s", tool_name, command)
                    self._emit_step(iteration, event="tool_call", tool_name=tool_name, command=command)
                else:
                    logger.info("LLM generated tool call [%s]: %s", tool_name, json.dumps(tool_input, ensure_ascii=False))
                    self._emit_step(iteration, event="tool_call", tool_name=tool_name)

                tool_result = await execute_tool(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_map=tool_map,
                    registry=self.registry,
                    executor_registry=self.executor_registry,
                    expanded_tools=self._expanded_tools,
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
            final_answer="达到最大工具调用迭代次数，请简化您的问题。",
            success=False,
            total_tokens=total_tokens,
            last_prompt_tokens=last_prompt_tokens,
            trace_id=trace_id,
            total_duration_ms=total_duration_ms,
        )

    # --- 内部方法 ---

    # 始终给完整 schema 的工具（元工具 / 内置工具）
    _FULL_SCHEMA_TOOLS = frozenset({"execute_bash", "recall_memory"})

    def _build_minimal_openai_tools(
        self,
        openai_tools: list[dict[str, Any]],
        tool_map: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """构建最小化工具列表：大部分工具只给 name + description，元工具给完整 schema"""
        minimal: list[dict[str, Any]] = []
        for t in openai_tools:
            func_info = t.get("function", {})
            name = func_info.get("name", "")
            if name in self._FULL_SCHEMA_TOOLS:
                minimal.append(t)
            elif name in tool_map:
                minimal.append(tool_map[name].to_minimal_openai_function())
            else:
                minimal.append(t)
        return minimal

    async def _handle_recall_memory(
        self,
        tool_call: Any,
        tool_input: dict,
        tool_name: str,
        iteration: int,
    ) -> list[dict] | None:
        """拦截 recall_memory 工具调用，调用记忆服务。返回追加的消息列表，或 None 表示不处理。"""
        if tool_name != "recall_memory" or not self._memory_service:
            return None

        query = tool_input.get("query", "")
        logger.info("Recall memory: %s", query)
        self._emit_step(iteration, event="tool_call", tool_name=tool_name)
        try:
            records = self._memory_service.recall(
                query=query,
                exclude_session=self._session_id,
            )
            tool_result = self._memory_service.format_recall_result(records)
        except Exception as e:
            logger.exception("Recall memory failed")
            tool_result = f"召回记忆失败: {e}"
        self._emit_step(iteration, event="tool_result", tool_name=tool_name, result_summary=tool_result[:200])
        return [{
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": tool_result,
        }]

    def _handle_skill_disclosure(
        self,
        tool_call: Any,
        tool_input: dict,
        tool_name: str,
        tool_map: dict,
        iteration: int,
    ) -> tuple[list[dict] | None, list[dict]] | None:
        """SKILL 工具渐进式披露：根据 skill_type 选择注入方式。

        返回 (replacement_messages, append_messages) 元组，或 None 表示不处理。
        replacement_messages: 需要替换整个 messages 列表时非 None（prompt 类型注入系统提示词）。
        append_messages: 需要追加到 messages 的消息列表。
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
        tool_map: dict,
        iteration: int,
    ) -> tuple[list, list[dict]] | None:
        """渐进式 schema 披露：首次调用时通过 tool result 返回完整参数定义。

        返回 (minimal_tools, append_messages) 元组，或 None 表示不处理。
        """
        if tool_name in self._disclosed_tools or tool_name not in tool_map:
            return None

        tool_obj = tool_map[tool_name]
        # SKILL 工具已有自己的披露机制，跳过
        if tool_obj.execution.type == ToolExecutionType.SKILL:
            return None

        self._disclosed_tools.add(tool_name)
        logger.info("Disclosing tool schema via tool result: %s (progressive disclosure)", tool_name)
        self._emit_step(iteration, event="schema_load", tool_name=tool_name, detail=tool_name)

        # 更新 minimal_tools 中该工具为完整 schema
        updated_tools = self._replace_tool_in_list(minimal_tools, tool_obj.to_openai_function())

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
        return (updated_tools, msgs)

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

    def _auto_save_memory(self, question: str, answer: str, token_count: int = 0) -> None:
        """QA 完成后自动写入长期记忆"""
        if not self._memory_service:
            return
        try:
            self._memory_service.save(
                question=question,
                answer=answer,
                session_id=self._session_id,
                source="evolve",
                model=self.llm.current_model,
                token_count=token_count,
            )
            logger.info("Auto-saved QA to memory (session=%s)", self._session_id[:8] if self._session_id else "?")
        except Exception:
            logger.exception("Auto-save memory failed")

    def _try_parse_evolve_result(self, content: str) -> EvolveResult | None:
        """尝试解析为 EvolveResult，用于识别 ask_user/delegate 等特殊输出"""
        try:
            json_str = extract_json_from_llm_output(content)
            result_dict = json.loads(json_str)
            if "route" in result_dict and "action" not in result_dict:
                result_dict["action"] = result_dict.pop("route")
            result = EvolveResult(**result_dict)
            if result.action in ("ask_user", "delegate"):
                return result
            return None
        except (json.JSONDecodeError, ValueError):
            return None

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
            })

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