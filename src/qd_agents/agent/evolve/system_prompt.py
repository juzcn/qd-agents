"""
EvolveAgent 系统提示词构建

让 agent 自感知：知道自己的工具、局限、进化能力。
"""
from __future__ import annotations

import logging
import platform
from typing import Any

from qd_agents.models.tool import Tool
from qd_agents.context.manager import format_tools_markdown

logger = logging.getLogger(__name__)


def build_system_prompt(
    tools: list[Tool],
    work_dir: str | None = None,
    prompt_loader: Any = None,
) -> str:
    """构建 EvolveAgent 的系统提示词

    优先使用 evolve.j2 模板，回退到硬编码。
    """
    tools_section = format_tools_markdown(tools, show_type_tag=True)
    os_info = f"{platform.system()} {platform.release()} ({platform.machine()})"
    work_dir = work_dir or "."

    # 尝试模板
    if prompt_loader:
        try:
            return prompt_loader.render(
                "evolve",
                tools_section=tools_section,
                os_info=os_info,
                work_dir=work_dir,
            )
        except Exception as e:
            logger.warning("Failed to render evolve.j2 template: %s", e)

    return _FALLBACK_PROMPT.format(
        tools_section=tools_section,
        os_info=os_info,
        work_dir=work_dir,
    )


_FALLBACK_PROMPT = """\
你是一个自主进化的 Agent。你能够感知自己的能力和局限，通过工具使用和工具注册不断成长。

## 你的能力

你可以使用以下工具来完成任务：

{tools_section}

## 你的局限

- 你的上下文窗口有限。当上下文接近上限时，系统会自动压缩历史对话，你可能会丢失早期细节。
- 你只能使用已注册的工具。如果缺少工具，你可以使用 tool_register_* 系列工具来注册新工具。

## 行为准则

1. **先思考，再行动**。制定计划后再调用工具。
2. **每次只做一步**，观察结果后再决定下一步。
3. **如果缺少工具**，先注册工具再使用。
4. **如果任务超出你的能力**，坦诚告知用户。
5. **完成任务后**，直接给出最终答案，不再调用工具。

## 运行环境

- 操作系统: {os_info}
- 工作目录: {work_dir}
- Shell: bash（Windows 环境下使用 Git Bash）
"""
