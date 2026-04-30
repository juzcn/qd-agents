"""
CLI 工具执行器

基于 BashToolExecutor，拼装完整命令后调用 bash 执行。
CLI 工具与 bash 工具的区别：CLI 工具有固定的 command + prefix_args，
LLM 只需提供参数部分，executor 负责拼装完整命令。
"""
from __future__ import annotations

import logging
from typing import Any

from .bash import BashToolExecutor
from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType

logger = logging.getLogger(__name__)


class CliToolExecutor(BashToolExecutor):
    """CLI 工具执行器 — 拼装完整命令后调用 bash 执行"""

    def __init__(self, shell_command: str = "", shell: str = "bash", timeout: int = 300, env: dict | None = None):
        super().__init__(shell_command=shell_command, shell=shell, timeout=timeout, env=env or {})

    async def execute(self, tool_input: dict, **kwargs) -> str:
        tool: Any = kwargs.get("tool")
        full_command = self._build_command(tool, tool_input)
        logger.info("CLI tool [%s] assembled command: %s", tool.name, full_command)
        return await super().execute({"command": full_command}, **kwargs)

    @staticmethod
    def _build_command(tool: Any, tool_input: dict) -> str:
        """拼装完整命令：command + prefix_args + user_args"""
        parts: list[str] = []

        # command（如 uvx、ffmpeg）
        if tool.execution.command:
            parts.append(tool.execution.command)

        # prefix_args（如 ["yt-dlp"]）
        if tool.execution.args:
            parts.extend(tool.execution.args)

        # 用户参数
        user_args = tool_input.get("arguments", "")
        if user_args:
            parts.append(str(user_args))

        return " ".join(parts)


def create_cli_tool(
    name: str,
    description: str,
    command: str,
    args: list[str] | None = None,
    parameters: dict[str, Any] | None = None,
    timeout: int = 300,
    scope: str = "user",
    tags: list[str] | None = None,
    version: str | None = None,
) -> Tool:
    """创建 CLI 工具"""
    if tags is None:
        tags = ["cli"]

    return Tool(
        id=f"cli.{name}",
        name=name,
        description=description,
        parameters=parameters or {
            "type": "object",
            "properties": {
                "arguments": {"type": "string", "description": f"传递给 {name} 的参数"},
            },
            "required": ["arguments"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.CLI,
            command=command,
            args=args or [],
            timeout=timeout,
        ),
        scope=scope,
        metadata=ToolMetadata(
            tags=tags,
            version=version,
        ),
    )
