"""
CLI 应用配置

负责 Typer 应用配置和命令注册。
"""

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from .commands import chat_async, list_models_async, list_tools, init_tools, show_version, mcp_add, mcp_list, mcp_remove, skill2mcp
from qd_agents.config import AgentMode


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


# tools skill2mcp 命令
@tools_app.command("skill2mcp", help="将技能转换为 MCP 工具")
def tools_skill2mcp(
    skill_name: str = typer.Argument(..., help="技能名称（在 tools/skills/ 目录下）"),
    output_dir: Optional[Path] = typer.Option(None, "--output", "-o", help="输出目录（默认为 tools/mcp/<技能名>）"),
    register: bool = typer.Option(False, "--register", "-r", help="注册到工具注册表"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
):
    """将技能转换为 MCP 工具"""
    # 构建技能路径
    if base_dir:
        skill_path = base_dir / "tools" / "skills" / skill_name
    else:
        skill_path = Path.cwd() / "tools" / "skills" / skill_name

    # 检查路径是否存在
    if not skill_path.exists():
        console.print(f"[red][ERROR][/] 技能路径不存在: {skill_path}")
        console.print(f"[dim]请确认技能 '{skill_name}' 在 tools/skills/ 目录下[/]")
        return

    # 如果未指定输出目录，默认使用 tools/mcp/<技能名>
    if output_dir is None:
        output_dir = Path("tools") / "mcp" / skill_name

    skill2mcp(console, skill_path, output_dir, register, config_file, base_dir)


# mcp add 命令
@mcp_app.command("add", help="添加 MCP 服务器")
def mcp_add_command(
    name: Optional[str] = typer.Argument(None, help="工具名称（如果未提供--json则为必需）"),
    server: Optional[str] = typer.Argument(None, help="MCP服务器标识（如果未提供--json则为必需）"),
    transport: str = typer.Option("stdio", "--transport", "-t", help="传输模式: stdio, sse, streamable-http"),
    command: Optional[str] = typer.Option(None, "--command", "--cmd", help="stdio 模式下的命令"),
    args: Optional[str] = typer.Option(None, "--args", "-a", help="stdio 模式下的参数 (JSON 数组或逗号分隔)"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="SSE 或 streamable-http 模式的 URL"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
    json_file: Optional[Path] = typer.Option(None, "--json", "-j", help="JSON配置文件路径（如果提供，将从文件读取配置）"),
):
    """添加 MCP 服务器"""
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
    mode: AgentMode = typer.Option(AgentMode.TOOL_USE, "--mode", help="智能体工作模式: tool-use, code-plan"),
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
        asyncio.run(chat_async(console, base_dir, config_file, None, None, mode))