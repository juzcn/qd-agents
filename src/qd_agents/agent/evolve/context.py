"""
EvolveContextManager — 上下文窗口管理

Token 估算 + 阈值检测 + LLM 摘要压缩。
保留 system prompt + 最近 N 轮，中间历史用 LLM 生成摘要替换。
"""
from __future__ import annotations

import logging
from typing import Any

from qd_agents.llm import LLMClient

logger = logging.getLogger(__name__)


class EvolveContextManager:
    """管理对话上下文，防止超出窗口限制"""

    def __init__(
        self,
        llm: LLMClient,
        max_context_tokens: int = 100_000,
        compact_threshold: float = 0.8,
        recent_turns: int = 4,
    ):
        self.llm = llm
        self.max_tokens = max_context_tokens
        self.compact_threshold = compact_threshold
        self.recent_turns = recent_turns

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """估算当前 messages 的 token 数（字符数/3 近似）"""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        total += len(part.get("text", ""))
                    else:
                        total += len(str(part))
        return total // 3

    def maybe_compact(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """如果接近上限，对历史消息进行摘要压缩"""
        if self.estimate_tokens(messages) < self.max_tokens * self.compact_threshold:
            return messages
        logger.info(
            "Context approaching limit (%d/%d), compacting...",
            self.estimate_tokens(messages),
            self.max_tokens,
        )
        return self._compact(messages)

    def _compact(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """压缩策略：保留 system + 最近 N 轮，中间部分用 LLM 生成摘要"""
        if len(messages) <= 2:
            return messages

        system_msg = messages[0] if messages[0].get("role") == "system" else None
        rest = messages[1:] if system_msg else messages

        # 保留最近 N 轮（每轮 = user + assistant + 可能的 tool 消息）
        # 从后往前找 N 个 user 消息作为分割点
        user_indices = [i for i, m in enumerate(rest) if m.get("role") == "user"]
        split_idx = 0
        if len(user_indices) > self.recent_turns:
            split_idx = user_indices[-self.recent_turns]

        older = rest[:split_idx]
        recent = rest[split_idx:]

        if not older:
            return messages

        # 生成摘要
        summary = self._generate_summary(older)

        # 构建新 messages
        result: list[dict[str, Any]] = []
        if system_msg:
            result.append(system_msg)

        result.append({
            "role": "user",
            "content": f"[之前对话的摘要]\n{summary}",
        })
        result.append({
            "role": "assistant",
            "content": "了解，我会参考之前的对话摘要继续。",
        })

        result.extend(recent)
        return result

    def _generate_summary(self, messages: list[dict[str, Any]]) -> str:
        """用 LLM 生成摘要，失败时截断"""
        text = self._messages_to_text(messages)

        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 在已有事件循环中，无法 await，走截断
                return self._truncate(text)

            response = loop.run_until_complete(
                self.llm.chat(
                    messages=[
                        {"role": "system", "content": "请将以下对话历史压缩为简洁摘要，保留关键信息、决策和结论。"},
                        {"role": "user", "content": text},
                    ],
                    temperature=0.3,
                )
            )
            return response.choices[0].message.content or self._truncate(text)
        except Exception as e:
            logger.warning("LLM summary failed, falling back to truncation: %s", e)
            return self._truncate(text)

    @staticmethod
    def _truncate(text: str, max_chars: int = 2000) -> str:
        """截断回退"""
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n...(已截断)"

    @staticmethod
    def _messages_to_text(messages: list[dict[str, Any]]) -> str:
        """将消息列表转为纯文本"""
        parts = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in content
                )
            parts.append(f"[{role}] {content}")
        return "\n".join(parts)
