"""
tools init 命令 — 初始化默认工具集

重新安装所有内置和默认工具（除 bash 外），通过调用各类型的 add 命令实现。
使用 --keep 保留用户自定义工具，否则删除。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from qd_agents.config import load_config
from qd_agents.cli.utils.registry import get_tool_registry
from qd_agents.cli.commands.cli import cli_add
from qd_agents.cli.commands.http import http_add
from qd_agents.cli.commands.mcp import mcp_add
from qd_agents.cli.commands.skills import skill_add

logger = logging.getLogger(__name__)

# 项目根目录下的工具配置目录
_TOOLS_DIR = Path(__file__).resolve().parents[4] / "tools"


def _iter_json_files(subdir: str) -> list[Path]:
    """返回 tools/<subdir>/ 下所有 .json 文件。"""
    d = _TOOLS_DIR / subdir
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix == ".json")


def _install_cli_tools(console: Console, base_dir: Optional[Path], config_file: Optional[Path], interactive: bool) -> None:
    """安装 tools/cli/ 下的所有 CLI 工具。"""
    for json_path in _iter_json_files("cli"):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            console.print(f"[yellow][SKIP][/] {json_path.name}: {e}")
            continue
        name = cfg.get("name", json_path.stem)
        console.print(f"  [dim]安装 CLI 工具: {name}[/]")
        cli_add(
            console=console,
            name=name,
            command=cfg.get("command", ""),
            args=",".join(str(a) for a in cfg.get("args", [])) or None,
            extra_env=list(cfg.get("env", {}).keys()) or None,
            timeout=cfg.get("timeout", 300),
            default=True,
            base_dir=base_dir,
            config_file=config_file,
            interactive=interactive,
            json_file=json_path,
        )


def _install_http_tools(console: Console, base_dir: Optional[Path], config_file: Optional[Path], interactive: bool) -> None:
    """安装 tools/ 下的 HTTP 工具（暂无 tools/http/ 目录）。"""
    http_dir = _TOOLS_DIR / "http"
    if not http_dir.is_dir():
        return
    for json_path in _iter_json_files("http"):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            console.print(f"[yellow][SKIP][/] {json_path.name}: {e}")
            continue
        name = cfg.get("name", json_path.stem)
        console.print(f"  [dim]安装 HTTP 工具: {name}[/]")
        http_add(
            console=console,
            name=name,
            url=cfg.get("base_url", ""),
            method=cfg.get("method", "GET"),
            headers=[f"{k}:{v}" for k, v in cfg.get("headers", {}).items()] or None,
            auth=cfg.get("auth_type", "none"),
            extra_env=cfg.get("env", []) or None,
            timeout=cfg.get("timeout", 30),
            default=True,
            base_dir=base_dir,
            config_file=config_file,
            interactive=interactive,
            json_file=json_path,
        )


def _install_mcp_tools(console: Console, base_dir: Optional[Path], config_file: Optional[Path], interactive: bool) -> None:
    """安装 tools/mcp/ 下的所有 MCP 工具。"""
    for json_path in _iter_json_files("mcp"):
        console.print(f"  [dim]安装 MCP 工具: {json_path.stem}[/]")
        mcp_add(
            console=console,
            json_file=json_path,
            default=True,
            base_dir=base_dir,
            config_file=config_file,
            interactive=interactive,
        )


def _install_skill_tools(console: Console, base_dir: Optional[Path], config_file: Optional[Path], interactive: bool) -> None:
    """安装 skills/ 目录下的所有技能。"""
    config = load_config(base_dir=base_dir, config_file=config_file)
    skills_dir = config.skills_dir
    if not skills_dir or not skills_dir.is_dir():
        console.print("[dim]  未找到 skills 目录，跳过[/]")
        return

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        sk_md = skill_dir / "SKILL.md"
        if not sk_md.exists():
            continue
        console.print(f"  [dim]安装 SKILL 工具: {skill_dir.name}[/]")
        skill_add(
            console=console,
            skill_path=skill_dir,
            default=True,
            base_dir=base_dir,
            config_file=config_file,
            interactive=interactive,
        )


def tools_init(
    keep: bool = typer.Option(False, "--keep", help="保留用户自定义工具"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", help="基础目录"),
    config_file: Optional[Path] = typer.Option(None, "--config-file", help="配置文件路径"),
    interactive: bool = typer.Option(True, "--interactive/--no-interactive", help="交互式输入缺失的 API Key"),
) -> None:
    """初始化默认工具集"""
    console = Console()
    console.print("[bold]初始化默认工具集[/]")

    config = load_config(base_dir=base_dir, config_file=config_file)
    registry = get_tool_registry(config)

    # 如果不保留用户工具，删除所有非 bash 工具
    if not keep:
        all_tools = registry.list_tools()
        removed = 0
        for tool in all_tools:
            if tool.execution.type.value != "bash":
                registry.remove(tool.id)
                removed += 1
        if removed:
            console.print(f"  已删除 {removed} 个现有工具")

    # 安装 bash 工具（始终存在）
    console.print("\n[bold]Bash 工具[/]")
    console.print("  [dim]bash 工具为内置工具，始终可用[/]")

    # 安装 CLI 工具
    console.print("\n[bold]CLI 工具[/]")
    _install_cli_tools(console, base_dir, config_file, interactive)

    # 安装 HTTP 工具
    console.print("\n[bold]HTTP 工具[/]")
    _install_http_tools(console, base_dir, config_file, interactive)

    # 安装 MCP 工具
    console.print("\n[bold]MCP 工具[/]")
    _install_mcp_tools(console, base_dir, config_file, interactive)

    # 安装 Skill 工具
    console.print("\n[bold]Skill 工具[/]")
    _install_skill_tools(console, base_dir, config_file, interactive)

    # 汇总
    all_tools = registry.list_tools()
    console.print(f"\n[bold green]初始化完成！共 {len(all_tools)} 个工具可用[/]")
