"""
工具注册管理

负责自动注册内置工具。
"""

import sys
from pathlib import Path
from typing import Optional

from rich.console import Console

from qd_agents.registry import ToolRegistry






def auto_register_bash_tools(
    console: Console,
    tool_registry: ToolRegistry,
) -> None:
    """
    自动注册 Bash 工具（如果尚未注册）

    Args:
        console: Rich 控制台对象，用于输出信息
        tool_registry: 工具注册表
    """
    from qd_agents.tools.executor import create_bash_tool

    # 检查通用bash工具是否已存在
    if not tool_registry.get("bash.execute"):
        bash_tool = create_bash_tool(
            name="execute_bash",
            description="执行bash/shell命令，支持管道、重定向等shell特性",
            shell_command="{command}",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的bash/shell命令"},
                },
                "required": ["command"],
            },
            category="shell",
            tags=["bash", "shell", "command"],
        )
        tool_registry.register(bash_tool)
        console.print("[dim][OK] 已自动注册工具: bash.execute[/]", style="dim")