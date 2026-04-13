"""
短期记忆 - 当前会话消息列表
"""

from typing import List, Dict, Any, Optional
from collections import deque


class ShortTermMemory:
    """
    短期记忆：维护当前会话的消息历史
    支持滚动窗口和压缩
    """

    def __init__(self, max_messages: int = 50):
        self._messages: deque = deque(maxlen=max_messages)
        self._max_messages = max_messages

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """
        添加消息到短期记忆

        Args:
            role: 消息角色 (user, assistant, system, tool)
            content: 消息内容
            **kwargs: 其他元数据 (message_id, timestamp 等)
        """
        message = {
            "role": role,
            "content": content,
            **kwargs
        }
        self._messages.append(message)

    def add_user_message(self, content: str, message_id: Optional[str] = None) -> None:
        """添加用户消息"""
        self.add_message("user", content, message_id=message_id)

    def add_assistant_message(self, content: str, message_id: Optional[str] = None) -> None:
        """添加助手消息"""
        self.add_message("assistant", content, message_id=message_id)

    def add_system_message(self, content: str) -> None:
        """添加系统消息"""
        self.add_message("system", content)

    def add_tool_result(self, content: str, tool_name: Optional[str] = None) -> None:
        """添加工具执行结果"""
        self.add_message("tool", content, tool_name=tool_name)

    def get_history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        获取消息历史

        Args:
            limit: 返回的消息数量限制（从最近的开始）
        """
        messages = list(self._messages)
        if limit is not None:
            messages = messages[-limit:]
        return messages

    def get_recent(self, n: int = 10) -> List[Dict[str, Any]]:
        """获取最近的n条消息"""
        return list(self._messages)[-n:]

    def clear(self) -> None:
        """清空短期记忆"""
        self._messages.clear()

    @property
    def size(self) -> int:
        """当前消息数量"""
        return len(self._messages)
