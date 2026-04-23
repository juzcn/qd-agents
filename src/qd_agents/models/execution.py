"""
执行相关数据模型
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ExecutionStatus(str, Enum):
    """执行状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionStep(BaseModel):
    """执行步骤"""
    step: int
    tool_id: str | None = None
    tool_name: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    error: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_ms: int = 0
    status: ExecutionStatus = ExecutionStatus.PENDING


class ExecutionResult(BaseModel):
    """执行结果"""
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str | None = None
    user_input: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    status: ExecutionStatus = ExecutionStatus.PENDING
    steps: list[ExecutionStep] = Field(default_factory=list)
    final_output: Any = None
    error: str | None = None
    total_duration_ms: int = 0
