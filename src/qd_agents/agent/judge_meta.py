"""
Judge 元Agent — 路由判断

判断用户问题应该由哪个路径处理：
- direct: 直接回答（基于知识）
- tool_use: 简单工具调用
- coding: 复杂工具编排
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Literal

from pydantic import BaseModel, Field

from ..llm import LLMClient
from ..context import ContextManager
from .base import MetaAgent, MetaAgentInput, MetaAgentOutput

logger = logging.getLogger(__name__)


class JudgeResult(BaseModel):
    """判断结果"""
    route: Literal["direct", "tool_use", "coding"] = Field(
        description="路由路径: direct(直接回答), tool_use(简单工具调用), coding(复杂工具编排)"
    )
    reasoning: str = Field(
        description="判断理由"
    )
    direct_answer: str | None = Field(
        default=None,
        description="如果route=direct，这里给出直接回答"
    )


class JudgeMetaAgent(MetaAgent):
    """路由判断元Agent"""

    name = "judge"

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

        # 构建消息（system prompt + history + user input）
        messages = self.context.build_judge_messages(
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
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        total_tokens = response.usage.total_tokens if hasattr(response, 'usage') and response.usage else 0

        # 解析结果
        judge_result = self._parse_result(content)

        # 追加 assistant 消息
        messages.append({"role": "assistant", "content": content})

        return MetaAgentOutput(
            output=judge_result,
            output_type="judge_result",
            success=True,
            messages=messages,
            model=self.llm.current_model,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )

    def _parse_result(self, content: str) -> JudgeResult:
        """解析判断结果"""
        try:
            json_str = self._extract_json(content)
            result_dict = json.loads(json_str)
            return JudgeResult(**result_dict)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse judge result: {e}")
            return JudgeResult(
                route="tool_use",
                reasoning=f"解析失败，默认使用工具调用: {e}",
            )

    def _extract_json(self, content: str) -> str:
        """从内容中提取 JSON"""
        # 匹配 ```json ... ``` 或 ``` ... ```
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if json_match:
            return json_match.group(1).strip()

        # 尝试找到 { } 包裹的内容
        brace_start = content.find('{')
        brace_end = content.rfind('}')
        if brace_start != -1 and brace_end != -1:
            return content[brace_start:brace_end + 1]

        return content