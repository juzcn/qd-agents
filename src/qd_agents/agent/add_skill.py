"""
AddSkill Analyzer — 技能分析器

调用大模型分析 SKILL.md 内容，识别技能的参数定义和工具依赖。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Literal

from pydantic import BaseModel, Field

from ..llm import LLMClient
from ..context import ContextManager
from ..utils.parsing import extract_json_from_llm_output

logger = logging.getLogger(__name__)


class AddSkillResult(BaseModel):
    """Add-Skill 分析结果"""
    name: str = Field(description="Skill 名称")
    description: str = Field(description="Skill 描述")
    parameters: dict = Field(default_factory=dict, description="参数 JSON Schema")
    skill_type: Literal["tool_manual", "prompt"] = Field(
        default="tool_manual",
        description="技能类型: tool_manual(工具说明书) prompt(行为指南/提示词)",
    )
    tool_deps: list[str] = Field(default_factory=list, description="依赖的已注册工具名列表")
    success: bool = Field(default=True, description="是否成功分析")
    failure_reason: str | None = Field(default=None, description="失败原因")


async def analyze_skill(
    skill_md: str,
    tools: list,
    llm_client: LLMClient,
    context_manager: ContextManager,
) -> AddSkillResult:
    """分析 SKILL.md 内容，识别参数和工具依赖"""
    if not skill_md:
        return AddSkillResult(
            name="",
            description="",
            success=False,
            failure_reason="SKILL.md 内容为空",
        )

    messages = context_manager.build_add_skill_messages(
        skill_md=skill_md,
        tools=tools,
    )

    llm_client.meta_agent_name = "add_skill"

    response = await llm_client.chat(
        messages=messages,
        temperature=0.1,
    )

    content = response.choices[0].message.content or ""

    return _parse_response(content)


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
