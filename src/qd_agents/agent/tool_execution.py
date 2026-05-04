"""
工具执行辅助函数

工具执行、SKILL 查找、结果格式化等逻辑。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..models.tool import Tool, ToolExecutionType
from ..registry import ToolRegistry
from ..tools import ToolExecutorRegistry

logger = logging.getLogger(__name__)


async def execute_tool(
    tool_name: str,
    tool_input: dict,
    tool_map: dict[str, Tool],
    registry: ToolRegistry | None = None,
    executor_registry: ToolExecutorRegistry | None = None,
    expanded_tools: list[Tool] | None = None,
) -> str:
    """执行单个工具调用并返回结果字符串"""
    tool = tool_map.get(tool_name)

    if not tool and registry:
        tool = registry.get(tool_name) or registry.get_by_name(tool_name)

    if not tool:
        return f"工具未找到: {tool_name}"

    if not executor_registry:
        return f"工具执行器不可用: {tool_name}"

    try:
        logger.info("Executing tool: %s (id: %s)", tool.name, tool.id)
        executor = executor_registry.get_executor(tool)

        # 当执行 execute_bash 时，合并 SKILL 工具的 env
        if tool_name == "execute_bash" and expanded_tools and hasattr(executor, 'env'):
            skill_env = {}
            for t in expanded_tools:
                if t.execution.type == ToolExecutionType.SKILL and t.execution.env:
                    skill_env.update(t.execution.env)
            if skill_env:
                executor.env = {**(executor.env or {}), **skill_env}

        if tool.execution.type == ToolExecutionType.MCP:
            tool_input_with_name = {"tool_name": tool.name, **tool_input}
            tool_result = await executor.execute(**tool_input_with_name)
        else:
            tool_result = await executor.execute(**tool_input)
        return format_tool_result(tool_result)
    except Exception as e:
        logger.exception("Tool execution failed")
        return f"工具调用失败: {e}"


def find_skill_tool(
    tool_name: str,
    tool_map: dict[str, Tool],
    registry: ToolRegistry | None = None,
) -> Tool | None:
    """检查工具名是否对应一个 SKILL 工具"""
    tool = tool_map.get(tool_name)
    if tool and tool.execution.type == ToolExecutionType.SKILL:
        return tool
    if registry:
        tool = registry.get(tool_name) or registry.get_by_name(tool_name)
        if tool and tool.execution.type == ToolExecutionType.SKILL:
            return tool
    return None


def ensure_bash_available(
    registry: ToolRegistry,
    executor_registry: ToolExecutorRegistry | None = None,
) -> None:
    """确保 execute_bash 在 executor_registry 中有注册的执行器

    UseToolAgent 和 FindToolsAgent 在构建工具列表时调用此函数。
    """
    if not executor_registry:
        return

    bash_tool = registry.get("execute_bash")
    if bash_tool and "execute_bash" not in executor_registry._functions:
        from ..tools.executors.bash import BashToolExecutor
        executor_registry.register_executor(
            bash_tool.id,
            BashToolExecutor(
                shell_command="",
                shell="bash",
                timeout=bash_tool.execution.timeout,
                env=bash_tool.execution.env,
            ),
        )
        logger.info("Registered execute_bash executor")


def format_tool_result(tool_result: Any) -> str:
    """将工具执行结果格式化为字符串"""
    if isinstance(tool_result, str):
        return tool_result
    if hasattr(tool_result, "text"):
        return tool_result.text
    if isinstance(tool_result, list):
        text_parts = []
        for item in tool_result:
            if hasattr(item, "text"):
                text_parts.append(item.text)
            elif hasattr(item, "type") and getattr(item, "type", None) == "text":
                text_parts.append(getattr(item, "text", str(item)))
            else:
                text_parts.append(str(item))
        return "\n\n".join(text_parts) if text_parts else ""
    try:
        return json.dumps(tool_result, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(tool_result)


def resolve_tool_map(
    tool_names: list[str],
    expanded_tool_map: dict[str, Tool],
    registry: ToolRegistry,
) -> dict[str, Tool]:
    """从工具名列表解析出 tool_map，自动展开 MCP 壳工具为 subtool

    查找顺序：
    1. expanded_tool_map 直接匹配（subtool 名）
    2. expanded_tool_map 中 MCP 工具的 server 字段匹配（壳工具名 → 展开所有 subtool）
    3. registry 查找（DB 存储）

    Args:
        tool_names: 工具名列表
        expanded_tool_map: 展开后的工具映射（含 MCP subtools）
        registry: 工具注册表

    Returns:
        工具名 → Tool 对象映射
    """
    tool_map: dict[str, Tool] = {}
    for name in tool_names:
        # 1. 直接匹配 subtool 名
        if name in expanded_tool_map:
            tool_map[name] = expanded_tool_map[name]
            continue

        # 2. 壳工具名匹配：展开该 server 下所有 subtool
        found = False
        for tname, tool in expanded_tool_map.items():
            if (tool.execution.type == ToolExecutionType.MCP
                    and tool.execution.server == name):
                tool_map[tname] = tool
                found = True
        if found:
            continue

        # 3. 从 registry 查找
        tool = registry.get(name) or registry.get_by_name(name)
        if tool:
            tool_map[name] = tool
        else:
            logger.warning("Tool not found: %s (checked expanded_tool_map and registry)", name)

    return tool_map


def build_tools_detail_section(
    tools: list[Tool],
    context: Any = None,
) -> str:
    """构建 tools_detail_section，包含参数 schema 和 SKILL.md

    Args:
        tools: 工具列表
        context: ContextManager 实例（用于加载 SKILL.md）

    Returns:
        渲染后的工具详情字符串
    """
    from ..context.manager import format_tools_markdown
    from ..models.tool import ToolExecutionType

    parts: list[str] = []
    for t in tools:
        parts.append(format_tools_markdown([t], detail=True))
        # skill 工具追加 SKILL.md 内容
        if t.execution and t.execution.type == ToolExecutionType.SKILL:
            skill_md = ""
            if context and hasattr(context, "_load_skill_md"):
                skill_md = context._load_skill_md(t.local_path or t.name) or ""
            if skill_md:
                parts.append(f"\n### {t.name} SKILL.md\n\n{skill_md}")
    return "\n".join(parts)