"""
AddSkill Analyzer — 技能分析器

调用大模型分析 SKILL.md 内容，识别技能的参数定义和工具依赖。
"""
from __future__ import annotations

import json
import logging
import time

from ..llm import LLMClient
from ..context import ContextManager
from ..models.add_skill import AddSkillResult
from ..utils.parsing import extract_json_from_llm_output

logger = logging.getLogger(__name__)


class AddSkillAnalyzer:
    """技能分析器 — 分析 SKILL.md，识别参数和工具依赖"""

    def __init__(self, llm_client: LLMClient, context_manager: ContextManager):
        self.llm = llm_client
        self.context = context_manager

    async def analyze(self, skill_md: str, tools: list) -> AddSkillResult:
        """
        分析 SKILL.md 内容，识别参数和工具依赖。

        Args:
            skill_md: SKILL.md 全文
            tools: 已注册工具列表（用于渲染提示词）

        Returns:
            AddSkillResult
        """
        if not skill_md:
            return AddSkillResult(
                name="",
                description="",
                success=False,
                failure_reason="SKILL.md 内容为空",
            )

        messages = self.context.build_add_skill_messages(
            skill_md=skill_md,
            tools=tools,
        )

        self.llm.meta_agent_name = "add_skill"

        response = await self.llm.chat(
            messages=messages,
            temperature=0.1,
        )

        content = response.choices[0].message.content or ""

        return self._parse_response(content)

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
