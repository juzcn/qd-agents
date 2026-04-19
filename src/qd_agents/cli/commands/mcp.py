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
    json_file: Optional[Path] = None,
) -> None:
    """
    添加 MCP 服务器

    Args:
        console: Rich 控制台对象
        name: 工具名称（必需）
        server: MCP服务器标识（必需）
        transport: 传输模式 ("stdio", "sse", "streamable-http")
        command: stdio 模式下的命令
        args: stdio 模式下的参数（JSON 字符串或逗号分隔）
        url: SSE 或 streamable-http 模式的 URL
        config_file: 配置文件路径
        base_dir: 基础目录
        json_file: JSON配置文件路径（如果提供，将从文件读取配置）
    """
    # 如果提供了 JSON 文件，从中读取配置
    json_config = {}
    if json_file:
        if not json_file.exists():
            console.print(f"[red][ERROR][/] JSON 文件不存在: {json_file}")
            return
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                json_config = json.load(f)
        except json.JSONDecodeError as e:
            console.print(f"[red][ERROR][/] JSON 文件解析失败: {e}")
            return
        except Exception as e:
            console.print(f"[red][ERROR][/] 读取 JSON 文件失败: {e}")
            return

    # 从 JSON 配置中提取 MCP 服务器配置
    extracted_config = {}
    if json_config:
        # 尝试多种 MCP 服务器配置格式
        servers = None
        server_key = server  # 使用命令行提供的 server 参数作为键

        # 格式1: {"mcp": {"servers": {"server_name": {...}}}}
        if "mcp" in json_config and "servers" in json_config["mcp"]:
            servers = json_config["mcp"]["servers"]
        # 格式2: {"mcpServers": {"server_name": {...}}}
        elif "mcpServers" in json_config:
            servers = json_config["mcpServers"]

        if servers:
            # 使用 server 参数作为键查找配置
            if server_key and server_key in servers:
                extracted_config = servers[server_key]
            elif servers:
                # 如果没有匹配的 server，使用第一个服务器
                first_server = next(iter(servers))
                extracted_config = servers[first_server]
        else:
            # 扁平配置格式
            extracted_config = json_config

    # 合并参数：JSON 配置作为默认值，命令行参数优先级更高
    final_name = name if name is not None else extracted_config.get("name")
    final_server = server if server is not None else extracted_config.get("server")
    final_transport = transport if transport != "stdio" or "transport" not in extracted_config else extracted_config.get("transport", "stdio")
    final_command = command if command is not None else extracted_config.get("command")
    final_args = args if args is not None else extracted_config.get("args")
    final_url = url if url is not None else extracted_config.get("url")
    final_env = extracted_config.get("env")

    # 验证必需参数
    if not final_name:
        console.print("[red][ERROR][/] 工具名称不能为空")
        return
    if not final_server:
        console.print("[red][ERROR][/] MCP服务器标识不能为空")
        return

    # 如果 args 是列表（来自 JSON），将其转换为 JSON 字符串以便后续解析
    if final_args and isinstance(final_args, list):
        final_args = json.dumps(final_args)

    config = load_config(base_dir=base_dir, config_file=config_file)

    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)

    # 解析 args 参数
    parsed_args: list[str] = []
    if final_args:
        # 首先去除可能的额外引号或空格
        args_str = final_args.strip()

        # 调试：输出接收到的args值
        logger.debug(f"Received args string: {repr(args_str)}")

        # 尝试多种解析方式
        try:
            # 方法1：尝试直接解析为JSON数组
            parsed_args = json.loads(args_str)
            if not isinstance(parsed_args, list):
                raise ValueError("args must be a JSON array")
            logger.debug(f"Successfully parsed as JSON array: {parsed_args}")
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug(f"Failed to parse as JSON: {e}")

            # 方法2：如果看起来像数组（以 [ 开头，以 ] 结尾）但不是有效JSON
            if args_str.startswith('[') and args_str.endswith(']'):
                # 移除方括号并尝试解析内容
                inner = args_str[1:-1].strip()
                logger.debug(f"Extracted inner content: {repr(inner)}")

                if inner:
                    # 尝试解析内部内容
                    try:
                        # 如果内部是JSON字符串（可能有引号）
                        if (inner.startswith('"') and inner.endswith('"')) or \
                           (inner.startswith("'") and inner.endswith("'")):
                            # 去除引号
                            inner = inner[1:-1]
                            parsed_args = [inner]
                        else:
                            # 尝试按逗号分割
                            parsed_args = [item.strip() for item in inner.split(",") if item.strip()]
                    except Exception:
                        # 如果所有方法都失败，将整个内容作为单个参数
                        parsed_args = [inner]
            else:
                # 方法3：尝试按逗号分割
                parsed_args = [arg.strip() for arg in args_str.split(",") if arg.strip()]

        # 如果解析后结果为空，但原始args非空，将其作为单个参数
        if not parsed_args and args_str:
            parsed_args = [args_str]

        logger.debug(f"Final parsed args: {parsed_args}")

    # 创建 MCP 工具
    tool = create_mcp_tool(
        name=final_name,
        description=f"MCP server: {final_server}",
        server=final_server,
        transport=final_transport,
        command=final_command,
        args=parsed_args,
        url=final_url,
        env=final_env,
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

    console.print(f"[green][OK][/] 已注册 MCP 服务器: {final_name} ({tool_id})")
    console.print(f"  服务器: {final_server}")
    console.print(f"  传输模式: {final_transport}")
    if final_command:
        console.print(f"  命令: {final_command}")
    if parsed_args:
        console.print(f"  参数: {parsed_args}")
    if final_url:
        console.print(f"  URL: {final_url}")


def mcp_add(
    console: Console,
    name: str = typer.Argument(..., help="工具名称（必需）"),
    server: str = typer.Argument(..., help="MCP服务器标识（必需）"),
    transport: str = typer.Option("stdio", "--transport", "-t", help="传输模式: stdio, sse, streamable-http"),
    command: Optional[str] = typer.Option(None, "--command", "-c", help="stdio 模式下的命令"),
    args: Optional[str] = typer.Option(None, "--args", "-a", help="stdio 模式下的参数 (JSON 数组或逗号分隔)"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="SSE 或 streamable-http 模式的 URL"),
    config_file: Optional[Path] = typer.Option(None, "--config", help="配置文件路径"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
    json_file: Optional[Path] = typer.Option(None, "--json", "-j", help="JSON配置文件路径（如果提供，将从文件读取配置）"),
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
        json_file=json_file,
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