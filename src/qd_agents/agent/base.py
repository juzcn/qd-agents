"""
Agent 基类和数据模型

Agent：从用户输入到最终回答的完整任务处理单元。
MetaAgent：标准 OpenAI 工具调用循环基类，所有 Agent 共享的感知-推理-行动模式。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from ..llm import LLMClient
from ..models.tool import Tool
from ..registry import ToolRegistry
from ..tools import ToolExecutorRegistry
from .tool_execution import (
    execute_tool,
    find_skill_tool,
    ensure_bash_available,
    format_tool_result,
)


logger = logging.getLogger(__name__)

# --- 步骤回调类型 ---


StepCallback = Callable[[dict[str, Any]], None]

# --- ask_user 回调类型 ---


AskUserCallback = Callable[[str, list[str] | None], Any]  # async callable


# --- Agent 数据模型 ---


@dataclass
class AgentResult:
    """Agent 执行结果"""

    final_answer: str
    success: bool
    working_memory: dict = field(default_factory=dict)
    interaction_log: list[dict] = field(default_factory=list)
    total_tokens: int = 0
    last_prompt_tokens: int = 0
    total_duration_ms: int = 0
    trace_id: str = ""


# --- Agent 抽象基类 ---


class Agent(ABC):
    """Agent：从用户输入到最终回答的完整任务处理单元"""

    name: str
    description: str

    @abstractmethod
    async def execute(self, user_input: str, history: list[dict], **kwargs) -> AgentResult: ...


# --- MetaAgent 基类 ---


class MetaAgent(Agent):
    """MetaAgent：标准 OpenAI 工具调用循环基类

    遵循 OpenAI 标准工具调用循环，直到模型返回最终答案。
    所有子 Agent（EvolveAgent、UseToolAgent、FindToolsAgent）共享此模式。

    核心循环：
    1. 调用 LLM API，传入 messages 和 tools，tool_choice="auto"
    2. 若响应包含 tool_calls：执行工具，追加结果，继续循环
    3. 若响应仅为文本：终止循环，返回最终答案
    4. ask_user 工具：暂停循环等待用户回复
    5. delegate 工具：由子类拦截处理，路由到子 Agent
    """

    name: str = "meta"
    description: str = "MetaAgent 基类"

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        executor_registry: ToolExecutorRegistry | None = None,
        max_iterations: int = 20,
        on_step: StepCallback | None = None,
        cancel_event: asyncio.Event | None = None,
        ask_user_callback: AskUserCallback | None = None,
        context_window_size: int = 0,
        context_summarizer_threshold: float = 0.75,
        task_background: str = "",
        task_requirements: str = "",
    ):
        self.llm = llm_client
        self.registry = tool_registry
        self.executor_registry = executor_registry
        self._max_iterations = max_iterations
        self._on_step = on_step
        self._cancel_event = cancel_event
        self._ask_user_callback = ask_user_callback
        self._context_window_size = context_window_size
        self._context_summarizer_threshold = context_summarizer_threshold
        self._task_background = task_background
        self._task_requirements = task_requirements

    async def run_loop(
        self,
        messages: list[dict],
        openai_tools: list[dict],
        tool_map: dict[str, Tool],
        *,
        temperature: float = 0.3,
        start_iteration: int = 0,
    ) -> AgentResult:
        """标准 OpenAI 工具调用循环

        Args:
            messages: 消息列表（就地修改）
            openai_tools: OpenAI 格式工具 schema 列表
            tool_map: 工具名 → Tool 对象映射
            temperature: LLM 温度
            start_iteration: 起始迭代计数

        Returns:
            AgentResult 包含 final_answer、token 统计等
        """
        iteration = start_iteration
        total_tokens = 0
        last_prompt_tokens = 0
        tools_used: list[str] = []
        start_time = time.perf_counter()

        while iteration < self._max_iterations:
            # 检查取消信号
            if self._cancel_event and self._cancel_event.is_set():
                logger.info("Loop cancelled by user (agent: %s)", self.name)
                return AgentResult(
                    final_answer="已取消",
                    success=False,
                    working_memory={"tools_used": tools_used},
                    total_tokens=total_tokens,
                    last_prompt_tokens=last_prompt_tokens,
                    total_duration_ms=int((time.perf_counter() - start_time) * 1000),
                )

            iteration += 1

            # 自动触发 context_summarizer：当 token 数超过上下文窗口阈值
            if last_prompt_tokens > 0 and self._context_window_size > 0:
                threshold = self._context_summarizer_threshold
                if last_prompt_tokens > self._context_window_size * threshold:
                    logger.info(
                        "Auto-triggering context_summarizer (tokens: %d > %d * %.2f)",
                        last_prompt_tokens, self._context_window_size, threshold,
                    )
                    await self._handle_context_summarizer(
                        tool_call=None, tool_input={}, messages=messages,
                    )

            # 调用 LLM（指数退避重试）
            response = None
            max_llm_retries = 3
            for llm_attempt in range(max_llm_retries):
                try:
                    response = await self.llm.chat(
                        messages=messages,
                        tools=openai_tools,
                        tool_choice="auto",
                        temperature=temperature,
                    )
                    break
                except Exception as e:
                    if llm_attempt + 1 >= max_llm_retries:
                        logger.error("LLM call failed after %d retries in %s: %s",
                                     max_llm_retries, self.name, e)
                        return AgentResult(
                            final_answer=f"LLM 调用失败（已重试 {max_llm_retries} 次）: {e}",
                            success=False,
                            working_memory={"tools_used": tools_used},
                            total_tokens=total_tokens,
                            last_prompt_tokens=last_prompt_tokens,
                            total_duration_ms=int((time.perf_counter() - start_time) * 1000),
                        )
                    delay = min(2 ** (llm_attempt + 1), 10)
                    logger.warning("LLM call failed (attempt %d/%d), retrying in %ds: %s",
                                   llm_attempt + 1, max_llm_retries, delay, e)
                    await asyncio.sleep(delay)

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
                return AgentResult(
                    final_answer=final_answer,
                    success=True,
                    working_memory={"tools_used": tools_used},
                    total_tokens=total_tokens,
                    last_prompt_tokens=last_prompt_tokens,
                    total_duration_ms=int((time.perf_counter() - start_time) * 1000),
                )

            # 执行工具调用
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_input = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_input = {"raw": tool_call.function.arguments}

                # --- 特殊工具拦截 ---

                # ask_user：暂停循环等待用户输入
                if tool_name == "ask_user":
                    result_content = await self._handle_ask_user(
                        tool_call, tool_input, iteration,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result_content,
                    })
                    continue

                # delegate：由子类拦截处理
                if tool_name == "delegate":
                    result_content = await self._handle_delegate(
                        tool_call, tool_input, tool_map, iteration, messages,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result_content,
                    })
                    continue

                # context_summarizer：由子类或自身处理
                if tool_name == "context_summarizer":
                    result_content = await self._handle_context_summarizer(
                        tool_call, tool_input, messages,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result_content,
                    })
                    continue

                # --- SKILL 渐进式披露 ---
                skill_result = await self._handle_skill_disclosure(
                    tool_call, tool_name, tool_map, iteration, messages,
                )
                if skill_result is not None:
                    messages.extend(skill_result)
                    continue

                # --- 普通工具执行 ---
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
                    expanded_tools=list(tool_map.values()),
                )

                if tool_name not in tools_used:
                    tools_used.append(tool_name)

                result_summary = tool_result[:200] if len(tool_result) > 200 else tool_result
                self._emit_step(iteration, event="tool_result", tool_name=tool_name, result_summary=result_summary)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                })

        # 达到最大迭代次数
        return AgentResult(
            final_answer="达到最大工具调用迭代次数，任务可能未完成。",
            success=False,
            working_memory={"tools_used": tools_used},
            total_tokens=total_tokens,
            last_prompt_tokens=last_prompt_tokens,
            total_duration_ms=int((time.perf_counter() - start_time) * 1000),
        )

    # --- 可被子类覆盖的特殊工具处理器 ---

    async def _handle_ask_user(
        self,
        tool_call: Any,
        tool_input: dict,
        iteration: int,
    ) -> str:
        """处理 ask_user 工具调用：暂停循环，等待用户输入

        返回 JSON 格式的工具结果。
        """
        question = tool_input.get("question", "请提供更多信息：")
        options = tool_input.get("options")
        reason = tool_input.get("reason", "")

        self._emit_step(iteration, event="ask_user", detail=question)

        if self._ask_user_callback:
            try:
                answer = await self._ask_user_callback(question, options)
                result = json.dumps({"answer": answer, "selected_option": None}, ensure_ascii=False)
            except Exception as e:
                logger.error("ask_user callback failed: %s", e)
                result = json.dumps({"answer": f"获取用户输入失败: {e}", "selected_option": None}, ensure_ascii=False)
        else:
            # 无回调时，返回提示信息让模型知道无法交互
            result = json.dumps({
                "answer": "无法与用户交互（ask_user_callback 未设置）",
                "selected_option": None,
            }, ensure_ascii=False)

        self._emit_step(iteration, event="ask_user_result", detail=result[:100])
        return result

    async def _handle_delegate(
        self,
        tool_call: Any,
        tool_input: dict,
        tool_map: dict[str, Tool],
        iteration: int,
        messages: list[dict],
    ) -> str:
        """处理 delegate 工具调用：路由到子 Agent

        子类（EvolveAgent）覆盖此方法实现实际路由。
        默认实现返回未实现提示。
        """
        agent_name = tool_input.get("agent", "Unknown")
        task = tool_input.get("task", "")
        self._emit_step(iteration, event="delegate_call", tool_name=agent_name, detail=task[:100])

        result = json.dumps({
            "success": False,
            "error": f"delegate 未实现（当前 Agent: {self.name}）",
        }, ensure_ascii=False)

        self._emit_step(iteration, event="delegate_result", tool_name=agent_name, result_summary=result[:100])
        return result

    async def _handle_context_summarizer(
        self,
        tool_call: Any,
        tool_input: dict,
        messages: list[dict],
    ) -> str:
        """处理 context_summarizer 工具调用

        调用大模型生成摘要，保留：用户原始目标、已完成步骤、未解决问题、重要中间结果。
        LLM 调用失败时回退到简单截断。
        """
        keep_recent = tool_input.get("keep_recent", 20)
        focus = tool_input.get("focus", "")

        if len(messages) <= keep_recent + 1:  # +1 for system message
            return json.dumps({"success": True, "message": "上下文长度正常，无需压缩"}, ensure_ascii=False)

        system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
        older_messages = messages[1:-keep_recent] if system_msg else messages[:-keep_recent]

        if not older_messages:
            return json.dumps({"success": True, "message": "无需压缩"}, ensure_ascii=False)

        # 构建摘要提示词
        older_text = "\n".join(
            f"[{m.get('role', '?')}]: {str(m.get('content', ''))[:500]}"
            for m in older_messages
        )
        focus_line = f"\n关注点: {focus}" if focus else ""
        summary_prompt = (
            "请总结以下对话历史，保留以下关键信息：\n"
            "1. 用户的原始目标\n"
            "2. 已经完成的步骤\n"
            "3. 尚未解决的问题\n"
            "4. 重要的中间结果\n"
            f"{focus_line}\n\n"
            f"对话历史：\n{older_text}\n\n"
            "请用200-300字生成摘要："
        )

        try:
            response = await self.llm.chat(
                messages=[{"role": "user", "content": summary_prompt}],
                temperature=0.3,
            )
            summary = response.choices[0].message.content or ""
        except Exception as e:
            logger.warning("LLM summarization failed, falling back to truncation: %s", e)
            summary = f"[已压缩 {len(older_messages)} 条早期消息]"

        # 替换早期消息为摘要
        recent = messages[-keep_recent:]
        messages.clear()
        if system_msg:
            messages.append(system_msg)
        messages.append({"role": "user", "content": f"[对话历史摘要]\n{summary}"})
        messages.extend(recent)

        logger.info("Context summarized via LLM: %d older messages → %d chars summary",
                     len(older_messages), len(summary))
        return json.dumps({
            "success": True,
            "summarized": len(older_messages),
            "summary_length": len(summary),
        }, ensure_ascii=False)

    async def _handle_skill_disclosure(
        self,
        tool_call: Any,
        tool_name: str,
        tool_map: dict[str, Tool],
        iteration: int,
        messages: list[dict],
    ) -> list[dict] | None:
        """SKILL 工具：通过 tool result 注入 SKILL.md

        返回 None 表示不是 SKILL 工具，由调用者继续正常执行。
        返回 list[dict] 表示已处理，追加到 messages。
        """
        skill_tool = find_skill_tool(tool_name, tool_map, self.registry)
        if not skill_tool:
            return None

        skill_type = skill_tool.dependencies.get("skill_type", "tool_manual")
        self._emit_step(iteration, event="skill_load", tool_name=tool_name, detail=tool_name)

        # 加载 SKILL.md（需要 context_manager，子类设置）
        skill_md = ""
        if hasattr(self, "context") and self.context:
            skill_md = self.context._load_skill_md(
                skill_tool.local_path or skill_tool.name
            ) or ""

        if skill_md:
            if skill_type == "prompt":
                # prompt 类型：注入到系统提示词
                logger.info("Injecting SKILL.md into system prompt (prompt type): %s", tool_name)
                if messages and messages[0].get("role") == "system":
                    messages[0]["content"] += f"\n\n## 技能指南: {tool_name}\n\n{skill_md}"
                return [{
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"已加载技能 {tool_name} 的行为指南到系统提示词。请在后续所有决策中遵循该指南。",
                }]
            else:
                # tool_manual 类型：注入到 tool result
                logger.info("Injecting SKILL.md into tool result (tool_manual type): %s", tool_name)
                return [{
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"已加载技能指南，请按照以下说明使用 execute_bash 执行：\n\n{skill_md}",
                }]
        else:
            return [{
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": f"技能 {tool_name} 的 SKILL.md 未找到。",
            }]

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
                "loop": self.name,
            })
