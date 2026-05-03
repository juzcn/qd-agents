"""tools init — 初始化工具箱

注册所有 builtin function 工具 + default 工具（local-search 等），
并按类型重注册已有的 user/default 工具。
"""

from __future__ import annotations

import logging
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Optional

from rich.console import Console

from qd_agents.config.loader import load_config
from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolExecutionType, ToolMetadata
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
from qd_agents.tools.builtin_register import (
    register_builtin_function_tools,
    register_meta_function_tools,
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
    """初始化工具箱：注册 builtin + default 工具，重注册已有工具。

    Args:
        keep_user: 为 True 时保留 scope=user 的工具不重注册
    """
    config = load_config(base_dir=base_dir, config_file=config_file)
    db_path = config.tool_registry.db_path if config.tool_registry else "data/tools.db"
    registry = ToolRegistry(db_path=str(db_path))

    # 1. 注册 builtin function 工具
    _register_builtin_tools(console, registry)

    # 2. 注册 default 工具（local-search 等）
    _register_default_tools(console, registry, base_dir, config_file)

    # 3. 重注册非 builtin 工具
    _reregister_user_tools(console, registry, base_dir, config_file, keep_user)


def _register_builtin_tools(console: Console, registry: ToolRegistry) -> None:
    """注册 builtin function 和 meta function 工具到数据库"""
    existing = {t.name for t in registry.list_all() if t.scope == "builtin"}

    before_builtin = len(existing)
    register_builtin_function_tools(registry)
    after_builtin = {t.name for t in registry.list_all() if t.scope == "builtin"}
    new_builtin = after_builtin - existing
    if new_builtin:
        console.print(f"\n[bold]注册 builtin function 工具 ({len(new_builtin)} 个新增)[/]")
        for name in sorted(new_builtin):
            console.print(f"  [green]OK[/] {name}")
    else:
        console.print(f"\n[bold]builtin function 工具[/] [dim]（已注册，跳过）[/]")

    existing_meta = {t.name for t in registry.list_all() if t.scope == "builtin"}
    register_meta_function_tools(registry)
    after_meta = {t.name for t in registry.list_all() if t.scope == "builtin"}
    new_meta = after_meta - existing_meta
    if new_meta:
        console.print(f"\n[bold]注册 meta function 工具 ({len(new_meta)} 个新增)[/]")
        for name in sorted(new_meta):
            console.print(f"  [green]OK[/] {name}")
    else:
        console.print(f"[bold]meta function 工具[/] [dim]（已注册，跳过）[/]")


def _register_default_tools(
    console: Console,
    registry: ToolRegistry,
    base_dir: Optional[Path],
    config_file: Optional[Path],
) -> None:
    """注册 default 工具（local-search、filesystem 等）"""
    existing_names = {t.name for t in registry.list_all()}

    # --- local-search ---
    # rg/grep 是大模型熟悉的工具，注册为 bash 类型，无需 schema
    if "local-search" not in existing_names:
        search_cmd = None
        for cmd in ("rg", "grep"):
            if shutil.which(cmd):
                search_cmd = cmd
                break
        if search_cmd:
            tool = Tool(
                id="default.local-search",
                name="local-search",
                description=f"搜索本地文本文件。使用 {search_cmd} 命令，支持正则表达式。用法: {search_cmd} <pattern> [path]",
                parameters={"type": "object", "properties": {}, "required": []},
                execution=ToolExecutionConfig(
                    type=ToolExecutionType.BASH,
                    shell_command=search_cmd,
                    timeout=30,
                ),
                scope="default",
                metadata=ToolMetadata(tags=["bash", "search", "local"]),
            )
            registry.register(tool)
            console.print(f"\n  [green]OK[/] local-search (command: {search_cmd})")
        else:
            console.print(f"\n  [yellow]SKIP[/] local-search: 未找到 rg 或 grep")
    else:
        console.print(f"\n  [dim]local-search 已注册，跳过[/]")

    # --- filesystem mcp ---
    if "filesystem" not in existing_names:
        try:
            tool = register_mcp_tool(server="filesystem", default=True)
            console.print(f"  [green]OK[/] filesystem (mcp)")
        except Exception as e:
            logger.warning("注册 filesystem mcp 失败: %s", e)
            console.print(f"  [yellow]SKIP[/] filesystem: {e}")
    else:
        console.print(f"  [dim]filesystem 已注册，跳过[/]")


def _reregister_user_tools(
    console: Console,
    registry: ToolRegistry,
    base_dir: Optional[Path],
    config_file: Optional[Path],
    keep_user: bool,
) -> None:
    """按类型重注册非 builtin 工具"""
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
                console.print("[yellow]没有需要重注册的工具[/]")
                return

    # 按 execution type 分组，跳过 builtin 和 bash 类型
    groups: dict[str, list] = defaultdict(list)
    for t in tools:
        if t.scope == "builtin":
            continue
        et = t.execution.type.value
        if et == "bash":
            continue
        if et == "function":
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
