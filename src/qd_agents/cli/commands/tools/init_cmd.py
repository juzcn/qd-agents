"""工具初始化命令 — 注册内置工具 + 从 tools/ 目录扫描调用 add 命令注册默认工具"""
import json
from pathlib import Path
from typing import Optional, List

from rich.console import Console

from qd_agents.config import load_config
from qd_agents.tools.executors import create_bash_tool
from qd_agents.cli.utils.registry import get_tool_registry
from qd_agents import __version__

from ..mcp import mcp_add
from ..cli import cli_add
from ..http import http_add
from ..skills import skill_add


# 内置工具名列表（用于迁移去重）
BUILTIN_TOOL_NAMES = {"execute_bash"}


def init_tools(
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
    keep_user: bool = False,
) -> None:
    """
    初始化工具箱：注册内置工具 + 从 tools/ 目录扫描调用 add 命令注册默认工具

    - keep_user=False（默认）：清除所有工具后重新注册
    - keep_user=True：保留用户添加的工具，只清除 builtin + default 后重新注册
    - builtin 类别：execute_bash，不可删除不可更新
    - default 类别：从 tools/mcp/、tools/cli/、tools/http/、tools/skills/ 扫描，调用对应 add 命令注册
    """
    config = load_config(base_dir=base_dir, config_file=config_file)

    # 确保数据目录存在
    if config.storage:
        config.storage.data_dir.mkdir(parents=True, exist_ok=True)

    registry = get_tool_registry(config)

    # 清除工具
    if keep_user:
        deleted = registry.delete_by_scopes(["builtin", "default"])
        if deleted > 0:
            console.print(f"[dim]已清除 {deleted} 个内置/默认工具（保留用户工具）[/]")
    else:
        all_tools = registry.list_all()
        for tool in all_tools:
            registry.delete(tool.id)
        if all_tools:
            console.print(f"[dim]已清除所有 {len(all_tools)} 个工具（完全初始化）[/]")

    # 迁移去重：删除与内置工具同名的旧工具
    for name in BUILTIN_TOOL_NAMES:
        existing = registry.get_by_name(name)
        if existing:
            registry.delete(existing.id)
            console.print(f"[dim]迁移去重：移除旧版工具 {name} (ID: {existing.id}, 属性: {existing.scope})[/]")

    registered_tools: List[str] = []

    # ==================== 内置工具 (builtin) ====================

    bash_tool = create_bash_tool(
        name="execute_bash",
        description="执行bash/shell命令，支持管道、重定向等shell特性",
        shell_command="{command}",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的bash/shell命令"},
            },
            "required": ["command"],
        },
        scope="builtin",
        tags=["bash", "shell", "command", "core"],
        version=__version__,
    )
    bash_tool.id = "builtin.execute_bash"
    registry.register(bash_tool)
    registered_tools.append(bash_tool.name)

    # ==================== 默认工具 (default) ====================
    # 从 tools/mcp/、tools/cli/、tools/http/、tools/skills/ 扫描，调用对应 add 命令注册

    tools_base = base_dir or Path(".")

    # --- tools/mcp/ → mcp_add ---
    mcp_dir = tools_base / "tools" / "mcp"
    if mcp_dir.exists():
        for json_file in sorted(mcp_dir.glob("*.json")):
            server_name = json_file.stem
            console.print(f"[dim]  注册默认 MCP: {server_name}[/]")
            mcp_add(
                console, server_name,
                config_file=config_file, base_dir=base_dir,
                json_file=json_file, default=True, interactive=False,
            )
            registered_tools.append(server_name)

    # --- tools/cli/ → cli_add ---
    cli_dir = tools_base / "tools" / "cli"
    if cli_dir.exists():
        for json_file in sorted(cli_dir.glob("*.json")):
            tool_name = json_file.stem
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    cli_config = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                console.print(f"[red][ERROR][/] 读取 CLI 配置失败: {json_file.name}: {e}")
                continue

            console.print(f"[dim]  注册默认 CLI: {tool_name}[/]")
            cli_add(
                console, tool_name,
                command=cli_config.get("command", ""),
                args=_json_args_to_str(cli_config.get("args", [])),
                extra_env=list(cli_config.get("env", {}).keys()) if isinstance(cli_config.get("env"), dict) else cli_config.get("env", []),
                timeout=cli_config.get("timeout", 300),
                default=True, base_dir=base_dir, config_file=config_file,
                interactive=False,
            )
            registered_tools.append(tool_name)

    # --- tools/http/ → http_add ---
    http_dir = tools_base / "tools" / "http"
    if http_dir.exists():
        for json_file in sorted(http_dir.glob("*.json")):
            tool_name = json_file.stem
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    http_config = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                console.print(f"[red][ERROR][/] 读取 HTTP 配置失败: {json_file.name}: {e}")
                continue

            console.print(f"[dim]  注册默认 HTTP: {tool_name}[/]")
            http_add(
                console, tool_name,
                url=http_config.get("base_url", ""),
                method=http_config.get("method", "GET"),
                headers=[f"{k}:{v}" for k, v in http_config.get("headers", {}).items()],
                auth=http_config.get("auth_type", "none") or "none",
                extra_env=http_config.get("env", []),
                timeout=http_config.get("timeout", 30),
                default=True, base_dir=base_dir, config_file=config_file,
                interactive=False,
            )
            registered_tools.append(tool_name)

    # --- tools/skills/ → skill_add ---
    skills_dir = tools_base / "tools" / "skills"
    if skills_dir.exists():
        for skill_dir_item in sorted(skills_dir.iterdir()):
            if not skill_dir_item.is_dir():
                continue
            skill_md = skill_dir_item / "SKILL.md"
            if not skill_md.exists():
                continue
            skill_name = skill_dir_item.name

            console.print(f"[dim]  注册默认 Skill: {skill_name}[/]")
            skill_add(
                console, skill_name,
                base_dir=base_dir, config_file=config_file,
                default=True, interactive=False,
            )
            registered_tools.append(skill_name)

    # ==================== 显示结果 ====================
    all_tools = registry.list_all()
    builtin_count = sum(1 for t in all_tools if t.scope == "builtin")
    default_count = sum(1 for t in all_tools if t.scope == "default")
    user_count = len(all_tools) - builtin_count - default_count

    console.print(f"\n[green]工具箱初始化完成 ({len(all_tools)} 个工具):[/]")
    console.print(f"  [cyan]内置工具[/] (builtin): {builtin_count} 个 — 不可删除、不可更新")
    console.print(f"  [green]默认工具[/] (default): {default_count} 个 — 不可删除、可更新")
    console.print(f"  [yellow]用户工具[/] (user): {user_count} 个 — 可删除、可更新")

    for tool in all_tools:
        tool_type = tool.execution.type.value.lower() if tool.execution.type else "unknown"
        scope = tool.scope
        style = {"builtin": "cyan", "default": "green"}.get(scope, "yellow")
        console.print(f"  - [{style}]{tool.name}[/]({tool_type}, {scope})")


def _json_args_to_str(args) -> str | None:
    """将 JSON args 转为逗号分隔字符串（供 cli_add 的 --args 参数）"""
    if not args:
        return None
    if isinstance(args, list):
        return ",".join(str(a) for a in args)
    return str(args)
