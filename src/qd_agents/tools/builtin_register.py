"""工具注册 builtin function — 供 LLM 直接调用管理工具箱

注册为 function 类型工具，LLM 可通过调用这些函数来自主管理工具箱。
包含：5 个工具注册函数 + fetch + delegate + ask_user + context_summarizer + tools_list
"""
from __future__ import annotations

import ast
import inspect
import logging
import re
import types
from typing import Any, get_args, get_origin

from .registrars import (
    register_cli_tool,
    register_http_tool,
    register_mcp_tool,
    register_skill_tool,
)
from .search import fetch

logger = logging.getLogger(__name__)


# --- 工具注册函数 ---


async def tool_register_cli(name: str, command: str, extra_env: list[str] | None = None, timeout: int = 300, default: bool = False) -> dict[str, Any]:
    """注册 CLI 工具。name 为工具名称。command 为完整命令行（如 "qd-agents memory list"）。extra_env 为额外环境变量列表。timeout 为执行超时秒数。default 为是否设为默认工具。"""
    try:
        tool = register_cli_tool(name=name, command=command, extra_env=extra_env, timeout=timeout, default=default)
        return {"success": True, "tool_id": tool.id, "name": tool.name, "description": tool.description}
    except Exception as e:
        logger.error("tool_register_cli 失败: %s", e)
        return {"success": False, "error": str(e)}


async def tool_register_mcp(server: str, default: bool = False) -> dict[str, Any]:
    """注册 MCP 服务器工具。server 为服务器名（从 tools/mcp/<server>.json 读取配置）。default 为是否设为默认工具。"""
    try:
        tool = register_mcp_tool(server=server, default=default)
        return {"success": True, "tool_id": tool.id, "name": tool.name, "description": tool.description}
    except Exception as e:
        logger.error("tool_register_mcp 失败: %s", e)
        return {"success": False, "error": str(e)}


async def tool_register_skill(skill_name: str, extra_env: list[str] | None = None, default: bool = False) -> dict[str, Any]:
    """注册 Skill 工具。skill_name 为 skill 目录名（在 tools/skills/ 下）。extra_env 为额外环境变量列表。default 为是否设为默认工具。"""
    try:
        tool = register_skill_tool(skill_name=skill_name, extra_env=extra_env, default=default)
        return {"success": True, "tool_id": tool.id, "name": tool.name, "description": tool.description}
    except Exception as e:
        logger.error("tool_register_skill 失败: %s", e)
        return {"success": False, "error": str(e)}


async def tool_register_http(name: str, openapi_url: str, filter_str: str | None = None, extra_env: list[str] | None = None, timeout: int = 30, default: bool = False) -> dict[str, Any]:
    """注册 HTTP/OpenAPI 工具。name 为工具名称。openapi_url 为 OpenAPI spec URL。filter_str 为 API 路径过滤字符串。extra_env 为额外环境变量列表。timeout 为请求超时秒数。default 为是否设为默认工具。"""
    try:
        tool = register_http_tool(name=name, openapi_url=openapi_url, filter_str=filter_str, extra_env=extra_env, timeout=timeout, default=default)
        return {"success": True, "tool_id": tool.id, "name": tool.name, "description": tool.description}
    except Exception as e:
        logger.error("tool_register_http 失败: %s", e)
        return {"success": False, "error": str(e)}


# --- 代码注册工具 ---


_BLOCKED_NAMES = {"eval", "exec", "__import__", "open", "compile", "globals", "locals", "getattr", "setattr", "delattr", "os.system", "subprocess.call", "subprocess.run", "subprocess.Popen"}
_BLOCKED_ATTRS = {"system", "popen", "spawn", "call", "run", "Popen"}


def _validate_code_safety(code: str) -> list[str]:
    """使用 ast 验证代码安全性，返回发现的违规列表"""
    violations = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"代码语法错误: {e}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            if func_name in _BLOCKED_NAMES or func_name in _BLOCKED_ATTRS:
                violations.append(f"禁止的函数调用: {func_name}")

        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("os", "subprocess", "sys"):
                    violations.append(f"禁止的 import: {alias.name}")
        if isinstance(node, ast.ImportFrom):
            if node.module in ("os", "subprocess", "sys"):
                violations.append(f"禁止的 from import: {node.module}")

    return violations


async def tool_register_code(name: str, description: str, code: str, parameters_schema: dict[str, Any] | None = None, default: bool = False) -> dict[str, Any]:
    """通过上传 Python 代码动态注册一个新工具（需沙盒验证）。name 为工具名称。description 为工具描述。code 为完整的 Python 函数代码。parameters_schema 为参数 JSON schema。default 为是否设为默认工具。"""
    # 安全验证
    violations = _validate_code_safety(code)
    if violations:
        return {"success": False, "error": f"代码安全验证失败: {violations}"}

    # 验证代码包含函数定义
    try:
        tree = ast.parse(code)
        func_defs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) or isinstance(n, ast.AsyncFunctionDef)]
        if not func_defs:
            return {"success": False, "error": "代码必须包含至少一个函数定义"}
    except SyntaxError as e:
        return {"success": False, "error": f"代码语法错误: {e}"}

    # 当前版本暂不实现运行时注册，留待未来版本
    return {"success": False, "error": "tool_register_code 尚未完全实现，将在未来版本中支持动态代码注册", "name": name, "description": description}


