"""
HTTP 工具管理命令

负责注册基于 OpenAPI 规范的 REST API 工具。
用法: http add NAME OPENAPI_URL [--filter METHOD:KEYWORD,...]
注册为壳工具，启动时从 spec 动态展开 subtools。
"""
import json
import logging
from pathlib import Path
from typing import Optional, List

import httpx
import yaml
from rich.console import Console

from qd_agents.config import load_config
from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType
from qd_agents.cli.utils.registry import get_tool_registry
from qd_agents.cli.utils.credentials import resolve_env_vars
from qd_agents.cli.utils.registration import register_tool_and_report

logger = logging.getLogger(__name__)


def http_add(
    console: Console,
    name: str,
    openapi_url: str,
    default: bool = False,
    filter_str: Optional[str] = None,
    extra_env: Optional[List[str]] = None,
    timeout: int = 30,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
    interactive: bool = True,
) -> None:
    """注册 OpenAPI 规范的 REST API 壳工具。"""
    # 1. 验证 OpenAPI 文档可访问
    spec = _fetch_openapi_spec(openapi_url)
    if not spec:
        console.print(f"[red][ERROR][/] 无法获取 OpenAPI 文档: {openapi_url}")
        return

    # 2. 提取 base_url 和安全方案
    base_url = _extract_base_url(spec, openapi_url)
    security_schemes = _extract_security_schemes(spec)

    # 3. 解析 filter
    filters = _parse_filter(filter_str) if filter_str else None

    # 4. 统计匹配的 endpoint 数量
    endpoints = _parse_endpoints(spec, filters)
    if not endpoints:
        console.print("[yellow]  未找到匹配的 endpoint[/]")
        return

    # 5. 处理环境变量
    env_names = extra_env or []
    env_dict: dict[str, str] = {}
    if env_names:
        resolved, _ = resolve_env_vars(env_names, console, base_dir=base_dir, interactive=interactive)
        env_dict.update(resolved)

    # 6. 为安全方案补充环境变量
    for scheme_name, scheme in security_schemes.items():
        scheme_type = scheme.get("type", "")
        if scheme_type == "apiKey":
            env_key = f"{name.upper()}_API_KEY"
            env_names.append(env_key)
            resolved_val = resolve_env_vars([env_key], console, base_dir=base_dir, interactive=interactive)[0]
            env_dict.update(resolved_val)
        elif scheme_type == "http" and scheme.get("scheme") == "bearer":
            env_key = f"{name.upper()}_BEARER_TOKEN"
            env_names.append(env_key)
            resolved_val = resolve_env_vars([env_key], console, base_dir=base_dir, interactive=interactive)[0]
            env_dict.update(resolved_val)
        elif scheme_type == "oauth2":
            env_key = f"{name.upper()}_TOKEN"
            env_names.append(env_key)
            resolved_val = resolve_env_vars([env_key], console, base_dir=base_dir, interactive=interactive)[0]
            env_dict.update(resolved_val)

    # 7. 注册壳工具（与 MCP 同模式，存 openapi_url + filter + auth）
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
            headers=_build_headers(security_schemes, env_dict),
            timeout=timeout,
            env=env_dict,
            openapi_url=openapi_url,
            openapi_filter=filter_str,
        ),
        scope="default" if default else "user",
        metadata=ToolMetadata(
            tags=["http", name],
        ),
    )

    register_tool_and_report(tool, console, base_dir=base_dir, config_file=config_file)
    console.print(f"  Base URL: {base_url}")
    console.print(f"  Endpoints: {len(endpoints)}")
    if filter_str:
        console.print(f"  过滤器: {filter_str}")
    if env_names:
        console.print(f"  所需环境变量: {', '.join(env_names)}")


def _parse_filter(filter_str: str) -> list[tuple[Optional[str], str]]:
    """解析过滤器字符串，格式: METHOD:KEYWORD,METHOD:KEYWORD 或 KEYWORD,KEYWORD。
    返回 [(method_or_None, keyword), ...]
    """
    filters = []
    for part in filter_str.split(","):
        part = part.strip()
        if ":" in part:
            method, keyword = part.split(":", 1)
            filters.append((method.lower(), keyword.lower()))
        else:
            filters.append((None, part.lower()))
    return filters


def _match_filter(method: str, path: str, filters: list[tuple[Optional[str], str]]) -> bool:
    """检查 endpoint 是否匹配过滤器。"""
    for filter_method, keyword in filters:
        if keyword in path.lower():
            if filter_method is None or filter_method == method.lower():
                return True
    return False


