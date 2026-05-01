"""注册基础设施 — 共享的 registry 创建和 tool 保存模式"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from qd_agents.config.loader import load_config
from qd_agents.models.tool import Tool
from qd_agents.registry.registry import ToolRegistry

logger = logging.getLogger(__name__)


def get_registry(base_dir: Optional[Path] = None, config_file: Optional[Path] = None) -> ToolRegistry:
    """加载配置并返回 ToolRegistry 实例"""
    config = load_config(base_dir=base_dir, config_file=config_file)
    db_path = config.tool_registry.db_path if config.tool_registry else "data/tools.db"
    return ToolRegistry(db_path=str(db_path))


def save_tool(tool: Tool, base_dir: Optional[Path] = None, config_file: Optional[Path] = None) -> Tool:
    """注册 Tool 到 registry 并返回"""
    registry = get_registry(base_dir, config_file)
    registry.register(tool)
    logger.info("工具已注册: %s (%s)", tool.name, tool.id)
    return tool