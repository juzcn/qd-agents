"""
Agent 模块 - 核心智能体

包含：
- MetaAgent/Agent 基类和数据模型
- JudgeMetaAgent（路由判断元Agent）
- ToolCallingMetaAgent（工具调用元Agent）
- CodingMetaAgent（代码编排元Agent）
- AddSkillMetaAgent（技能分析元Agent）
- ToolUseAgent（Agent）
- CodePlanAgent（Agent）
- QDAgent（Agent 容器 + 资源管理器）
- MCPService（MCP 连接管理）
- ToolService（工具注册/缓存）
"""
from .base import (
    MetaAgent,
    MetaAgentInput,
    MetaAgentOutput,
    Agent,
    AgentResult,
)
from .judge_meta import JudgeMetaAgent
from .tool_calling_meta import ToolCallingMetaAgent
from .coding_meta import CodingMetaAgent
from .add_skill_meta import AddSkillMetaAgent
from .tool_use import ToolUseAgent
from .code_plan import CodePlanAgent
from .core import QDAgent
from .mcp_service import MCPService
from .tool_service import ToolService

# JudgeResult 现在从 models 模块导出，这里保留向后兼容的重新导出
from ..models import JudgeResult

__all__ = [
    # 基类
    "MetaAgent",
    "MetaAgentInput",
    "MetaAgentOutput",
    "Agent",
    "AgentResult",
    # 元Agent
    "JudgeMetaAgent",
    "JudgeResult",
    "ToolCallingMetaAgent",
    "CodingMetaAgent",
    "AddSkillMetaAgent",
    # Agent
    "ToolUseAgent",
    "CodePlanAgent",
    # 容器
    "QDAgent",
    # 服务
    "MCPService",
    "ToolService",
]