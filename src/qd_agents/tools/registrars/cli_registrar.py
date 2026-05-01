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

    # 执行 --help 获取帮助文本
    description = f"CLI tool: {name}"
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    try:
        result = subprocess.run(
            [executable, *args, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        help_text = result.stdout or result.stderr
        if help_text.strip():
            parsed = parse_help_with_llm(help_text, name, base_dir, config_file)
            description = parsed.get("description", description)
            parameters = parsed.get("parameters", parameters)
    except Exception as e:
        logger.warning("执行 %s --help 失败: %s", command, e)

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