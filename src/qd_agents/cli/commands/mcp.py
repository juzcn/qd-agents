"""MCP 服务器工具注册命令"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.console import Console

from qd_agents.cli.commands._registration_base import run_registration
from qd_agents.tools.registrars import register_mcp_tool


def mcp_add(
    console: Console,
    server: str,
    default: bool = False,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> None:
    """注册 MCP 服务器工具。SERVER 为 MCP 服务器名"""
    tool = run_registration(
        console, "MCP 服务器", server, register_mcp_tool,
        server=server, default=default,
        base_dir=base_dir,
        config_file=config_file,
    )
    if tool:
        console.print(f"  服务器: {tool.execution.server}")
        if tool.execution.env:
            env_display = [k for k in tool.execution.env if k != "__mcp_config__"]
            if env_display:
                console.print(f"  所需环境变量: {', '.join(env_display)}")
