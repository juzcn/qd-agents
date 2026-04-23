"""
数据模型模块

集中存放跨模块共享的数据模型。
"""
from .execution import ExecutionStatus, ExecutionStep, ExecutionResult
from .judge import JudgeResult

__all__ = [
    "ExecutionStatus",
    "ExecutionStep",
    "ExecutionResult",
    "JudgeResult",
]