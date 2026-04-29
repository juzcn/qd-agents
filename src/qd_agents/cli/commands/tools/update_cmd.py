"""工具版本检测和更新命令"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console

from qd_agents.config import load_config
from qd_agents.cli.utils.registry import get_tool_registry


def _detect_package_version(command: str, args: list[str]) -> tuple[str | None, str | None]:
    """检测 MCP 工具包的版本和安装源

    Returns:
        (version, install_source) — 版本号和安装源（如 npm/pip 包名）
    """
    install_source = None
    version = None

    # 从 args 中提取包名
    if command in ("npx", "npm") and args:
        # npx -y @scope/package → 提取 @scope/package
        filtered = [a for a in args if a not in ("-y", "--yes", "--")]
        if filtered:
            install_source = filtered[0]
    elif command in ("uvx", "pip") and args:
        # uvx package-name → 提取 package-name
        filtered = [a for a in args if not a.startswith("-")]
        if filtered:
            install_source = filtered[0]

    if not install_source:
        return None, None

    # 尝试获取已安装版本
    try:
        if command in ("npx", "npm"):
            version = _npm_detect_version(install_source)
        elif command in ("uvx", "pip"):
            version = _pip_detect_version(install_source)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass

    return version, install_source


def _npm_detect_version(package: str) -> str | None:
    """npm 包版本检测：npm list -g → npm view"""
    # 1. 尝试 npm list -g（本地已安装）
    result = subprocess.run(
        ["npm", "list", "-g", package, "--depth=0", "--json"],
        capture_output=True, text=True, timeout=10, shell=True,
    )
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            deps = data.get("dependencies", {})
            if package in deps:
                return deps[package].get("version")
        except json.JSONDecodeError:
            pass
    # 2. 尝试 npm view（远程查询最新版本）
    result = subprocess.run(
        ["npm", "view", package, "version"],
        capture_output=True, text=True, timeout=10, shell=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def _pip_detect_version(package: str) -> str | None:
    """pip/uvx 包版本检测：uv pip show → pip index versions"""
    # 1. 尝试 uv pip show（当前 venv 已安装）
    result = subprocess.run(
        ["uv", "pip", "show", package],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    # 2. 尝试 pip index versions（PyPI 远程查询）
    result = subprocess.run(
        ["pip", "index", "versions", package],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0:
        # 输出格式: package (X.Y.Z)\nAvailable versions: ...
        first_line = result.stdout.strip().splitlines()[0]
        match = re.search(r"\(([^)]+)\)", first_line)
        if match:
            return match.group(1)
    return None


def _get_latest_version(command: str, install_source: str) -> str | None:
    """查询包的最新版本"""
    try:
        if command in ("npx", "npm"):
            result = subprocess.run(
                ["npm", "view", install_source, "version"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        elif command in ("uvx", "pip"):
            result = subprocess.run(
                ["pip", "index", "versions", install_source],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                # 输出格式: pip index versions package → available versions: 1.0, 0.9
                for line in result.stdout.splitlines():
                    if "available versions" in line.lower() or "LATEST" in line:
                        # 取第一个（最新）版本
                        versions = line.split(":")[-1].strip().split(",")
                        if versions:
                            return versions[0].strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


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

        latest = _get_latest_version(command, install_source)
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
        latest = _get_latest_version(command, install_source)
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
            new_version, _ = _detect_package_version(command, tool.execution.args)
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