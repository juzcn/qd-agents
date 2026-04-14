"""
CLI 主入口
"""
import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.prompt import Prompt

from ..config import load_config
from ..llm import LLMClient
from ..registry import ToolRegistry
from ..prompts import PromptLoader
from ..agent import QDAgent


# 配置日志
logging.basicConfig(
    level="INFO",
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True)],
)

logger = logging.getLogger("qd-agents")

app = typer.Typer(
    name="qd-agents",
    help="从对话到自动化流程的智能体系统",
    no_args_is_help=True,
)

console = Console()


@app.command()
def chat(
    base_dir: Optional[Path] = typer.Option(
        None, "--base-dir", "-d", help="项目根目录"
    ),
    env_file: Optional[Path] = typer.Option(
        None, "--env", "-e", help=".env 文件路径"
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="指定使用的模型"
    ),
):
    """
    启动交互式聊天会话
    """
    asyncio.run(_chat_async(base_dir, env_file, model))


async def _chat_async(
    base_dir: Optional[Path],
    env_file: Optional[Path],
    model: Optional[str],
):
    """异步聊天实现"""
    console.print("[bold blue]qd-agents[/] - 智能体系统", style="bold")
    console.print("输入 'quit' 或 'exit' 退出\n", style="dim")

    # 加载配置
    config = load_config(base_dir=base_dir, env_file=env_file)

    # 确保数据目录存在
    if config.storage:
        config.storage.data_dir.mkdir(parents=True, exist_ok=True)

    # 获取 NVIDIA API Key
    nvidia_config = config.llm.providers.get("nvidia")
    if not nvidia_config or not nvidia_config.api_key:
        console.print("[red]错误: 未找到 NVIDIA_API_KEY[/]")
        console.print("请在 .env 文件中设置 NVIDIA_API_KEY")
        raise typer.Exit(1)

    # 创建 LLM 客户端
    console.print(f"[dim]正在连接 {nvidia_config.base_url}...[/]")

    llm_client = LLMClient(
        api_key=nvidia_config.api_key,
        base_url=nvidia_config.base_url,
    )

    # 创建 Tool Registry
    tool_registry = ToolRegistry(
        db_path=config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    )

    # 创建 Prompt Loader
    prompt_loader = None
    if config.prompts and config.prompts.template_dir.exists():
        prompt_loader = PromptLoader(template_dir=config.prompts.template_dir)

    # 创建 Agent
    agent = QDAgent(
        config=config,
        llm_client=llm_client,
        tool_registry=tool_registry,
        prompt_loader=prompt_loader,
    )

    # 初始化 Agent
    with console.status("[dim]正在初始化 Agent...[/]"):
        await agent.initialize()

    console.print(f"[green]当前模型:[/] {llm_client.current_model}\n")

    # 聊天循环
    while True:
        try:
            user_input = Prompt.ask("[bold cyan]你[/]")

            if user_input.lower() in ["quit", "exit", "q"]:
                console.print("[bold]再见！[/]")
                break

            if not user_input.strip():
                continue

            # 调用 Agent 处理
            with console.status("[bold]思考中...[/]"):
                result = await agent.process(user_input=user_input)

            # 显示回复
            console.print(f"\n[bold green]助手[/]: {result.final_output}\n")
            console.print(f"[dim]耗时: {result.total_duration_ms}ms[/]", style="dim")

        except KeyboardInterrupt:
            console.print("\n[yellow]中断[/]")
            continue
        except Exception as e:
            console.print(f"\n[red]错误: {e}[/]\n")


@app.command()
def list_models(
    base_dir: Optional[Path] = typer.Option(
        None, "--base-dir", "-d", help="项目根目录"
    ),
    env_file: Optional[Path] = typer.Option(
        None, "--env", "-e", help=".env 文件路径"
    ),
):
    """列出可用模型"""
    asyncio.run(_list_models_async(base_dir, env_file))


async def _list_models_async(base_dir: Optional[Path], env_file: Optional[Path]):
    """异步列出模型"""
    config = load_config(base_dir=base_dir, env_file=env_file)

    nvidia_config = config.llm.providers.get("nvidia")
    if not nvidia_config or not nvidia_config.api_key:
        console.print("[red]错误: 未找到 NVIDIA_API_KEY[/]")
        raise typer.Exit(1)

    console.print(f"连接到: {nvidia_config.base_url}")
    console.print("正在获取模型列表...\n")

    async with LLMClient(
        api_key=nvidia_config.api_key,
        base_url=nvidia_config.base_url,
    ) as llm_client:
        try:
            models_response = await llm_client._client.models.list()

            console.print("[bold]可用模型:[/]\n")
            for m in models_response.data:
                console.print(f"  - [cyan]{m.id}[/]")
                if hasattr(m, "created") and m.created:
                    console.print(f"    创建时间: {m.created}", style="dim")

        except Exception as e:
            console.print(f"[red]获取模型列表失败: {e}[/]")
            raise typer.Exit(1)


@app.command()
def list_tools(
    base_dir: Optional[Path] = typer.Option(
        None, "--base-dir", "-d", help="项目根目录"
    ),
    env_file: Optional[Path] = typer.Option(
        None, "--env", "-e", help=".env 文件路径"
    ),
):
    """列出已注册的工具"""
    config = load_config(base_dir=base_dir, env_file=env_file)

    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)

    tools = registry.list_all()

    console.print(f"[bold]已注册工具 ({len(tools)} 个):[/]\n")
    for tool in tools:
        console.print(f"  - [cyan]{tool.name}[/] ({tool.id})")
        console.print(f"    描述: {tool.description}", style="dim")
        console.print(f"    分类: {tool.metadata.category}", style="dim")
        console.print()


@app.command()
def version():
    """显示版本信息"""
    from .. import __version__
    console.print(f"qd-agents 版本: [bold]{__version__}[/]")


def main():
    """CLI 主函数"""
    app()


if __name__ == "__main__":
    main()
