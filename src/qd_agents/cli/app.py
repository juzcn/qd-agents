"""
CLI 应用配置

负责 Typer 应用配置和命令注册。
"""

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from .commands import chat_async, list_models_async, list_tools, init_tools, show_version, mcp_add, mcp_list, mcp_remove


# 创建 Typer 应用实例
app = typer.Typer(
    name="qd-agents",
    help="从对话到自动化流程的智能体系统",
    no_args_is_help=False,
    add_completion=False,
)

# 创建 tools 子命令组
tools_app = typer.Typer(
    name="tools",
    help="工具管理命令",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()

# 将 tools 子命令组添加到主应用
app.add_typer(tools_app)

# 创建 mcp 子命令组
mcp_app = typer.Typer(
    name="mcp",
    help="MCP 服务器管理命令",
    no_args_is_help=True,
    add_completion=False,
)

# 将 mcp 子命令组添加到 tools 子命令组
tools_app.add_typer(mcp_app)

# tools list 命令
@tools_app.command("list", help="列出已注册的工具")
def tools_list(
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
):
    """列出已注册的工具"""
    list_tools(console, base_dir, config_file)

# tools init 命令
@tools_app.command("init", help="初始化内置工具")
def tools_init(
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
):
    """初始化内置工具"""
    init_tools(console, base_dir, config_file)




# mcp add 命令
@mcp_app.command("add", help="添加 MCP 服务器")
def mcp_add_command(
    name: str = typer.Argument(..., help="工具名称（必需）"),
    server: str = typer.Argument(..., help="MCP服务器标识（必需）"),
    transport: str = typer.Option("stdio", "--transport", "-t", help="传输模式: stdio, sse, streamable-http"),
    command: Optional[str] = typer.Option(None, "--command", "--cmd", help="stdio 模式下的命令"),
    args: Optional[str] = typer.Option(None, "--args", "-a", help="stdio 模式下的参数 (JSON 数组或逗号分隔)"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="SSE 或 streamable-http 模式的 URL"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
    json: bool = typer.Option(False, "--json", "-j", help="从同名JSON文件读取配置（tools/mcp/<name>.json）"),
):
    """添加 MCP 服务器"""
    # 验证必需参数（防止空字符串）
    if not name or not name.strip():
        console.print("[red][ERROR][/] 工具名称不能为空")
        return
    if not server or not server.strip():
        console.print("[red][ERROR][/] MCP服务器标识不能为空")
        return

    # 如果指定了 --json，构建JSON文件路径
    json_file: Optional[Path] = None
    if json:
        if base_dir:
            json_file = base_dir / "tools" / "mcp" / f"{name}.json"
        else:
            json_file = Path("tools") / "mcp" / f"{name}.json"

    mcp_add(console, name, server, transport, command, args, url, config_file, base_dir, json_file)


# mcp list 命令
@mcp_app.command("list", help="列出已注册的 MCP 服务器")
def mcp_list_command(
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
):
    """列出已注册的 MCP 服务器"""
    mcp_list(console, config_file, base_dir)


# mcp remove 命令
@mcp_app.command("remove", help="移除 MCP 服务器")
def mcp_remove_command(
    name: str = typer.Argument(..., help="工具名称"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
):
    """移除 MCP 服务器"""
    mcp_remove(console, name, config_file, base_dir)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    list_models: bool = typer.Option(False, "--list-models", help="列出可用模型"),
    version: bool = typer.Option(False, "--version", help="显示版本信息"),
    agent: Optional[str] = typer.Option(None, "--agent", "-a", help="指定 Agent: tool-use"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
):
    """
    qd-agents - 从对话到自动化流程的智能体系统

    默认启动交互式聊天会话。使用选项执行其他操作。
    多个选项可以同时使用。
    """
    if ctx.invoked_subcommand is not None:
        return

    # 收集所有为 True 的选项
    options_executed = False

    # 按特定顺序执行所有为 True 的选项
    # 1. 版本信息（通常最先显示）
    if version:
        show_version(console)
        options_executed = True

    # 2. 列出模型
    if list_models:
        if options_executed:
            print()  # 添加空行分隔不同选项的输出
        asyncio.run(list_models_async(console, base_dir, config_file, None))
        options_executed = True


    # 如果没有任何选项被指定，执行默认操作（聊天）
    if not options_executed:
        asyncio.run(chat_async(console, base_dir, config_file, None, None, agent))