"""
Python 函数工具执行器

处理 Python 函数调用的工具执行器。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from .base import ToolExecutor
from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType


logger = logging.getLogger(__name__)


class FunctionToolExecutor(ToolExecutor):
    """Python 函数执行器"""

    def __init__(self, func: Callable):
        self.func = func

    async def execute(self, **kwargs: Any) -> Any:
        logger.info("Executing function tool: %s", self.func.__name__)

        if asyncio.iscoroutinefunction(self.func):
            return await self.func(**kwargs)
        else:
            return self.func(**kwargs)


def create_function_tool(
    name: str,
    description: str,
    func: Callable,
    parameters: dict[str, Any] | None = None,
) -> Tool:
    """创建 Python 函数工具"""
    return Tool(
        id=name,
        name=name,
        description=description,
        parameters=parameters or {"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module=func.__module__,
            function=func.__name__,
        ),
        metadata=ToolMetadata(),
    )