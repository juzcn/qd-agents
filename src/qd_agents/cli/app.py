"""
CLI 应用配置

负责 Typer 应用配置和命令注册。
"""

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from .commands import chat_async, list_models_async, list_tools, init_tools, show_version
from qd_agents.config import AgentMode


# 创建 Typer 应用实例
app = typer.Typer(
    name="qd-agents",
    help="从对话到自动化流程的智能体系统",
    no_args_is_help=False,
    add_completion=False,
)

console = Console()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    list_models: bool = typer.Option(False, "--list-models", help="列出可用模型"),
    list_tools_option: bool = typer.Option(False, "--list-tools", help="列出已注册的工具"),
    init_tools_option: bool = typer.Option(False, "--init-tools", help="初始化内置工具"),
    version: bool = typer.Option(False, "--version", help="显示版本信息"),
    mode: AgentMode = typer.Option(AgentMode.TOOL_USE, "--mode", help="智能体工作模式: tool-use, code-plan"),
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
        asyncio.run(list_models_async(console, None, None, None))
        options_executed = True

    # 3. 列出工具
    if list_tools_option:
        if options_executed:
            print()  # 添加空行分隔不同选项的输出
        list_tools(console, None, None)
        options_executed = True

    # 4. 初始化工具
    if init_tools_option:
        if options_executed:
            print()  # 添加空行分隔不同选项的输出
        init_tools(console, None, None)
        options_executed = True

    # 如果没有任何选项被指定，执行默认操作（聊天）
    if not options_executed:
        asyncio.run(chat_async(console, None, None, None, None, mode))