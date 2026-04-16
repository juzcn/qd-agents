"""
CLI 输出格式化

负责格式化控制台输出的辅助函数。
"""

from rich.console import Console
from rich.text import Text
from typing import Any, Optional


def print_success(console: Console, message: str) -> None:
    """
    打印成功消息

    Args:
        console: Rich 控制台对象
        message: 消息内容
    """
    console.print(f"[green]✓[/] {message}")


def print_error(console: Console, message: str) -> None:
    """
    打印错误消息

    Args:
        console: Rich 控制台对象
        message: 消息内容
    """
    console.print(f"[red]✗[/] {message}")


def print_warning(console: Console, message: str) -> None:
    """
    打印警告消息

    Args:
        console: Rich 控制台对象
        message: 消息内容
    """
    console.print(f"[yellow]⚠[/] {message}")


def print_info(console: Console, message: str) -> None:
    """
    打印信息消息

    Args:
        console: Rich 控制台对象
        message: 消息内容
    """
    console.print(f"[cyan]ℹ[/] {message}")


def print_dim(console: Console, message: str) -> None:
    """
    打印暗淡消息

    Args:
        console: Rich 控制台对象
        message: 消息内容
    """
    console.print(f"[dim]{message}[/]")


def print_bold(console: Console, message: str) -> None:
    """
    打印粗体消息

    Args:
        console: Rich 控制台对象
        message: 消息内容
    """
    console.print(f"[bold]{message}[/]")