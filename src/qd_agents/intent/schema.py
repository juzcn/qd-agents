"""
意图对象 Schema 定义
"""

from typing import Any, Optional, List, Dict, Literal
from pydantic import BaseModel, Field
from uuid import uuid4


def generate_intent_id() -> str:
    """生成唯一的意图ID"""
    return f"intent_{uuid4().hex[:8]}"


class Constraints(BaseModel):
    """意图约束条件"""
    require_confirmation: Optional[bool] = Field(default=None, description="是否需要用户确认")
    timeout_seconds: Optional[int] = Field(default=None, description="超时时间（秒）")
    priority: Optional[Literal["low", "normal", "high"]] = Field(default="normal", description="优先级")
    async_: Optional[bool] = Field(default=None, alias="async", description="是否异步执行")


class Dependency(BaseModel):
    """意图依赖关系"""
    intent_id: str = Field(..., description="依赖的意图ID")
    condition: Optional[str] = Field(default=None, description="条件表达式")


class Meta(BaseModel):
    """意图元数据"""
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度 0-1")
    source_message_ids: List[str] = Field(default_factory=list, description="来源消息ID列表")
    user_id: str = Field(..., description="用户ID")
    session_id: str = Field(..., description="会话ID")


class Intent(BaseModel):
    """
    意图对象 - 自包含的结构化意图
    下游无需额外上下文即可执行
    """
    id: str = Field(default_factory=generate_intent_id, description="意图唯一标识")
    action: str = Field(..., description="动作动词，如 'query_weather'")
    domain: str = Field(..., description="领域，如 'weather'")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="所有参数已消歧、补全")
    constraints: Optional[Constraints] = Field(default=None, description="约束条件")
    depends_on: Optional[Dependency] = Field(default=None, description="依赖关系")
    fallback: Optional["Intent"] = Field(default=None, description="失败时的备选意图")
    meta: Meta = Field(..., description="元数据")

    model_config = {
        "populate_by_name": True
    }

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self.model_dump(by_alias=True, exclude_none=True)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Intent":
        """从字典创建意图对象"""
        return cls.model_validate(data)
