"""qd-agents evolve — 启动自主进化 Agent 会话

Evolve Agent 拥有独立的工具箱，从最小能力起步，通过交互逐渐成长。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

from ...agent.evolve import EvolveAgent, EvolveContextManager
from ...config import load_config
from ...llm import LLMClient
from ...registry import ToolRegistry
from ...tools import ToolExecutorRegistry
from ...tools.builtin_register import (
    tool_register_cli,
    tool_register_mcp,
    tool_register_skill,
    tool_register_http,
    register_builtin_function_tools,
)
from ...tools.builtins import echo
from ...tools.search import serper_search, tavily_search

logger = logging.getLogger(__name__)
console = Console()


def evolve_cmd(
    provider: str = typer.Option(None, "--provider", "-p", help="LLM 提供商"),
    model: str = typer.Option(None, "--model", "-m", help="模型名称"),
    config_path: str = typer.Option(None, "--config", "-c", help="配置文件路径"),
) -> None:
    """启动自主进化 Agent 会话"""
    asyncio.run(_evolve_async(provider, model, config_path))


async def _evolve_async(
    provider: str | None,
    model: str | None,
    config_path: str | None,
) -> None:
    """Evolve 交互式会话主逻辑"""
    # 1. 加载配置
    config = load_config(config_path)

    # 2. 初始化 LLM
    provider_name = provider or config.llm.default_provider
    provider_config = config.llm.providers.get(provider_name)
    if not provider_config or not provider_config.api_key:
        console.print(f"[red]错误: 未找到 {provider_name} 的 API key[/red]")
        raise typer.Exit(1)

    model_names = provider_config.get_model_names()
    if model:
        model_names = [model]

    llm = LLMClient(
        api_key=provider_config.api_key,
        base_url=provider_config.base_url,
        model_names=model_names if model_names else None,
    )

    # 发现模型
    if provider_config.auto_discover and not model_names:
        with console.status("正在发现可用模型..."):
            await llm.discover_models()
    elif not model_names:
        with console.status("加载默认模型列表..."):
            await llm.discover_models(top_k=0)

    console.print(f"[green]当前模型:[/green] {provider_name}/{llm.current_model}")

    # 3. 初始化独立的 Registry（Evolve 有自己的工具箱，独立 db）
    evolve_db_path = Path("data/evolve_tools.db")
    registry = ToolRegistry(evolve_db_path)

    # 4. 初始化独立的 Executor Registry + 注册最小工具集
    executor_registry = ToolExecutorRegistry()
    _register_minimal_tools(executor_registry, registry)

    # 5. 初始化 Memory（可选）
    memory_service = _init_memory_service(config)

    # 6. 初始化 Context Manager
    ctx_manager = EvolveContextManager(
        llm=llm,
        max_context_tokens=config.execution.evolve_context_window,
        compact_threshold=config.execution.evolve_compact_threshold,
        recent_turns=config.execution.evolve_recent_turns,
    )

    # 7. 创建 EvolveAgent
    agent = EvolveAgent(
        llm=llm,
        registry=registry,
        executor_registry=executor_registry,
        context_manager=ctx_manager,
        memory_service=memory_service,
        max_iterations=config.execution.max_evolve_iterations,
    )

    # 8. 欢迎信息
    tools_count = len(registry.list_all())
    console.print(Panel(
        f"[bold green]Evolve Agent[/bold green] — 自主进化 Agent\n"
        f"初始工具数: {tools_count} | Agent 将通过注册新工具自主成长\n"
        "输入任务，Agent 将自主决策并执行。输入 /quit 退出。",
        title="qd-agents evolve",
    ))

    # 9. 交互循环
    history: list[dict] = []

    while True:
        try:
            user_input = console.input("[bold cyan]You>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见！[/dim]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("/quit", "/exit", "/q"):
            console.print("[dim]再见！[/dim]")
            break

        # 步骤回调
        def on_step(step: dict) -> None:
            iteration = step.get("iteration", "?")
            max_iter = step.get("max_iterations", "?")
            event = step.get("event", "")
            tool_name = step.get("tool_name", "")
            prefix = f"[dim][{iteration}/{max_iter}][/dim]"

            if event == "tool_call":
                if tool_name == "execute_bash":
                    cmd = step.get("command", "")
                    cmd_display = cmd if len(cmd) <= 80 else cmd[:77] + "..."
                    console.print(f"  {prefix} [yellow]⚡ bash:[/yellow] {cmd_display}")
                else:
                    console.print(f"  {prefix} [yellow]⚡ tool:[/yellow] {tool_name}")
            elif event == "tool_result":
                summary = step.get("result_summary", "").replace("\n", " ").strip()
                if len(summary) > 60:
                    summary = summary[:57] + "..."
                if summary:
                    console.print(f"         [dim]← {summary}[/dim]")
            elif event == "tool_registered":
                console.print(f"  [bold green]+ new tool:[/bold green] {tool_name}")

        # 执行
        console.print("[dim]Thinking...[/dim]")
        result = await agent.run(
            user_input=user_input,
            history=history,
            on_step=on_step,
            work_dir=str(Path.cwd()),
        )

        # 输出结果
        if result.answer:
            console.print(Panel(Markdown(result.answer), title="Evolve", border_style="green"))

        # 更新历史
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": result.answer})

        # 统计
        stats_parts = []
        if result.tools_used:
            stats_parts.append(f"工具: {', '.join(result.tools_used)}")
        stats_parts.append(f"迭代: {result.iterations}")
        if result.total_tokens:
            stats_parts.append(f"tokens: {result.total_tokens:,}")
        if result.total_duration_ms:
            stats_parts.append(f"耗时: {result.total_duration_ms}ms")
        console.print(f"[dim]{' | '.join(stats_parts)}[/dim]")

    # 清理
    try:
        await llm.close()
        if memory_service:
            memory_service.close()
        console.print("[dim]资源已清理[/dim]")
    except Exception as e:
        console.print(f"[dim]清理资源时出错: {e}[/dim]")


def _register_minimal_tools(
    executor_registry: ToolExecutorRegistry,
    registry: ToolRegistry,
) -> None:
    """注册 Evolve 的最小工具集

    最小工具集：
    - execute_bash: 万能工具，可以执行任何命令
    - tool_register_cli/mcp/skill/http: 注册新工具的能力
    - echo: 简单测试工具
    - serper_search/tavily_search: 搜索能力

    这些是 Evolve 的"种子"工具，它可以通过 tool_register_* 自主扩展。
    """
    # 注册 Python 函数到 executor_registry
    executor_registry.register_function("echo", echo)
    executor_registry.register_function("serper_search", serper_search)
    executor_registry.register_function("tavily_search", tavily_search)
    executor_registry.register_function("tool_register_cli", tool_register_cli)
    executor_registry.register_function("tool_register_mcp", tool_register_mcp)
    executor_registry.register_function("tool_register_skill", tool_register_skill)
    executor_registry.register_function("tool_register_http", tool_register_http)

    # 将4个注册工具持久化到数据库（scope=builtin）
    register_builtin_function_tools(registry)

    logger.info("Registered minimal tool set for evolve")


def _init_memory_service(config):
    """初始化长期记忆服务（可选）"""
    if not config.memory:
        return None
    try:
        from ...memory.service import MemoryService
        return MemoryService(config.memory)
    except Exception as e:
        logger.warning("Failed to initialize memory service: %s", e)
        return None