# --- OpenAPI 解析工具函数 ---


def fetch_openapi_spec(url: str) -> Optional[dict]:
    """公开接口：获取 OpenAPI 文档。"""
    return _fetch_openapi_spec(url)


def _fetch_openapi_spec(url: str) -> Optional[dict]:
    """获取 OpenAPI 文档。"""
    return _try_fetch(url)


def _try_fetch(url: str) -> Optional[dict]:
    """尝试下载并解析 OpenAPI 文档。"""
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        content = resp.text
        if url.endswith(".json") or content.strip().startswith("{"):
            return json.loads(content)
        return yaml.safe_load(content)
    except Exception:
        return None


def extract_base_url(spec: dict, fallback_url: str) -> str:
    """公开接口：提取 base URL。"""
    return _extract_base_url(spec, fallback_url)


def _extract_base_url(spec: dict, fallback_url: str) -> str:
    """从 OpenAPI spec 提取 base URL。"""
    servers = spec.get("servers", [])
    if servers:
        return servers[0].get("url", fallback_url)

    host = spec.get("host")
    base_path = spec.get("basePath", "")
    schemes = spec.get("schemes", ["https"])
    if host:
        return f"{schemes[0]}://{host}{base_path}"

    from urllib.parse import urlparse
    parsed = urlparse(fallback_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _extract_security_schemes(spec: dict) -> dict:
    """提取安全方案。"""
    components = spec.get("components", {})
    schemes = components.get("securitySchemes", {})
    if not schemes:
        schemes = spec.get("securityDefinitions", {})
    return schemes


def parse_endpoints(spec: dict, filters: Optional[list[tuple[Optional[str], str]]] = None) -> list[dict]:
    """公开接口：解析所有 endpoint。"""
    return _parse_endpoints(spec, filters)


def _parse_endpoints(spec: dict, filters: Optional[list[tuple[Optional[str], str]]] = None) -> list[dict]:
    """解析所有 endpoint，可选按过滤器过滤。"""
    endpoints = []
    paths = spec.get("paths", {})

    for path, path_item in paths.items():
        path_params = _resolve_params(path_item.get("parameters", []))

        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if not operation:
                continue

            # 过滤
            if filters and not _match_filter(method, path, filters):
                continue

            operation_id = operation.get("operationId") or f"{method}_{path.replace('/', '_').strip('_')}"
            summary = operation.get("summary", "")
            parameters = _build_parameters_schema(operation, path_params)

            endpoints.append({
                "method": method,
                "path": path,
                "operation_id": operation_id,
                "summary": summary,
                "parameters": parameters,
            })

    return endpoints


def _resolve_params(params: list) -> list[dict]:
    """解析参数列表，处理 $ref 引用。"""
    resolved = []
    for param in params:
        if isinstance(param, dict) and "name" in param:
            resolved.append(param)
    return resolved


def _build_parameters_schema(operation: dict, path_params: list[dict]) -> dict:
    """从 OpenAPI operation 构建 JSON Schema parameters。"""
    properties = {}
    required = []

    for param in path_params:
        name = param.get("name", "")
        if not name:
            continue
        properties[name] = _param_to_schema(param)
        if param.get("in") == "path" or param.get("required"):
            required.append(name)

    for param in _resolve_params(operation.get("parameters", [])):
        name = param.get("name", "")
        if not name:
            continue
        properties[name] = _param_to_schema(param)
        if param.get("required"):
            required.append(name)

    request_body = operation.get("requestBody")
    if request_body:
        content = request_body.get("content", {})
        json_content = content.get("application/json", {})
        schema = json_content.get("schema", {})
        if schema.get("type") == "object" and "properties" in schema:
            for prop_name, prop_schema in schema["properties"].items():
                properties[prop_name] = prop_schema
            for req in schema.get("required", []):
                required.append(req)
        else:
            properties["body"] = schema
            if request_body.get("required"):
                required.append("body")

    result = {"type": "object", "properties": properties}
    if required:
        result["required"] = required
    return result


def _param_to_schema(param: dict) -> dict:
    """将 OpenAPI parameter 转为 JSON Schema property。"""
    schema = param.get("schema", {"type": "string"})
    desc = param.get("description", "")
    if desc:
        schema["description"] = desc
    return schema


def _build_headers(security_schemes: dict, env_dict: dict) -> dict[str, str]:
    """根据安全方案构建请求头。"""
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
    return headers
