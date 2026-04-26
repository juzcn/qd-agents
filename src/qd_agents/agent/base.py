"""
Agent/MetaAgent 基类和数据模型

元Agent（MetaAgent）：原子 LLM 调用单元，一个系统提示词 + 一种上下文构建 + 一种处理逻辑(含终止条件)。
Agent：从用户输入到最终回答的完整任务处理单元，可以是单个元Agent的简单包装，也可以是多个元Agent+引擎的编排协作。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


# --- 步骤回调类型 ---


StepCallback = Callable[[dict[str, Any]], None]


# --- 元Agent 数据模型 ---


@dataclass
class MetaAgentInput:
    """元Agent 输入"""

    user_message: str
    history: list[dict]
    context: dict = field(default_factory=dict)


@dataclass
class MetaAgentOutput:
    """元Agent 输出"""

    output: Any
    output_type: str  # "text" | "tool_list" | "plan" | "code" | "answer"
    success: bool
    messages: list[dict] = field(default_factory=list)
    model: str = ""
    total_tokens: int = 0
    latency_ms: int = 0
    iterations: int = 1


# --- Agent 数据模型 ---


@dataclass
class AgentResult:
    """Agent 执行结果"""

    final_answer: str
    success: bool
    meta_traces: list[MetaAgentOutput] = field(default_factory=list)
    working_memory: dict = field(default_factory=dict)
    interaction_log: list[dict] = field(default_factory=list)
    trace_id: str = ""
    total_duration_ms: int = 0


# --- 抽象基类 ---


class MetaAgent(ABC):
    """元Agent：原子 LLM 调用单元"""

    name: str

    @abstractmethod
    async def run(self, input: MetaAgentInput) -> MetaAgentOutput: ...


class Agent(ABC):
    """Agent：从用户输入到最终回答的完整任务处理单元"""

    name: str
    description: str

    @abstractmethod
    async def execute(self, user_input: str, history: list[dict], **kwargs) -> AgentResult: ...
