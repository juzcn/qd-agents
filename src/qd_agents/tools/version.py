"""包版本检测 — MCP/CLI 工具的版本和安装源检测"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Optional


def detect_package_version(command: str, args: list[str]) -> tuple[Optional[str], Optional[str]]:
    """检测 MCP 工具包的版本和安装源

    Returns:
        (version, install_source) — 版本号和安装源（如 npm/pip 包名）
    """
    install_source = None
    version = None

    # 从 args 中提取包名
    if command in ("npx", "npm") and args:
        skip_next = False
        filtered = []
        for a in args:
            if skip_next:
                skip_next = False
                continue
            if a in ("-y", "--yes", "--"):
                continue
            if a in ("-p", "--package"):
                skip_next = True
                continue
            if a.startswith("-p") and len(a) > 2:
                continue
            filtered.append(a)
        if filtered:
            install_source = filtered[0]
    elif command in ("uvx", "pip") and args:
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


def detect_version_simple(command: str, args: list[str]) -> tuple[Optional[str], Optional[str]]:
    """简单版本检测 — 直接运行 command --version（用于本地可执行文件）"""
    try:
        result = subprocess.run(
            [command, *args[:1], "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = (result.stdout or result.stderr).strip()
        if output:
            return output.split()[0] if output else None, command
    except Exception:
        pass
    return None, None


def _npm_detect_version(package: str) -> Optional[str]:
    """npm 包版本检测：npm list -g → npm view"""
    result = subprocess.run(
        ["npm", "list", "-g", package, "--depth=0", "--json"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, timeout=10, shell=True,
    )
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            deps = data.get("dependencies", {})
            if package in deps:
                return deps[package].get("version")
        except json.JSONDecodeError:
            pass
    result = subprocess.run(
        ["npm", "view", package, "version"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, timeout=10, shell=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def _pip_detect_version(package: str) -> Optional[str]:
    """pip/uvx 包版本检测：uv pip show → pip index versions"""
    result = subprocess.run(
        ["uv", "pip", "show", package],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    result = subprocess.run(
        ["pip", "index", "versions", package],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 0:
        first_line = result.stdout.strip().splitlines()[0]
        match = re.search(r"\(([^)]+)\)", first_line)
        if match:
            return match.group(1)
    return None


def get_latest_version(command: str, install_source: str) -> Optional[str]:
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
                for line in result.stdout.splitlines():
                    if "available versions" in line.lower() or "LATEST" in line:
                        versions = line.split(":")[-1].strip().split(",")
                        if versions:
                            return versions[0].strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None
