"""
意图对象构建器
"""

from typing import Any, Dict, Optional, List
from .schema import Intent, Constraints, Dependency, Meta, generate_intent_id


class IntentBuilder:
    """意图构建器 - 流式API构建Intent对象"""

    def __init__(self):
        self._id: Optional[str] = None
        self._action: Optional[str] = None
        self._domain: Optional[str] = None
        self._parameters: Dict[str, Any] = {}
        self._constraints: Optional[Constraints] = None
        self._depends_on: Optional[Dependency] = None
        self._fallback: Optional[Intent] = None
        self._confidence: float = 0.9
        self._source_message_ids: List[str] = []
        self._user_id: Optional[str] = None
        self._session_id: Optional[str] = None

    def id(self, intent_id: str) -> "IntentBuilder":
        """设置意图ID"""
        self._id = intent_id
        return self

    def action(self, action: str) -> "IntentBuilder":
        """设置动作"""
        self._action = action
        return self

    def domain(self, domain: str) -> "IntentBuilder":
        """设置领域"""
        self._domain = domain
        return self

    def param(self, key: str, value: Any) -> "IntentBuilder":
        """添加单个参数"""
        self._parameters[key] = value
        return self

    def params(self, parameters: Dict[str, Any]) -> "IntentBuilder":
        """批量添加参数"""
        self._parameters.update(parameters)
        return self

    def require_confirmation(self, value: bool = True) -> "IntentBuilder":
        """设置是否需要确认"""
        if self._constraints is None:
            self._constraints = Constraints()
        self._constraints.require_confirmation = value
        return self

    def timeout(self, seconds: int) -> "IntentBuilder":
        """设置超时时间"""
        if self._constraints is None:
            self._constraints = Constraints()
        self._constraints.timeout_seconds = seconds
        return self

    def priority(self, priority: str) -> "IntentBuilder":
        """设置优先级"""
        if self._constraints is None:
            self._constraints = Constraints()
        self._constraints.priority = priority  # type: ignore
        return self

    def async_(self, value: bool = True) -> "IntentBuilder":
        """设置是否异步"""
        if self._constraints is None:
            self._constraints = Constraints()
        self._constraints.async_ = value
        return self

    def depends_on(self, intent_id: str, condition: Optional[str] = None) -> "IntentBuilder":
        """设置依赖关系"""
        self._depends_on = Dependency(intent_id=intent_id, condition=condition)
        return self

    def fallback(self, fallback_intent: Intent) -> "IntentBuilder":
        """设置备选意图"""
        self._fallback = fallback_intent
        return self

    def confidence(self, value: float) -> "IntentBuilder":
        """设置置信度"""
        self._confidence = value
        return self

    def source_message(self, message_id: str) -> "IntentBuilder":
        """添加来源消息ID"""
        self._source_message_ids.append(message_id)
        return self

    def source_messages(self, message_ids: List[str]) -> "IntentBuilder":
        """批量添加来源消息ID"""
        self._source_message_ids.extend(message_ids)
        return self

    def user_id(self, user_id: str) -> "IntentBuilder":
        """设置用户ID"""
        self._user_id = user_id
        return self

    def session_id(self, session_id: str) -> "IntentBuilder":
        """设置会话ID"""
        self._session_id = session_id
        return self

    def build(self) -> Intent:
        """
        构建意图对象

        Raises:
            ValueError: 必填字段缺失时
        """
        if self._action is None:
            raise ValueError("action is required")
        if self._domain is None:
            raise ValueError("domain is required")
        if self._user_id is None:
            raise ValueError("user_id is required")
        if self._session_id is None:
            raise ValueError("session_id is required")

        meta = Meta(
            confidence=self._confidence,
            source_message_ids=self._source_message_ids,
            user_id=self._user_id,
            session_id=self._session_id
        )

        return Intent(
            id=self._id or generate_intent_id(),
            action=self._action,
            domain=self._domain,
            parameters=self._parameters,
            constraints=self._constraints,
            depends_on=self._depends_on,
            fallback=self._fallback,
            meta=meta
        )
