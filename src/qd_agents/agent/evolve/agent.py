"""
EvolveAgent — 自主进化 Agent

单循环架构：LLM 拥有所有工具，自主决策每一步。
从最小能力起步，通过交互逐渐成长——添加工具、改进自身。

核心哲学：
- Agency comes from model training, not from external code orchestration
- One loop & Bash is all you need
- Adding a tool means adding new capacity
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable

from qd_agents.llm import LLMClient
from qd_agents.registry import ToolRegistry
from qd_agents.models.tool import Tool, ToolExecutionType
from qd_agents.tools import ToolExecutorRegistry
from qd_agents.context.manager import format_tools_markdown
from qd_agents.memory.service import MemoryService

from .context import EvolveContextManager
from .system_prompt import build_system_prompt

logger = logging.getLogger(__name__)


# 回调类型：每步触发，传递事件信息
StepCallback = Callable[[dict[str, Any]], None]


class EvolveResult:
    """EvolveAgent 执行结果"""

    __slots__ = (
        "answer", "success", "tools_used", "iterations",
        "total_tokens", "last_prompt_tokens",
        "trace_id", "total_duration_ms",
    )

    def __init__(
        self,
        answer: str = "",
        success: bool = False,
        tools_used: list[str] | None = None,
        iterations: int = 0,
        total_tokens: int = 0,
        last_prompt_tokens: int = 0,
        trace_id: str = "",
        total_duration_ms: int = 0,
    ):
        self.answer = answer
        self.success = success
        self.tools_used = tools_used or []
        self.iterations = iterations
        self.total_tokens = total_tokens
        self.last_prompt_tokens = last_prompt_tokens
        self.trace_id = trace_id
        self.total_duration_ms = total_duration_ms


class EvolveAgent:
    """自主进化 Agent — 单循环架构

    LLM 拥有所有工具的完整 schema，自主决定调用什么、何时停止。
    不需要路由 JSON，不需要子循环，不需要 Job 对象。
    """

    name = "evolve"
    description = "自主进化 Agent，单循环架构，LLM 自主决策"

    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        executor_registry: ToolExecutorRegistry,
        context_manager: EvolveContextManager,
        memory_service: MemoryService | None = None,
        max_iterations: int = 30,
        on_step: StepCallback | None = None,
    ):
        self.llm = llm
        self.registry = registry
        self.executor_registry = executor_registry
        self.ctx_manager = context_manager
        self.memory = memory_service
        self.max_iterations = max_iterations
        self._on_step = on_step
        self._cancel_event: asyncio.Event | None = None

    async def run(
        self,
        user_input: str,
        history: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> EvolveResult:
        """执行主循环

        Args:
            user_input: 用户输入
            history: 历史对话（可选，用于多轮）
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

        # 1. 加载工具
        tools = self._load_tools()
        openai_tools = [t.to_openai_function() for t in tools]
        tool_map = {t.name: t for t in tools}

        # 2. 构建系统提示词
        system_prompt = build_system_prompt(tools=tools, work_dir=kwargs.get("work_dir"))

        # 3. 构建初始 messages
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        # 追加历史
        if history:
            messages.extend(history)

        # 追加用户输入
        messages.append({"role": "user", "content": user_input})

        # 4. 记忆召回（如果有相关记忆）
        if self.memory and user_input:
            try:
                recall_result = self.memory.recall(query=user_input)
                if recall_result:
                    memory_text = self.memory.format_recall_result(recall_result)
                    if memory_text and memory_text != "未找到相关的历史记忆。":
                        messages.append({
                            "role": "user",
                            "content": f"[相关记忆]\n{memory_text}",
                        })
                        messages.append({
                            "role": "assistant",
                            "content": "了解，我会参考这些记忆信息。",
                        })
            except Exception as e:
                logger.warning("Memory recall failed: %s", e)

        # 5. 主循环
        iteration = 0
        total_tokens = 0
        last_prompt_tokens = 0
        tools_used: list[str] = []

        while iteration < self.max_iterations:
            if self._cancel_event and self._cancel_event.is_set():
                logger.info("Evolve loop cancelled by user")
                return EvolveResult(
                    answer="已取消",
                    success=False,
                    tools_used=tools_used,
                    iterations=iteration,
                    total_tokens=total_tokens,
                    last_prompt_tokens=last_prompt_tokens,
                    trace_id=trace_id,
                    total_duration_ms=int((time.perf_counter() - start_time) * 1000),
                )

            iteration += 1

            # 上下文管理：检查并压缩
            messages = self.ctx_manager.maybe_compact(messages)

            # LLM 调用
            response = await self.llm.chat(
                messages=messages,
                tools=openai_tools,
                tool_choice="auto",
                temperature=0.3,
            )

            choice = response.choices[0]
            assistant_message = choice.message

            # Token 统计
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
            if not (hasattr(assistant_message, "tool_calls") and assistant_message.tool_calls):
                content = assistant_message.content or ""
                answer = content.strip() if content.strip() else "任务执行完成，但未生成明确答案。"
                total_duration_ms = int((time.perf_counter() - start_time) * 1000)

                # 保存记忆
                if self.memory:
                    self._save_memory(user_input, answer, tools_used)

                return EvolveResult(
                    answer=answer,
                    success=True,
                    tools_used=tools_used,
                    iterations=iteration,
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

                # 日志
                if tool_name == "execute_bash":
                    command = tool_input.get("command", tool_input.get("code", ""))
                    logger.info("Evolve [%d] bash: %s", iteration, command)
                    self._emit_step(iteration, event="tool_call", tool_name=tool_name, command=command)
                else:
                    logger.info("Evolve [%d] tool: %s %s", iteration, tool_name,
                                json.dumps(tool_input, ensure_ascii=False)[:200])
                    self._emit_step(iteration, event="tool_call", tool_name=tool_name)

                # 执行
                tool_result = await self._execute_tool(tool_name, tool_input, tool_map)

                # 记录使用的工具
                if tool_name not in tools_used:
                    tools_used.append(tool_name)

                # 如果注册了新工具，刷新工具列表
                if tool_name.startswith("tool_register_"):
                    openai_tools, tool_map = self._refresh_tools(openai_tools, tool_map)

                # 回调
                result_summary = tool_result[:200] if len(tool_result) > 200 else tool_result
                self._emit_step(iteration, event="tool_result", tool_name=tool_name,
                                result_summary=result_summary)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                })

        # 达到最大迭代次数
        total_duration_ms = int((time.perf_counter() - start_time) * 1000)
        return EvolveResult(
            answer="达到最大迭代次数，任务可能未完成。",
            success=False,
            tools_used=tools_used,
            iterations=iteration,
            total_tokens=total_tokens,
            last_prompt_tokens=last_prompt_tokens,
            trace_id=trace_id,
            total_duration_ms=total_duration_ms,
        )

    # --- 内部方法 ---

    def _load_tools(self) -> list[Tool]:
        """从 registry 加载所有活跃工具"""
        return self.registry.list_all()

    def _refresh_tools(
        self,
        openai_tools: list[dict],
        tool_map: dict[str, Tool],
    ) -> tuple[list[dict], dict[str, Tool]]:
        """刷新工具列表（注册新工具后调用）"""
        current_tools = self._load_tools()
        current_names = {t.name for t in current_tools}

        # 添加新工具
        for tool in current_tools:
            if tool.name not in tool_map:
                openai_tools.append(tool.to_openai_function())
                tool_map[tool.name] = tool
                logger.info("Evolve: new tool registered → %s", tool.name)
                self._emit_step(0, event="tool_registered", tool_name=tool.name)

        return openai_tools, tool_map

    async def _execute_tool(
        self,
        tool_name: str,
        tool_input: dict,
        tool_map: dict[str, Tool],
    ) -> str:
        """执行单个工具调用"""
        tool = tool_map.get(tool_name)
        if not tool:
            # 尝试从 registry 查找
            tool = self.registry.get(tool_name) or self.registry.get_by_name(tool_name)
            if tool:
                tool_map[tool_name] = tool
            else:
                return f"工具未找到: {tool_name}"

        try:
            executor = self.executor_registry.get_executor(tool)

            # MCP 工具需要传入 tool_name
            if tool.execution.type == ToolExecutionType.MCP:
                tool_input_with_name = {"tool_name": tool.name, **tool_input}
                result = await executor.execute(**tool_input_with_name)
            else:
                result = await executor.execute(**tool_input)

            return self._format_result(result)
        except Exception as e:
            logger.exception("Tool execution failed: %s", tool_name)
            return f"工具调用失败: {e}"

    @staticmethod
    def _format_result(result: Any) -> str:
        """将工具执行结果格式化为字符串"""
        if isinstance(result, str):
            return result
        if hasattr(result, "text"):
            return result.text
        if isinstance(result, list):
            text_parts = []
            for item in result:
                if hasattr(item, "text"):
                    text_parts.append(item.text)
                elif isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", str(item)))
                else:
                    text_parts.append(str(item))
            return "\n\n".join(text_parts) if text_parts else ""
        try:
            return json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(result)

    def _save_memory(self, user_input: str, answer: str, tools_used: list[str]) -> None:
        """保存对话记忆"""
        if not self.memory:
            return
        try:
            content = f"用户: {user_input}\n回答: {answer}"
            if tools_used:
                content += f"\n使用工具: {', '.join(tools_used)}"
            self.memory.save(
                question=user_input,
                answer=answer,
                source="evolve",
                tags=tools_used or None,
            )
        except Exception as e:
            logger.warning("Failed to save memory: %s", e)

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
                "max_iterations": self.max_iterations,
                "event": event,
                "tool_name": tool_name,
                "command": command,
                "result_summary": result_summary,
                "detail": detail,
                "loop": "evolve",
            })
