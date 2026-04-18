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
        self._cached_system_prompt: str | None = None  # 缓存的系统提示词
        self._cached_tools: list[Any] | None = None    # 缓存的工具列表
        self._tool_use_cache: dict[tuple, str] = {}    # 缓存工具调用提示词

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



    def build_tool_use_messages(
        self,
        user_input: str,
        tools: list[Tool],
        search_web_available: bool = False,
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        """
        构建工具调用消息（优化的单阶段提示词）

        Args:
            user_input: 当前用户输入
            tools: 可用工具列表
            search_web_available: search.web 工具是否可用
            history: 会话历史（如果不传则使用内部存储的历史）

        Returns:
            完整的消息列表
        """
        # 构建缓存键：使用工具ID列表和search_web_available
        tool_ids = tuple(sorted(tool.id for tool in tools))
        cache_key = (tool_ids, search_web_available)

        # 检查缓存
        if cache_key in self._tool_use_cache:
            system_prompt = self._tool_use_cache[cache_key]
            logger.debug("Using cached system prompt for tool_use")
        else:
            if self.prompts:
                system_prompt = self.prompts.render(
                    "tool_use",
                    tools=tools,
                    search_web_available=search_web_available,
                )
            else:
                # 回退到硬编码
                if search_web_available:
                    system_prompt = (
                        "你是一个智能助手，可以调用工具帮助用户。\n"
                        "如果用户的问题需要实时信息或外部知识，请优先使用 search.web 工具进行网络搜索。\n"
                        "注意：我们是在Windows环境下工作。"
                    )
                else:
                    system_prompt = "你是一个智能助手，可以调用工具帮助用户。\n注意：我们是在Windows环境下工作。"

            # 缓存结果
            self._tool_use_cache[cache_key] = system_prompt
            logger.debug(f"Cached system prompt for {len(tools)} tools, search_web_available={search_web_available}")

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
