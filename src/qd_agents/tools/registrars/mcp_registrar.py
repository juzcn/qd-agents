"""MCP 服务器工具注册"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolExecutionType, ToolMetadata
from qd_agents.tools.env import resolve_env_vars_noninteractive
from qd_agents.tools.version import detect_package_version, detect_version_simple
from qd_agents.tools.errors import ToolNotFoundError
from qd_agents.tools.registrars.base import save_tool

logger = logging.getLogger(__name__)


def register_mcp_tool(
    server: str,
    *,
    default: bool = False,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> Tool:
    """注册 MCP 服务器工具（纯逻辑）。

    Args:
        server: MCP 服务器名（从 tools/mcp/<server>.json 读取配置）
        default: 是否为默认工具

    Returns:
        注册后的 Tool 对象
    """
    from qd_agents.tools.executors.mcp import extract_mcp_servers_config

    json_file = Path("tools/mcp") / f"{server}.json"
    if not json_file.exists():
        raise ToolNotFoundError(f"MCP 配置文件不存在: {json_file}")

    with open(json_file, "r", encoding="utf-8") as f:
        json_config = json.load(f)

    servers, first_server_name = extract_mcp_servers_config(json_config)

    if servers:
        if server in servers:
            extracted_config = servers[server]
            config_server_name = server
        elif first_server_name:
            extracted_config = servers[first_server_name]
            config_server_name = first_server_name
        else:
            raise ValueError("JSON 文件中未找到服务器配置")
    else:
        extracted_config = json_config
        config_server_name = server

    final_name = config_server_name or server
    final_server = config_server_name or server
    final_transport = extracted_config.get("transport", "stdio")
    final_command = extracted_config.get("command")
    final_args = extracted_config.get("args")
    final_url = extracted_config.get("url")
    env_raw = extracted_config.get("env")

    # 环境变量
    env_names: list[str] = []
    env_fixed: dict[str, str] = {}
    if isinstance(env_raw, list):
        env_names = env_raw
    elif isinstance(env_raw, dict):
        for k, v in env_raw.items():
            env_fixed[k] = str(v)

    final_env: dict[str, str] = {}
    final_env.update(env_fixed)
    if env_names:
        final_env.update(resolve_env_vars_noninteractive(env_names, base_dir))

    final_env = final_env.copy()
    final_env["__mcp_config__"] = json.dumps(json_config, ensure_ascii=False)

    # 解析 args
    parsed_args: list[str] = []
    if final_args:
        if isinstance(final_args, list):
            parsed_args = final_args
        elif isinstance(final_args, str):
            try:
                parsed_args = json.loads(final_args)
                if not isinstance(parsed_args, list):
                    parsed_args = [final_args]
            except json.JSONDecodeError:
                parsed_args = [arg.strip() for arg in final_args.split(",") if arg.strip()]

    # 检测版本（仅对本地可执行文件，跳过 uvx/npx 等包运行器）
    version, install_source = None, None
    if final_command and parsed_args and final_command not in ("uvx", "npx", "pnpx", "bunx"):
        # 对包运行器使用完整检测，对本地可执行文件使用简单检测
        if final_command in ("uvx", "npx", "npm", "pip", "pnpx", "bunx"):
            version, install_source = detect_package_version(final_command, parsed_args)
        else:
            version, install_source = detect_version_simple(final_command, parsed_args)

    tool = Tool(
        id=f"mcp.{final_name}",
        name=final_name,
        description=f"MCP server: {final_server}",
        parameters={
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "要执行的 MCP 工具名称"},
                "arguments": {"type": "object", "description": "工具参数", "additionalProperties": True},
            },
            "required": ["tool_name", "arguments"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.MCP,
            server=final_server,
            transport=final_transport,
            command=final_command,
            args=parsed_args,
            url=final_url,
            env=final_env,
            timeout=30,
        ),
        scope="default" if default else "user",
        metadata=ToolMetadata(
            tags=["mcp", final_server],
            version=version,
            install_source=install_source,
        ),
        source_path=str(json_file),
    )

    return save_tool(tool, base_dir, config_file)


def extract_registration_args(tool: Tool) -> dict:
    """从已注册的 Tool 提取重注册所需的参数"""
    server = tool.execution.server or tool.name
    return {
        "server": server,
        "default": tool.scope == "default",
    }