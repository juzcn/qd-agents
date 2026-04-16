"""
版本信息命令

负责显示版本信息。
"""

from rich.console import Console


def show_version(console: Console) -> None:
    """
    显示版本信息

    Args:
        console: Rich 控制台对象
    """
    from qd_agents import __version__
    console.print(f"qd-agents 版本: [bold]{__version__}[/]")