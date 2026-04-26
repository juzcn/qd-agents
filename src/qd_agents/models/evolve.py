"""
Evolve 自主进化Agent 数据模型

EvolveMetaAgent 直接通过 function calling 调用工具，
EvolveResult 只用于 ask_user 和 delegate 两种特殊输出。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AskUserInfo(BaseModel):
    """向用户请求信息"""
    question: str = Field(description="向用户提出的问题")
    options: list[str] = Field(default_factory=list, description="可选的选项列表")
    reason: str = Field(description="为什么需要用户输入")


class DelegateInfo(BaseModel):
    """委托用户执行"""
    task: str = Field(description="需要用户执行的具体操作")
    guide: str = Field(description="详细的操作指南")
    reason: str = Field(description="为什么需要用户执行而非自己完成")


class EvolveResult(BaseModel):
    """Evolve 特殊输出结果（仅用于 ask_user 和 delegate）"""
    action: Literal["ask_user", "delegate"] = Field(
        description="行动模式: ask_user(请求用户输入), delegate(委托用户执行)"
    )
    ask_user: AskUserInfo | None = Field(
        default=None,
        description="如果action=ask_user，向用户请求的信息"
    )
    delegate: DelegateInfo | None = Field(
        default=None,
        description="如果action=delegate，委托用户执行的信息"
    )
    reflection: str | None = Field(
        default=None,
        description="对当前决策的反思"
    )