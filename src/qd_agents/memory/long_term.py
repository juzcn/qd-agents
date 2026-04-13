"""
长期记忆 - 用户档案、历史偏好
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field


@dataclass
class UserProfile:
    """用户档案"""
    user_id: str
    preferences: Dict[str, Any] = field(default_factory=dict)
    default_values: Dict[str, Any] = field(default_factory=dict)
    interaction_history: List[str] = field(default_factory=list)

    def set_preference(self, key: str, value: Any) -> None:
        """设置用户偏好"""
        self.preferences[key] = value

    def get_preference(self, key: str, default: Any = None) -> Any:
        """获取用户偏好"""
        return self.preferences.get(key, default)

    def set_default(self, key: str, value: Any) -> None:
        """设置默认值"""
        self.default_values[key] = value

    def get_default(self, key: str, default: Any = None) -> Any:
        """获取默认值"""
        return self.default_values.get(key, default)


class LongTermMemory:
    """
    长期记忆：用户档案、历史偏好、向量库（预留接口）
    """

    def __init__(self):
        self._user_profiles: Dict[str, UserProfile] = {}
        self._vector_store = None  # 预留：向量数据库接口

    def get_or_create_profile(self, user_id: str) -> UserProfile:
        """获取或创建用户档案"""
        if user_id not in self._user_profiles:
            self._user_profiles[user_id] = UserProfile(user_id=user_id)
        return self._user_profiles[user_id]

    def get_profile(self, user_id: str) -> Optional[UserProfile]:
        """获取用户档案"""
        return self._user_profiles.get(user_id)

    def recall_context(self, user_id: str, keywords: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        召回相关上下文信息

        Args:
            user_id: 用户ID
            keywords: 关键词列表（用于向量检索，预留）

        Returns:
            相关上下文字典
        """
        profile = self.get_profile(user_id)
        if profile is None:
            return {}

        return {
            "preferences": profile.preferences,
            "default_values": profile.default_values,
        }

    def store_fact(self, user_id: str, key: str, value: Any) -> None:
        """
        存储一个事实到长期记忆

        Args:
            user_id: 用户ID
            key: 事实键
            value: 事实值
        """
        profile = self.get_or_create_profile(user_id)
        profile.set_preference(key, value)

    def set_vector_store(self, vector_store: Any) -> None:
        """设置向量存储（预留接口）"""
        self._vector_store = vector_store
