"""
Agent 模块 - 核心智能体

包含：
- MetaAgent/Agent 基类和数据模型
- ToolCallingMetaAgent（元Agent）
- ToolUseAgent（Agent）
- QDAgent（Agent 容器 + 资源管理器）
"""
from .base import (
    MetaAgent,
    MetaAgentInput,
    MetaAgentOutput,
    Agent,
    AgentResult,
)
from .tool_use_meta import ToolCallingMetaAgent
from .tool_use import ToolUseAgent
from .core import QDAgent

__all__ = [
    # 基类
    "MetaAgent",
    "MetaAgentInput",
    "MetaAgentOutput",
    "Agent",
    "AgentResult",
    # 元Agent
    "ToolCallingMetaAgent",
    # Agent
    "ToolUseAgent",
    # 容器
    "QDAgent",
]
