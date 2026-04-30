"""
HTTP 工具管理命令

负责注册和管理 HTTP/REST API 工具（如 GitHub API 等）。
HTTP 工具通过 httpx 发送请求，支持认证（Bearer / API Key）和 base_url + path 拼接。
"""
import logging
import os
from pathlib import Path
from typing import Optional, List

from rich.console import Console

from qd_agents.config import load_config, load_runtime_config, save_runtime_config
from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType
from qd_agents.cli.utils.registry import get_tool_registry
from qd_agents.cli.utils.credentials import env_var_to_tool_name

logger = logging.getLogger(__name__)


def http_add(
    console: Console,
    name: str,
    url: str,
    method: str = "GET",
    headers: Optional[List[str]] = None,
    auth: str = "none",
    extra_env: Optional[List[str]] = None,
    timeout: int = 30,
    default: bool = False,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
    interactive: bool = True,
) -> None:
    """添加 HTTP 工具（REST API）"""
    # 解析 headers
    parsed_headers: dict[str, str] = {}
    if headers:
        for h in headers:
            if ":" in h:
                key, value = h.split(":", 1)
                parsed_headers[key.strip()] = value.strip()
            else:
                console.print(f"[yellow]警告: 忽略无效 header 格式（需 Key:Value）: {h}[/]")

    # 处理环境变量 + 认证
    env: dict[str, str] = {}
    auth_env_key: str | None = None
    runtime_changed = False

    if extra_env:
        runtime_config = load_runtime_config(base_dir=base_dir)
        # 第一个 env 变量作为认证 token 来源
        if auth != "none" and extra_env:
            auth_env_key = extra_env[0]

        for var in extra_env:
            tool_name = env_var_to_tool_name(var)
            api_key_value = runtime_config.tools_credentials.get_api_key(tool_name)
            if api_key_value:
                env[var] = api_key_value
                console.print(f"  [dim]{var}[/]: 从 runtime.json (tools_credentials.{tool_name}) 加载")
            else:
                if interactive:
                    console.print(f"  [yellow]{var}[/] 未在 runtime.json 中配置，请输入 API Key:")
                    api_key_input = input(f"  {var}=").strip()
                    if api_key_input:
                        env[var] = api_key_input
                        runtime_config.tools_credentials.set_api_key(tool_name, api_key_input)
                        runtime_changed = True
                        console.print(f"  [green]已将 {var} 写入 runtime.json (tools_credentials.{tool_name})[/]")
                    else:
                        env[var] = ""
                        console.print(f"  [yellow]警告: {var} 未设置，工具执行时可能失败[/]")
                else:
                    env[var] = os.environ.get(var, "")
        if runtime_changed:
            save_runtime_config(runtime_config, base_dir=base_dir)
            console.print("  [dim]runtime.json 已更新[/]")

    # 注册工具
    config = load_config(base_dir=base_dir, config_file=config_file)
    registry = get_tool_registry(config)

    tool = Tool(
        id=f"http.{name}",
        name=name,
        description=f"HTTP API: {url}",
        parameters={
            "type": "object",
            "properties": {
                "endpoint": {
                    "type": "string",
                    "description": "API 路径（如 /repos/owner/repo/contents/path）",
                },
                "method": {
                    "type": "string",
                    "description": "HTTP 方法",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                },
                "params": {
                    "type": "object",
                    "description": "查询参数",
                    "additionalProperties": True,
                },
                "body": {
                    "type": "object",
                    "description": "请求体（JSON）",
                    "additionalProperties": True,
                },
            },
            "required": ["endpoint"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.HTTP,
            base_url=url,
            method=method,
            headers=parsed_headers,
            auth_type=auth if auth != "none" else None,
            auth_env_key=auth_env_key,
            env=env,
            timeout=timeout,
        ),
        scope="default" if default else "user",
        metadata=ToolMetadata(
            tags=["http", name],
        ),
        dependencies={
            "auth_type": auth,
        },
    )

    tool_id = registry.register(tool)

    console.print(f"[green][OK][/] 已注册 HTTP 工具: {name} ({tool_id})")
    console.print(f"  Base URL: {url}")
    console.print(f"  默认方法: {method}")
    if auth != "none":
        console.print(f"  认证: {auth}" + (f" (via {auth_env_key})" if auth_env_key else ""))
    if parsed_headers:
        console.print(f"  自定义头: {', '.join(parsed_headers.keys())}")
    console.print(f"  超时: {timeout}s")
    if extra_env:
        console.print(f"  所需环境变量: {', '.join(extra_env)}")
