"""
工具Agent - 无状态的工具选择与参数生成
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from ..intent.schema import Intent


@dataclass
class ToolCall:
    """工具调用"""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class ToolAgentOutput:
    """工具Agent输出"""
    tool_calls: List[ToolCall]
    reasoning: Optional[str] = None


class ToolAgent:
    """
    工具Agent（无状态）

    职责：
    - 接收意图对象，通过原生Tool Calling机制选择具体工具
    - 生成工具调用参数
    - 完全无状态，不访问会话历史、长期记忆
    """

    def __init__(self):
        pass

    def process(
        self,
        intent: Intent,
        tool_schemas: List[Dict[str, Any]]
    ) -> ToolAgentOutput:
        """
        处理意图，生成工具调用

        Args:
            intent: 意图对象
            tool_schemas: 工具Schema列表

        Returns:
            ToolAgentOutput: 工具调用列表
        """
        # TODO: 实际实现需要调用启用了Tool Calling的LLM
        # 这里是占位实现，展示接口设计
        return self._mock_process(intent, tool_schemas)

    def _mock_process(
        self,
        intent: Intent,
        tool_schemas: List[Dict[str, Any]]
    ) -> ToolAgentOutput:
        """
        模拟处理 - 仅用于演示接口

        实际实现应该：
        1. 构建提示词：意图对象 + 工具列表
        2. 调用启用Tool Calling的LLM
        3. 解析返回的tool_calls
        """
        # 根据意图的action和domain查找匹配的工具
        matched_tool = None
        for schema in tool_schemas:
            if intent.action in schema["name"] or intent.domain in schema["name"]:
                matched_tool = schema
                break

        if matched_tool is None and tool_schemas:
            # 如果没有匹配的，默认用第一个
            matched_tool = tool_schemas[0]

        tool_calls = []
        if matched_tool:
            tool_calls.append(
                ToolCall(
                    id=f"call_{intent.id}",
                    name=matched_tool["name"],
                    arguments=intent.parameters
                )
            )

        return ToolAgentOutput(
            tool_calls=tool_calls,
            reasoning=f"根据意图 '{intent.action}' 选择工具"
        )
