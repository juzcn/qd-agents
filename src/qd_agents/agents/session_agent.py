"""
会话Agent - 有状态的对话理解与意图生成
"""

import json
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from ..intent.schema import Intent, Meta, Constraints
from ..memory.short_term import ShortTermMemory
from ..memory.long_term import LongTermMemory
from ..models.nvidia_pool import NvidiaModelPool
from ..utils.debug import debug_print, debug_step


INTENT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "natural_language_response": {"type": "string", "description": "给用户的自然语言回复"},
        "needs_clarification": {"type": "boolean", "description": "是否需要用户澄清"},
        "clarification_question": {"type": "string", "description": "如果需要澄清，澄清的问题是什么"},
        "intent": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "动作动词，如 'query_weather'"},
                "domain": {"type": "string", "description": "领域，如 'weather'"},
                "parameters": {"type": "object", "description": "参数字典"},
                "confidence": {"type": "number", "description": "置信度 0-1"}
            },
            "required": ["action", "domain", "parameters", "confidence"]
        }
    },
    "required": ["natural_language_response", "needs_clarification"]
}


SYSTEM_PROMPT_TEMPLATE = """你是一个智能助手，负责理解用户意图并生成结构化的意图对象。

{personality}

{system_rules}

请按以下JSON格式输出：
{schema}

注意：
1. 如果用户请求涉及外部操作（查询天气、搜索、发送邮件等），需要生成intent对象
2. 如果只是闲聊，不需要生成intent
3. 如果参数不完整，设置needs_clarification=true并提出澄清问题
4. 从对话历史中解决指代问题（如"它"、"那里"）
"""


@dataclass
class SessionAgentOutput:
    """会话Agent输出"""
    natural_language_response: str
    intent: Optional[Intent] = None
    needs_clarification: bool = False
    clarification_question: Optional[str] = None


class SessionAgent:
    """
    会话Agent（有状态）

    职责：
    - 维护短期记忆（当前会话消息列表）和长期记忆（用户档案、历史偏好）
    - 理解用户意图，识别动作和领域
    - 抽取实体，解决指代，补全缺失信息
    - 输出自然语言回复 + 结构化意图对象
    """

    def __init__(
        self,
        user_id: str,
        session_id: str,
        long_term_memory: LongTermMemory,
        model_pool: NvidiaModelPool,
        personality: Optional[str] = None,
        system_rules: Optional[str] = None
    ):
        self.user_id = user_id
        self.session_id = session_id
        self.long_term_memory = long_term_memory
        self.model_pool = model_pool
        self.short_term_memory = ShortTermMemory()
        self.personality = personality or "你是一个有帮助的助手。"
        self.system_rules = system_rules or ""

    def process(
        self,
        user_message: str,
        message_id: Optional[str] = None
    ) -> SessionAgentOutput:
        """
        处理用户消息

        Args:
            user_message: 用户输入消息
            message_id: 消息ID（可选）

        Returns:
            SessionAgentOutput: 自然语言回复 + 可选的意图对象
        """
        # 添加用户消息到短期记忆
        self.short_term_memory.add_user_message(user_message, message_id=message_id)

        debug_step("会话Agent", "构建LLM提示词...")

        # 构建系统提示词
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            personality=self.personality,
            system_rules=self.system_rules,
            schema=json.dumps(INTENT_OUTPUT_SCHEMA, ensure_ascii=False, indent=2)
        )

        # 从长期记忆中获取上下文
        context = self.long_term_memory.recall_context(self.user_id)

        # 构建消息列表
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt}
        ]

        # 添加历史对话
        history = self.short_term_memory.get_history(limit=20)
        for msg in history:
            role = msg["role"]
            if role == "tool":
                role = "assistant"
            messages.append({"role": role, "content": str(msg["content"])})

        if context:
            debug_print("召回的上下文", context, style="cyan")

        # 调用LLM
        debug_step("会话Agent", "调用LLM生成响应...")

        try:
            response = self.model_pool.chat_completion(
                messages=messages,
                temperature=0.7
            )

            # 解析响应
            return self._parse_response(response)

        except Exception as e:
            debug_print("LLM调用失败", str(e), style="red")
            # 降级到简单回复
            return SessionAgentOutput(
                natural_language_response="抱歉，我遇到了一些问题，请稍后再试。",
                intent=None
            )

    def _parse_response(self, response: str) -> SessionAgentOutput:
        """解析LLM响应"""
        # 尝试提取JSON
        json_str = self._extract_json(response)

        if not json_str:
            debug_print("解析失败", "无法提取JSON，使用纯文本回复", style="yellow")
            return SessionAgentOutput(
                natural_language_response=response.strip(),
                intent=None
            )

        try:
            data = json.loads(json_str)
            debug_print("解析的JSON", data, style="green")

            intent_obj = None
            if not data.get("needs_clarification") and "intent" in data and data["intent"]:
                intent_data = data["intent"]
                intent_obj = Intent(
                    action=intent_data["action"],
                    domain=intent_data["domain"],
                    parameters=intent_data.get("parameters", {}),
                    meta=Meta(
                        confidence=intent_data.get("confidence", 0.8),
                        user_id=self.user_id,
                        session_id=self.session_id
                    )
                )

            return SessionAgentOutput(
                natural_language_response=data.get("natural_language_response", response.strip()),
                needs_clarification=data.get("needs_clarification", False),
                clarification_question=data.get("clarification_question"),
                intent=intent_obj
            )

        except Exception as e:
            debug_print("JSON解析失败", str(e), style="red")
            return SessionAgentOutput(
                natural_language_response=response.strip(),
                intent=None
            )

    def _extract_json(self, text: str) -> Optional[str]:
        """从文本中提取JSON"""
        text = text.strip()

        # 尝试直接解析
        if text.startswith("{") and text.endswith("}"):
            return text

        # 尝试查找 ```json ... ```
        if "```json" in text and "```" in text.split("```json", 1)[1]:
            parts = text.split("```json", 1)[1].split("```", 1)
            return parts[0].strip()

        # 尝试查找 ``` ... ```
        if "```" in text:
            parts = text.split("```", 2)
            if len(parts) >= 3:
                candidate = parts[1].strip()
                if candidate.startswith("{"):
                    return candidate

        # 尝试查找第一个 { 和最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start:end+1]

        return None

    def add_tool_result(self, result: str, tool_name: Optional[str] = None) -> None:
        """
        添加工具执行结果到短期记忆

        Args:
            result: 工具执行结果
            tool_name: 工具名称
        """
        self.short_term_memory.add_tool_result(result, tool_name=tool_name)

    def add_assistant_message(self, content: str, message_id: Optional[str] = None) -> None:
        """
        添加助手消息到短期记忆

        Args:
            content: 消息内容
            message_id: 消息ID
        """
        self.short_term_memory.add_assistant_message(content, message_id=message_id)

    def get_conversation_history(self, limit: Optional[int] = None) -> list:
        """获取对话历史"""
        return self.short_term_memory.get_history(limit)
