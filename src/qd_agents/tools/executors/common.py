"""
公共工具函数和常量

包含执行器模块共用的工具函数和常量。
"""
from __future__ import annotations

import json
from typing import Any


def try_parse_json(output: bytes) -> dict[str, Any] | None:
    """
    尝试解析JSON输出

    Args:
        output: 字节输出

    Returns:
        解析后的JSON字典，如果解析失败返回None
    """
    try:
        return json.loads(output.decode())
    except json.JSONDecodeError:
        return None


def format_command_string(cmd_parts: list[str]) -> str:
    """
    格式化命令字符串用于日志记录

    Args:
        cmd_parts: 命令部分列表

    Returns:
        格式化的命令字符串
    """
    import shlex
    return " ".join(shlex.quote(p) for p in cmd_parts)