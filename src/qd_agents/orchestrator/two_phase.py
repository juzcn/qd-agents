"""
两阶段调度器
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ..llm import LLMClient
from ..registry import ToolRegistry, Tool
from ..prompts import PromptLoader
from ..context import ContextManager


logger = logging.getLogger(__name__)


class Phase(str, Enum):
    """调度阶段"""
    PHASE_ONE = "phase_one"
    PHASE_TWO = "phase_two"
    COMPLETED = "completed"


@dataclass
class PhaseOneResult:
    """第一阶段结果"""
    tool_choice: str
    tool_input: dict[str, Any] = field(default_factory=dict)
    found_tools: list[Tool] = field(default_factory=list)
    response: str | None = None
    latency_ms: int = 0


@dataclass
class PhaseTwoResult:
    """第二阶段结果"""
    tool_choice: str
    tool_input: dict[str, Any] = field(default_factory=dict)
    generated_code: str | None = None
    latency_ms: int = 0


@dataclass
class OrchestrationResult:
    """调度结果"""
    trace_id: str
    user_input: str
    session_id: str | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    phase_one: PhaseOneResult | None = None
    phase_two: PhaseTwoResult | None = None
    final_output: Any = None
    final_status: str = "pending"
    total_latency_ms: int = 0
    # OpenAI tool calling 标准流程字段
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] = field(default_factory=dict)
    needs_more_rounds: bool = False


class TwoPhaseOrchestrator:
    """
    两阶段调度器

    第一阶段：使用元工具路由
    第二阶段：使用检索到的工具规划执行
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        context_manager: ContextManager | None = None,
        prompt_loader: PromptLoader | None = None,
        tool_threshold: int = 50,
        two_phase_enabled: bool = True,
    ):
        """
        初始化两阶段调度器

        Args:
            llm_client: LLM 客户端
            tool_registry: 工具注册中心
            context_manager: 上下文管理器
            prompt_loader: 提示词加载器
            tool_threshold: 工具数量阈值，超过则启用两阶段
            two_phase_enabled: 是否启用两阶段
        """
        self.llm = llm_client
        self.registry = tool_registry
        self.prompts = prompt_loader
        self.context = context_manager or ContextManager(prompt_loader=prompt_loader)
        self.tool_threshold = tool_threshold
        self.two_phase_enabled = two_phase_enabled

        # 内置元工具定义
        self._meta_tools = self._build_meta_tools()

    def _build_meta_tools(self) -> dict[str, dict[str, Any]]:
        """构建元工具定义（现在为空，使用标准工具调用）"""
        return {}

    async def orchestrate(
        self,
        user_input: str,
        session_id: str | None = None,
        trace_id: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> OrchestrationResult:
        """
        执行单阶段工具调用调度

        Args:
            user_input: 用户输入
            session_id: 会话 ID
            trace_id: 追踪 ID
            history: 会话历史消息列表

        Returns:
            调度结果
        """
        import time
        import uuid

        start_time = time.perf_counter()
        trace_id = trace_id or str(uuid.uuid4())

        result = OrchestrationResult(
            trace_id=trace_id,
            session_id=session_id,
            user_input=user_input,
        )

        logger.info("Starting orchestration (trace_id: %s)", trace_id)

        # 获取所有工具
        all_tools = self.registry.list_all()
        # 始终使用单阶段工具调用
        result = await self._run_tool_use(result, user_input, all_tools, history)

        result.total_latency_ms = int((time.perf_counter() - start_time) * 1000)
        return result

    async def _run_tool_use(
        self,
        result: OrchestrationResult,
        user_input: str,
        tools: list[Tool],
        history: list[dict[str, str]] | None = None,
    ) -> OrchestrationResult:
        """执行单阶段工具调用流程"""
        import time

        logger.info("Executing Tool Use Phase")
        start_time = time.perf_counter()

        openai_tools = []

        # 优先添加 search.web 工具（如果可用）
        search_web = self.registry.get("search.web")
        search_web_available = search_web is not None
        if search_web:
            openai_tools.append(search_web.to_openai_function())
            # 从 tools 列表中移除，避免重复
            tools = [t for t in tools if t.id != "search.web"]

        # 添加其他工具
        openai_tools.extend([t.to_openai_function() for t in tools])
        # 注意：不再添加元工具，使用标准的 OpenAI Tool Calling 格式

        # 使用 ContextManager 构建消息（使用优化的 tool_use 提示词）
        messages = self.context.build_tool_use_messages(
            user_input=user_input,
            tools=tools,
            search_web_available=search_web_available,
            history=history,
        )

        response = await self.llm.chat(
            messages=messages,
            tools=openai_tools,
            tool_choice="auto",
        )

        choice = response.choices[0]
        phase_two_result = PhaseTwoResult(
            tool_choice="",
            latency_ms=int((time.perf_counter() - start_time) * 1000),
        )

        if choice.message.tool_calls:
            tool_call = choice.message.tool_calls[0]
            phase_two_result.tool_choice = tool_call.function.name

            try:
                phase_two_result.tool_input = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                phase_two_result.tool_input = {"raw": tool_call.function.arguments}

            # 不再处理 coding_tool_use，因为不再包含元工具

        elif choice.message.content:
            # LLM 选择直接回复，而不是调用工具
            phase_two_result.tool_choice = "direct"
            result.final_output = choice.message.content

        result.phase_two = phase_two_result
        result.final_status = "orchestrated"

        return result

