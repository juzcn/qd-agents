"""HTTP/OpenAPI 工具注册命令"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, List

from rich.console import Console

from qd_agents.cli.commands._registration_base import run_registration
from qd_agents.tools.registrars import register_http_tool


def http_add(
    console: Console,
    name: str,
    openapi_url: str,
    default: bool = False,
    filter_str: Optional[str] = None,
    extra_env: Optional[List[str]] = None,
    timeout: int = 30,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> None:
    """注册 HTTP/OpenAPI 工具。NAME 为工具组名，OPENAPI_URL 为 spec URL"""
    tool = run_registration(
        console, "HTTP 工具", name, register_http_tool,
        name=name, openapi_url=openapi_url,
        filter_str=filter_str,
        extra_env=extra_env or None,
        timeout=timeout, default=default,
        base_dir=base_dir,
        config_file=config_file,
    )
    if tool:
        console.print(f"  Base URL: {tool.execution.base_url}")
        if filter_str:
            console.print(f"  过滤器: {filter_str}")
        if tool.execution.env:
            console.print(f"  所需环境变量: {', '.join(tool.execution.env.keys())}")
