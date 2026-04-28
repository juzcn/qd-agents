"""
Add-Skill 分析结果数据模型
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AddSkillResult(BaseModel):
    """Add-Skill 元Agent 分析结果"""
    name: str = Field(
        description="Skill 名称"
    )
    description: str = Field(
        description="Skill 描述"
    )
    parameters: dict = Field(
        default_factory=dict,
        description="参数 JSON Schema"
    )
    skill_type: Literal["tool_manual", "prompt"] = Field(
        default="tool_manual",
        description=(
            "技能类型: "
            "tool_manual(纯工具说明书，有具体命令/参数，按指南用 execute_bash 执行) "
            "prompt(行为指南/提示词，定义 LLM 应如何行为，有全局意义)"
        ),
    )
    tool_deps: list[str] = Field(
        default_factory=list,
        description="依赖的已注册工具名列表"
    )
    success: bool = Field(
        default=True,
        description="是否成功分析"
    )
    failure_reason: str | None = Field(
        default=None,
        description="失败原因（如依赖工具不存在）"
    )