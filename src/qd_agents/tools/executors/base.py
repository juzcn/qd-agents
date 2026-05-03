"""
工具执行器基类

定义所有执行器的公共接口和抽象基类。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any


logger = logging.getLogger(__name__)


class ToolExecutor(ABC):
    """工具执行器基类"""

    @abstractmethod
    async def execute(self, tool_input: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        """执行工具

        Args:
            tool_input: 工具输入参数字典
            **kwargs: 额外参数（如 tool 对象）
        """
        pass