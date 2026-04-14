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
        prompt_loader: PromptLoader | None = None,
        tool_threshold: int = 50,
        two_phase_enabled: bool = True,
    ):
        """
        初始化两阶段调度器

        Args:
            llm_client: LLM 客户端
            tool_registry: 工具注册中心
            prompt_loader: 提示词加载器
            tool_threshold: 工具数量阈值，超过则启用两阶段
            two_phase_enabled: 是否启用两阶段
        """
        self.llm = llm_client
        self.registry = tool_registry
        self.prompts = prompt_loader
        self.tool_threshold = tool_threshold
        self.two_phase_enabled = two_phase_enabled

        # 内置元工具定义
        self._meta_tools = self._build_meta_tools()

    def _build_meta_tools(self) -> dict[str, dict[str, Any]]:
        """构建元工具定义"""
        return {
            "direct": {
                "type": "function",
                "function": {
                    "name": "direct",
                    "description": "直接生成自然语言回复，不调用任何工具",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "response": {
                                "type": "string",
                                "description": "给用户的自然语言回复内容"
                            }
                        },
                        "required": ["response"]
                    }
                }
            },
            "find_tools": {
                "type": "function",
                "function": {
                    "name": "find_tools",
                    "description": "根据用户需求检索相关工具",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "描述用户需要什么功能的自然语言"
                            }
                        },
                        "required": ["query"]
                    }
                }
            },
            "coding_tool_use": {
                "type": "function",
                "function": {
                    "name": "coding_tool_use",
                    "description": "生成Python代码来编排多个工具的执行",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": "Python代码字符串"
                            }
                        },
                        "required": ["code"]
                    }
                }
            },
            "step_down": {
                "type": "function",
                "function": {
                    "name": "step_down",
                    "description": "当无法通过工具完成任务时，降级为人工友好的回复",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reason": {
                                "type": "string",
                                "enum": ["no_matching_tools", "too_complex", "safety_concern", "user_confirmation_required"]
                            },
                            "message": {
                                "type": "string",
                                "description": "给用户的解释信息"
                            }
                        },
                        "required": ["reason", "message"]
                    }
                }
            }
        }

    async def orchestrate(
        self,
        user_input: str,
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> OrchestrationResult:
        """
        执行两阶段调度

        Args:
            user_input: 用户输入
            session_id: 会话 ID
            trace_id: 追踪 ID

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

        # 决定是否使用两阶段
        all_tools = self.registry.list_all()
        use_two_phase = self.two_phase_enabled and len(all_tools) > self.tool_threshold

        if use_two_phase:
            result = await self._run_two_phase(result, user_input)
        else:
            result = await self._run_single_phase(result, user_input, all_tools)

        result.total_latency_ms = int((time.perf_counter() - start_time) * 1000)
        return result

    async def _run_two_phase(
        self,
        result: OrchestrationResult,
        user_input: str,
    ) -> OrchestrationResult:
        """执行两阶段流程"""
        import time

        # ========== 第一阶段 ==========
        logger.info("Executing Phase One")
        phase_one_start = time.perf_counter()

        # 构建第一阶段消息
        phase_one_tools = [
            self._meta_tools["direct"],
            self._meta_tools["find_tools"],
        ]

        # 加载高频工具 search.web（如果已注册）
        search_web = self.registry.get("search.web")
        if search_web:
            phase_one_tools.append(search_web.to_openai_function())

        messages = self._build_phase_one_messages(user_input)

        # 调用 LLM
        response = await self.llm.chat(
            messages=messages,
            tools=phase_one_tools,
            tool_choice="auto",
        )

        choice = response.choices[0]
        phase_one_result = PhaseOneResult(
            tool_choice="",
            latency_ms=int((time.perf_counter() - phase_one_start) * 1000),
        )

        # 解析第一阶段结果
        if choice.message.tool_calls:
            tool_call = choice.message.tool_calls[0]
            phase_one_result.tool_choice = tool_call.function.name

            try:
                phase_one_result.tool_input = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                phase_one_result.tool_input = {"raw": tool_call.function.arguments}

            # 处理 direct
            if tool_call.function.name == "direct":
                phase_one_result.response = phase_one_result.tool_input.get("response", "")
                result.phase_one = phase_one_result
                result.final_output = phase_one_result.response
                result.final_status = "completed"
                return result

            # 处理 find_tools
            if tool_call.function.name == "find_tools":
                query = phase_one_result.tool_input.get("query", user_input)
                phase_one_result.found_tools = self.registry.search(query, top_k=10)
                logger.info("Found %d tools", len(phase_one_result.found_tools))

        elif choice.message.content:
            phase_one_result.tool_choice = "direct"
            phase_one_result.response = choice.message.content
            result.phase_one = phase_one_result
            result.final_output = phase_one_result.response
            result.final_status = "completed"
            return result

        result.phase_one = phase_one_result

        # ========== 第二阶段 ==========
        logger.info("Executing Phase Two")
        phase_two_start = time.perf_counter()

        # 构建第二阶段可用工具
        phase_two_tools = [t.to_openai_function() for t in phase_one_result.found_tools]

        # 添加 coding_tool_use 和 step_down
        phase_two_tools.append(self._meta_tools["coding_tool_use"])
        phase_two_tools.append(self._meta_tools["step_down"])

        messages = self._build_phase_two_messages(
            user_input,
            phase_one_result.found_tools
        )

        # 调用 LLM
        response = await self.llm.chat(
            messages=messages,
            tools=phase_two_tools if phase_two_tools else None,
            tool_choice="auto",
        )

        choice = response.choices[0]
        phase_two_result = PhaseTwoResult(
            tool_choice="",
            latency_ms=int((time.perf_counter() - phase_two_start) * 1000),
        )

        if choice.message.tool_calls:
            tool_call = choice.message.tool_calls[0]
            phase_two_result.tool_choice = tool_call.function.name

            try:
                phase_two_result.tool_input = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                phase_two_result.tool_input = {"raw": tool_call.function.arguments}

            if tool_call.function.name == "coding_tool_use":
                phase_two_result.generated_code = phase_two_result.tool_input.get("code", "")

        elif choice.message.content:
            phase_two_result.tool_choice = "direct"
            result.final_output = choice.message.content

        result.phase_two = phase_two_result
        result.final_status = "orchestrated"

        return result

    async def _run_single_phase(
        self,
        result: OrchestrationResult,
        user_input: str,
        tools: list[Tool],
    ) -> OrchestrationResult:
        """执行单阶段流程"""
        import time

        logger.info("Executing Single Phase")
        start_time = time.perf_counter()

        openai_tools = []

        # 优先添加 search.web 工具（如果可用）
        search_web = self.registry.get("search.web")
        if search_web:
            openai_tools.append(search_web.to_openai_function())
            # 从 tools 列表中移除，避免重复
            tools = [t for t in tools if t.id != "search.web"]

        # 添加其他工具
        openai_tools.extend([t.to_openai_function() for t in tools])
        openai_tools.append(self._meta_tools["coding_tool_use"])
        openai_tools.append(self._meta_tools["step_down"])

        if self.prompts:
            # 检查 search.web 是否可用
            search_web_available = self.registry.get("search.web") is not None
            system_prompt = self.prompts.render(
                "system_prompt",
                tools=tools,
                search_web_available=search_web_available,
            )
        else:
            # 回退到硬编码
            search_web_available = self.registry.get("search.web") is not None
            if search_web_available:
                system_prompt = (
                    "你是一个智能助手，可以调用工具帮助用户。\n"
                    "如果用户的问题需要实时信息或外部知识，请优先使用 search.web 工具进行网络搜索。"
                )
            else:
                system_prompt = "你是一个智能助手，可以调用工具帮助用户。"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]

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

            if tool_call.function.name == "coding_tool_use":
                phase_two_result.generated_code = phase_two_result.tool_input.get("code", "")

        elif choice.message.content:
            phase_two_result.tool_choice = "direct"
            result.final_output = choice.message.content

        result.phase_two = phase_two_result
        result.final_status = "orchestrated"

        return result

    def _build_phase_one_messages(self, user_input: str) -> list[dict[str, str]]:
        """构建第一阶段消息"""
        if self.prompts:
            # 检查 search.web 是否可用
            search_web_available = self.registry.get("search.web") is not None
            system_prompt = self.prompts.render(
                "phase_one",
                search_web_available=search_web_available,
            )
        else:
            # 回退到硬编码
            system_prompt = (
                "你是一个智能路由助手。"
                "你有三个工具可用：\n"
                "1. direct - 直接回答用户问题\n"
                "2. find_tools - 检索相关工具\n"
                "3. search.web - 网络搜索（如果可用）\n"
                "请选择合适的工具处理用户请求。"
            )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]

    def _build_phase_two_messages(
        self,
        user_input: str,
        found_tools: list[Tool],
    ) -> list[dict[str, str]]:
        """构建第二阶段消息"""
        if self.prompts:
            system_prompt = self.prompts.render(
                "phase_two",
                tools=found_tools,
            )
        else:
            # 回退到硬编码
            tool_descriptions = "\n".join([
                f"- {t.name}: {t.description}"
                for t in found_tools
            ])
            system_prompt = (
                "你是一个智能规划助手。\n"
                f"可用工具：\n{tool_descriptions}\n"
                "你也可以使用 coding_tool_use 生成 Python 代码来编排多个工具。"
            )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]
