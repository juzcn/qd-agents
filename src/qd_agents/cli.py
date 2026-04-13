"""
命令行入口
"""

import typer
from typing import Optional
from rich.prompt import Prompt
from rich.table import Table
from rich.console import Console

from .orchestrator import Orchestrator
from .models.nvidia_pool import NvidiaModelPool
from .utils import (
    enable_debug,
    disable_debug,
    is_debug_enabled,
    print_header,
    print_normal,
    debug_print,
    debug_separator
)


_console = Console()
app = typer.Typer(
    name="qd-agents",
    help="意图驱动的上下文隔离多Agent系统",
    add_completion=False
)


def _register_sample_tools(orch: Orchestrator) -> None:
    """注册示例工具"""

    def get_weather(location: str, datetime: Optional[str] = None) -> str:
        """获取天气信息"""
        return f"{location} 的天气: 晴朗, 25°C"

    def search_web(query: str) -> str:
        """搜索网络"""
        return f"搜索 '{query}' 的结果: ..."

    def send_email(to: str, subject: str, body: str) -> str:
        """发送邮件"""
        return f"已发送邮件给 {to}"

    orch.executor.register_function(
        name="get_weather",
        description="获取指定地点的天气信息",
        parameters={
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "城市名称"},
                "datetime": {"type": "string", "description": "日期时间（可选）"}
            },
            "required": ["location"]
        },
        handler=get_weather
    )

    orch.executor.register_function(
        name="search_web",
        description="搜索网络信息",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"}
            },
            "required": ["query"]
        },
        handler=search_web
    )

    orch.executor.register_function(
        name="send_email",
        description="发送邮件",
        parameters={
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "收件人邮箱"},
                "subject": {"type": "string", "description": "邮件主题"},
                "body": {"type": "string", "description": "邮件内容"}
            },
            "required": ["to", "subject", "body"]
        },
        handler=send_email,
        require_confirmation=True
    )


def _display_model_list(model_pool: NvidiaModelPool) -> None:
    """显示模型列表"""
    models = model_pool.get_all_models()
    current_idx = model_pool._current_index

    table = Table(title="可用模型")
    table.add_column("#", style="cyan")
    table.add_column("当前", style="green")
    table.add_column("模型 ID", style="yellow")
    table.add_column("优先级", style="magenta")

    for i, model in enumerate(models):
        is_current = "*" if i == current_idx else ""
        table.add_row(str(i), is_current, model.id, str(model.priority))

    _console.print(table)


@app.command()
def chat(
    debug: bool = typer.Option(False, "--debug", "-d", help="启用调试模式，显示中间输出"),
    user_id: str = typer.Option("user_001", "--user", "-u", help="用户ID"),
    message: Optional[str] = typer.Argument(None, help="单次消息模式：直接发送消息")
):
    """
    交互式聊天模式

    特殊命令:
      /models, /model list  - 显示所有可用模型
      /model <序号>         - 切换到指定模型
      /debug                - 切换调试模式
      /quit, /exit          - 退出
    """
    if debug:
        enable_debug()

    print_header()

    if is_debug_enabled():
        print_normal("[yellow]调试模式已启用[/]")
        print_normal()

    # 初始化模型池和编排器
    debug_step("初始化", "创建 NVIDIA 模型池...")
    model_pool = NvidiaModelPool()
    orch = Orchestrator(model_pool=model_pool)
    _register_sample_tools(orch)

    # 显示当前模型
    current_model = model_pool.get_current_model()
    print_normal(f"当前模型: [bold cyan]{current_model.id}[/]")
    print_normal("输入 /models 查看所有可用模型")
    print_normal()

    # 创建会话
    session_id = orch.create_session(user_id)
    debug_print("会话创建", {"session_id": session_id, "user_id": user_id}, style="green")

    if message:
        # 单次消息模式
        response = orch.process_message(session_id, message)
        print_normal()
        print_normal(f"[bold]回复:[/] {response}")
        return

    # 交互式模式
    print_normal("输入 'quit' 或 'exit' 退出")
    print_normal("输入 '/models' 查看可用模型，输入 '/model <序号>' 切换模型")
    print_normal()

    while True:
        try:
            user_input = Prompt.ask("[bold cyan]你[/]")
        except (KeyboardInterrupt, EOFError):
            print_normal()
            break

        if not user_input.strip():
            continue

        # 处理特殊命令
        if user_input.lower() in ("quit", "exit", "q"):
            break

        if user_input.lower() == "debug":
            if is_debug_enabled():
                disable_debug()
                print_normal("[yellow]调试模式已关闭[/]")
            else:
                enable_debug()
                print_normal("[yellow]调试模式已启用[/]")
            print_normal()
            continue

        # 模型列表命令
        if user_input.lower() in ("/models", "/model list"):
            _display_model_list(model_pool)
            print_normal()
            continue

        # 模型切换命令
        if user_input.lower().startswith("/model "):
            parts = user_input.split(maxsplit=2)
            if len(parts) >= 2:
                try:
                    idx = int(parts[1])
                    if orch.set_model(idx):
                        new_model = model_pool.get_current_model()
                        print_normal(f"[green]已切换到模型: {new_model.id}[/]")
                    else:
                        print_normal("[red]无效的模型序号[/]")
                        _display_model_list(model_pool)
                except ValueError:
                    print_normal("[red]请输入有效的数字序号[/]")
            print_normal()
            continue

        # 处理消息
        response = orch.process_message(session_id, user_input)

        print_normal()
        print_normal(f"[bold green]助手[/]: {response}")
        print_normal()


@app.command()
def models():
    """列出所有可用的 NVIDIA 模型"""
    print_header()
    model_pool = NvidiaModelPool()
    _display_model_list(model_pool)


@app.command()
def version():
    """显示版本信息"""
    print_normal("qd-agents: 意图驱动的上下文隔离多Agent系统")
    print_normal("版本: 0.1.0")


def main():
    """主函数入口"""
    app()


if __name__ == "__main__":
    main()
