"""工具列表命令"""

from pathlib import Path
from typing import Optional, List

from rich.console import Console
from rich.table import Table

from qd_agents.config import load_config
from qd_agents.cli.utils.registry import get_tool_registry


def list_tools(
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
    type_filter: Optional[List[str]] = None,
) -> None:
    """
    列出工具

    Args:
        console: Rich 控制台对象
        base_dir: 基础目录
        config_file: 配置文件路径
        type_filter: 工具类型过滤列表（如 ["mcp", "skill", "function"]）
    """
    config = load_config(base_dir=base_dir, config_file=config_file)

    registry = get_tool_registry(config)

    tools = registry.list_all()

    # 按类型过滤
    if type_filter:
        tools = [t for t in tools if t.execution.type.value.lower() in type_filter]

    if not tools:
        if type_filter:
            console.print(f"[yellow]未找到类型为 {', '.join(type_filter)} 的工具[/]")
        else:
            console.print("[yellow]未找到已注册的工具[/]")
        return

    table = Table(title=f"已注册工具 ({len(tools)} 个)")
    table.add_column("名称", style="cyan")
    table.add_column("类型", style="green")
    table.add_column("描述", style="dim", max_width=50)
    table.add_column("属性", style="magenta")
    table.add_column("版本", style="dim")
    table.add_column("ID", style="dim")

    for tool in tools:
        tool_type = tool.execution.type.value.lower() if tool.execution.type else "unknown"
        version = tool.metadata.version or "-"
        table.add_row(
            tool.name,
            tool_type,
            tool.description,
            tool.scope,
            version,
            tool.id,
        )

    console.print(table)
