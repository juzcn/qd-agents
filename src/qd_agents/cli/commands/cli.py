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

import typer
from rich.console import Console

from qd_agents.config import load_config
from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType
from qd_agents.cli.utils.registry import get_tool_registry
from qd_agents.cli.utils.credentials import env_var_to_tool_name, resolve_env_vars
from qd_agents.cli.utils.registration import register_tool_and_report

logger = logging.getLogger(__name__)

cli_app = typer.Typer(name="cli", help="CLI 工具管理")


@cli_app.command("add")
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
    json_file: Optional[Path] = None,
) -> None:
    """添加 CLI 工具"""
    # 从 JSON 文件读取配置
    if json_file and json_file.exists():
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                cli_config = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            console.print(f"[red][ERROR][/] 读取 CLI 配置失败: {json_file.name}: {e}")
            return
        command = cli_config.get("command", command)
        if not args:
            json_args = cli_config.get("args", [])
            if json_args:
                args = ",".join(str(a) for a in json_args) if isinstance(json_args, list) else str(json_args)
        if not extra_env:
            json_env = cli_config.get("env", {})
            extra_env = list(json_env.keys()) if isinstance(json_env, dict) else json_env
        timeout = cli_config.get("timeout", timeout)

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
    if extra_env:
        env, _ = resolve_env_vars(extra_env, console, base_dir=base_dir, interactive=interactive)

    # 注册工具
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

    register_tool_and_report(tool, console, base_dir=base_dir, config_file=config_file)
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