"""
Evolve 元Agent — 路由判断

与 JudgeMetaAgent 逻辑相同，使用独立的系统提示词和输出模型。
判断用户问题应该由哪个路径处理：
- direct: 直接回答（基于知识）
- tool_use: 简单工具调用
- coding: 复杂工具编排
"""
from __future__ import annotations

import json
import logging
import time

from ..llm import LLMClient
from ..context import ContextManager
from ..models import EvolveResult
from ..utils.parsing import extract_json_from_llm_output
from .base import MetaAgent, MetaAgentInput, MetaAgentOutput

logger = logging.getLogger(__name__)


class EvolveMetaAgent(MetaAgent):
    """Evolve 路由判断元Agent"""

    name = "evolve"

    def __init__(
        self,
        llm_client: LLMClient,
        context_manager: ContextManager,
        temperature: float = 0.1,
    ):
        self.llm = llm_client
        self.context = context_manager
        self.temperature = temperature

    async def run(self, input: MetaAgentInput) -> MetaAgentOutput:
        """
        执行路由判断。

        input.context 需包含：
          - tools: list[Tool]  可用工具列表
        """
        start_time = time.perf_counter()

        tools = input.context.get("tools", [])

        messages = self.context.build_evolve_messages(
            user_input=input.user_message,
            tools=tools,
            history=input.history,
        )

        self.llm.meta_agent_name = self.name

        response = await self.llm.chat(
            messages=messages,
            temperature=self.temperature,
        )

        content = response.choices[0].message.content or ""
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        total_tokens = response.usage.total_tokens if hasattr(response, 'usage') and response.usage else 0

        evolve_result = self._parse_result(content)

        messages.append({"role": "assistant", "content": content})

        return MetaAgentOutput(
            output=evolve_result,
            output_type="evolve_result",
            success=True,
            messages=messages,
            model=self.llm.current_model,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )

    def _parse_result(self, content: str) -> EvolveResult:
        """解析判断结果"""
        try:
            json_str = extract_json_from_llm_output(content)
            result_dict = json.loads(json_str)
            return EvolveResult(**result_dict)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse evolve result: {e}")
            return EvolveResult(
                route="tool_use",
                reasoning=f"解析失败，默认使用工具调用: {e}",
            )
