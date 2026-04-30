import logging
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table

from qd_agents.config import load_config
from qd_agents.cli.utils.registry import get_tool_registry

logger = logging.getLogger(__name__)


def init_tools(
    console: Console,
    base_dir: Optional[Path],
    config_file: Optional[Path],
    keep_user: bool = False,
) -> None:
    """初始化工具箱：删除所有工具，重新安装 builtin 和 default 工具。"""
    config = load_config(base_dir=base_dir, config_file=config_file)
    registry = get_tool_registry(config)

    # 1. 记录需要重新安装的 builtin 和 default 工具
    all_tools = registry.list_all()
    to_reinstall = [t for t in all_tools if t.scope in ("builtin", "default")]

    # 2. 删除所有工具
    for tool in all_tools:
        registry.delete(tool.id)
    console.print(f"  已删除 {len(all_tools)} 个工具")

    # 3. 重新安装 builtin 和 default 工具
    for tool in to_reinstall:
        registry.register(tool)
    if to_reinstall:
        console.print(f"  已重新安装 {len(to_reinstall)} 个 builtin/default 工具")

    # 4. 汇总
    _print_summary(console, registry)


def _print_summary(console: Console, registry) -> None:
    """打印工具箱汇总。"""
    all_tools = registry.list_all()

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("名称", style="green")
    table.add_column("类型", style="yellow")
    table.add_column("Scope")

    for tool in all_tools:
        table.add_row(
            tool.id,
            tool.name,
            tool.execution.type.value,
            tool.scope,
        )

    console.print(table)
