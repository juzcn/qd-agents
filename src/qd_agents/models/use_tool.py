"""Use-Tool 子循环结果模型"""
from __future__ import annotations

from pydantic import BaseModel, Field


class UseToolResult(BaseModel):
    """Use-Tool 子循环执行结果"""
    final_answer: str = Field(description="任务最终答案")
    success: bool = Field(description="是否成功完成")
    tools_used: list[str] = Field(
        default_factory=list,
        description="实际使用的工具名列表"
    )
    total_tokens: int = Field(default=0, description="总 token 数")
    last_prompt_tokens: int = Field(default=0, description="末轮 prompt token 数")
    total_duration_ms: int = Field(default=0, description="总耗时（毫秒）")
