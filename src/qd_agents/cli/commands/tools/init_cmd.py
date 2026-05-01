"""tools init — 从数据库读取已注册工具，按类型重新调用纯逻辑层注册"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional

from rich.console import Console

from qd_agents.config.loader import load_config
from qd_agents.registry.registry import ToolRegistry
from qd_agents.tools.registrars import (
    register_cli_tool,
    register_http_tool,
    register_mcp_tool,
    register_skill_tool,
    cli_extract_args,
    mcp_extract_args,
    skill_extract_args,
    http_extract_args,
)

logger = logging.getLogger(__name__)

# 工具类型 → (注册函数, 参数提取函数) 的分发表
REGISTRATION_DISPATCH: dict[str, tuple] = {
    "cli": (register_cli_tool, cli_extract_args),
    "mcp": (register_mcp_tool, mcp_extract_args),
    "skill": (register_skill_tool, skill_extract_args),
    "http": (register_http_tool, http_extract_args),
}


def init_tools(
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
    keep_user: bool = False,
) -> None:
    """初始化工具箱：按类型重新调用纯逻辑层注册所有工具。

    Args:
        keep_user: 为 True 时保留 scope=user 的工具不重注册
    """
    config = load_config(base_dir=base_dir, config_file=config_file)
    db_path = config.tool_registry.db_path if config.tool_registry else "data/tools.db"
    registry = ToolRegistry(db_path=str(db_path))
    tools = registry.list_all()

    if not tools:
        console.print("[yellow]数据库中没有已注册的工具[/]")
        return

    # 不保留用户工具时，删除 scope=user 的工具
    if not keep_user:
        user_tools = [t for t in tools if t.scope == "user"]
        if user_tools:
            deleted = registry.delete_by_scopes(["user"])
            console.print(f"[dim]删除 {deleted} 个用户工具[/]")
            tools = [t for t in tools if t.scope != "user"]
            if not tools:
                console.print("[yellow]没有需要重注册的默认工具[/]")
                return
    else:
        # keep_user 模式下过滤掉用户工具不重注册
        tools = [t for t in tools if t.scope != "user"]
        if not tools:
            console.print("[yellow]没有需要重注册的默认工具[/]")
            return

    # 按 execution type 分组，跳过 builtin 类型
    groups: dict[str, list] = defaultdict(list)
    for t in tools:
        et = t.execution.type.value
        if et in ("bash", "function"):
            continue
        groups[et].append(t)

    stats: dict[str, int] = defaultdict(int)
    errors: dict[str, int] = defaultdict(int)

    for tool_type, tool_list in groups.items():
        dispatch = REGISTRATION_DISPATCH.get(tool_type)
        if not dispatch:
            console.print(f"[yellow]跳过未知类型: {tool_type} ({len(tool_list)} 个)[/]")
            errors[tool_type] += len(tool_list)
            continue

        register_fn, extract_fn = dispatch
        console.print(f"\n[bold]重注册 {tool_type} 工具 ({len(tool_list)} 个)[/]")

        for t in tool_list:
            name = t.name
            try:
                kwargs = extract_fn(t)
                register_fn(**kwargs, base_dir=base_dir, config_file=config_file)
                stats[tool_type] += 1
                console.print(f"  [green]OK[/] {name}")
            except Exception as e:
                logger.warning("重注册 %s (%s) 失败: %s", name, tool_type, e)
                console.print(f"  [red]FAIL {name}: {e}[/]")
                errors[tool_type] += 1

    # 汇总
    console.print("\n[bold]重注册完成[/]")
    for tool_type in sorted(set(list(stats.keys()) + list(errors.keys()))):
        ok = stats.get(tool_type, 0)
        err = errors.get(tool_type, 0)
        console.print(f"  {tool_type}: {ok} 成功, {err} 失败")
