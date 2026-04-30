"""Tool registration helper — common load_config + get_registry + register + report pattern."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.console import Console

from qd_agents.config import load_config
from qd_agents.config.models import Config
from qd_agents.cli.utils.registry import get_tool_registry
from qd_agents.models.tool import Tool


def register_tool_and_report(
    tool: Tool,
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
    config: Optional[Config] = None,
) -> str:
    """加载配置，注册工具，打印成功消息。

    Args:
        tool: 要注册的工具
        console: Rich 控制台
        base_dir: 基础目录
        config_file: 配置文件路径
        config: 已加载的配置（若提供则跳过 load_config）

    Returns:
        注册的工具 ID。
    """
    if config is None:
        config = load_config(base_dir=base_dir, config_file=config_file)
    registry = get_tool_registry(config)
    tool_id = registry.register(tool)

    tool_type = tool.execution.type.value.upper()
    console.print(f"[green][OK][/] 已注册 {tool_type} 工具: {tool.name} ({tool_id})")
    return tool_id
