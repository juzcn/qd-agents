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
from ...config.paths import resolve_template_dir
from ...llm import LLMClient
from ...prompts import PromptLoader
from ...registry import ToolRegistry
from ...tools import ToolExecutorRegistry
from ...models.tool import Tool, ToolExecutionConfig, ToolExecutionType, ToolMetadata
from ...utils.logging import setup_session_logging

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

    # 2. 配置会话日志
    log_level = config.observability.log_level if config.observability else "INFO"
    log_dir = config.observability.log_session_dir if (config.observability and config.observability.log_session_dir) else Path(".")
    log_external_api = config.observability.log_external_api if config.observability else False
    log_immediate_flush = config.observability.log_immediate_flush if config.observability else True

    log_file, trace_id = setup_session_logging(
        log_dir=log_dir,
        level=log_level,
        log_external_api=log_external_api,
        log_immediate_flush=log_immediate_flush,
    )
    console.print(f"[dim]日志文件: {log_file}[/]")

    # 3. 初始化 LLM
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
    llm.meta_agent_name = "evolve"

    # 发现模型
    if provider_config.auto_discover and not model_names:
        with console.status("正在发现可用模型..."):
            await llm.discover_models()
    elif not model_names:
        with console.status("加载默认模型列表..."):
            await llm.discover_models(top_k=0)

    console.print(f"[green]当前模型:[/green] {provider_name}/{llm.current_model}")

    # 4. 初始化独立的 Registry（Evolve 有自己的工具箱，独立 db）
    evolve_db_path = Path("data/evolve_tools.db")
    registry = ToolRegistry(evolve_db_path)

    # 5. 初始化独立的 Executor Registry + 注册最小工具集
    executor_registry = ToolExecutorRegistry(registry=registry)
    _register_minimal_tools(executor_registry, registry)

    # 6. 初始化 Memory（可选，预加载 embedding 模型）
    memory_service = _init_memory_service(config, console=console)

    # 7. 初始化 PromptLoader
    prompt_loader = PromptLoader(template_dir=resolve_template_dir(config))

    # 8. 初始化 Context Manager
    ctx_manager = EvolveContextManager(
        llm=llm,
        max_context_tokens=config.execution.evolve_context_window,
        compact_threshold=config.execution.evolve_compact_threshold,
        recent_turns=config.execution.evolve_recent_turns,
    )

    # 9. 创建 EvolveAgent
    agent = EvolveAgent(
        llm=llm,
        registry=registry,
        executor_registry=executor_registry,
        context_manager=ctx_manager,
        prompt_loader=prompt_loader,
        memory_service=memory_service,
        max_iterations=config.execution.max_evolve_iterations,
    )

    # 10. 欢迎信息
    tools_count = len(registry.list_all())
    console.print(Panel(
        f"[bold green]Evolve Agent[/bold green] — 自主进化 Agent\n"
        f"初始工具数: {tools_count} | Agent 将通过注册新工具自主成长\n"
        "输入任务，Agent 将自主决策并执行。输入 /quit 退出。",
        title="qd-agents evolve",
    ))

    # 11. 交互循环
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
    """注册 Evolve 的最小工具集 — 仅 execute_bash

    Evolve 从唯一工具 execute_bash 起步，通过 bash 执行 qd-agents CLI
    自主注册新工具，实现自我进化。
    """
    # 清空旧工具（每次启动重置）
    registry.clear_all()

    # 注册唯一的种子工具：execute_bash
    bash_tool = Tool(
        id="builtin.execute_bash",
        name="execute_bash",
        description="执行 bash/shell 命令并返回输出。可用于文件操作、代码运行、系统管理等任务。",
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令",
                },
            },
            "required": ["command"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.BASH,
            shell_command="{command}",
            shell="bash",
            timeout=300,
        ),
        scope="builtin",
        metadata=ToolMetadata(
            tags=["builtin", "bash", "shell"],
            version="0.1.0",
        ),
    )
    registry.register(bash_tool)
    logger.info("Registered minimal tool set for evolve (1 tool: execute_bash)")


def _init_memory_service(config, console=None):
    """初始化长期记忆服务（可选），预加载 embedding 模型"""
    if not config.memory:
        return None
    try:
        from ...memory.service import MemoryService
        service = MemoryService(config.memory)

        # 预加载 embedding 模型，避免首次交互时延迟
        if console:
            with console.status("正在加载记忆模型..."):
                service.preload()
        else:
            service.preload()

        return service
    except Exception as e:
        logger.warning("Failed to initialize memory service: %s", e)
        return None
