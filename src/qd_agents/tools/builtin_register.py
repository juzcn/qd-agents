"""工具注册 builtin function — 供 LLM 直接调用管理工具箱

注册为 function 类型工具，LLM 可通过调用这些函数来自主管理工具箱。
"""

from __future__ import annotations

import inspect
import logging
import re
import types
from typing import Any, get_args, get_origin

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


# 所有 builtin function 工具函数列表
_BUILTIN_FUNCTIONS = [tool_register_cli, tool_register_mcp, tool_register_skill, tool_register_http]

# Python 类型 → OpenAI schema 类型映射
_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _generate_openai_schema(func: Any) -> dict:
    """从函数签名自动生成 OpenAI function calling schema。

    从类型注解推导参数类型，从 docstring 提取函数描述和参数描述。
    使用 typing.get_type_hints 解析 from __future__ annotations 延迟求值的类型。
    """
    import typing

    sig = inspect.signature(func)
    hints = typing.get_type_hints(func, include_extras=True)
    doc = inspect.getdoc(func) or ""

    # 从 docstring 首行提取描述
    description = doc.split("。")[0] if doc else func.__name__

    # 从 docstring 提取参数描述 (格式: "参数名 为描述" 或 "参数名: 描述")
    param_descriptions: dict[str, str] = {}
    for line in doc.split("。"):
        m = re.match(r"(\w+)\s*[为:：]\s*(.+)", line.strip())
        if m:
            param_descriptions[m.group(1)] = m.group(2).strip()

    properties: dict[str, Any] = {}
    required: list[str] = []

    for pname, param in sig.parameters.items():
        if pname == "return":
            continue

        prop: dict[str, Any] = {}
        ann = hints.get(pname, param.annotation)

        if ann is inspect.Parameter.empty or isinstance(ann, str):
            prop["type"] = "string"
        elif isinstance(ann, types.UnionType):
            # X | None 语法 (Python 3.10+)
            args = get_args(ann)
            inner = next((a for a in args if a is not type(None)), str)
            prop.update(_resolve_type(inner))
        elif get_origin(ann) is list:
            prop["type"] = "array"
            args = get_args(ann)
            prop["items"] = {"type": _TYPE_MAP.get(args[0], "string")} if args else {"type": "string"}
        elif ann in _TYPE_MAP:
            prop["type"] = _TYPE_MAP[ann]
        else:
            prop["type"] = "string"

        # 参数描述
        if pname in param_descriptions:
            prop["description"] = param_descriptions[pname]

        # 默认值
        if param.default is not inspect.Parameter.empty:
            prop["default"] = param.default
        else:
            required.append(pname)

        properties[pname] = prop

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _resolve_type(ann: Any) -> dict[str, Any]:
    """将单个类型注解解析为 OpenAI schema 属性片段。"""
    if ann in _TYPE_MAP:
        return {"type": _TYPE_MAP[ann]}
    if get_origin(ann) is list:
        args = get_args(ann)
        return {
            "type": "array",
            "items": {"type": _TYPE_MAP.get(args[0], "string")} if args else {"type": "string"},
        }
    return {"type": "string"}


def register_builtin_function_tools(registry: Any) -> None:
    """将4个工具注册 function 注册到数据库（scope=builtin）。

    与 execute_bash 一致，作为核心 builtin 工具持久化到 ToolRegistry。
    从函数签名自动生成 schema，避免手写重复。
    """
    from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolExecutionType, ToolMetadata

    MODULE = "qd_agents.tools.builtin_register"

    for func in _BUILTIN_FUNCTIONS:
        name = func.__name__
        doc = inspect.getdoc(func) or ""
        description = doc.split("。")[0] if doc else name
        parameters = _generate_openai_schema(func)

        tool = Tool(
            id=f"builtin.{name}",
            name=name,
            description=description,
            parameters=parameters,
            execution=ToolExecutionConfig(
                type=ToolExecutionType.FUNCTION,
                module=MODULE,
                function=name,
            ),
            scope="builtin",
            metadata=ToolMetadata(tags=["builtin", "register", name.split("_")[-1]], version="0.1.0"),
        )

        registry.register(tool)
        logger.info("Registered builtin function tool: %s", tool.id)
