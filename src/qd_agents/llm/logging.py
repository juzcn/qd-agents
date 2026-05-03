"""LLM 日志记录器

将 LLMClient 的日志逻辑分离出来，使其专注于 API 调用。
"""
from __future__ import annotations

import logging
from typing import Any

from .formatters import format_messages_for_logging, tool_calls_to_dicts

logger = logging.getLogger(__name__)


class LLMLogger:
    """LLM 日志记录器 — 增量日志、系统提示词变化检测"""

    def __init__(self) -> None:
        self._meta_agent_message_counts: dict[str, int] = {}
        self._last_system_prompts: dict[str, str] = {}

    def reset_log_count(self, meta_agent_name: str, messages: list[dict[str, Any]] | None = None) -> None:
        """重置增量日志计数（新轮次开始时调用）

        首次调用（无 system prompt 缓存）时设为0，输出完整 prompt。
        后续调用时设为 len(messages)-1，只输出当前用户输入。
        """
        is_first_call = meta_agent_name not in self._last_system_prompts

        if messages and messages[0].get("role") == "system":
            self._last_system_prompts[meta_agent_name] = messages[0].get("content", "")

        if is_first_call:
            self._meta_agent_message_counts[meta_agent_name] = 0
        else:
            count = max(0, len(messages) - 1) if messages else 0
            self._meta_agent_message_counts[meta_agent_name] = count

    def log_prompt(self, messages: list[dict[str, Any]], meta_agent_name: str, is_stream: bool = False) -> None:
        """记录 LLM 输入消息（增量日志）

        只输出自上次记录以来的新增消息。
        SKILL 注入导致 system prompt 变化时，只输出 appended 的差异部分。
        """
        # 检测系统提示词变化 → 只输出 appended 差异，不重置计数
        if messages and messages[0].get("role") == "system":
            current_system = messages[0].get("content", "")
            last_system = self._last_system_prompts.get(meta_agent_name)
            if current_system != last_system and last_system is not None:
                if current_system.startswith(last_system):
                    appended = current_system[len(last_system):]
                    logger.info(
                        "LLM Prompt (MetaAgent: %s) [system prompt appended %d chars]:\n%s",
                        meta_agent_name, len(appended), appended,
                    )
                else:
                    logger.info(
                        "LLM Prompt (MetaAgent: %s) [system prompt changed]:\n%s",
                        meta_agent_name, current_system,
                    )
            self._last_system_prompts[meta_agent_name] = current_system

        logged_count = self._meta_agent_message_counts.get(meta_agent_name, 0)

        new_msg_count = len(messages) - logged_count
        prefix = "stream, " if is_stream else ""

        if new_msg_count > 0:
            formatted = format_messages_for_logging(messages, logged_count)
            logger.info(
                "LLM Prompt (%sMetaAgent: %s) [%d new messages]:\n%s",
                prefix, meta_agent_name, new_msg_count, formatted,
            )
            self._meta_agent_message_counts[meta_agent_name] = len(messages)
        else:
            logger.info("LLM Prompt (%sMetaAgent: %s): [no new messages]", prefix, meta_agent_name)

    def log_completion(self, response: Any, model: str, meta_agent_name: str) -> None:
        """记录 LLM 输出（非流式）"""
        if not response.choices:
            return

        message = response.choices[0].message
        completion_display: dict[str, Any] = {"role": "assistant"}
        if message.content:
            completion_display["content"] = message.content
        if hasattr(message, 'tool_calls') and message.tool_calls:
            completion_display["tool_calls"] = tool_calls_to_dicts(message.tool_calls)
        if not completion_display.get("content") and "tool_calls" not in completion_display:
            completion_display["content"] = "[no content or tool calls]"

        logger.info(
            "LLM Completion (MetaAgent: %s):\n%s",
            meta_agent_name,
            format_messages_for_logging([completion_display]),
        )

    def log_token_usage(self, usage: Any, model: str) -> None:
        """记录 token 使用情况"""
        if usage:
            logger.info(
                "Token usage - model: %s, prompt: %d, completion: %d, total: %d",
                model, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens,
            )