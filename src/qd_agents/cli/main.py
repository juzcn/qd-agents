"""
CLI 主入口
"""
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
import questionary

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


class ChatCommandCompleter(Completer):
    """聊天命令补全器"""

    COMMANDS = {
        "/quit": "退出程序",
        "/help": "显示帮助信息",
        "/clear": "清空屏幕",
        "/history": "显示历史记录",
        "/model": "显示当前模型",
        "/models": "列出并切换可用模型",
        "/tools": "列出可用工具",
    }

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        # 如果以 / 开头，补全命令
        if text.startswith("/"):
            for cmd, desc in self.COMMANDS.items():
                if cmd.startswith(text):
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display_meta=desc,
                    )


# 定义 prompt 样式
prompt_style = Style.from_dict(
    {
        "prompt": "bold cyan",
    }
)


@app.command()
def chat(
    base_dir: Optional[Path] = typer.Option(
        None, "--base-dir", "-d", help="项目根目录"
    ),
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="config.json 文件路径"
    ),
    provider: Optional[str] = typer.Option(
        None, "--provider", "-p", help="指定 LLM 提供商 (nvidia/xunfei)"
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="指定使用的模型"
    ),
):
    """
    启动交互式聊天会话
    """
    asyncio.run(_chat_async(base_dir, config_file, provider, model))


