"""HTTP/OpenAPI 工具注册命令"""

from __future__ import annotations

from typing import Optional

import click
from rich.console import Console

from qd_agents.cli.commands._registration_base import optional_path, run_registration
from qd_agents.tools.registrars import register_http_tool


@click.command("http")
@click.argument("name")
@click.argument("openapi_url")
@click.option("--default", is_flag=True, help="设为默认工具")
@click.option("--filter", "filter_str", default=None, help="endpoint 过滤器")
@click.option("--env", "extra_env", multiple=True, help="需要的环境变量名")
@click.option("--timeout", type=int, default=30, help="超时秒数")
@click.option("--base-dir", type=click.Path(file_okay=False), default=None)
@click.option("--config-file", type=click.Path(exists=True), default=None)
def http_add(
    name: str,
    openapi_url: str,
    default: bool,
    filter_str: Optional[str],
    extra_env: tuple[str, ...],
    timeout: int,
    base_dir: Optional[str],
    config_file: Optional[str],
) -> None:
    """注册 HTTP/OpenAPI 工具。NAME 为工具组名，OPENAPI_URL 为 spec URL"""
    console = Console()
    tool = run_registration(
        console, "HTTP 工具", name, register_http_tool,
        name=name, openapi_url=openapi_url,
        filter_str=filter_str,
        extra_env=list(extra_env) or None,
        timeout=timeout, default=default,
        base_dir=optional_path(base_dir),
        config_file=optional_path(config_file),
    )
    if tool:
        console.print(f"  Base URL: {tool.execution.base_url}")
        if filter_str:
            console.print(f"  过滤器: {filter_str}")
        if tool.execution.env:
            console.print(f"  所需环境变量: {', '.join(tool.execution.env.keys())}")
