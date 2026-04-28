"""
CLI 应用配置

负责 Typer 应用配置和命令注册。
"""

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from .commands import chat_async, list_models_async, list_tools, init_tools, add_tool_from_url, remove_tools, update_check, update_tools, show_version, mcp_add, skill_add, recall_memories, recall_memory


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

# 创建 skill 子命令组
skill_app = typer.Typer(
    name="skill",
    help="Skill 工具管理命令",
    no_args_is_help=True,
    add_completion=False,
)

# 将 skill 子命令组添加到 tools 子命令组
tools_app.add_typer(skill_app)

# 创建 memory 子命令组
memory_app = typer.Typer(
    name="memory",
    help="长期记忆管理命令",
    no_args_is_help=True,
    add_completion=False,
)

# 将 memory 子命令组添加到主应用
app.add_typer(memory_app)

# tools list 命令
@tools_app.command("list", help="列出已注册的工具")
def tools_list(
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
    mcp: bool = typer.Option(False, "--mcp", help="只列出 MCP 工具"),
    skill: bool = typer.Option(False, "--skill", help="只列出 Skill 工具"),
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
    if function:
        type_filter.append("function")
    if bash:
        type_filter.append("bash")
    if http:
        type_filter.append("http")
    list_tools(console, base_dir, config_file, type_filter=type_filter or None)

# tools init 命令
@tools_app.command("init", help="初始化内置工具")
def tools_init(
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
):
    """初始化内置工具"""
    init_tools(console, base_dir, config_file)


# tools add 命令（从 URL 自动安装工具）
@tools_app.command("add", help="从 URL 自动安装工具（支持 pypi/mcp/skill/skillset）")
def tools_add_command(
    url: str = typer.Argument(..., help="工具 URL（pypi/MCP/skill/skillset）"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
):
    """从 URL 自动安装工具"""
    add_tool_from_url(console, url, base_dir, config_file)


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


# skill add 命令
@skill_app.command("add", help="添加 Skill 工具")
def skill_add_command(
    skill_name: str = typer.Argument(..., help="Skill 名称（tools/skills/ 下的文件夹名）"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
):
    """添加 Skill 工具"""
    if not skill_name or not skill_name.strip():
        console.print("[red][ERROR][/] Skill 名称不能为空")
        return
    skill_add(console, skill_name, base_dir, config_file)


# memory list 命令
@memory_app.command("list", help="显示所有永久记忆")
def memory_list(
    asc: bool = typer.Option(False, "--asc", help="按时间正序输出（默认倒序）"),
    interval: Optional[str] = typer.Option(None, "--interval", "-i", help="时间区间（如 1d, 3h, today, 04-25~04-27）"),
    session: Optional[str] = typer.Option(None, "--session", "-s", help="按 session ID 筛选"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
):
    """显示所有永久记忆"""
    recall_memories(console, base_dir, config_file, asc=asc, interval=interval, session=session)


# memory recall 命令
@memory_app.command("recall", help="语义召回永久记忆")
def memory_recall(
    query: str = typer.Argument(..., help="查询语句"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
):
    """语义召回永久记忆"""
    recall_memory(console, query, base_dir, config_file)


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
        asyncio.run(chat_async(console, base_dir, config_file, None, None))