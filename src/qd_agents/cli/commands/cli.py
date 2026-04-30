"""
CLI 工具管理命令

负责注册和管理 CLI 工具（如 yt-dlp、ffmpeg 等）。
CLI 工具通过 bash 执行，但有固定的 command + prefix_args，
LLM 只需提供参数部分。
"""
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional, List

from rich.console import Console

from qd_agents.config import load_config
from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType
from qd_agents.cli.utils.registry import get_tool_registry
from qd_agents.cli.utils.credentials import env_var_to_tool_name
from qd_agents.config import load_runtime_config, save_runtime_config

logger = logging.getLogger(__name__)


def cli_add(
    console: Console,
    name: str,
    command: str,
    args: Optional[str] = None,
    extra_env: Optional[List[str]] = None,
    timeout: int = 300,
    default: bool = False,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
    interactive: bool = True,
) -> None:
    """添加 CLI 工具"""
    # 解析 args
    parsed_args: list[str] = []
    if args:
        try:
            parsed_args = json.loads(args)
            if not isinstance(parsed_args, list):
                parsed_args = [args]
        except json.JSONDecodeError:
            parsed_args = [a.strip() for a in args.split(",") if a.strip()]

    # 尝试获取 --help 信息作为描述
    help_text = _fetch_help(command, parsed_args)
    description = help_text or f"CLI tool: {name}"

    # 处理环境变量
    env: dict[str, str] = {}
    runtime_changed = False
    if extra_env:
        runtime_config = load_runtime_config(base_dir=base_dir)
        for var in extra_env:
            tool_name = env_var_to_tool_name(var)
            api_key_value = runtime_config.tools_credentials.get_api_key(tool_name)
            if api_key_value:
                env[var] = api_key_value
                console.print(f"  [dim]{var}[/]: 从 runtime.json (tools_credentials.{tool_name}) 加载")
            else:
                if interactive:
                    console.print(f"  [yellow]{var}[/] 未在 runtime.json 中配置，请输入 API Key:")
                    api_key_input = input(f"  {var}=").strip()
                    if api_key_input:
                        env[var] = api_key_input
                        runtime_config.tools_credentials.set_api_key(tool_name, api_key_input)
                        runtime_changed = True
                        console.print(f"  [green]已将 {var} 写入 runtime.json (tools_credentials.{tool_name})[/]")
                    else:
                        env[var] = ""
                        console.print(f"  [yellow]警告: {var} 未设置，工具执行时可能失败[/]")
                else:
                    env[var] = os.environ.get(var, "")
        if runtime_changed:
            save_runtime_config(runtime_config, base_dir=base_dir)
            console.print("  [dim]runtime.json 已更新[/]")

    # 注册工具
    config = load_config(base_dir=base_dir, config_file=config_file)
    registry = get_tool_registry(config)

    tool = Tool(
        id=f"cli.{name}",
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "arguments": {
                    "type": "string",
                    "description": f"传递给 {name} 的参数",
                },
            },
            "required": ["arguments"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.CLI,
            command=command,
            args=parsed_args,
            timeout=timeout,
            env=env,
        ),
        scope="default" if default else "user",
        metadata=ToolMetadata(
            tags=["cli", name],
        ),
        dependencies={
            "cli_command": command,
            "cli_args": parsed_args,
        },
    )

    tool_id = registry.register(tool)

    console.print(f"[green][OK][/] 已注册 CLI 工具: {name} ({tool_id})")
    console.print(f"  命令: {command}")
    if parsed_args:
        console.print(f"  前缀参数: {parsed_args}")
    console.print(f"  超时: {timeout}s")
    if extra_env:
        console.print(f"  所需环境变量: {', '.join(extra_env)}")
    if help_text:
        console.print(f"  [dim]描述来自 --help 输出[/]")


def _fetch_help(command: str, prefix_args: list[str]) -> str | None:
    """尝试执行 command --help 获取帮助信息"""
    cmd_parts = [command] + prefix_args + ["--help"]
    try:
        result = subprocess.run(
            cmd_parts,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        # --help 输出可能在 stdout 或 stderr
        output = result.stdout or result.stderr
        if output:
            # 截取前 500 字符作为描述
            lines = output.strip().splitlines()
            # 取前几行（通常是工具名 + 简短描述）
            desc_lines = []
            for line in lines[:5]:
                stripped = line.strip()
                if stripped:
                    desc_lines.append(stripped)
            return " ".join(desc_lines)[:500] if desc_lines else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None