"""GitHub API 辅助函数 — 版本获取、目录下载、请求头构建"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from rich.console import Console

from qd_agents.config import load_runtime_config


def _github_headers(base_dir: Optional[Path] = None) -> dict[str, str]:
    """构建 GitHub API 请求头（支持 GITHUB_TOKEN 避免速率限制）

    优先级：环境变量 GITHUB_TOKEN > 环境变量 GITHUB_TOKEN_CLASSIC > runtime.json tools_credentials.github
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN_CLASSIC")
    if not token:
        try:
            runtime_config = load_runtime_config(base_dir=base_dir)
            token = runtime_config.tools_credentials.get_api_key("github") or ""
        except Exception:
            token = ""
    headers: dict[str, str] = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "qd-agents",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_github_version(url: str) -> str | None:
    """从 GitHub 项目的配置文件中提取版本号

    支持 pyproject.toml、package.json、setup.py 等常见配置文件。
    将 GitHub 页面 URL 转换为 raw 内容 URL 后获取。

    Returns:
        版本号字符串，如 "1.2.3"；无法提取时返回 None
    """
    import httpx

    # 从 URL 中提取 owner/repo
    # 支持: https://github.com/owner/repo, https://github.com/owner/repo/...
    match = re.match(r"https://github\.com/([^/]+/[^/]+)", url)
    if not match:
        return None

    repo_path = match.group(1).rstrip("/")

    # 按优先级尝试的配置文件列表
    config_files = [
        ("pyproject.toml", _extract_version_from_pyproject),
        ("package.json", _extract_version_from_package_json),
        ("setup.py", _extract_version_from_setup_py),
    ]

    for filename, extractor in config_files:
        raw_url = f"https://raw.githubusercontent.com/{repo_path}/HEAD/{filename}"
        try:
            resp = httpx.get(raw_url, follow_redirects=True, timeout=10)
            if resp.status_code == 200:
                version = extractor(resp.text)
                if version:
                    return version
        except httpx.HTTPError:
            continue

    return None


def _extract_version_from_pyproject(content: str) -> str | None:
    """从 pyproject.toml 内容中提取版本号"""
    # version = "1.2.3" (动态版本声明)
    match = re.search(r'^version\s*=\s*"(\d+\.\d+(?:\.\d+)?)"', content, re.MULTILINE)
    if match:
        return match.group(1)

    # [project] 下的 version = "1.2.3"
    match = re.search(r'\[project\].*?\n.*?version\s*=\s*"(\d+\.\d+(?:\.\d+)?)"', content, re.DOTALL)
    if match:
        return match.group(1)

    return None


def _extract_version_from_package_json(content: str) -> str | None:
    """从 package.json 内容中提取版本号"""
    import json

    try:
        data = json.loads(content)
        version = data.get("version")
        if version and re.match(r"\d+\.\d+(?:\.\d+)?", version):
            return version
    except json.JSONDecodeError:
        pass
    return None


def _extract_version_from_setup_py(content: str) -> str | None:
    """从 setup.py 内容中提取版本号"""
    # version="1.2.3" 或 version='1.2.3'
    match = re.search(r'version\s*=\s*["\'](\d+\.\d+(?:\.\d+)?)["\']', content)
    if match:
        return match.group(1)

    return None


def _download_github_skill_dir(
    console: Console,
    gh_repo_path: str,
    skill_name: str,
    skill_dir: Path,
    base_dir: Optional[Path] = None,
) -> int:
    """通过 GitHub Contents API 下载 skill 文件夹的全部文件

    Args:
        console: Rich 控制台
        gh_repo_path: GitHub 仓库路径，如 "tavily-ai/skills"
        skill_name: skill 名称，如 "tavily-search"
        skill_dir: 本地保存目录
        base_dir: 基础目录（用于读取 runtime.json 中的 token）

    Returns:
        成功下载的文件数量
    """
    import httpx

    headers = _github_headers(base_dir=base_dir)

    # 尝试常见的 skill 目录路径
    # skillset 仓库通常结构: skills/<name>/ 或 <name>/
    candidate_paths = [
        f"skills/{skill_name}",
        skill_name,
    ]

    dir_listing = None
    for dir_path in candidate_paths:
        api_url = f"https://api.github.com/repos/{gh_repo_path}/contents/{dir_path}"
        try:
            resp = httpx.get(api_url, headers=headers, follow_redirects=True, timeout=15)
            if resp.status_code == 200:
                dir_listing = resp.json()
                break
            elif resp.status_code == 403:
                console.print(f"    [yellow]GitHub API 403: {resp.json().get('message', 'rate limit exceeded')[:100]}[/]")
        except httpx.HTTPError:
            continue

    if not dir_listing:
        return 0

    # 递归下载目录中的所有文件
    return _download_github_dir_recursive(console, dir_listing, skill_dir, base_dir)


def _download_github_dir_recursive(
    console: Console,
    entries: list[dict],
    target_dir: Path,
    base_dir: Optional[Path] = None,
) -> int:
    """递归下载 GitHub 目录条目

    Args:
        console: Rich 控制台
        entries: GitHub Contents API 返回的目录条目列表
        target_dir: 本地保存目录
        base_dir: 基础目录（用于读取 runtime.json 中的 token）

    Returns:
        成功下载的文件数量
    """
    import httpx

    headers = _github_headers(base_dir=base_dir)
    downloaded = 0

    for entry in entries:
        entry_name = entry.get("name", "")
        entry_type = entry.get("type", "")
        download_url = entry.get("download_url", "")

        if entry_type == "file" and download_url:
            try:
                resp = httpx.get(download_url, headers=headers, follow_redirects=True, timeout=30)
                if resp.status_code == 200:
                    file_path = target_dir / entry_name
                    file_path.write_text(resp.text, encoding="utf-8")
                    downloaded += 1
                    console.print(f"    [dim]下载: {entry_name}[/]")
            except httpx.HTTPError as e:
                console.print(f"    [yellow]下载 {entry_name} 失败: {e}[/]")

        elif entry_type == "dir":
            # 递归处理子目录
            sub_dir = target_dir / entry_name
            sub_dir.mkdir(parents=True, exist_ok=True)
            api_url = entry.get("url", "")
            if api_url:
                try:
                    resp = httpx.get(api_url, headers=headers, follow_redirects=True, timeout=15)
                    if resp.status_code == 200:
                        sub_entries = resp.json()
                        downloaded += _download_github_dir_recursive(console, sub_entries, sub_dir, base_dir)
                except httpx.HTTPError as e:
                    console.print(f"    [yellow]枚举子目录 {entry_name} 失败: {e}[/]")

    return downloaded