"""
聊天命令处理

负责交互式聊天会话的命令处理。
"""

import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import Optional, Any, Dict, List

import questionary
import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console

from ..managers import (
    LLMClientManager,
    setup_configuration,
)
from qd_agents.registry import ToolRegistry
from qd_agents.prompts import PromptLoader
from qd_agents.context import ContextManager
from qd_agents.config import Config
from qd_agents.llm import LLMClient
from qd_agents.cli.utils.registry import get_tool_registry


class ChatCommandHandler:
    """处理聊天会话中的各种命令"""

    def __init__(
        self,
        console: Console,
        config: Config,
        llm_manager: LLMClientManager,
        tool_registry: ToolRegistry,
        current_config_file: Path,
        session: PromptSession,
    ):
        """
        初始化聊天命令处理器

        Args:
            console: Rich 控制台对象
            config: 应用配置
            llm_manager: LLM 客户端管理器
            tool_registry: 工具注册表
            current_config_file: 当前配置文件路径
            session: Prompt 会话对象
        """
        self.console = console
        self.config = config
        self.llm_manager = llm_manager
        self.tool_registry = tool_registry
        self.current_config_file = current_config_file
        self.session = session

    async def handle_command(self, user_input: str) -> bool:
        """
        处理用户输入的命令

        Args:
            user_input: 用户输入

        Returns:
            continue_chat: 是否继续聊天
        """
        if user_input.lower() == "/quit":
            return False

        if user_input.lower() == "/help":
            self._show_help()
            return True

        if user_input.lower() == "/model":
            self._show_current_model()
            return True

        if user_input.lower() == "/models":
            await self._handle_models_command()
            return True

        if user_input.lower() == "/tools":
            self._show_tools()
            return True

        if user_input.strip().startswith("/"):
            self.console.print(f"\n[red]错误: 未知命令 '{user_input}'[/]")
            self.console.print("输入 /help 查看可用命令\n")
            return True

        if not user_input.strip():
            return True

        # 不是命令，处理用户消息
        return await self._handle_user_message(user_input)

    def _show_help(self):
        """显示帮助信息"""
        self.console.print("\n[bold]可用命令:[/]")
        self.console.print("  /quit - 退出程序")
        self.console.print("  /model - 显示当前模型")
        self.console.print("  /models - 列出并切换可用模型")
        self.console.print("  /tools - 列出可用工具")
        self.console.print("  /help - 显示此帮助\n")

    def _show_current_model(self):
        """显示当前模型"""
        if self.llm_manager.llm_client and self.llm_manager.provider_name:
            self.console.print(
                f"\n[bold]当前模型:[/] {self.llm_manager.provider_name}/{self.llm_manager.llm_client.current_model}\n"
            )
        else:
            self.console.print("\n[red]错误: 模型未初始化[/]\n")

    def _show_tools(self):
        """显示可用工具"""
        tools = self.tool_registry.list_all()
        self.console.print(f"\n[bold]可用工具 ({len(tools)}):[/]")
        for tool in tools:
            self.console.print(f"  - [cyan]{tool.name}[/]: {tool.description}")
        self.console.print()


    async def _handle_models_command(self):
        """处理 /models 命令：列出并切换模型"""
        all_models: List[Dict[str, Any]] = []

        # 添加当前提供商的模型
        if self.llm_manager.llm_client:
            current_models = self.llm_manager.llm_client.available_models
            for model_name in current_models:
                all_models.append({
                    "provider": self.llm_manager.provider_name or "",
                    "model": model_name,
                    "is_current": (model_name == self.llm_manager.llm_client.current_model),
                    "is_current_provider": True
                })

        # 添加其他提供商的模型
        for other_provider, other_config in self.config.llm.providers.items():
            if other_provider == self.llm_manager.provider_name:
                continue

            # 如果配置了模型列表，直接添加
            if other_config.models:
                for model_name in other_config.get_model_names():
                    all_models.append({
                        "provider": other_provider,
                        "model": model_name,
                        "is_current": False,
                        "is_current_provider": False
                    })
            # 如果启用了自动发现，需要从 API 获取模型列表
            elif other_config.auto_discover:
                try:
                    # 创建临时客户端来发现模型
                    async with LLMClient(
                        api_key=other_config.api_key,
                        base_url=other_config.base_url,
                    ) as temp_client:
                        # 对于其他 auto_discover 提供商，也使用模型池逻辑（top_k=5）
                        await temp_client.discover_models(top_k=5)
                        for model_name in temp_client.available_models:
                            all_models.append({
                                "provider": other_provider,
                                "model": model_name,
                                "is_current": False,
                                "is_current_provider": False
                            })
                except Exception as e:
                    self.console.print(f"[dim]无法获取 {other_provider} 的模型列表: {e}[/]")

        if not all_models:
            self.console.print("\n[yellow]没有可用模型[/]\n")
            return

        # 构建选择项，格式为 provider/model
        choices = []
        for item in all_models:
            display_name = f"{item['provider']}/{item['model']}"
            if item['is_current']:
                choices.append(questionary.Choice(f"{display_name} (当前)", value=item))
            else:
                choices.append(questionary.Choice(display_name, value=item))

        try:
            # 使用 questionary 默认样式，有明显的菜单框
            selected = await questionary.select(
                "选择模型:",
                choices=choices,
                qmark=">",
                instruction="(↑↓ 选择, Enter 确认)",
            ).ask_async()

            if selected:
                selected_item = selected
                selected_provider = selected_item["provider"]
                selected_model = selected_item["model"]

                # 如果是当前提供商的模型，直接切换
                if selected_item["is_current_provider"]:
                    if self.llm_manager.switch_model(selected_model):
                        self.console.print(f"\n[green]已切换到模型:[/] {selected_provider}/{selected_model}")
                        self._update_config(selected_provider, selected_model)
                    else:
                        self.console.print(f"\n[red]切换模型失败[/]\n")
                else:
                    # 动态切换提供商
                    self.console.print(f"[dim]切换到提供商 {selected_provider}...[/]")
                    if await self.llm_manager.initialize(selected_provider, selected_model):
                        self._update_config(selected_provider, selected_model)

        except (KeyboardInterrupt, EOFError):
            self.console.print()

    def _update_config(self, provider: str, model: str):
        """更新配置文件中的默认提供商和模型"""
        selected_provider_config = self.config.llm.providers.get(provider)
        if selected_provider_config and not selected_provider_config.auto_discover:
            if self.current_config_file.exists():
                with open(self.current_config_file, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                config_data['llm']['default_provider'] = provider
                config_data['llm']['default_model'] = model
                with open(self.current_config_file, 'w', encoding='utf-8') as f:
                    json.dump(config_data, f, ensure_ascii=False, indent=2)
                self.console.print(f"[dim]已更新 config.json[/]")
            self.console.print()

    async def _handle_user_message(self, user_input: str) -> bool:
        """处理用户的实际消息"""
        if self.llm_manager.agent is None:
            self.console.print("\n[red]错误: Agent 未初始化[/]\n")
            return True

        self.console.print("[dim]按 Esc 取消执行[/]")

        # 构造步骤回调，实时输出中间过程
        def on_step(step_info: dict) -> None:
            iteration = step_info.get("iteration", "?")
            max_iter = step_info.get("max_iterations", "?")
            event = step_info.get("event", "")
            tool_name = step_info.get("tool_name", "")
            command = step_info.get("command", "")
            result_summary = step_info.get("result_summary", "")
            detail = step_info.get("detail", "")
            loop_name = step_info.get("loop", "")
            loop_tag = f"[{loop_name}]" if loop_name else ""
            prefix = f"[dim][{iteration}/{max_iter}]{loop_tag}[/]"

            if event == "route_decision":
                self.console.print(f"{prefix} [blue]路由决策[/]")
            elif event == "route_result":
                self.console.print(f"{prefix} [blue]路由结果[/]: {detail}")
            elif event == "skill_load":
                self.console.print(f"{prefix} [cyan]加载技能[/]: {tool_name}")
            elif event == "schema_load":
                self.console.print(f"{prefix} [cyan]加载参数[/]: {tool_name}")
            elif event == "tool_call":
                if tool_name == "execute_bash" and command:
                    # 截断长命令
                    cmd_display = command if len(command) <= 80 else command[:77] + "..."
                    self.console.print(f"{prefix} [yellow]执行[/]: {cmd_display}")
                else:
                    self.console.print(f"{prefix} [yellow]调用工具[/]: {tool_name}")
            elif event == "tool_result":
                # 截断结果摘要
                summary = result_summary.replace("\n", " ").strip()
                if len(summary) > 60:
                    summary = summary[:57] + "..."
                if summary:
                    self.console.print(f"       [dim]→ {summary}[/]")

        # Escape 键监听：后台线程检测按键，设置 cancel_event
        agent = self.llm_manager.agent

        def _esc_listener():
            """后台线程：监听 Escape 键"""
            if sys.platform == "win32":
                import msvcrt
                while not _esc_done.is_set():
                    if msvcrt.kbhit() and msvcrt.getch() == b'\x1b':
                        if agent._cancel_event:
                            agent._cancel_event.set()
                        self.console.print("\n[bold red]正在取消...[/]")
                        break
                    _esc_done.wait(0.1)
            else:
                import select
                while not _esc_done.is_set():
                    if select.select([sys.stdin], [], [], 0.1)[0]:
                        ch = sys.stdin.read(1)
                        if ch == '\x1b':
                            if agent._cancel_event:
                                agent._cancel_event.set()
                            self.console.print("\n[bold red]正在取消...[/]")
                            break
                    _esc_done.wait(0.1)

        _esc_done = threading.Event()

        # 启动 Escape 监听线程
        esc_thread = threading.Thread(target=_esc_listener, daemon=True)
        esc_thread.start()

        try:
            result = await self.llm_manager.agent.process(user_input=user_input, on_step=on_step)
        finally:
            _esc_done.set()
            esc_thread.join(timeout=0.5)

        self.console.print(f"\n[bold green]助手[/]: {result.final_answer}\n")
        self.console.print(f"[dim]耗时: {result.total_duration_ms}ms[/]", style="dim")
        if result.total_tokens:
            prompt_info = f" (末轮prompt: {result.last_prompt_tokens:,})" if result.last_prompt_tokens else ""
            self.console.print(f"[dim]Token: {result.total_tokens:,}{prompt_info}[/]", style="dim")
        return True


async def chat_async(
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> None:
    """
    异步聊天实现

    Args:
        console: Rich 控制台对象
        base_dir: 基础目录
        config_file: 配置文件路径
        provider: 提供商名称
        model: 模型名称
    """
    console.print("[bold blue]qd-agents[/] - 智能体系统", style="bold")
    console.print("输入 /quit 退出，输入 /help 查看帮助\n", style="dim")

    # 1. 配置初始化
    config = setup_configuration(console, base_dir, config_file)


    # 3. 初始化工具和上下文
    tool_registry = get_tool_registry(config)


    prompt_loader = (
        PromptLoader(template_dir=config.prompts.template_dir)
        if config.prompts and config.prompts.template_dir.exists()
        else None
    )

    context_manager = ContextManager(prompt_loader=prompt_loader)

    # 4. 初始化 LLM 客户端和代理
    provider_name = provider or config.llm.default_provider
    llm_manager = LLMClientManager(console, config, tool_registry, prompt_loader, context_manager)
    if not await llm_manager.initialize(provider_name, model):
        raise typer.Exit(1)

    # 5. 保存配置信息
    current_base_dir = base_dir or Path.cwd()
    current_config_file = config_file or (current_base_dir / "config.json")

    # 6. 创建聊天会话（使用 prompt_style）
    from .. import prompt_style, ChatCommandCompleter  # 从 main.py 导入
    session: PromptSession[str] = PromptSession(
        completer=ChatCommandCompleter(),
        history=InMemoryHistory(),  # 使用内存历史记录
        style=prompt_style,
        complete_while_typing=True,
    )

    # 7. 创建命令处理器
    command_handler = ChatCommandHandler(
        console=console,
        config=config,
        llm_manager=llm_manager,
        tool_registry=tool_registry,
        current_config_file=current_config_file,
        session=session,
    )

    # 8. 聊天循环
    while True:
        try:
            user_input = await session.prompt_async(
                [("class:prompt", "你: ")],
            )

            continue_chat = await command_handler.handle_command(user_input)
            if not continue_chat:
                break

        except KeyboardInterrupt:
            console.print("\n[yellow]按 /quit 退出[/]")
            continue
        except EOFError:
            console.print("\n[bold]再见！[/]")
            break
        except Exception as e:
            console.print(f"\n[red]错误: {e}[/]\n")

    # 清理资源
    try:
        await llm_manager.close()
        console.print("[dim]资源已清理[/]")
    except Exception as e:
        console.print(f"[dim]清理资源时出错: {e}[/]")

    console.print("\n[bold]再见！[/]")
