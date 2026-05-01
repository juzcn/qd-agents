"""CLI 工具注册命令"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, List

from rich.console import Console

from qd_agents.cli.commands._registration_base import run_registration
from qd_agents.tools.registrars import register_cli_tool


def cli_add(
    console: Console,
    name: str,
    command: str,
    default: bool = False,
    extra_env: Optional[List[str]] = None,
    timeout: int = 300,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> None:
    """注册 CLI 工具。NAME 为工具名，COMMAND 为完整命令行"""
    tool = run_registration(
        console, "CLI 工具", name, register_cli_tool,
        name=name, command=command,
        extra_env=extra_env or None,
        timeout=timeout, default=default,
        base_dir=base_dir,
        config_file=config_file,
    )
    if tool and tool.execution.env:
        console.print(f"  所需环境变量: {', '.join(tool.execution.env.keys())}")
