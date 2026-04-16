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
    async def execute(self, **kwargs: Any) -> Any:
        """执行工具"""
        pass