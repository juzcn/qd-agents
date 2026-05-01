"""工具版本检测和更新命令"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console

from qd_agents.config import load_config
from qd_agents.cli.utils.registry import get_tool_registry
from qd_agents.tools.version import detect_package_version, get_latest_version


def update_check(
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> None:
    """检查 default 类别 MCP 工具是否有新版本"""
    config = load_config(base_dir=base_dir, config_file=config_file)
    registry = get_tool_registry(config)

    # 只检查 default 类别的 MCP 工具
    default_tools = [t for t in registry.list_all() if t.scope == "default"]
    if not default_tools:
        console.print("[yellow]没有默认 MCP 工具需要检查[/]")
        return

    console.print(f"检查 {len(default_tools)} 个默认 MCP 工具的版本更新...\n")

    has_update = False
    for tool in default_tools:
        install_source = tool.metadata.install_source
        current_version = tool.metadata.version
        command = tool.execution.command

        if not install_source or not command:
            console.print(f"  [dim]{tool.name}: 无安装源信息，跳过[/]")
            continue

        latest = get_latest_version(command, install_source)
        if latest and latest != current_version:
            has_update = True
            console.print(
                f"  [yellow]{tool.name}[/]: {current_version or '未知'} → [green]{latest}[/] (可更新)"
            )
        elif latest:
            console.print(f"  [green]{tool.name}[/]: {current_version or '未知'} (已是最新)")
        else:
            console.print(f"  [dim]{tool.name}: 无法查询远程版本[/]")

    if not has_update:
        console.print("\n[green]所有默认工具均为最新版本[/]")


def update_tools(
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> None:
    """更新 default 类别 MCP 工具到最新版本"""
    import subprocess

    config = load_config(base_dir=base_dir, config_file=config_file)
    registry = get_tool_registry(config)

    default_tools = [t for t in registry.list_all() if t.scope == "default"]
    if not default_tools:
        console.print("[yellow]没有默认 MCP 工具需要更新[/]")
        return

    updated_count = 0
    for tool in default_tools:
        install_source = tool.metadata.install_source
        command = tool.execution.command

        if not install_source or not command:
            console.print(f"  [dim]{tool.name}: 无安装源信息，跳过[/]")
            continue

        # 先检查是否有更新
        latest = get_latest_version(command, install_source)
        current_version = tool.metadata.version
        if latest and latest == current_version:
            console.print(f"  [green]{tool.name}[/]: 已是最新 ({current_version})")
            continue

        # 执行更新
        console.print(f"  更新 {tool.name} ({install_source})...")
        try:
            if command in ("npx", "npm"):
                result = subprocess.run(
                    ["npm", "install", "-g", install_source],
                    capture_output=True, text=True, timeout=120,
                )
            elif command in ("uvx", "pip"):
                result = subprocess.run(
                    ["pip", "install", "--upgrade", install_source],
                    capture_output=True, text=True, timeout=120,
                )
            else:
                console.print(f"  [yellow]{tool.name}: 不支持的包管理器 {command}[/]")
                continue

            if result.returncode != 0:
                console.print(f"  [red]{tool.name}: 更新失败 — {result.stderr[:200]}[/]")
                continue

            # 重新检测版本并更新注册表
            new_version, _ = detect_package_version(command, tool.execution.args)
            tool.metadata.version = new_version or latest
            tool.updated_at = datetime.utcnow()
            registry.register(tool)
            updated_count += 1
            console.print(f"  [green]{tool.name}[/]: 更新成功 → {new_version or latest}")
        except subprocess.TimeoutExpired:
            console.print(f"  [red]{tool.name}: 更新超时[/]")
        except Exception as e:
            console.print(f"  [red]{tool.name}: 更新失败 — {e}[/]")

    console.print(f"\n[green]更新完成: {updated_count} 个工具已更新[/]")
