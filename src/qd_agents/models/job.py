"""Job 路由决策模型

Evolve 主循环的输出，决定路由到哪个子循环。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .evolve import AskUserInfo, DelegateInfo


class Job(BaseModel):
    """Evolve 路由决策输出

    Evolve 主循环分析用户输入后，输出此对象决定路由方向。
    """
    route: Literal["direct-answer", "use-tool", "find-tools", "ask_user", "delegate"] = Field(
        description="路由方向: direct-answer(直接回答), use-tool(使用已有工具), find-tools(发现缺失工具), ask_user(请求用户输入), delegate(委托用户执行)"
    )
    task_background: str = Field(
        default="",
        description="任务背景上下文，传递给子循环"
    )
    task_description: str = Field(
        default="",
        description="任务具体描述，传递给子循环"
    )
    tool_list: list[str] = Field(
        default_factory=list,
        description="需要使用的工具名列表（use-tool 路由时使用）"
    )
    orchestration_logic: str = Field(
        default="",
        description="工具编排逻辑描述（use-tool 路由时使用）"
    )
    direct_answer: str | None = Field(
        default=None,
        description="直接回答（仅 direct-answer 路由）"
    )
    ask_user: AskUserInfo | None = Field(
        default=None,
        description="向用户请求的信息（仅 ask_user 路由）"
    )
    delegate: DelegateInfo | None = Field(
        default=None,
        description="委托用户执行的信息（仅 delegate 路由）"
    )
    reflection: str | None = Field(
        default=None,
        description="对当前决策的反思"
    )
