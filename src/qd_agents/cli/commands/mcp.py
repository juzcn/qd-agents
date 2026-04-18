"""
MCP 服务器管理命令

负责注册和管理 MCP (Model Context Protocol) 服务器。
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

import typer
from rich.console import Console
from rich.table import Table
from rich.syntax import Syntax

from qd_agents.config import load_config
from qd_agents.registry import ToolRegistry, Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType
from qd_agents.tools.executors import create_mcp_tool


logger = logging.getLogger(__name__)


async def mcp_add_async(
    console: Console,
    name: str,
    server: str,
    transport: str = "stdio",
    command: Optional[str] = None,
    args: Optional[str] = None,
    url: Optional[str] = None,
    config_file: Optional[Path] = None,
    base_dir: Optional[Path] = None,
) -> None:
    """
    添加 MCP 服务器

    Args:
        console: Rich 控制台对象
        name: 工具名称
        server: MCP服务器标识
        transport: 传输模式 ("stdio", "sse", "streamable-http")
        command: stdio 模式下的命令
        args: stdio 模式下的参数（JSON 字符串或逗号分隔）
        url: SSE 或 streamable-http 模式的 URL
        config_file: 配置文件路径
        base_dir: 基础目录
    """
    config = load_config(base_dir=base_dir, config_file=config_file)

    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)

    # 解析 args 参数
    parsed_args: list[str] = []
    if args:
        try:
            # 尝试解析为 JSON 数组
            parsed_args = json.loads(args)
            if not isinstance(parsed_args, list):
                raise ValueError("args must be a JSON array")
        except json.JSONDecodeError:
            # 如果不是 JSON，尝试按逗号分割
            parsed_args = [arg.strip() for arg in args.split(",") if arg.strip()]

    # 创建 MCP 工具
    tool = create_mcp_tool(
        name=name,
        description=f"MCP server: {server}",
        server=server,
        transport=transport,
        command=command,
        args=parsed_args,
        url=url,
        parameters={
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "要执行的 MCP 工具名称"},
                "arguments": {"type": "object", "description": "工具参数", "additionalProperties": True},
            },
            "required": ["tool_name", "arguments"],
        },
    )

    # 注册工具
    tool_id = registry.register(tool)

    console.print(f"[green][OK][/] 已注册 MCP 服务器: {name} ({tool_id})")
    console.print(f"  服务器: {server}")
    console.print(f"  传输模式: {transport}")
    if command:
        console.print(f"  命令: {command}")
    if parsed_args:
        console.print(f"  参数: {parsed_args}")
    if url:
        console.print(f"  URL: {url}")


def mcp_add(
    console: Console,
    name: str = typer.Argument(..., help="工具名称"),
    server: str = typer.Argument(..., help="MCP服务器标识"),
    transport: str = typer.Option("stdio", "--transport", "-t", help="传输模式: stdio, sse, streamable-http"),
    command: Optional[str] = typer.Option(None, "--command", "-c", help="stdio 模式下的命令"),
    args: Optional[str] = typer.Option(None, "--args", "-a", help="stdio 模式下的参数 (JSON 数组或逗号分隔)"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="SSE 或 streamable-http 模式的 URL"),
    config_file: Optional[Path] = typer.Option(None, "--config", help="配置文件路径"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
) -> None:
    """添加 MCP 服务器"""
    asyncio.run(mcp_add_async(
        console=console,
        name=name,
        server=server,
        transport=transport,
        command=command,
        args=args,
        url=url,
        config_file=config_file,
        base_dir=base_dir,
    ))


async def mcp_list_async(
    console: Console,
    config_file: Optional[Path] = None,
    base_dir: Optional[Path] = None,
) -> None:
    """
    列出已注册的 MCP 服务器

    Args:
        console: Rich 控制台对象
        config_file: 配置文件路径
        base_dir: 基础目录
    """
    config = load_config(base_dir=base_dir, config_file=config_file)

    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)

    # 获取所有工具并筛选 MCP 类型
    all_tools = registry.list_all()
    mcp_tools = [tool for tool in all_tools if tool.execution.type == ToolExecutionType.MCP]

    if not mcp_tools:
        console.print("[yellow][WARN][/] 未找到已注册的 MCP 服务器")
        return

    # 创建表格
    table = Table(title=f"已注册 MCP 服务器 ({len(mcp_tools)} 个)")
    table.add_column("名称", style="cyan")
    table.add_column("服务器", style="green")
    table.add_column("传输模式", style="magenta")
    table.add_column("命令/URL", style="dim")
    table.add_column("ID", style="dim")

    for tool in mcp_tools:
        exec_config = tool.execution
        command_or_url = exec_config.command or exec_config.endpoint or "N/A"
        table.add_row(
            tool.name,
            exec_config.server or "N/A",
            exec_config.transport or "stdio",
            command_or_url,
            tool.id,
        )

    console.print(table)


def mcp_list(
    console: Console,
    config_file: Optional[Path] = typer.Option(None, "--config", help="配置文件路径"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
) -> None:
    """列出已注册的 MCP 服务器"""
    asyncio.run(mcp_list_async(console, config_file, base_dir))


async def mcp_remove_async(
    console: Console,
    name: str,
    config_file: Optional[Path] = None,
    base_dir: Optional[Path] = None,
) -> None:
    """
    移除 MCP 服务器

    Args:
        console: Rich 控制台对象
        name: 工具名称
        config_file: 配置文件路径
        base_dir: 基础目录
    """
    config = load_config(base_dir=base_dir, config_file=config_file)

    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)

    # 查找工具
    tool = registry.get_by_name(name)
    if not tool or tool.execution.type != ToolExecutionType.MCP:
        console.print(f"[red][ERROR][/] 未找到 MCP 服务器: {name}")
        return

    # 删除工具
    success = registry.delete(tool.id)
    if success:
        console.print(f"[green][OK][/] 已移除 MCP 服务器: {name}")
    else:
        console.print(f"[red][ERROR][/] 移除 MCP 服务器失败: {name}")


def mcp_remove(
    console: Console,
    name: str = typer.Argument(..., help="工具名称"),
    config_file: Optional[Path] = typer.Option(None, "--config", help="配置文件路径"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
) -> None:
    """移除 MCP 服务器"""
    asyncio.run(mcp_remove_async(console, name, config_file, base_dir))