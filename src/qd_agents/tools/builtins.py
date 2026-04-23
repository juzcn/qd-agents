"""
内置工具函数
"""
from datetime import datetime
from typing import Any


async def echo(message: str) -> dict[str, Any]:
    """回显消息"""
    return {"message": message, "timestamp": datetime.utcnow().isoformat()}
