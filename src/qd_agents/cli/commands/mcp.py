"""MCP 服务器工具注册命令"""

from __future__ import annotations

from typing import Optional

import click
from rich.console import Console

from qd_agents.cli.commands._registration_base import optional_path, run_registration
from qd_agents.tools.registrars import register_mcp_tool


@click.command("mcp")
@click.argument("server")
@click.option("--default", is_flag=True, help="设为默认工具")
@click.option("--base-dir", type=click.Path(file_okay=False), default=None)
@click.option("--config-file", type=click.Path(exists=True), default=None)
def mcp_add(
    server: str,
    default: bool,
    base_dir: Optional[str],
    config_file: Optional[str],
) -> None:
    """注册 MCP 服务器工具。SERVER 为 MCP 服务器名"""
    console = Console()
    tool = run_registration(
        console, "MCP 服务器", server, register_mcp_tool,
        server=server, default=default,
        base_dir=optional_path(base_dir),
        config_file=optional_path(config_file),
    )
    if tool:
        console.print(f"  服务器: {tool.execution.server}")
        if tool.execution.env:
            env_display = [k for k in tool.execution.env if k != "__mcp_config__"]
            if env_display:
                console.print(f"  所需环境变量: {', '.join(env_display)}")