async def _chat_async(
    base_dir: Optional[Path],
    config_file: Optional[Path],
    provider: Optional[str],
    model: Optional[str],
):
    """异步聊天实现"""
    console.print("[bold blue]qd-agents[/] - 智能体系统", style="bold")
    console.print("输入 /quit 退出，输入 /help 查看帮助\n", style="dim")

    # 加载配置
    config = load_config(base_dir=base_dir, config_file=config_file)

    # 确保数据目录存在
    if config.storage:
        config.storage.data_dir.mkdir(parents=True, exist_ok=True)
        history_file = config.storage.data_dir / "chat_history.txt"
    else:
        history_file = Path("data/chat_history.txt")
        history_file.parent.mkdir(parents=True, exist_ok=True)

    # 选择提供商
    provider_name = provider or config.llm.default_provider

    # 保存配置相关信息用于后续更新
    current_base_dir = base_dir or Path.cwd()
    current_config_file = config_file or (current_base_dir / "config.json")

    # 创建 Tool Registry
    tool_registry = ToolRegistry(
        db_path=config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    )

    # 创建 Prompt Loader
    prompt_loader = None
    if config.prompts and config.prompts.template_dir.exists():
        prompt_loader = PromptLoader(template_dir=config.prompts.template_dir)

    # 定义一个函数来初始化/重新初始化 LLM 客户端和 Agent
    async def init_llm_and_agent(p: str, m: str = None):
        nonlocal llm_client, agent, provider_name, provider_config

        provider_name = p
        provider_config = config.llm.providers.get(provider_name)
        if not provider_config or not provider_config.api_key:
            console.print(f"[red]错误: 未找到 {provider_name.upper()}_API_KEY[/]")
            return False

        # 关闭旧的客户端
        if llm_client is not None:
            await llm_client.close()

        console.print(f"[dim]正在连接 {provider_config.base_url}...[/]")

        model_names = provider_config.models.copy() if provider_config.models else []
        if m:
            model_names = [m]

        llm_client = LLMClient(
            api_key=provider_config.api_key,
            base_url=provider_config.base_url,
            model_names=model_names if model_names else None,
        )

        # 如果启用了自动发现且没有预定义模型，则发现模型
        if provider_config.auto_discover and not model_names:
            with console.status("[dim]正在发现可用模型...[/]"):
                await llm_client.discover_models()
        elif not model_names:
            # 使用默认模型
            with console.status("[dim]加载默认模型列表...[/]"):
                await llm_client.discover_models(top_k=0)

        # 重新创建 Agent
        agent = QDAgent(
            config=config,
            llm_client=llm_client,
            tool_registry=tool_registry,
            prompt_loader=prompt_loader,
        )

        with console.status("[dim]正在初始化 Agent...[/]"):
            await agent.initialize()

        console.print(f"[green]当前模型:[/] {provider_name}/{llm_client.current_model}\n")
        return True

    # 初始化
    llm_client = None
    agent = None
    provider_config = None
    if not await init_llm_and_agent(provider_name, model):
        raise typer.Exit(1)

    # 创建 prompt session
    session: PromptSession[str] = PromptSession(
        completer=ChatCommandCompleter(),
        history=FileHistory(str(history_file)),
        style=prompt_style,
        complete_while_typing=True,
    )

    # 聊天循环
    while True:
        try:
            user_input = await session.prompt_async(
                [("class:prompt", "你: ")],
            )

            if user_input.lower() == "/quit":
                console.print("[bold]再见！[/]")
                break

            if user_input.lower() == "/help":
                console.print("\n[bold]可用命令:[/]")
                console.print("  /quit - 退出程序")
                console.print("  /clear - 清空屏幕")
                console.print("  /history - 显示历史记录")
                console.print("  /model - 显示当前模型")
                console.print("  /models - 列出并切换可用模型")
                console.print("  /tools - 列出可用工具")
                console.print("  /help - 显示此帮助\n")
                continue

            if user_input.lower() == "/clear":
                console.clear()
                continue

            if user_input.lower() == "/model":
                console.print(f"\n[bold]当前模型:[/] {provider_name}/{llm_client.current_model}\n")
                continue

            # 处理 /models 命令
            if user_input.lower() == "/models":
                # 收集所有提供商的模型
                all_models = []

                # 添加当前提供商的模型
                current_models = llm_client.available_models
                for model_name in current_models:
                    all_models.append({
                        "provider": provider_name,
                        "model": model_name,
                        "is_current": (model_name == llm_client.current_model),
                        "is_current_provider": True
                    })

                # 添加其他提供商的模型
                for other_provider, other_config in config.llm.providers.items():
                    if other_provider == provider_name:
                        continue
                    if other_config.models:
                        for model_name in other_config.models:
                            all_models.append({
                                "provider": other_provider,
                                "model": model_name,
                                "is_current": False,
                                "is_current_provider": False
                            })

                if not all_models:
                    console.print("\n[yellow]没有可用模型[/]\n")
                    continue

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
                            if llm_client.switch_model(selected_model):
                                console.print(f"\n[green]已切换到模型:[/] {selected_provider}/{selected_model}")
                                # 更新配置（仅对 auto_discover: false 的提供商保存 default_model）
                                selected_provider_config = config.llm.providers.get(selected_provider)
                                if selected_provider_config and not selected_provider_config.auto_discover:
                                    # 直接更新 config.json 文件
                                    if current_config_file.exists():
                                        with open(current_config_file, 'r', encoding='utf-8') as f:
                                            config_data = json.load(f)
                                        config_data['llm']['default_provider'] = selected_provider
                                        config_data['llm']['default_model'] = selected_model
                                        with open(current_config_file, 'w', encoding='utf-8') as f:
                                            json.dump(config_data, f, ensure_ascii=False, indent=2)
                                        console.print(f"[dim]已更新 config.json[/]")
                                console.print()
                            else:
                                console.print(f"\n[red]切换模型失败[/]\n")
                        else:
                            # 动态切换提供商
                            console.print(f"[dim]切换到提供商 {selected_provider}...[/]")
                            if await init_llm_and_agent(selected_provider, selected_model):
                                # 更新配置
                                selected_provider_config = config.llm.providers.get(selected_provider)
                                if selected_provider_config and not selected_provider_config.auto_discover:
                                    # 直接更新 config.json 文件
                                    if current_config_file.exists():
                                        with open(current_config_file, 'r', encoding='utf-8') as f:
                                            config_data = json.load(f)
                                        config_data['llm']['default_provider'] = selected_provider
                                        config_data['llm']['default_model'] = selected_model
                                        with open(current_config_file, 'w', encoding='utf-8') as f:
                                            json.dump(config_data, f, ensure_ascii=False, indent=2)
                                        console.print(f"[dim]已更新 config.json[/]")
                except (KeyboardInterrupt, EOFError):
                    console.print()
                continue

            if user_input.lower() == "/tools":
                tools = tool_registry.list_all()
                console.print(f"\n[bold]可用工具 ({len(tools)}):[/]")
                for tool in tools:
                    console.print(f"  - [cyan]{tool.name}[/]: {tool.description}")
                console.print()
                continue

            if not user_input.strip():
                continue

            # 调用 Agent 处理
            with console.status("[bold]思考中...[/]"):
                result = await agent.process(user_input=user_input)

            # 显示回复
            console.print(f"\n[bold green]助手[/]: {result.final_output}\n")
            console.print(f"[dim]耗时: {result.total_duration_ms}ms[/]", style="dim")

        except KeyboardInterrupt:
            console.print("\n[yellow]按 /quit 退出[/]")
            continue
        except EOFError:
            console.print("\n[bold]再见！[/]")
            break
        except Exception as e:
            console.print(f"\n[red]错误: {e}[/]\n")


