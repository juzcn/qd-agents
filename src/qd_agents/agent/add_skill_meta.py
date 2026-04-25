"""
Add-Skill 元Agent

用 LLM 分析 SKILL.md 内容，识别技能的参数定义和工具依赖。
单轮 LLM 调用，输出日志。
"""
from __future__ import annotations

import json
import logging
import time

from ..llm import LLMClient
from ..context import ContextManager
from ..models.add_skill import AddSkillResult
from ..utils.parsing import extract_json_from_llm_output
from .base import MetaAgent, MetaAgentInput, MetaAgentOutput

logger = logging.getLogger(__name__)


class AddSkillMetaAgent(MetaAgent):
    """Add-Skill 元Agent：分析 SKILL.md，识别参数和工具依赖"""

    name = "add_skill"

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
        分析 SKILL.md 内容，识别参数和工具依赖。

        input.context 需包含：
          - skill_md: SKILL.md 全文
          - tools: 已注册工具列表（用于渲染提示词）
        """
        start_time = time.perf_counter()

        skill_md = input.context.get("skill_md", "")
        tools = input.context.get("tools", [])

        if not skill_md:
            result = AddSkillResult(
                name="",
                description="",
                success=False,
                failure_reason="SKILL.md 内容为空",
            )
            return MetaAgentOutput(
                output=result,
                output_type="add_skill_result",
                success=False,
            )

        # 构建消息（system prompt + SKILL.md 作为 user message）
        messages = self.context.build_add_skill_messages(
            skill_md=skill_md,
            tools=tools,
        )

        # 设置当前元Agent 名称
        self.llm.meta_agent_name = self.name

        response = await self.llm.chat(
            messages=messages,
            temperature=self.temperature,
        )

        content = response.choices[0].message.content or ""
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        total_tokens = response.usage.total_tokens if hasattr(response, "usage") and response.usage else 0

        # 解析结果
        result = self._parse_response(content)

        # 追加 assistant 消息
        messages.append({"role": "assistant", "content": content})

        return MetaAgentOutput(
            output=result,
            output_type="add_skill_result",
            success=result.success,
            messages=messages,
            model=self.llm.current_model,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _parse_response(content: str) -> AddSkillResult:
        """解析 LLM 返回的 JSON"""
        try:
            json_str = extract_json_from_llm_output(content)
            data = json.loads(json_str)
            return AddSkillResult(**data)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Failed to parse add_skill result: %s", e)
            return AddSkillResult(
                name="",
                description="",
                success=False,
                failure_reason=f"解析 LLM 返回失败: {e}",
            )