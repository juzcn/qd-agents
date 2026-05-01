"""Skill 工具注册命令"""

from __future__ import annotations

from typing import Optional

import click
from rich.console import Console

from qd_agents.cli.commands._registration_base import optional_path, run_registration
from qd_agents.tools.registrars import register_skill_tool


@click.command("skill")
@click.argument("skill_name")
@click.option("--default", is_flag=True, help="设为默认工具")
@click.option("--env", "extra_env", multiple=True, help="需要的环境变量名")
@click.option("--base-dir", type=click.Path(file_okay=False), default=None)
@click.option("--config-file", type=click.Path(exists=True), default=None)
def skill_add(
    skill_name: str,
    default: bool,
    extra_env: tuple[str, ...],
    base_dir: Optional[str],
    config_file: Optional[str],
) -> None:
    """注册 Skill 工具。SKILL_NAME 为 skill 目录名"""
    console = Console()
    tool = run_registration(
        console, "Skill 工具", skill_name, register_skill_tool,
        skill_name=skill_name,
        extra_env=list(extra_env) or None,
        default=default,
        base_dir=optional_path(base_dir),
        config_file=optional_path(config_file),
    )
    if tool and tool.execution.env:
        console.print(f"  所需环境变量: {', '.join(tool.execution.env.keys())}")