@app.command()
def list_models(
    base_dir: Optional[Path] = typer.Option(
        None, "--base-dir", "-d", help="项目根目录"
    ),
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="config.json 文件路径"
    ),
    provider: Optional[str] = typer.Option(
        None, "--provider", "-p", help="指定 LLM 提供商 (nvidia/xunfei)"
    ),
):
    """列出可用模型"""
    asyncio.run(_list_models_async(base_dir, config_file, provider))


async def _list_models_async(base_dir: Optional[Path], config_file: Optional[Path], provider: Optional[str]):
    """异步列出模型"""
    config = load_config(base_dir=base_dir, config_file=config_file)

    # 选择提供商
    provider_name = provider or config.llm.default_provider

    # 获取提供商配置
    provider_config = config.llm.providers.get(provider_name)
    if not provider_config or not provider_config.api_key:
        console.print(f"[red]错误: 未找到 {provider_name.upper()}_API_KEY[/]")
        raise typer.Exit(1)

    console.print(f"提供商: {provider_name}")
    console.print(f"连接到: {provider_config.base_url}")

    # 如果配置了模型列表，直接显示
    if provider_config.models and not provider_config.auto_discover:
        console.print("\n[bold]已配置的模型:[/]\n")
        for model_name in provider_config.models:
            console.print(f"  - [cyan]{provider_name}/{model_name}[/]")
        return

    # 否则从 API 获取
    console.print("正在获取模型列表...\n")

    async with LLMClient(
        api_key=provider_config.api_key,
        base_url=provider_config.base_url,
    ) as llm_client:
        try:
            models_response = await llm_client._client.models.list()

            console.print("[bold]可用模型:[/]\n")
            for m in models_response.data:
                console.print(f"  - [cyan]{provider_name}/{m.id}[/]")
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
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="config.json 文件路径"
    ),
):
    """列出已注册的工具"""
    config = load_config(base_dir=base_dir, config_file=config_file)

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
def init_tools(
    base_dir: Optional[Path] = typer.Option(
        None, "--base-dir", "-d", help="项目根目录"
    ),
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="config.json 文件路径"
    ),
):
    """初始化内置工具"""
    config = load_config(base_dir=base_dir, config_file=config_file)

    # 确保数据目录存在
    if config.storage:
        config.storage.data_dir.mkdir(parents=True, exist_ok=True)

    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)

    # 注册内置工具
    from ..registry import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType

    registered_tools = []

    # ==================== 元工具 ====================

    # meta.direct
    direct_tool = Tool(
        id="meta.direct",
        name="direct",
        description="直接生成自然语言回复，不调用任何工具",
        parameters={
            "type": "object",
            "properties": {
                "response": {"type": "string", "description": "生成的回复内容"}
            },
            "required": ["response"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.agent.builtin_tools",
            function="meta_direct",
        ),
        metadata=ToolMetadata(
            category="meta",
            tags=["meta", "direct"],
        ),
    )
    registry.register(direct_tool)
    registered_tools.append(direct_tool.name)

    # meta.find_tools
    find_tools_tool = Tool(
        id="meta.find_tools",
        name="find_tools",
        description="根据用户需求检索相关工具",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "用于检索工具的关键词或描述"}
            },
            "required": ["query"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.agent.builtin_tools",
            function="meta_find_tools",
        ),
        metadata=ToolMetadata(
            category="meta",
            tags=["meta", "search", "tools"],
        ),
    )
    registry.register(find_tools_tool)
    registered_tools.append(find_tools_tool.name)

    # meta.coding_tool_use
    coding_tool = Tool(
        id="meta.coding_tool_use",
        name="coding_tool_use",
        description="生成Python代码来编排多个工具的执行（支持条件、循环等复杂逻辑）。优先使用已注册工具，也可使用Python标准库进行数据处理。",
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python代码字符串，可调用已注册工具，也可使用Python标准库"}
            },
            "required": ["code"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.agent.builtin_tools",
            function="meta_coding_tool_use",
        ),
        metadata=ToolMetadata(
            category="meta",
            tags=["meta", "coding", "workflow"],
        ),
    )
    registry.register(coding_tool)
    registered_tools.append(coding_tool.name)

    # meta.step_down
    step_down_tool = Tool(
        id="meta.step_down",
        name="step_down",
        description="当无法通过工具完成任务时，降级为人工友好的回复",
        parameters={
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": ["no_matching_tools", "too_complex", "safety_concern", "user_confirmation_required"],
                    "description": "降级原因",
                },
                "message": {"type": "string", "description": "给用户的友好提示信息"},
            },
            "required": ["reason", "message"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.agent.builtin_tools",
            function="meta_step_down",
        ),
        metadata=ToolMetadata(
            category="meta",
            tags=["meta", "fallback"],
        ),
    )
    registry.register(step_down_tool)
    registered_tools.append(step_down_tool.name)

    # ==================== 搜索工具 ====================

    # search.serper
    serper_tool = Tool(
        id="search.serper",
        name="serper_search",
        description="使用 Serper API 进行网络搜索，获取网页摘要和链接",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词或问题"},
                "num": {"type": "integer", "description": "返回结果数量，默认 10", "default": 10},
            },
            "required": ["query"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.agent.builtin_tools",
            function="serper_search",
        ),
        metadata=ToolMetadata(
            category="search",
            tags=["web", "search", "serper"],
        ),
    )
    registry.register(serper_tool)
    registered_tools.append(serper_tool.name)

    # search.tavily
    tavily_tool = Tool(
        id="search.tavily",
        name="tavily_search",
        description="使用 Tavily API 进行 AI 增强的网络搜索，支持深度搜索和答案提取",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词或问题"},
                "search_depth": {
                    "type": "string",
                    "enum": ["basic", "advanced"],
                    "description": "搜索深度，默认 basic",
                    "default": "basic",
                },
                "include_answer": {
                    "type": "boolean",
                    "description": "是否包含 AI 生成的答案",
                    "default": True,
                },
                "max_results": {
                    "type": "integer",
                    "description": "返回结果数量，默认 5",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.agent.builtin_tools",
            function="tavily_search",
        ),
        metadata=ToolMetadata(
            category="search",
            tags=["web", "search", "tavily", "ai"],
        ),
    )
    registry.register(tavily_tool)
    registered_tools.append(tavily_tool.name)

    # search.baidu
    baidu_tool = Tool(
        id="search.baidu",
        name="baidu_search",
        description="使用百度搜索 API 进行中文网络搜索",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词或问题"},
                "pn": {"type": "integer", "description": "起始结果页码，默认 0", "default": 0},
            },
            "required": ["query"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.agent.builtin_tools",
            function="baidu_search",
        ),
        metadata=ToolMetadata(
            category="search",
            tags=["web", "search", "baidu", "chinese"],
        ),
    )
    registry.register(baidu_tool)
    registered_tools.append(baidu_tool.name)

    # search.web (统一接口)
    web_search_tool = Tool(
        id="search.web",
        name="web_search",
        description="通用网络搜索工具，自动选择合适的搜索引擎进行搜索",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词或问题"},
                "num_results": {
                    "type": "integer",
                    "description": "返回结果数量，默认 5",
                    "default": 5,
                },
                "engine": {
                    "type": "string",
                    "enum": ["auto", "serper", "tavily", "baidu"],
                    "description": "指定搜索引擎，auto 表示自动选择",
                    "default": "auto",
                },
                "language": {
                    "type": "string",
                    "description": "搜索结果语言偏好，例如 zh-CN、en-US",
                    "default": "zh-CN",
                },
            },
            "required": ["query"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.agent.builtin_tools",
            function="web_search",
        ),
        metadata=ToolMetadata(
            category="search",
            tags=["web", "search", "unified"],
        ),
    )
    registry.register(web_search_tool)
    registered_tools.append(web_search_tool.name)

    # ==================== 实用工具 ====================

    from ..agent.builtins import echo

    # util.echo
    echo_tool = Tool(
        id="util.echo",
        name="echo",
        description="回显输入的消息",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "要回显的消息"}
            },
            "required": ["message"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module="qd_agents.agent.builtins",
            function="echo",
        ),
        metadata=ToolMetadata(
            category="utilities",
            tags=["echo", "utility"],
        ),
    )
    registry.register(echo_tool)
    registered_tools.append(echo_tool.name)

    console.print(f"[green]已注册内置工具 ({len(registered_tools)} 个):[/]")
    for tool_name in registered_tools:
        console.print(f"  - {tool_name}")


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
