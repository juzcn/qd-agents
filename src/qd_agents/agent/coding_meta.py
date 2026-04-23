"""
Coding 元Agent — 复杂工具编排

通过代码生成和执行来实现复杂的工具编排逻辑。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..llm import LLMClient
from ..context import ContextManager
from ..prompts import PromptLoader
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
        prompt_loader: PromptLoader | None = None,
        execution_engine: ExecutionEngine | None = None,
        temperature: float = 0.3,
    ):
        self.llm = llm_client
        self.context = context_manager
        self.executor_registry = executor_registry
        self.prompts = prompt_loader
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

        # 渲染系统提示词
        if self.prompts:
            system_prompt = self.prompts.render(
                "coding",
                tools=tools,
                user_input=input.user_message,
            )
        else:
            system_prompt = self._build_prompt(tools, input.user_message)

        messages = [{"role": "user", "content": system_prompt}]

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

    def _build_prompt(self, tools: list, user_input: str) -> str:
        """构建提示词（无模板时的回退）"""
        tools_info = "\n".join(
            f"- {getattr(t, 'name', str(t))}: {getattr(t, 'description', '')}"
            for t in tools
        )
        return f"""生成Python代码编排工具调用：

可用工具:
{tools_info or '暂无'}

用户需求: {user_input}

要求:
1. 使用 await 调用异步工具
2. 结果赋值给 result 变量
3. 只使用列出的工具
"""

    def _extract_code(self, content: str) -> str:
        """从内容中提取代码"""
        import re
        # 匹配 ```python ... ``` 或 ``` ... ```
        code_match = re.search(r'```(?:python)?\s*([\s\S]*?)```', content)
        if code_match:
            return code_match.group(1).strip()
        return ""
