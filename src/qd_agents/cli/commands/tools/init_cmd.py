"""tools init — 初始化工具箱

空数据库时：从 config.json preset_tools 注册所有预装工具。
有数据时：重注册所有工具（保持原有 scope 不变）。
"""

from __future__ import annotations

import logging
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Optional

from rich.console import Console

from qd_agents.config.loader import load_config
from qd_agents.config.models import Config as ConfigType
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
from qd_agents.tools.builtin_register import register_builtin_function_tools

logger = logging.getLogger(__name__)

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
    """初始化工具箱"""
    config = load_config(base_dir=base_dir, config_file=config_file)
    db_path = config.tool_registry.db_path if config.tool_registry else "data/tools.db"
    registry = ToolRegistry(db_path=str(db_path))

    existing = registry.list_all()
    is_empty = len(existing) == 0

    console.print("\n[bold]初始化工具箱[/]")

    if is_empty:
        _register_preset_tools(console, registry, config, base_dir, config_file)
    else:
        _reregister_tools(console, registry, base_dir, config_file, keep_user)

    total = len(registry.list_all())
    console.print(f"\n[bold]完成[/] 工具箱共 {total} 个工具")


def _register_preset_tools(
    console: Console,
    registry: ToolRegistry,
    config: ConfigType,
    base_dir: Optional[Path],
    config_file: Optional[Path],
) -> None:
    """空数据库：注册 builtin + 预装 default 工具"""
    # builtin
    before = {t.name for t in registry.list_all() if t.scope == "builtin"}
    register_builtin_function_tools(registry)
    new = {t.name for t in registry.list_all() if t.scope == "builtin"} - before
    for name in sorted(new):
        console.print(f"  [green]OK[/] {name} [dim](builtin/function)[/]")
    if not new:
        console.print("  [dim]builtin 工具已注册[/]")

    # local-search (bash)
    search_cmd = None
    for cmd in ("rg", "grep"):
        if shutil.which(cmd):
            search_cmd = cmd
            break
    if search_cmd:
        tool = Tool(
            id="default.local-search",
            name="local-search",
            description=f"搜索本地文本文件。使用 {search_cmd} 命令，支持正则表达式。",
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
        console.print(f"  [green]OK[/] local-search [dim](default/bash)[/]")
    else:
        console.print(f"  [yellow]SKIP[/] local-search: 未找到 rg 或 grep")

    # preset_tools from config
    if not config.preset_tools:
        return

    for preset in config.preset_tools:
        try:
            if preset.type == "cli":
                register_cli_tool(
                    name=preset.name,
                    command=preset.command or "",
                    timeout=preset.timeout,
                    default=True,
                    base_dir=base_dir,
                    config_file=config_file,
                )
            elif preset.type == "mcp":
                register_mcp_tool(
                    server=preset.server or preset.name,
                    default=True,
                    base_dir=base_dir,
                    config_file=config_file,
                )
            elif preset.type == "skill":
                register_skill_tool(
                    skill_name=preset.skill_name or preset.name,
                    default=True,
                    base_dir=base_dir,
                    config_file=config_file,
                )
            elif preset.type == "http":
                register_http_tool(
                    name=preset.name,
                    openapi_url=preset.spec_url or preset.spec_path or "",
                    default=True,
                    base_dir=base_dir,
                    config_file=config_file,
                )
            else:
                console.print(f"  [yellow]SKIP[/] {preset.name}: 未知类型 {preset.type}")
                continue
            console.print(f"  [green]OK[/] {preset.name} [dim](default/{preset.type})[/]")
        except Exception as e:
            logger.warning("注册预装工具 %s 失败: %s", preset.name, e)
            console.print(f"  [red]FAIL[/] {preset.name}: {e}")


def _reregister_tools(
    console: Console,
    registry: ToolRegistry,
    base_dir: Optional[Path],
    config_file: Optional[Path],
    keep_user: bool,
) -> None:
    """有数据时：重注册所有工具，保持原有 scope 不变"""
    tools = registry.list_all()

    # 不保留 user 工具时，先删除
    if not keep_user:
        user_tools = [t for t in tools if t.scope == "user"]
        if user_tools:
            deleted = registry.delete_by_scopes(["user"])
            console.print(f"  [dim]删除 {deleted} 个 user 工具[/]")
            tools = [t for t in tools if t.scope != "user"]

    # 重注册 builtin
    builtin_tools = [t for t in tools if t.scope == "builtin"]
    if builtin_tools:
        register_builtin_function_tools(registry)
        for t in builtin_tools:
            console.print(f"  [green]OK[/] {t.name} [dim]({t.scope}/function)[/]")

    # 按 execution type 分组重注册非 builtin 工具
    groups: dict[str, list] = defaultdict(list)
    for t in tools:
        if t.scope == "builtin":
            continue
        et = t.execution.type.value
        if et in ("bash", "function"):
            continue
        groups[et].append(t)

    for tool_type, tool_list in groups.items():
        dispatch = REGISTRATION_DISPATCH.get(tool_type)
        if not dispatch:
            console.print(f"  [yellow]跳过未知类型: {tool_type}[/]")
            continue

        register_fn, extract_fn = dispatch
        for t in tool_list:
            name = t.name
            scope = t.scope
            try:
                kwargs = extract_fn(t)
                # 保持原有 scope：default=True 注册为 default，否则 user
                register_fn(**kwargs, default=(scope == "default"), base_dir=base_dir, config_file=config_file)
                console.print(f"  [green]OK[/] {name} [dim]({scope}/{tool_type})[/]")
            except Exception as e:
                logger.warning("重注册 %s 失败: %s", name, e)
                console.print(f"  [red]FAIL[/] {name}: {e}")