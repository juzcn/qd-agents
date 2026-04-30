"""工具列表命令"""

import asyncio
from pathlib import Path
from typing import Optional, List

from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from qd_agents.config import load_config
from qd_agents.cli.utils.registry import get_tool_registry
from qd_agents.models.tool import ToolExecutionType
from qd_agents.services.mcp_service import MCPService


def list_tools(
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
    type_filter: Optional[List[str]] = None,
    skill_detail: bool = False,
    mcp_detail: bool = False,
) -> None:
    """
    列出工具

    Args:
        console: Rich 控制台对象
        base_dir: 基础目录
        config_file: 配置文件路径
        type_filter: 工具类型过滤列表（如 ["mcp", "skill", "function"]）
        skill_detail: 是否显示 Skill 工具详细属性
        mcp_detail: 是否显示 MCP 工具详细属性及 subtools
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

    # Skill 详细模式
    if skill_detail:
        _list_skill_detail(console, tools)
        return

    # MCP 详细模式
    if mcp_detail:
        asyncio.run(_list_mcp_detail(console, tools))
        return

    # 通用列表模式
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


def _list_skill_detail(console: Console, tools: list) -> None:
    """列出 Skill 工具详细属性"""
    skill_tools = [t for t in tools if t.execution.type == ToolExecutionType.SKILL]
    if not skill_tools:
        console.print("[yellow]未找到已注册的 Skill 工具[/]")
        return

    table = Table(title=f"Skill 工具 ({len(skill_tools)} 个)")
    table.add_column("名称", style="cyan")
    table.add_column("描述", style="dim", max_width=30)
    table.add_column("skill_type", style="green")
    table.add_column("tool_deps", style="magenta")
    table.add_column("env", style="yellow")
    table.add_column("scope", style="blue")
    table.add_column("local_path", style="dim")

    for tool in skill_tools:
        deps = tool.dependencies or {}
        skill_type = deps.get("skill_type", "-")
        tool_deps = ", ".join(deps.get("tool_deps", [])) or "-"
        env_keys = ", ".join(tool.execution.env.keys()) or "-"
        table.add_row(
            tool.name,
            tool.description,
            skill_type,
            tool_deps,
            env_keys,
            tool.scope,
            tool.local_path or tool.source_path or "-",
        )

    console.print(table)


async def _list_mcp_detail(console: Console, tools: list) -> None:
    """列出 MCP 工具详细属性及 subtools（需连接 MCP 服务器获取 subtools）"""
    mcp_tools = [t for t in tools if t.execution.type == ToolExecutionType.MCP]
    if not mcp_tools:
        console.print("[yellow]未找到已注册的 MCP 工具[/]")
        return

    # 通过 MCPService 连接服务器获取 subtools
    mcp_service = MCPService()
    server_configs = mcp_service._build_server_configs(mcp_tools)

    # 并行连接所有 MCP 服务器
    subtools_map: dict[str, list] = {}
    if server_configs:
        tasks = [
            _fetch_mcp_subtools(mcp_service, server_key, config)
            for server_key, config in server_configs.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (server_key, _), result in zip(server_configs.items(), results):
            if isinstance(result, Exception):
                subtools_map[server_key] = []
            else:
                subtools_map[server_key] = result

    await mcp_service.close()

    tree = Tree(f"MCP 工具 ({len(mcp_tools)} 个 server)")

    for tool in mcp_tools:
        cmd = tool.execution.command or "-"
        args = " ".join(tool.execution.args) if tool.execution.args else ""
        transport = tool.execution.transport or "stdio"
        server_key = tool.execution.server or tool.name

        # Server 节点
        server_label = f"[cyan]{tool.name}[/] [dim]({transport})[/]"
        detail_parts = [f"cmd: [green]{cmd}[/]"]
        if args:
            detail_parts.append(f"args: [green]{args}[/]")
        if tool.execution.env:
            env_keys = ", ".join(tool.execution.env.keys())
            detail_parts.append(f"env: [yellow]{env_keys}[/]")
        server_node = tree.add(f"{server_label} — {', '.join(detail_parts)}")

        # Subtools
        subtools = subtools_map.get(server_key, [])
        if subtools:
            for st in subtools:
                st_desc = st.description or ""
                st_desc_short = st_desc[:50] + "..." if len(st_desc) > 50 else st_desc
                server_node.add(f"[magenta]{st.name}[/]: {st_desc_short}")
        else:
            server_node.add("[dim]未能加载 subtools[/]")

    console.print(tree)


async def _fetch_mcp_subtools(mcp_service: MCPService, server_key: str, config: dict) -> list:
    """获取单个 MCP 服务器的 subtools"""
    try:
        subtools, _ = await mcp_service.get_server_tools(config)
        return subtools
    except Exception:
        return []
