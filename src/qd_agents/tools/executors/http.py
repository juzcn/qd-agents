"""
HTTP 工具执行器

处理 HTTP/HTTPS 请求的工具执行器。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .base import ToolExecutor
from qd_agents.registry import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType


logger = logging.getLogger(__name__)


class HTTPToolExecutor(ToolExecutor):
    """HTTP 工具执行器"""

    def __init__(
        self,
        endpoint: str,
        method: str = "POST",
        headers: dict[str, str] | None = None,
        timeout: int = 30,
    ):
        self.endpoint = endpoint
        self.method = method.upper()
        self.headers = headers or {}
        self.timeout = timeout

    async def execute(self, **kwargs: Any) -> Any:
        logger.info("Executing HTTP tool: %s %s", self.method, self.endpoint)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            request_kwargs: dict[str, Any] = {
                "headers": self.headers,
            }

            if self.method in ["POST", "PUT", "PATCH"]:
                request_kwargs["json"] = kwargs
            else:
                request_kwargs["params"] = kwargs

            response = await client.request(
                method=self.method,
                url=self.endpoint,
                **request_kwargs
            )

            response.raise_for_status()
            return response.json()


def create_http_tool(
    name: str,
    description: str,
    endpoint: str,
    method: str = "POST",
    parameters: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> Tool:
    """创建 HTTP 工具"""
    return Tool(
        id=name,
        name=name,
        description=description,
        parameters=parameters or {"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.HTTP,
            endpoint=endpoint,
            method=method,
            headers=headers or {},
            timeout=timeout,
        ),
        metadata=ToolMetadata(),
    )