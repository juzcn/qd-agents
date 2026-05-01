"""工具注册 builtin function — 供 LLM 直接调用管理工具箱

注册为 function 类型工具，LLM 可通过调用这些函数来自主管理工具箱。
"""

from __future__ import annotations

import logging
from typing import Any

from .register import (
    register_cli_tool,
    register_http_tool,
    register_mcp_tool,
    register_skill_tool,
)

logger = logging.getLogger(__name__)


async def tool_register_cli(name: str, command: str, extra_env: list[str] | None = None, timeout: int = 300, default: bool = False) -> dict[str, Any]:
    """注册 CLI 工具。command 为完整命令行（如 "qd-agents memory list"）。"""
    try:
        tool = register_cli_tool(name=name, command=command, extra_env=extra_env, timeout=timeout, default=default)
        return {"success": True, "tool_id": tool.id, "name": tool.name, "description": tool.description}
    except Exception as e:
        logger.error("tool_register_cli 失败: %s", e)
        return {"success": False, "error": str(e)}


async def tool_register_mcp(server: str, default: bool = False) -> dict[str, Any]:
    """注册 MCP 服务器工具。server 为服务器名（从 tools/mcp/<server>.json 读取配置）。"""
    try:
        tool = register_mcp_tool(server=server, default=default)
        return {"success": True, "tool_id": tool.id, "name": tool.name, "description": tool.description}
    except Exception as e:
        logger.error("tool_register_mcp 失败: %s", e)
        return {"success": False, "error": str(e)}


async def tool_register_skill(skill_name: str, extra_env: list[str] | None = None, default: bool = False) -> dict[str, Any]:
    """注册 Skill 工具。skill_name 为 skill 目录名（在 tools/skills/ 下）。"""
    try:
        tool = register_skill_tool(skill_name=skill_name, extra_env=extra_env, default=default)
        return {"success": True, "tool_id": tool.id, "name": tool.name, "description": tool.description}
    except Exception as e:
        logger.error("tool_register_skill 失败: %s", e)
        return {"success": False, "error": str(e)}


async def tool_register_http(name: str, openapi_url: str, filter_str: str | None = None, extra_env: list[str] | None = None, timeout: int = 30, default: bool = False) -> dict[str, Any]:
    """注册 HTTP/OpenAPI 工具。openapi_url 为 OpenAPI spec URL。"""
    try:
        tool = register_http_tool(name=name, openapi_url=openapi_url, filter_str=filter_str, extra_env=extra_env, timeout=timeout, default=default)
        return {"success": True, "tool_id": tool.id, "name": tool.name, "description": tool.description}
    except Exception as e:
        logger.error("tool_register_http 失败: %s", e)
        return {"success": False, "error": str(e)}


# OpenAI function calling schema 定义
TOOL_REGISTER_FUNCTIONS = [
    {
        "type": "function",
        "function": {
            "name": "tool_register_cli",
            "description": "注册 CLI 工具。command 为完整命令行（如 'qd-agents memory list'）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "工具名"},
                    "command": {"type": "string", "description": "完整命令行"},
                    "extra_env": {"type": "array", "items": {"type": "string"}, "description": "需要的环境变量名列表"},
                    "timeout": {"type": "integer", "description": "超时秒数", "default": 300},
                    "default": {"type": "boolean", "description": "是否为默认工具", "default": False},
                },
                "required": ["name", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_register_mcp",
            "description": "注册 MCP 服务器工具。server 为服务器名（从 tools/mcp/<server>.json 读取配置）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "MCP 服务器名"},
                    "default": {"type": "boolean", "description": "是否为默认工具", "default": False},
                },
                "required": ["server"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_register_skill",
            "description": "注册 Skill 工具。skill_name 为 skill 目录名（在 tools/skills/ 下）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "skill 目录名"},
                    "extra_env": {"type": "array", "items": {"type": "string"}, "description": "需要的环境变量名列表"},
                    "default": {"type": "boolean", "description": "是否为默认工具", "default": False},
                },
                "required": ["skill_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_register_http",
            "description": "注册 HTTP/OpenAPI 工具。openapi_url 为 OpenAPI spec URL。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "工具组名"},
                    "openapi_url": {"type": "string", "description": "OpenAPI spec URL"},
                    "filter_str": {"type": "string", "description": "endpoint 过滤器"},
                    "extra_env": {"type": "array", "items": {"type": "string"}, "description": "需要的环境变量名列表"},
                    "timeout": {"type": "integer", "description": "超时秒数", "default": 30},
                    "default": {"type": "boolean", "description": "是否为默认工具", "default": False},
                },
                "required": ["name", "openapi_url"],
            },
        },
    },
]


def register_builtin_function_tools(registry: Any) -> None:
    """将4个工具注册 function 注册到数据库（scope=builtin）。

    与 execute_bash 一致，作为核心 builtin 工具持久化到 ToolRegistry。
    """
    from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolExecutionType, ToolMetadata

    MODULE = "qd_agents.tools.builtin_register"

    tools = [
        Tool(
            id="builtin.tool_register_cli",
            name="tool_register_cli",
            description="注册 CLI 工具。command 为完整命令行（如 'qd-agents memory list'）。",
            parameters=TOOL_REGISTER_FUNCTIONS[0]["function"]["parameters"],
            execution=ToolExecutionConfig(
                type=ToolExecutionType.FUNCTION,
                module=MODULE,
                function="tool_register_cli",
            ),
            scope="builtin",
            metadata=ToolMetadata(tags=["builtin", "register", "cli"], version="0.1.0"),
        ),
        Tool(
            id="builtin.tool_register_mcp",
            name="tool_register_mcp",
            description="注册 MCP 服务器工具。server 为服务器名（从 tools/mcp/<server>.json 读取配置）。",
            parameters=TOOL_REGISTER_FUNCTIONS[1]["function"]["parameters"],
            execution=ToolExecutionConfig(
                type=ToolExecutionType.FUNCTION,
                module=MODULE,
                function="tool_register_mcp",
            ),
            scope="builtin",
            metadata=ToolMetadata(tags=["builtin", "register", "mcp"], version="0.1.0"),
        ),
        Tool(
            id="builtin.tool_register_skill",
            name="tool_register_skill",
            description="注册 Skill 工具。skill_name 为 skill 目录名（在 tools/skills/ 下）。",
            parameters=TOOL_REGISTER_FUNCTIONS[2]["function"]["parameters"],
            execution=ToolExecutionConfig(
                type=ToolExecutionType.FUNCTION,
                module=MODULE,
                function="tool_register_skill",
            ),
            scope="builtin",
            metadata=ToolMetadata(tags=["builtin", "register", "skill"], version="0.1.0"),
        ),
        Tool(
            id="builtin.tool_register_http",
            name="tool_register_http",
            description="注册 HTTP/OpenAPI 工具。openapi_url 为 OpenAPI spec URL。",
            parameters=TOOL_REGISTER_FUNCTIONS[3]["function"]["parameters"],
            execution=ToolExecutionConfig(
                type=ToolExecutionType.FUNCTION,
                module=MODULE,
                function="tool_register_http",
            ),
            scope="builtin",
            metadata=ToolMetadata(tags=["builtin", "register", "http"], version="0.1.0"),
        ),
    ]

    for tool in tools:
        registry.register(tool)
        logger.info("Registered builtin function tool: %s", tool.id)
