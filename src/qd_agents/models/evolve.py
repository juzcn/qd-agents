"""
Evolve 路由判断数据模型
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EvolveResult(BaseModel):
    """Evolve 判断结果"""
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
    tool_list: list[str] = Field(
        default_factory=list,
        description="如果route=tool_use或coding，这里列出需要的工具名称"
    )
