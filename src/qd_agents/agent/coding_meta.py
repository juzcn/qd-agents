"""
Coding 元Agent — 复杂工具编排

通过代码生成和执行来实现复杂的工具编排逻辑。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from ..llm import LLMClient
from ..context import ContextManager
from ..tools import ToolExecutorRegistry
from ..execution import ExecutionEngine
from .base import MetaAgent, MetaAgentInput, MetaAgentOutput

logger = logging.getLogger(__name__)


class CodingMetaAgent(MetaAgent):
    """代码生成执行元Agent"""

    name = "coding"

    def __init__(
        self,
        llm_client: LLMClient,
        context_manager: ContextManager,
        executor_registry: ToolExecutorRegistry,
        execution_engine: ExecutionEngine | None = None,
        temperature: float = 0.3,
    ):
        self.llm = llm_client
        self.context = context_manager
        self.executor_registry = executor_registry
        self.execution = execution_engine or ExecutionEngine()
        self.temperature = temperature

    async def run(self, input: MetaAgentInput) -> MetaAgentOutput:
        """
        执行代码生成和执行。

        input.context 需包含：
          - tools: list[Tool]  可用工具列表
          - tool_functions: dict  工具名到函数的映射（可选）
        """
        start_time = time.perf_counter()

        tools = input.context.get("tools", [])
        tool_functions = input.context.get("tool_functions", {})

        # 构建消息（system prompt + history + user input）
        messages = self.context.build_coding_messages(
            user_input=input.user_message,
            tools=tools,
            history=input.history,
        )

        # 设置当前元Agent 名称
        self.llm.meta_agent_name = self.name

        response = await self.llm.chat(
            messages=messages,
            temperature=self.temperature,
        )

        content = response.choices[0].message.content or ""
        total_tokens = response.usage.total_tokens if hasattr(response, 'usage') and response.usage else 0

        # 提取代码
        code = self._extract_code(content)

        if not code:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            messages.append({"role": "assistant", "content": content})
            return MetaAgentOutput(
                output="无法生成有效代码",
                output_type="text",
                success=False,
                messages=messages,
                model=self.llm.current_model,
                total_tokens=total_tokens,
                latency_ms=latency_ms,
            )

        # 执行代码
        try:
            exec_result = await self.execution.execute_code(
                code=code,
                globals_env=tool_functions,
            )

            if exec_result.success:
                output = exec_result.result or exec_result.output
            else:
                output = f"代码执行失败: {exec_result.error}"

        except Exception as e:
            logger.exception("Code execution failed")
            output = f"代码执行异常: {e}"

        latency_ms = int((time.perf_counter() - start_time) * 1000)

        # 追加 assistant 消息
        messages.append({"role": "assistant", "content": content})

        return MetaAgentOutput(
            output=output,
            output_type="text",
            success=True,
            messages=messages,
            model=self.llm.current_model,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )

    def _extract_code(self, content: str) -> str:
        """从内容中提取代码"""
        # 匹配 ```python ... ``` 或 ``` ... ```
        code_match = re.search(r'```(?:python)?\s*([\s\S]*?)```', content)
        if code_match:
            return code_match.group(1).strip()
        return ""
