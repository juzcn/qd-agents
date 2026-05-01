"""CLI 工具注册命令"""

from __future__ import annotations

from typing import Optional

import click
from rich.console import Console

from qd_agents.cli.commands._registration_base import optional_path, run_registration
from qd_agents.tools.registrars import register_cli_tool


@click.command("cli")
@click.argument("name")
@click.argument("command")
@click.option("--default", is_flag=True, help="设为默认工具")
@click.option("--env", "extra_env", multiple=True, help="需要的环境变量名")
@click.option("--timeout", type=int, default=300, help="超时秒数")
@click.option("--base-dir", type=click.Path(file_okay=False), default=None)
@click.option("--config-file", type=click.Path(exists=True), default=None)
def cli_add(
    name: str,
    command: str,
    default: bool,
    extra_env: tuple[str, ...],
    timeout: int,
    base_dir: Optional[str],
    config_file: Optional[str],
) -> None:
    """注册 CLI 工具。NAME 为工具名，COMMAND 为完整命令行"""
    console = Console()
    tool = run_registration(
        console, "CLI 工具", name, register_cli_tool,
        name=name, command=command,
        extra_env=list(extra_env) or None,
        timeout=timeout, default=default,
        base_dir=optional_path(base_dir),
        config_file=optional_path(config_file),
    )
    if tool and tool.execution.env:
        console.print(f"  所需环境变量: {', '.join(tool.execution.env.keys())}")
