"""
CLI 应用配置

负责 Typer 应用配置和命令注册。
"""

import asyncio
from pathlib import Path
from typing import Optional, List

import typer
from rich.console import Console

from .commands import chat_async, list_models_async, show_version
from .commands.cli import cli_app
from .commands.http import http_app
from .commands.mcp import mcp_app
from .commands.skills import skill_app
from .commands.memory import memory_app
from .commands.tools import list_tools, init_tools, remove_tools, update_check, update_tools


# 主应用
app = typer.Typer(
    name="qd-agents",
    help="从对话到自动化流程的智能体系统",
    no_args_is_help=False,
    add_completion=False,
)

# tools 子命令组
tools_app = typer.Typer(
    name="tools",
    help="工具管理命令",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()

# 注册子命令组
app.add_typer(tools_app)
tools_app.add_typer(mcp_app)
tools_app.add_typer(skill_app)
tools_app.add_typer(cli_app)
tools_app.add_typer(http_app)
app.add_typer(memory_app)


# tools list 命令
@tools_app.command("list", help="列出已注册的工具")
def tools_list(
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
    mcp: bool = typer.Option(False, "--mcp", "-m", help="列出 MCP 工具及 subtools"),
    skill: bool = typer.Option(False, "--skill", "-s", help="列出 Skill 工具及重要属性"),
    cli: bool = typer.Option(False, "--cli", help="只列出 CLI 工具"),
    function: bool = typer.Option(False, "--function", help="只列出 Function 工具"),
    bash: bool = typer.Option(False, "--bash", help="只列出 Bash 工具"),
    http: bool = typer.Option(False, "--http", help="只列出 HTTP 工具"),
):
    """列出已注册的工具"""
    type_filter = []
    if mcp:
        type_filter.append("mcp")
    if skill:
        type_filter.append("skill")
    if cli:
        type_filter.append("cli")
    if function:
        type_filter.append("function")
    if bash:
        type_filter.append("bash")
    if http:
        type_filter.append("http")
    list_tools(console, base_dir, config_file, type_filter=type_filter or None, skill_detail=skill, mcp_detail=mcp)


# tools init 命令
@tools_app.command("init", help="初始化工具箱（默认完全初始化，--keep 保留用户工具）")
def tools_init(
    keep: bool = typer.Option(False, "--keep", "-k", help="保留用户创建的工具（默认完全初始化）"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
):
    """初始化工具箱"""
    init_tools(console, base_dir, config_file, keep_user=keep)


# tools remove 命令
@tools_app.command("remove", help="移除已注册的工具")
def tools_remove_command(
    tool_identifier: str = typer.Argument(..., help="工具名称或 ID"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
    keep_credentials: bool = typer.Option(False, "--keep-credentials", help="保留工具凭证配置"),
):
    """移除已注册的工具（支持所有类型）"""
    remove_tools(console, tool_identifier, base_dir, config_file, keep_credentials)


# tools update-check 命令
@tools_app.command("update-check", help="检查默认 MCP 工具版本更新")
def tools_update_check(
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
):
    """检查默认 MCP 工具版本更新"""
    update_check(console, base_dir, config_file)


# tools update 命令
@tools_app.command("update", help="更新默认 MCP 工具到最新版本")
def tools_update(
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
):
    """更新默认 MCP 工具到最新版本"""
    update_tools(console, base_dir, config_file)


# 主回调（默认启动聊天）
@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    list_models: bool = typer.Option(False, "--list-models", help="列出可用模型"),
    version: bool = typer.Option(False, "--version", help="显示版本信息"),
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

    options_executed = False

    if version:
        show_version(console)
        options_executed = True

    if list_models:
        if options_executed:
            print()
        asyncio.run(list_models_async(console, base_dir, config_file, None))
        options_executed = True

    if not options_executed:
        asyncio.run(chat_async(console, base_dir, config_file, None, None))
