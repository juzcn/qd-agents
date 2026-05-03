"""
Bash 工具执行器

处理 shell 命令执行的工具执行器。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import os
import subprocess
import locale
from typing import Any

from .base import ToolExecutor
from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType


logger = logging.getLogger(__name__)


def safe_decode(bytes_data: bytes, encoding: str | None = None) -> str:
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
        return bytes_data.decode('utf-8', errors='replace')


class BashToolExecutor(ToolExecutor):
    """Bash 工具执行器"""

    def __init__(
        self,
        shell_command: str,
        shell: str = "bash",
        timeout: int = 30,
        env: dict[str, str] | None = None,
        use_exec: bool = False,
        command: str | None = None,
    ):
        self.shell_command = shell_command
        self.shell = shell
        self.timeout = timeout
        self.env = env
        self.use_exec = use_exec
        self.command = command

    async def execute(self, tool_input: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        import shlex

        # 合并 tool_input 到 kwargs（兼容旧调用方式）
        if tool_input:
            kwargs = {**tool_input, **kwargs}

        # 合并环境变量：当前进程环境 + 工具指定的额外环境变量
        process_env = None
        if self.env:
            process_env = {**os.environ, **self.env}

        # Windows 下子进程 stdout 默认 GBK 编码，强制 UTF-8
        if sys.platform == "win32":
            if process_env is None:
                process_env = {**os.environ}
            process_env["PYTHONUTF8"] = "1"

        if self.use_exec and self.command:
            # exec 模式：直接构建 argv，不经过 shell 解析
            cmd_parts = [sys.executable, self.command]
            args_json = json.dumps(kwargs, ensure_ascii=False)
            cmd_parts.append(args_json)
            executed_command = " ".join(cmd_parts)
            logger.info("Executing bash tool (exec mode): %s", executed_command)
            proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=process_env,
            )
        else:
            # 替换命令中的占位符
            formatted_command = self.shell_command
            for key, value in kwargs.items():
                placeholder = f"{{{key}}}"
                if placeholder in formatted_command:
                    formatted_command = formatted_command.replace(placeholder, str(value))

            executed_command = formatted_command

            # Windows: 检测 python 脚本 + JSON 参数模式，自动切换为 exec 模式
            if sys.platform == "win32" and not self.use_exec:
                json_argv_match = re.match(
                    r'(python\d*)\s+(\S+)\s+[\'"](\{.*\})[\'"]\s*$',
                    executed_command.strip(),
                )
                if json_argv_match:
                    script_path = json_argv_match.group(2)
                    json_arg = json_argv_match.group(3)
                    self.use_exec = True
                    self.command = script_path
                    executed_command = json_arg
                    logger.info("Windows: auto-switched to exec mode for JSON argument")

            logger.info("Executing bash tool: %s", executed_command)
            proc = await asyncio.create_subprocess_shell(
                formatted_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True,
                env=process_env,
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
        stdout_str = safe_decode(stdout)
        stderr_str = safe_decode(stderr)
        success = proc.returncode == 0

        if not success:
            logger.warning(
                "Bash tool execution failed (returncode=%d): %s\nstderr: %s",
                proc.returncode, executed_command, stderr_str[:500]
            )

        result = {
            "stdout": stdout_str,
            "stderr": stderr_str,
            "returncode": proc.returncode,
            "success": success
        }

        # 如果输出是JSON，也提供解析后的版本
        try:
            result["json"] = json.loads(stdout_str)
        except json.JSONDecodeError:
            pass

        return result


def create_bash_tool(
    name: str,
    description: str,
    shell_command: str,
    parameters: dict[str, Any] | None = None,
    shell: str = "bash",
    timeout: int = 30,
    scope: str = "user",
    tags: list[str] | None = None,
    version: str | None = None,
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
        scope=scope,
        metadata=ToolMetadata(
            tags=tags,
            version=version,
        ),
    )
