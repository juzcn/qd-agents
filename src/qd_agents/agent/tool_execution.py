"""
工具执行辅助函数

从 EvolveAgent 中提取的工具执行、SKILL 注入、bash 可用性检查等逻辑。
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
    openai_tools: list[dict],
    tool_map: dict[str, Tool],
    registry: ToolRegistry | None = None,
) -> tuple[list[dict], dict[str, Tool]]:
    """确保 execute_bash 在 openai_tools 中可用（evolve agent 的元工具）"""
    existing_names = {t.get("function", {}).get("name") for t in openai_tools if "function" in t}
    if "execute_bash" in existing_names:
        return openai_tools, tool_map

    if registry:
        bash_tool = registry.get("execute_bash")
        if bash_tool and bash_tool.name not in existing_names:
            openai_tools.append(bash_tool.to_openai_function())
            tool_map[bash_tool.name] = bash_tool
            logger.info("Adding execute_bash to openai_tools (evolve meta-tool)")

    return openai_tools, tool_map


def inject_skill_into_system_prompt(
    messages: list[dict[str, Any]],
    skill_name: str,
    skill_md: str,
) -> list[dict[str, Any]]:
    """将 SKILL.md 注入系统提示词，返回更新后的 messages"""
    if not messages or messages[0].get("role") != "system":
        return messages

    skill_section = f"\n\n## 技能指南: {skill_name}\n\n{skill_md}"
    messages[0]["content"] = messages[0]["content"] + skill_section
    logger.info("System prompt updated: injected SKILL.md for %s (%d chars added)", skill_name, len(skill_section))
    logger.info("System prompt appended content:\n%s", skill_section)
    return messages


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