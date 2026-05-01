"""HTTP/OpenAPI 工具注册"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolExecutionType, ToolMetadata
from qd_agents.tools.env import resolve_env_vars_noninteractive
from qd_agents.tools.openapi import fetch_openapi_spec, parse_filter, parse_endpoints
from qd_agents.tools.errors import OpenAPISpecError, ToolValidationError
from qd_agents.tools.registrars.base import save_tool

logger = logging.getLogger(__name__)


def register_http_tool(
    name: str,
    openapi_url: str,
    *,
    filter_str: Optional[str] = None,
    extra_env: list[str] | None = None,
    timeout: int = 30,
    default: bool = False,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> Tool:
    """注册 HTTP/OpenAPI 工具（纯逻辑）。

    Args:
        name: 工具组名
        openapi_url: OpenAPI spec URL
        filter_str: 过滤字符串
        extra_env: 额外环境变量名列表
        timeout: 超时秒数
        default: 是否为默认工具

    Returns:
        注册后的 Tool 对象
    """
    # 获取 OpenAPI spec
    try:
        spec = fetch_openapi_spec(openapi_url)
    except ValueError as e:
        raise OpenAPISpecError(str(e))

    # 提取 base_url
    servers = spec.get("servers", [])
    base_url = servers[0].get("url", "") if servers else ""
    if not base_url:
        from urllib.parse import urlparse
        parsed = urlparse(openapi_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

    # 提取安全方案
    components = spec.get("components", {})
    security_schemes = components.get("securitySchemes", {}) or spec.get("securityDefinitions", {})

    # 解析 filter
    filters = parse_filter(filter_str) if filter_str else None

    # 统计匹配的 endpoint
    endpoints = parse_endpoints(spec, filters)
    if not endpoints:
        raise ToolValidationError("未找到匹配的 endpoint")

    # 环境变量
    env_names = list(extra_env or [])
    env_dict: dict[str, str] = {}
    for scheme_name, scheme in security_schemes.items():
        scheme_type = scheme.get("type", "")
        if scheme_type == "apiKey":
            env_key = f"{name.upper()}_API_KEY"
            env_names.append(env_key)
        elif scheme_type == "http" and scheme.get("scheme") == "bearer":
            env_key = f"{name.upper()}_BEARER_TOKEN"
            env_names.append(env_key)
        elif scheme_type == "oauth2":
            env_key = f"{name.upper()}_TOKEN"
            env_names.append(env_key)

    if env_names:
        env_dict = resolve_env_vars_noninteractive(env_names, base_dir)

    # 构建请求头
    headers = {"Content-Type": "application/json"}
    for scheme_name, scheme in security_schemes.items():
        scheme_type = scheme.get("type", "")
        if scheme_type == "apiKey" and scheme.get("in") == "header":
            header_name = scheme.get("name", "X-API-Key")
            env_key = next((k for k in env_dict if "API_KEY" in k.upper()), "")
            if env_key and env_dict.get(env_key):
                headers[header_name] = f"{{{env_key}}}"
        elif scheme_type == "http" and scheme.get("scheme") == "bearer":
            env_key = next((k for k in env_dict if "BEARER_TOKEN" in k.upper()), "")
            if env_key and env_dict.get(env_key):
                headers["Authorization"] = f"Bearer {{{env_key}}}"
        elif scheme_type == "oauth2":
            env_key = next((k for k in env_dict if k.upper().endswith("_TOKEN")), "")
            if env_key and env_dict.get(env_key):
                headers["Authorization"] = f"Bearer {{{env_key}}}"

    tool = Tool(
        id=f"http.{name}",
        name=name,
        description=f"REST API: {name} ({len(endpoints)} endpoints)",
        parameters={"type": "object", "properties": {}},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.HTTP,
            base_url=base_url,
            method="",
            path="",
            headers=headers,
            timeout=timeout,
            env=env_dict,
            openapi_url=openapi_url,
            openapi_filter=filter_str,
        ),
        scope="default" if default else "user",
        metadata=ToolMetadata(tags=["http", name]),
    )

    return save_tool(tool, base_dir, config_file)


def extract_registration_args(tool: Tool) -> dict:
    """从已注册的 Tool 提取重注册所需的参数"""
    return {
        "name": tool.name,
        "openapi_url": tool.execution.openapi_url or "",
        "filter_str": tool.execution.openapi_filter,
        "timeout": tool.execution.timeout,
        "default": tool.scope == "default",
    }