"""CLI 工具注册"""

from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path
from typing import Any, Optional

from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolExecutionType, ToolMetadata
from qd_agents.tools.env import resolve_env_vars_noninteractive
from qd_agents.tools.llm_helpers import parse_help_with_llm
from qd_agents.tools.errors import ToolValidationError
from qd_agents.tools.registrars.base import save_tool

logger = logging.getLogger(__name__)


def register_cli_tool(
    name: str,
    command: str,
    *,
    extra_env: list[str] | None = None,
    timeout: int = 300,
    default: bool = False,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> Tool:
    """注册 CLI 工具（纯逻辑）。

    Args:
        name: 工具名
        command: 完整命令行（如 "qd-agents memory list"）
        extra_env: 需要的环境变量名列表
        timeout: 超时秒数
        default: 是否为默认工具

    Returns:
        注册后的 Tool 对象
    """
    parts = shlex.split(command)
    if not parts:
        raise ToolValidationError("命令不能为空")
    executable = parts[0]
    args = parts[1:]

    # 如果 executable 不是绝对路径，尝试用 shutil.which 解析完整路径
    import shutil
    resolved = shutil.which(executable)
    if resolved:
        executable = resolved
    elif not Path(executable).is_file():
        raise ToolValidationError(f"可执行文件不存在: {executable}")

    # 执行 --help 获取帮助文本（必须成功）
    try:
        result = subprocess.run(
            [executable, *args, "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except FileNotFoundError as e:
        raise ToolValidationError(f"可执行文件不存在: {executable}") from e
    except subprocess.TimeoutExpired as e:
        raise ToolValidationError(f"执行 {command} --help 超时") from e
    except Exception as e:
        raise ToolValidationError(f"执行 {command} --help 失败: {e}") from e

    help_text = result.stdout or result.stderr
    if not help_text.strip():
        raise ToolValidationError(f"{command} --help 无输出")

    # LLM 解析 --help 输出（必须成功）
    parsed = parse_help_with_llm(help_text, name, base_dir, config_file)
    if not parsed or "parameters" not in parsed:
        raise ToolValidationError(f"LLM 解析 {command} --help 失败，无法提取参数 schema")

    description = parsed.get("description", f"CLI tool: {name}")
    parameters = parsed["parameters"]

    # 移除 --help 自身（所有命令都有，不是业务参数）
    properties = parameters.get("properties", {})
    properties.pop("help", None)
    required = [r for r in parameters.get("required", []) if r != "help"]
    parameters["properties"] = properties
    parameters["required"] = required

    # 环境变量
    env_dict = resolve_env_vars_noninteractive(extra_env or [], base_dir) if extra_env else {}

    tool = Tool(
        id=f"cli.{name}",
        name=name,
        description=description,
        parameters=parameters,
        execution=ToolExecutionConfig(
            type=ToolExecutionType.CLI,
            command=executable,
            args=args,
            timeout=timeout,
            env=env_dict,
        ),
        scope="default" if default else "user",
        metadata=ToolMetadata(tags=["cli", name]),
        source_path=name,
    )

    return save_tool(tool, base_dir, config_file)


def extract_registration_args(tool: Tool) -> dict:
    """从已注册的 Tool 提取重注册所需的参数"""
    parts = [tool.execution.command or ""]
    if tool.execution.args:
        parts.extend(tool.execution.args)
    command = " ".join(parts)
    env_names = list(tool.execution.env.keys()) if tool.execution.env else None
    return {
        "name": tool.name,
        "command": command,
        "extra_env": env_names,
        "timeout": tool.execution.timeout,
        "default": tool.scope == "default",
    }