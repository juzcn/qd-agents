"""
CLI 和 Bash 工具执行器

处理命令行和shell命令执行的工具执行器。
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import locale
from typing import Any

from .base import ToolExecutor
from qd_agents.registry import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType


logger = logging.getLogger(__name__)


def safe_decode(bytes_data: bytes, encoding: str = None) -> str:
    """
    安全解码字节数据

    Args:
        bytes_data: 要解码的字节数据
        encoding: 指定的编码，如果为None则自动检测

    Returns:
        解码后的字符串
    """
    if not bytes_data:
        return ""

    if encoding:
        try:
            return bytes_data.decode(encoding)
        except UnicodeDecodeError:
            pass

    # 尝试多种常见编码
    encodings_to_try = [
        locale.getpreferredencoding(),
        sys.getdefaultencoding(),
        'utf-8',
        'gbk',  # Windows中文编码
        'gb2312',
        'latin-1',  # 不会失败的编码
    ]

    # 去重
    encodings_to_try = list(dict.fromkeys(encodings_to_try))

    for enc in encodings_to_try:
        try:
            return bytes_data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

    # 所有编码都失败，使用latin-1或替换字符
    try:
        return bytes_data.decode('latin-1')
    except UnicodeDecodeError:
        # 最后的手段：使用错误处理
        return bytes_data.decode('utf-8', errors='replace')


class CLIToolExecutor(ToolExecutor):
    """CLI 工具执行器"""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        timeout: int = 30,
    ):
        self.command = command
        self.args = args or []
        self.timeout = timeout

    def _format_arg(self, arg: str, **kwargs: Any) -> str:
        """格式化参数，替换占位符"""
        result = arg
        for key, value in kwargs.items():
            placeholder = f"{{{key}}}"
            if placeholder in result:
                result = result.replace(placeholder, str(value))
        return result

    async def execute(self, **kwargs: Any) -> Any:
        import shlex

        # 构建命令
        cmd_parts = [self.command]
        cmd_parts.extend(self._format_arg(arg, **kwargs) for arg in self.args)

        cmd_str = " ".join(shlex.quote(p) for p in cmd_parts)
        logger.info("Executing CLI tool: %s", cmd_str)

        # 执行命令
        proc = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"Command timed out after {self.timeout}s")

        # 始终返回包含 stdout、stderr 和 returncode 的结构化结果
        # 这样可以保持与 OpenAI tool calling 标准的兼容性
        result = {
            "stdout": safe_decode(stdout),
            "stderr": safe_decode(stderr),
            "returncode": proc.returncode,
            "success": proc.returncode == 0
        }

        # 如果输出是 JSON，也提供解析后的版本
        try:
            result["json"] = json.loads(safe_decode(stdout))
        except json.JSONDecodeError:
            pass

        return result


class BashToolExecutor(ToolExecutor):
    """Bash 工具执行器"""

    def __init__(
        self,
        shell_command: str,
        shell: str = "bash",
        timeout: int = 30,
    ):
        self.shell_command = shell_command
        self.shell = shell
        self.timeout = timeout

    async def execute(self, **kwargs: Any) -> Any:
        import shlex

        # 替换命令中的占位符
        formatted_command = self.shell_command
        for key, value in kwargs.items():
            placeholder = f"{{{key}}}"
            if placeholder in formatted_command:
                formatted_command = formatted_command.replace(placeholder, str(value))

        logger.info("Executing bash tool: %s", formatted_command)

        # 使用指定的shell执行命令
        # 在Windows上，如果shell是"bash"，可能需要使用"wsl bash -c"或其他方式
        # 这里简化处理，假设shell在PATH中
        proc = await asyncio.create_subprocess_shell(
            formatted_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,  # 使用shell执行
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"Bash command timed out after {self.timeout}s")

        # 返回结构化结果
        result = {
            "stdout": safe_decode(stdout),
            "stderr": safe_decode(stderr),
            "returncode": proc.returncode,
            "success": proc.returncode == 0
        }

        # 如果输出是JSON，也提供解析后的版本
        try:
            result["json"] = json.loads(safe_decode(stdout))
        except json.JSONDecodeError:
            pass

        return result


def create_cli_tool(
    name: str,
    description: str,
    command: str,
    args: list[str] | None = None,
    parameters: dict[str, Any] | None = None,
    timeout: int = 30,
) -> Tool:
    """创建 CLI 工具"""
    return Tool(
        id=name,
        name=name,
        description=description,
        parameters=parameters or {"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.CLI,
            command=command,
            args=args or [],
            timeout=timeout,
        ),
        metadata=ToolMetadata(),
    )


def create_bash_tool(
    name: str,
    description: str,
    shell_command: str,
    parameters: dict[str, Any] | None = None,
    shell: str = "bash",
    timeout: int = 30,
    category: str = "shell",
    tags: list[str] | None = None,
) -> Tool:
    """创建 Bash 工具"""
    if tags is None:
        tags = ["bash", "shell"]

    return Tool(
        id=name,
        name=name,
        description=description,
        parameters=parameters or {"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.BASH,
            shell_command=shell_command,
            shell=shell,
            timeout=timeout,
        ),
        metadata=ToolMetadata(
            category=category,
            tags=tags,
        ),
    )