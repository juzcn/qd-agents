"""
上下文管理器

负责构建和管理 LLM 调用的完整上下文：
- 系统提示词（分阶段）
- 会话历史记录
- 工具描述
"""
from __future__ import annotations

import logging
from typing import Any

from ..prompts import PromptLoader
from ..registry import Tool


logger = logging.getLogger(__name__)


class ContextManager:
    """
    上下文管理器

    统一管理 LLM 调用的上下文构建
    """

    def __init__(
        self,
        prompt_loader: PromptLoader | None = None,
    ):
        """
        初始化上下文管理器

        Args:
            prompt_loader: 提示词加载器
        """
        self.prompts = prompt_loader
        self._session_history: list[dict[str, str]] = []

    def add_to_history(self, role: str, content: str) -> None:
        """
        添加消息到会话历史

        Args:
            role: 角色 (user/assistant/system/tool)
            content: 内容
        """
        self._session_history.append({"role": role, "content": content})

    def clear_history(self) -> None:
        """清空会话历史"""
        self._session_history.clear()

    def get_history(self) -> list[dict[str, str]]:
        """获取会话历史"""
        return self._session_history.copy()

    def build_phase_one_messages(
        self,
        user_input: str,
        search_web_available: bool = False,
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        """
        构建第一阶段消息

        Args:
            user_input: 当前用户输入
            search_web_available: search.web 工具是否可用
            history: 会话历史（如果不传则使用内部存储的历史）

        Returns:
            完整的消息列表
        """
        if self.prompts:
            system_prompt = self.prompts.render(
                "phase_one",
                search_web_available=search_web_available,
            )
        else:
            # 回退到硬编码
            system_prompt = (
                "你是一个智能路由助手。"
                "你有三个工具可用：\n"
                "1. direct - 直接回答用户问题\n"
                "2. find_tools - 检索相关工具\n"
                "3. search.web - 网络搜索（如果可用）\n"
                "请选择合适的工具处理用户请求。"
            )

        return self._build_messages(
            system_prompt=system_prompt,
            user_input=user_input,
            history=history,
        )

    def build_phase_two_messages(
        self,
        user_input: str,
        found_tools: list[Tool],
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        """
        构建第二阶段消息

        Args:
            user_input: 当前用户输入
            found_tools: 检索到的工具列表
            history: 会话历史（如果不传则使用内部存储的历史）

        Returns:
            完整的消息列表
        """
        if self.prompts:
            system_prompt = self.prompts.render(
                "phase_two",
                tools=found_tools,
            )
        else:
            # 回退到硬编码
            tool_descriptions = "\n".join([
                f"- {t.name}: {t.description}"
                for t in found_tools
            ])
            system_prompt = (
                "你是一个智能规划助手。\n"
                f"可用工具：\n{tool_descriptions}\n"
                "你也可以使用 coding_tool_use 生成 Python 代码来编排多个工具。"
            )

        return self._build_messages(
            system_prompt=system_prompt,
            user_input=user_input,
            history=history,
        )

    def build_single_phase_messages(
        self,
        user_input: str,
        tools: list[Tool],
        search_web_available: bool = False,
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        """
        构建单阶段消息

        Args:
            user_input: 当前用户输入
            tools: 可用工具列表
            search_web_available: search.web 工具是否可用
            history: 会话历史（如果不传则使用内部存储的历史）

        Returns:
            完整的消息列表
        """
        if self.prompts:
            system_prompt = self.prompts.render(
                "system_prompt",
                tools=tools,
                search_web_available=search_web_available,
            )
        else:
            # 回退到硬编码
            if search_web_available:
                system_prompt = (
                    "你是一个智能助手，可以调用工具帮助用户。\n"
                    "如果用户的问题需要实时信息或外部知识，请优先使用 search.web 工具进行网络搜索。"
                )
            else:
                system_prompt = "你是一个智能助手，可以调用工具帮助用户。"

        return self._build_messages(
            system_prompt=system_prompt,
            user_input=user_input,
            history=history,
        )

    def _build_messages(
        self,
        system_prompt: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        """
        内部方法：构建完整消息列表

        格式：[system_prompt] + [history] + [current_user_input]

        Args:
            system_prompt: 系统提示词
            user_input: 当前用户输入
            history: 会话历史（可选）

        Returns:
            完整的消息列表
        """
        messages: list[dict[str, str]] = []

        # 1. 系统提示词
        messages.append({"role": "system", "content": system_prompt})

        # 2. 会话历史（使用传入的历史，或内部存储的历史）
        hist = history if history is not None else self._session_history
        if hist:
            messages.extend(hist)

        # 3. 当前用户输入
        messages.append({"role": "user", "content": user_input})

        return messages
