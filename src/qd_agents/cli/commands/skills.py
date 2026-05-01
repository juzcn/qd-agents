"""Skill 工具注册命令"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.console import Console

from qd_agents.cli.commands._registration_base import optional_path, run_registration
from qd_agents.tools.registrars import register_skill_tool


def skill_add(
    skill_name: str,
    default: bool = False,
    extra_env: list[str] | None = None,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> None:
    """注册 Skill 工具。SKILL_NAME 为 skill 目录名"""
    console = Console()
    tool = run_registration(
        console, "Skill 工具", skill_name, register_skill_tool,
        skill_name=skill_name,
        extra_env=extra_env,
        default=default,
        base_dir=base_dir,
        config_file=config_file,
    )
    if tool and tool.execution.env:
        console.print(f"  所需环境变量: {', '.join(tool.execution.env.keys())}")
