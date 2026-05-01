"""CLI 注册命令共享模式 — 消除各命令文件的重复样板"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Callable, Any

from rich.console import Console

from qd_agents.models.tool import Tool

logger = logging.getLogger(__name__)


def optional_path(value: Optional[str]) -> Optional[Path]:
    """将可选字符串转换为可选 Path"""
    return Path(value) if value else None


def run_registration(
    console: Console,
    tool_label: str,
    tool_name: str,
    register_fn: Callable[..., Tool],
    **kwargs: Any,
) -> Tool | None:
    """执行工具注册的标准模式：打印标题 → 调用注册函数 → 打印 OK/FAIL。

    Args:
        console: Rich 控制台
        tool_label: 显示标签（如 "CLI 工具"、"MCP 服务器"）
        tool_name: 工具名称/标识符
        register_fn: 注册函数
        **kwargs: 传递给 register_fn 的参数

    Returns:
        注册成功的 Tool 对象，失败返回 None
    """
    console.print(f"[bold]注册 {tool_label}:[/] {tool_name}")
    try:
        tool = register_fn(**kwargs)
        console.print(f"  [green]OK[/] {tool.name}: {tool.description}")
        return tool
    except Exception as e:
        console.print(f"  [red]FAIL {tool_name}: {e}[/]")
        logger.error("注册 %s 失败: %s", tool_label, e)
        return None
