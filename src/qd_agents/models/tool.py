"""
工具数据模型

集中定义 Tool 相关的 Pydantic 数据模型，供 registry、agent、context、tools 等模块共享引用。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ToolVersionStatus(str, Enum):
    """工具版本状态"""
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class ToolExecutionType(str, Enum):
    """工具执行类型"""
    FUNCTION = "function"
    CLI = "cli"
    HTTP = "http"
    MCP = "mcp"
    BASH = "bash"
    SKILL = "skill"


class ToolExecutionConfig(BaseModel):
    """工具执行配置"""
    type: ToolExecutionType
    endpoint: str | None = None
    method: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    timeout: int = 30
    module: str | None = None
    function: str | None = None
    server: str | None = None
    tool: str | None = None
    transport: str = "stdio"  # MCP传输模式：stdio, sse, streamable-http
    shell_command: str | None = None  # bash工具：完整的shell命令字符串
    shell: str = "bash"  # 使用的shell，默认为bash


class ToolMetadata(BaseModel):
    """工具元数据"""
    category: str = "utilities"
    tags: list[str] = Field(default_factory=list)
    version: str = "1.0.0"
    version_status: ToolVersionStatus = ToolVersionStatus.ACTIVE
    changelog: str = ""
    deprecated_since: str | None = None
    retirement_date: str | None = None
    replaces: str | None = None
    replaced_by: str | None = None


class Tool(BaseModel):
    """工具定义"""
    id: str
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    execution: ToolExecutionConfig
    security: list[str] = Field(default_factory=list)
    metadata: ToolMetadata = Field(default_factory=ToolMetadata)
    dependencies: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, v):
        if not v:
            return {
                "type": "object",
                "properties": {},
                "required": [],
            }
        return v

    def to_openai_function(self) -> dict[str, Any]:
        """转换为 OpenAI Function Calling 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def get_search_text(self) -> str:
        """获取用于检索的文本"""
        parts = [
            self.name,
            self.description,
            self.metadata.category,
            " ".join(self.metadata.tags),
        ]
        return " ".join(filter(None, parts))