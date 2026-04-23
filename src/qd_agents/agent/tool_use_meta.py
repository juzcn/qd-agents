"""
ToolCalling 元Agent — 多轮 LLM↔工具循环

终止条件：LLM 不返回 tool_calls，或达到最大迭代次数。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..llm import LLMClient
from ..registry import Tool, ToolExecutionType
from ..context import ContextManager
from ..tools import ToolExecutorRegistry
from .base import MetaAgent, MetaAgentInput, MetaAgentOutput

logger = logging.getLogger(__name__)


class ToolCallingMetaAgent(MetaAgent):
    """多轮 Tool Calling 元Agent

    LLM↔工具循环，直到 LLM 不返回 tool_calls。
    """

    name = "tool_calling"

    def __init__(
        self,
        llm_client: LLMClient,
        context_manager: ContextManager,
        executor_registry: ToolExecutorRegistry,
        tool_registry: Any = None,
        temperature: float = 0.7,
        max_iterations: int = 10,
    ):
        self.llm = llm_client
        self.context = context_manager
        self.executor_registry = executor_registry
        self.tool_registry = tool_registry
        self.temperature = temperature
        self.max_iterations = max_iterations

    async def run(self, input: MetaAgentInput) -> MetaAgentOutput:
        """
        执行多轮 Tool Calling 循环。

        input.context 需包含：
          - expanded_tools: list[Tool]  展开后的工具列表
          - openai_tools: list[dict]    OpenAI 格式工具列表
          - tool_map: dict[str, Tool]   工具名→Tool 映射
          - search_web_available: bool  search.web 是否可用
        """
        start_time = time.perf_counter()

        expanded_tools = input.context.get("expanded_tools", [])
        openai_tools = input.context.get("openai_tools", [])
        tool_map = input.context.get("tool_map", {})
        search_web_available = input.context.get("search_web_available", False)

        if not openai_tools:
            return MetaAgentOutput(
                output="没有可用的工具",
                output_type="text",
                success=False,
            )

        # 构建消息（system prompt + history + user input）
        messages = self.context.build_tool_use_messages(
            user_input=input.user_message,
            tools=expanded_tools,
            search_web_available=search_web_available,
            history=input.history,
        )

        iteration = 0
        total_tokens = 0

        while iteration < self.max_iterations:
            iteration += 1

            response = await self.llm.chat(
                messages=messages,
                tools=openai_tools,
                tool_choice="auto",
                temperature=self.temperature,
            )

            choice = response.choices[0]
            assistant_message = choice.message

            # 统计 token
            if hasattr(response, "usage") and response.usage:
                total_tokens += response.usage.total_tokens

            # 追加 assistant 消息
            messages.append({
                "role": "assistant",
                "content": assistant_message.content,
                "tool_calls": assistant_message.tool_calls if hasattr(assistant_message, "tool_calls") else None,
            })

            # 终止条件：LLM 不返回 tool_calls
            if not assistant_message.tool_calls:
                final_output = assistant_message.content or "抱歉，无法生成回答"
                latency_ms = int((time.perf_counter() - start_time) * 1000)
                return MetaAgentOutput(
                    output=final_output,
                    output_type="text",
                    success=True,
                    messages=messages,
                    model=self.llm.current_model,
                    total_tokens=total_tokens,
                    latency_ms=latency_ms,
                    iterations=iteration,
                )

            # 执行工具调用
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_input = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_input = {"raw": tool_call.function.arguments}

                tool_result = await self._execute_tool(tool_name, tool_input, tool_map)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                })

        # 达到最大迭代次数
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        return MetaAgentOutput(
            output="达到最大工具调用迭代次数，请简化您的问题。",
            output_type="text",
            success=False,
            messages=messages,
            model=self.llm.current_model,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            iterations=iteration,
        )

    async def _execute_tool(
        self,
        tool_name: str,
        tool_input: dict,
        tool_map: dict[str, Tool],
    ) -> str:
        """执行单个工具调用并返回结果字符串"""
        tool = tool_map.get(tool_name)

        if not tool and self.tool_registry:
            tool = self.tool_registry.get(tool_name) or self.tool_registry.get_by_name(tool_name)

        if not tool:
            return f"工具未找到: {tool_name}"

        try:
            logger.info("Executing tool: %s (id: %s)", tool.name, tool.id)
            executor = self.executor_registry.get_executor(tool)
            if tool.execution.type == ToolExecutionType.MCP:
                tool_input_with_name = {"tool_name": tool.name, **tool_input}
                tool_result = await executor.execute(**tool_input_with_name)
            else:
                tool_result = await executor.execute(**tool_input)
            return self._format_tool_result(tool_result)
        except Exception as e:
            logger.exception("Tool execution failed")
            return f"工具调用失败: {e}"

    @staticmethod
    def _format_tool_result(tool_result: Any) -> str:
        """将工具执行结果格式化为字符串"""
        if isinstance(tool_result, str):
            return tool_result
        if hasattr(tool_result, "text"):
            return tool_result.text
        if isinstance(tool_result, list):
            text_parts = []
            for item in tool_result:
                if hasattr(item, "text"):
                    text_parts.append(item.text)
                elif hasattr(item, "type") and getattr(item, "type", None) == "text":
                    text_parts.append(getattr(item, "text", str(item)))
                else:
                    text_parts.append(str(item))
            return "\n\n".join(text_parts) if text_parts else ""
        try:
            return json.dumps(tool_result, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(tool_result)
