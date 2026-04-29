"""
Agent 基类和数据模型

Agent：从用户输入到最终回答的完整任务处理单元。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


# --- 步骤回调类型 ---


StepCallback = Callable[[dict[str, Any]], None]


# --- Agent 数据模型 ---


@dataclass
class AgentResult:
    """Agent 执行结果"""

    final_answer: str
    success: bool
    working_memory: dict = field(default_factory=dict)
    interaction_log: list[dict] = field(default_factory=list)
    total_tokens: int = 0
    last_prompt_tokens: int = 0
    total_duration_ms: int = 0
    trace_id: str = ""


# --- Agent 抽象基类 ---


class Agent(ABC):
    """Agent：从用户输入到最终回答的完整任务处理单元"""

    name: str
    description: str

    @abstractmethod
    async def execute(self, user_input: str, history: list[dict], **kwargs) -> AgentResult: ...