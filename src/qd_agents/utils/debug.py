"""
调试输出工具
"""

from typing import Any, Optional
from dataclasses import dataclass
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.json import JSON
from rich.syntax import Syntax


_console = Console()
_debug_enabled = False


def enable_debug() -> None:
    """启用调试模式"""
    global _debug_enabled
    _debug_enabled = True


def disable_debug() -> None:
    """禁用调试模式"""
    global _debug_enabled
    _debug_enabled = False


def is_debug_enabled() -> bool:
    """检查调试模式是否启用"""
    return _debug_enabled


def debug_print(
    title: str,
    content: Any,
    style: str = "blue"
) -> None:
    """
    调试输出 - 仅在调试模式下显示

    Args:
        title: 输出标题
        content: 输出内容
        style: 颜色样式
    """
    if not _debug_enabled:
        return

    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

    if isinstance(content, (dict, list)):
        try:
            import json
            content_str = json.dumps(content, ensure_ascii=False, indent=2)
            content_render = JSON(content_str)
        except Exception:
            content_render = str(content)
    elif isinstance(content, str):
        if content.strip().startswith("{") or content.strip().startswith("["):
            try:
                content_render = JSON(content)
            except Exception:
                content_render = content
        else:
            content_render = content
    else:
        content_render = str(content)

    panel = Panel(
        content_render,
        title=f"[bold {style}]{title}[/]",
        subtitle=f"[{style}]{timestamp}[/{style}]",
        border_style=style
    )
    _console.print(panel)


def debug_step(step_name: str, description: str) -> None:
    """
    输出处理步骤

    Args:
        step_name: 步骤名称
        description: 步骤描述
    """
    if not _debug_enabled:
        return

    _console.print(f"  [dim]→[/] [cyan]{step_name}[/]: {description}")


def debug_table(title: str, data: list[dict[str, Any]]) -> None:
    """
    以表格形式输出调试信息

    Args:
        title: 表格标题
        data: 数据列表
    """
    if not _debug_enabled or not data:
        return

    table = Table(title=title)
    first_row = data[0]
    for key in first_row.keys():
        table.add_column(key)

    for row in data:
        table.add_row(*[str(row.get(key, "")) for key in first_row.keys()])

    _console.print(table)


def debug_separator() -> None:
    """输出分隔线"""
    if not _debug_enabled:
        return

    _console.print()
    _console.rule("[dim]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/]")
    _console.print()


def print_header() -> None:
    """打印程序头"""
    _console.print()
    _console.print("[bold green]qd-agents[/] - 意图驱动的上下文隔离多Agent系统")
    _console.print(f"[dim]版本: 0.1.0[/]")
    _console.print()


def print_normal(message: str = "") -> None:
    """正常输出（不受调试模式影响）"""
    _console.print(message)
