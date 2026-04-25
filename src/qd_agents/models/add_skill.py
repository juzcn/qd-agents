"""
Add-Skill 分析结果数据模型
"""
from __future__ import annotations

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