# --- delegate 工具 ---


async def delegate(agent: str, task: str, task_background: str, tools: list[str]) -> dict[str, Any]:
    """调用子 Agent 执行任务。agent 为子 Agent 名称，必须根据工具箱概览判断：有功能直接匹配的专用工具填 Use-Tool，无专用工具填 Find-Tools。execute_bash 是通用执行工具不算功能匹配。Find-Tools 从网络搜索下载注册新工具，不在本地查找。task 为任务描述。task_background 为任务背景上下文，包含用户原始需求、对话关键信息、环境约束、前序步骤结果。tools 为需要使用的工具名列表，必须包含 execute_bash 和 ask_user。Find-Tools 还需包含搜索工具（如 google_search、fetch）和工具注册工具（如 tool_register_cli、tool_register_mcp）。"""
    # 实际路由由 MetaAgent.run_loop() 拦截处理
    # 此函数体仅作为占位，不会被执行
    return {"status": "routing", "agent": agent, "task": task}


# --- ask_user 工具 ---


async def ask_user(question: str, options: list[str] | None = None, reason: str = "", timeout_seconds: int | None = None) -> dict[str, Any]:
    """向用户提问并等待回复。question 为问题内容，不要在 question 中列出选项。options 为选项列表，提供时用户通过选择器选取，不要在 question 中重复列出选项。reason 为提问原因。timeout_seconds 为等待超时秒数。"""
    # 实际交互由 MetaAgent.run_loop() 拦截处理
    return {"status": "waiting_for_user", "question": question}


# --- context_summarizer 工具 ---


async def context_summarizer(focus: str = "", keep_recent: int = 20) -> dict[str, Any]:
    """主动总结对话历史，压缩上下文。focus 为总结关注点。keep_recent 为保留最近的消息数。"""
    # 实际压缩由 MetaAgent.run_loop() 拦截处理
    return {"status": "summarizing", "focus": focus}


# --- tools_list 工具 ---


async def tools_list(filter_scope: str | None = None) -> dict[str, Any]:
    """列出当前所有可用工具，按 scope 分组显示。filter_scope 为按范围过滤（builtin/default/user）。"""
    # 实际查询由 FunctionToolExecutor 执行，需要 registry 参数
    # 此函数体仅作为占位，实际执行时通过 registry 查询
    return {"status": "listing", "filter_scope": filter_scope}


# --- 所有 builtin function 工具函数列表 ---


_BUILTIN_FUNCTIONS = [
    tool_register_cli,
    tool_register_mcp,
    tool_register_skill,
    tool_register_http,
    tool_register_code,
    delegate,
    ask_user,
    context_summarizer,
    tools_list,
]

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

        # 默认值（None 不写入 JSON schema default）
        if param.default is not inspect.Parameter.empty and param.default is not None:
            prop["default"] = param.default
        elif param.default is inspect.Parameter.empty:
            required.append(pname)

        properties[pname] = prop

    # delegate 工具的 agent 参数添加 enum
    if func.__name__ == "delegate" and "agent" in properties:
        properties["agent"]["enum"] = ["Use-Tool", "Find-Tools", "Coding"]

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
    """将工具注册 function 和内置搜索/fetch 工具注册到数据库（scope=builtin）。

    与 execute_bash 一致，作为核心 builtin 工具持久化到 ToolRegistry。
    从函数签名自动生成 schema，避免手写重复。
    """
    from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolExecutionType, ToolMetadata

    DEFAULT_MODULE = "qd_agents.tools.builtin_register"
    SEARCH_MODULE = "qd_agents.tools.search"

    # 搜索/fetch 函数来自不同的模块
    search_funcs = {fetch}

    for func in _BUILTIN_FUNCTIONS:
        name = func.__name__
        doc = inspect.getdoc(func) or ""
        description = doc.split("。")[0] if doc else name
        parameters = _generate_openai_schema(func)

        module = SEARCH_MODULE if func in search_funcs else DEFAULT_MODULE

        tool = Tool(
            id=f"builtin.{name}",
            name=name,
            description=description,
            parameters=parameters,
            execution=ToolExecutionConfig(
                type=ToolExecutionType.FUNCTION,
                module=module,
                function=name,
            ),
            scope="builtin",
            metadata=ToolMetadata(
                tags=["builtin", name.split("_")[-1] if "_" in name else name],
                version="0.1.0",
            ),
        )

        registry.register(tool)
        logger.info("Registered builtin function tool: %s (module: %s)", tool.id, module)


