"""
HTTP 工具执行器

处理 HTTP/HTTPS 请求的工具执行器，支持认证和 base_url + path 拼接。
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import httpx

from .base import ToolExecutor
from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType


logger = logging.getLogger(__name__)


class HTTPToolExecutor(ToolExecutor):
    """HTTP 工具执行器"""

    def __init__(
        self,
        endpoint: str = "",
        method: str = "GET",
        headers: dict[str, str] | None = None,
        timeout: int = 30,
    ):
        self.endpoint = endpoint
        self.method = method.upper()
        self.headers = headers or {}
        self.timeout = timeout

    async def execute(self, tool_input: dict[str, Any] | None = None, **kwargs: Any) -> str:
        tool: Any = kwargs.get("tool")
        exec_config: ToolExecutionConfig | None = tool.execution if tool else None
        inp = tool_input or {}

        # 解析 URL
        url = self._resolve_url(exec_config, inp)

        # 构建请求头：静态 headers + 认证头
        headers = dict(exec_config.headers) if exec_config else dict(self.headers)
        if exec_config:
            self._inject_auth(headers, exec_config)

        # 解析方法
        method = (inp.get("method") or (exec_config.method if exec_config else None) or self.method).upper()

        # 构建请求参数
        request_kwargs: dict[str, Any] = {"headers": headers}
        if method in ("POST", "PUT", "PATCH"):
            body = inp.get("body")
            if body is None:
                # 兼容：如果没有 body 字段，把除保留字段外的参数作为 body
                reserved = {"endpoint", "method", "params", "body"}
                body = {k: v for k, v in inp.items() if k not in reserved}
            request_kwargs["json"] = body if body else {}
        else:
            params = inp.get("params")
            if params:
                request_kwargs["params"] = params

        logger.info("Executing HTTP tool: %s %s", method, url)

        async with httpx.AsyncClient(timeout=exec_config.timeout if exec_config else self.timeout) as client:
            response = await client.request(method=method, url=url, **request_kwargs)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                import json
                return json.dumps(response.json(), ensure_ascii=False)
            return response.text

    @staticmethod
    def _resolve_url(exec_config: ToolExecutionConfig | None, tool_input: dict) -> str:
        """解析请求 URL：base_url + endpoint 拼接，或直接用 endpoint"""
        path = tool_input.get("endpoint", "")

        if exec_config and exec_config.base_url:
            # base_url + path 拼接模式
            base = exec_config.base_url.rstrip("/")
            if path:
                if path.startswith("/"):
                    return base + path
                return base + "/" + path
            return base

        if exec_config and exec_config.endpoint:
            # 完整 endpoint 模式
            return exec_config.endpoint

        # 兜底：tool_input 中直接提供完整 URL
        if path and path.startswith(("http://", "https://")):
            return path

        return path or ""

    @staticmethod
    def _inject_auth(headers: dict[str, str], exec_config: ToolExecutionConfig) -> None:
        """根据 auth_type 注入认证头"""
        auth_type = exec_config.auth_type
        if not auth_type or auth_type == "none":
            return

        auth_env_key = exec_config.auth_env_key
        if not auth_env_key:
            return

        # 从 execution.env 中读取 token 值
        token = exec_config.env.get(auth_env_key, "")
        if not token:
            logger.warning("HTTP auth token not found for env key: %s", auth_env_key)
            return

        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {token}"
        elif auth_type == "api-key":
            headers["X-API-KEY"] = token


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
