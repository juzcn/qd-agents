"""
编排器 - 协调各模块的交互
"""

from typing import Optional
from uuid import uuid4

from .agents.session_agent import SessionAgent
from .agents.tool_agent import ToolAgent
from .execution.executor import Executor, ExecutionStatus
from .memory.long_term import LongTermMemory
from .intent.builder import IntentBuilder
from .models.nvidia_pool import NvidiaModelPool, ModelInfo
from .utils import debug_print, debug_step, debug_separator


def generate_id() -> str:
    """生成唯一ID"""
    return uuid4().hex[:12]


class Orchestrator:
    """
    编排器：协调整个多Agent系统的数据流

    数据流：
    用户输入 → 会话Agent → 意图对象 → 工具Agent → tool_calls → 执行层 → 结果 → 会话Agent
    """

    def __init__(self, model_pool: Optional[NvidiaModelPool] = None):
        self.long_term_memory = LongTermMemory()
        self.tool_agent = ToolAgent()
        self.executor = Executor()
        self.model_pool = model_pool or NvidiaModelPool()
        self._sessions: dict[str, SessionAgent] = {}

    def create_session(
        self,
        user_id: str,
        personality: Optional[str] = None,
        system_rules: Optional[str] = None
    ) -> str:
        """
        创建新会话

        Returns:
            session_id: 会话ID
        """
        session_id = generate_id()
        session_agent = SessionAgent(
            user_id=user_id,
            session_id=session_id,
            long_term_memory=self.long_term_memory,
            model_pool=self.model_pool,
            personality=personality,
            system_rules=system_rules
        )
        self._sessions[session_id] = session_agent
        return session_id

    def get_session(self, session_id: str) -> Optional[SessionAgent]:
        """获取会话"""
        return self._sessions.get(session_id)

    def get_model_pool(self) -> NvidiaModelPool:
        """获取模型池"""
        return self.model_pool

    def set_model(self, index: int) -> bool:
        """
        手动选择模型

        Args:
            index: 模型索引（从0开始）

        Returns:
            是否成功
        """
        models = self.model_pool.get_all_models()
        if 0 <= index < len(models):
            self.model_pool._current_index = index
            return True
        return False

    def process_message(
        self,
        session_id: str,
        user_message: str,
        confirmed: bool = False
    ) -> str:
        """
        处理用户消息 - 主入口

        Args:
            session_id: 会话ID
            user_message: 用户消息
            confirmed: 是否是确认消息（用于需要用户确认的场景）

        Returns:
            自然语言回复
        """
        debug_separator()
        debug_print("用户输入", user_message, style="green")

        session = self.get_session(session_id)
        if session is None:
            debug_print("错误", f"会话不存在: {session_id}", style="red")
            return f"会话不存在: {session_id}"

        debug_step("会话", f"session_id={session_id}, user_id={session.user_id}")
        debug_step("当前模型", self.model_pool.get_current_model().id)

        # 1. 会话Agent处理用户消息
        debug_step("步骤 1/4", "会话Agent处理用户消息")
        output = session.process(user_message)

        debug_print("会话Agent输出", {
            "natural_language_response": output.natural_language_response,
            "needs_clarification": output.needs_clarification,
            "clarification_question": output.clarification_question,
            "has_intent": output.intent is not None
        }, style="blue")

        if output.intent:
            debug_print("意图对象", output.intent.to_dict(), style="yellow")

        # 添加助手回复到记忆
        session.add_assistant_message(output.natural_language_response)

        # 如果需要澄清，直接返回
        if output.needs_clarification:
            debug_step("结果", "需要用户澄清，结束流程")
            return output.natural_language_response

        # 如果没有意图，直接返回回复
        if output.intent is None:
            debug_step("结果", "无需调用工具，直接返回回复")
            return output.natural_language_response

        # 2. 工具Agent处理意图
        debug_step("步骤 2/4", "工具Agent处理意图")
        tool_schemas = self.executor.list_tool_schemas()
        debug_print("可用工具", [s["name"] for s in tool_schemas], style="cyan")

        tool_output = self.tool_agent.process(output.intent, tool_schemas)
        debug_print("工具Agent输出", {
            "tool_calls_count": len(tool_output.tool_calls),
            "reasoning": tool_output.reasoning
        }, style="blue")

        if tool_output.tool_calls:
            for tc in tool_output.tool_calls:
                debug_print(f"工具调用: {tc.name}", tc.arguments, style="magenta")

        if not tool_output.tool_calls:
            response = "抱歉，没有找到合适的工具来处理你的请求。"
            session.add_assistant_message(response)
            debug_step("结果", "无匹配工具")
            return response

        # 3. 执行层执行工具调用
        debug_step("步骤 3/4", "执行层执行工具调用")
        tool_call = tool_output.tool_calls[0]
        exec_result = self.executor.execute(
            tool_call.name,
            tool_call.arguments,
            confirmed=confirmed
        )

        debug_print("执行结果", {
            "status": exec_result.status.value,
            "data": exec_result.data,
            "error_message": exec_result.error_message,
            "need_confirmation": exec_result.confirmation_question
        }, style="green" if exec_result.status == ExecutionStatus.SUCCESS else "red")

        # 4. 根据执行结果处理
        if exec_result.status == ExecutionStatus.NEED_CONFIRMATION:
            debug_step("结果", "需要用户确认")
            return exec_result.confirmation_question or "需要确认"

        if exec_result.status == ExecutionStatus.ERROR:
            error_msg = f"执行出错: {exec_result.error_message}"
            session.add_tool_result(error_msg, tool_call.name)
            session.add_assistant_message(error_msg)
            debug_step("结果", "执行出错")
            return error_msg

        # 成功：将结果添加到会话记忆，生成最终回复
        result_str = str(exec_result.data)
        session.add_tool_result(result_str, tool_call.name)

        # TODO: 实际实现中，应该再次调用会话Agent来基于结果生成最终回复
        final_response = f"执行结果: {result_str}"
        session.add_assistant_message(final_response)

        debug_step("步骤 4/4", "流程完成")
        debug_separator()

        return final_response
