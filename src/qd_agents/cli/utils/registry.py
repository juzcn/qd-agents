"""Registry 实例化辅助 — 消除重复的 db_path 解析 + ToolRegistry() 模式"""
from __future__ import annotations

from pathlib import Path

from qd_agents.config.models import Config
from qd_agents.registry import ToolRegistry


def get_tool_registry(config: Config) -> ToolRegistry:
    """从 config 解析 db_path 并返回 ToolRegistry 实例"""
    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    return ToolRegistry(db_path=db_path)
