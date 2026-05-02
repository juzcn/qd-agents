"""Find-Tools 子循环结果模型"""
from __future__ import annotations

from pydantic import BaseModel, Field


class FindToolsResult(BaseModel):
    """Find-Tools 子循环执行结果"""
    found_tool_names: list[str] = Field(
        default_factory=list,
        description="新注册的工具名列表"
    )
    search_summary: str = Field(
        default="",
        description="搜索结果摘要"
    )
    success: bool = Field(default=True, description="是否成功发现并注册工具")
    failure_reason: str | None = Field(
        default=None,
        description="失败原因"
    )
    total_tokens: int = Field(default=0, description="总 token 数")
    last_prompt_tokens: int = Field(default=0, description="末轮 prompt token 数")
    total_duration_ms: int = Field(default=0, description="总耗时（毫秒）")
