"""OpenAPI spec 获取与解析"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def fetch_openapi_spec(url: str, timeout: int = 15) -> dict:
    """获取并解析 OpenAPI spec，失败时抛出 ValueError。"""
    import httpx
    import yaml

    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        content = resp.text
        if url.endswith(".json") or content.strip().startswith("{"):
            return json.loads(content)
        return yaml.safe_load(content)
    except Exception as e:
        raise ValueError(f"无法获取 OpenAPI 文档: {url} ({e})")


def fetch_openapi_spec_safe(url: str, timeout: int = 15) -> dict | None:
    """获取并解析 OpenAPI spec，失败时返回 None。"""
    try:
        return fetch_openapi_spec(url, timeout)
    except Exception:
        return None


def parse_filter(filter_str: str) -> list[tuple[str | None, str]]:
    """解析 endpoint 过滤字符串，格式: METHOD:KEYWORD,KEYWORD"""
    filters: list[tuple[str | None, str]] = []
    for part in filter_str.split(","):
        part = part.strip()
        if ":" in part:
            method, keyword = part.split(":", 1)
            filters.append((method.lower(), keyword.lower()))
        else:
            filters.append((None, part.lower()))
    return filters


def parse_endpoints(
    spec: dict, filters: Optional[list[tuple[Optional[str], str]]] = None
) -> list[dict]:
    """从 OpenAPI spec 提取匹配的 endpoint 列表"""
    endpoints = []
    paths = spec.get("paths", {})

    for path, path_item in paths.items():
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if not operation:
                continue
            if filters and not match_filter(method, path, filters):
                continue
            operation_id = operation.get("operationId") or f"{method}_{path.replace('/', '_').strip('_')}"
            summary = operation.get("summary", "")
            endpoints.append({"method": method, "path": path, "operation_id": operation_id, "summary": summary})

    return endpoints


def match_filter(method: str, path: str, filters: list[tuple[Optional[str], str]]) -> bool:
    """检查 endpoint 是否匹配过滤器"""
    for filter_method, keyword in filters:
        if keyword in path.lower():
            if filter_method is None or filter_method == method.lower():
                return True
    return False
