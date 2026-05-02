"""
QDAgent 核心容器

负责资源管理和三循环协调：
- EvolveAgent (Loop 1): 路由决策
- UseToolAgent (Loop 2): 工具执行
- FindToolsAgent (Loop 3): 工具发现

MCP 连接管理委托给 MCPService，工具注册/缓存委托给 ToolService。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import Config
from ..llm import LLMClient
from ..models.tool import Tool, ToolExecutionType, ToolMetadata
from ..models.job import Job
from ..registry import ToolRegistry
from ..prompts import PromptLoader
from ..tools import ToolExecutorRegistry
from ..tools.executors.mcp import MCPToolExecutor
from ..utils import RetryConfig, RetryExecutor, CircuitBreaker, CircuitBreakerConfig, BackoffStrategy
from ..context import ContextManager
from ..services import MCPService, ToolService
from .base import Agent, AgentResult, StepCallback
from .evolve import EvolveAgent
from .use_tool import UseToolAgent
from .find_tools import FindToolsAgent

if TYPE_CHECKING:
    from ..memory.service import MemoryService

logger = logging.getLogger(__name__)


class QDAgent:
    """
    主智能体类 — 资源管理器 + 三循环协调器

    管理工具注册、MCP 连接、上下文压缩等资源，
    协调 Evolve → Use-Tool / Find-Tools 三循环。
    """

    def __init__(
        self,
        config: Config,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        prompt_loader: PromptLoader | None = None,
        context_manager: ContextManager | None = None,
        base_dir: Path | None = None,
    ):
        self.config = config
        self.llm = llm_client
        self.registry = tool_registry
        self.prompts = prompt_loader
        self.executor_registry = ToolExecutorRegistry()
        self.base_dir = base_dir

        self.context = context_manager or ContextManager(
            prompt_loader=prompt_loader,
            base_dir=base_dir,
        )

        # 三个 Agent
        self._evolve_agent: EvolveAgent | None = None
        self._use_tool_agent: UseToolAgent | None = None
        self._find_tools_agent: FindToolsAgent | None = None

        # 初始化重试和熔断
        self._setup_retry_and_circuit_breaker()

        # MCP 服务（委托 MCP 连接管理）
        self._mcp_service = MCPService()

        # 工具服务（委托工具注册和缓存）
        self._tool_service = ToolService()

        # 展开工具缓存
        self._expanded_tools_cache: list[Tool] | None = None
        self._openai_tools_cache: list[dict[str, Any]] | None = None
        self._tool_map_cache: dict[str, Tool] = {}

        # 取消信号（Escape 键设置，Agent 循环中检查）
        self._cancel_event: asyncio.Event | None = None

        # 长期记忆服务
        self._memory_service: MemoryService | None = None
        self._session_id: str = str(uuid.uuid4())

    def _setup_retry_and_circuit_breaker(self) -> None:
        """配置重试和熔断器"""
        self.retry_config = RetryConfig(
            max_attempts=self.config.execution.max_attempts,
            backoff_strategy=BackoffStrategy(self.config.execution.backoff_strategy),
            initial_delay_ms=self.config.execution.initial_delay_ms,
            max_delay_ms=self.config.execution.max_delay_ms,
        )

        self.circuit_breaker = CircuitBreaker(
            CircuitBreakerConfig(
                enabled=self.config.execution.circuit_breaker_enabled,
                error_rate_threshold=self.config.execution.circuit_breaker_error_rate,
                minimum_requests=self.config.execution.circuit_breaker_min_requests,
                half_open_timeout_ms=self.config.execution.circuit_breaker_half_open_timeout,
            )
        )

        self.retry_executor: RetryExecutor = RetryExecutor(
            config=self.retry_config,
            circuit_breaker=self.circuit_breaker,
        )

    async def initialize(self) -> None:
        """初始化智能体"""
        logger.info("Initializing QDAgent...")

        # 发现 LLM 模型
        if not self.llm.current_model:
            await self.llm.discover_models(top_k=5)

        # 注册内置工具
        await self._register_builtin_tools()

        # 预加载 MCP 工具并缓存展开后的工具列表
        await self._load_and_cache_tools()

        # 初始化长期记忆服务
        self._init_memory_service()

        # 创建三个 Agent
        self._evolve_agent = EvolveAgent(
            llm_client=self.llm,
            tool_registry=self.registry,
            context_manager=self.context,
            expanded_tools_cache=self._expanded_tools_cache,
            max_iterations=self.config.execution.max_iterations,
            memory_service=self._memory_service,
            session_id=self._session_id,
        )

        self._use_tool_agent = UseToolAgent(
            llm_client=self.llm,
            tool_registry=self.registry,
            context_manager=self.context,
            executor_registry=self.executor_registry,
            max_iterations=self.config.execution.max_use_tool_iterations,
        )

        self._find_tools_agent = FindToolsAgent(
            llm_client=self.llm,
            tool_registry=self.registry,
            context_manager=self.context,
            executor_registry=self.executor_registry,
            max_iterations=self.config.execution.max_find_tools_iterations,
        )

        logger.info("QDAgent initialized with 3 agents (evolve, use-tool, find-tools). Models: %s", self.llm._model_names)

    @property
    def agent(self) -> EvolveAgent | None:
        """获取 EvolveAgent（向后兼容）"""
        return self._evolve_agent

    async def process(
        self,
        user_input: str,
        session_id: str | None = None,
        on_step: StepCallback | None = None,
    ) -> AgentResult:
        """处理用户输入，协调三循环。"""
        trace_id = str(uuid.uuid4())
        start_time = time.perf_counter()
        logger.info("Processing user input (trace_id: %s): %s", trace_id, user_input[:100])

        # 创建取消信号
        self._cancel_event = asyncio.Event()

        try:
            if self._evolve_agent is None:
                raise ValueError("Agent not initialized")

            # 获取当前历史（执行前的 Q&A 对）
            conversation_history = self.context.get_history()

            total_tokens = 0
            last_prompt_tokens = 0

            # ===== Loop 1: Evolve 路由决策 =====
            evolve_result = await self._evolve_agent.execute(
                user_input=user_input,
                history=conversation_history,
                trace_id=trace_id,
                on_step=on_step,
                cancel_event=self._cancel_event,
            )

            total_tokens += evolve_result.total_tokens
            last_prompt_tokens = evolve_result.last_prompt_tokens

            # 获取 Job（如果 Evolve 决定路由到子循环）
            job: Job | None = evolve_result.working_memory.get("job") if evolve_result.working_memory else None

            if job is None:
                # 直接回答 / ask_user / delegate — Evolve 已产生最终答案
                final_answer = evolve_result.final_answer
                total_duration_ms = int((time.perf_counter() - start_time) * 1000)

                # QA 自动写入长期记忆
                self._auto_save_memory(user_input, final_answer, total_tokens)

                # 记录 QA 到历史
                self.add_to_history("user", user_input)
                self.add_to_history("assistant", final_answer)

                return AgentResult(
                    final_answer=final_answer,
                    success=evolve_result.success,
                    total_tokens=total_tokens,
                    last_prompt_tokens=last_prompt_tokens,
                    trace_id=trace_id,
                    total_duration_ms=total_duration_ms,
                )

            # ===== Loop 3: Find-Tools（如果需要） =====
            if job.route == "find-tools":
                logger.info("Routing to find-tools sub-loop (trace_id: %s)", trace_id)

                find_result = await self._find_tools_agent.execute(
                    job=job,
                    trace_id=trace_id,
                    on_step=on_step,
                    cancel_event=self._cancel_event,
                )

                total_tokens += find_result.total_tokens
                last_prompt_tokens = find_result.last_prompt_tokens

                if not find_result.success:
                    final_answer = f"工具发现失败：{find_result.final_answer}"
                    total_duration_ms = int((time.perf_counter() - start_time) * 1000)

                    self._auto_save_memory(user_input, final_answer, total_tokens)
                    self.add_to_history("user", user_input)
                    self.add_to_history("assistant", final_answer)

                    return AgentResult(
                        final_answer=final_answer,
                        success=False,
                        total_tokens=total_tokens,
                        last_prompt_tokens=last_prompt_tokens,
                        trace_id=trace_id,
                        total_duration_ms=total_duration_ms,
                    )

                # 刷新工具缓存（新工具已注册）
                await self._refresh_tool_caches()

                # 将发现的工具加入 Job 的 tool_list
                found_tool_names = find_result.working_memory.get("found_tool_names", []) if find_result.working_memory else []
                if found_tool_names:
                    job.tool_list.extend(found_tool_names)
                    logger.info("Find-tools discovered %d tools: %s", len(found_tool_names), found_tool_names)

                # 如果 find-tools 没有找到任何工具，直接返回结果
                if not job.tool_list:
                    final_answer = find_result.final_answer
                    total_duration_ms = int((time.perf_counter() - start_time) * 1000)

                    self._auto_save_memory(user_input, final_answer, total_tokens)
                    self.add_to_history("user", user_input)
                    self.add_to_history("assistant", final_answer)

                    return AgentResult(
                        final_answer=final_answer,
                        success=True,
                        total_tokens=total_tokens,
                        last_prompt_tokens=last_prompt_tokens,
                        trace_id=trace_id,
                        total_duration_ms=total_duration_ms,
                    )

            # ===== Loop 2: Use-Tool =====
            if job.route in ("use-tool", "find-tools"):
                logger.info("Routing to use-tool sub-loop (trace_id: %s, tools: %s)", trace_id, job.tool_list)

                use_result = await self._use_tool_agent.execute(
                    job=job,
                    trace_id=trace_id,
                    on_step=on_step,
                    cancel_event=self._cancel_event,
                )

                total_tokens += use_result.total_tokens
                last_prompt_tokens = use_result.last_prompt_tokens

                final_answer = use_result.final_answer
                total_duration_ms = int((time.perf_counter() - start_time) * 1000)

                # QA 自动写入长期记忆
                self._auto_save_memory(user_input, final_answer, total_tokens)

                # 记录 QA 到历史（只有 final answer，中间过程不保留）
                self.add_to_history("user", user_input)
                self.add_to_history("assistant", final_answer)

                return AgentResult(
                    final_answer=final_answer,
                    success=use_result.success,
                    total_tokens=total_tokens,
                    last_prompt_tokens=last_prompt_tokens,
                    trace_id=trace_id,
                    total_duration_ms=total_duration_ms,
                )

            # 未知路由（不应到达此处）
            final_answer = evolve_result.final_answer or "抱歉，无法处理您的请求。"
            total_duration_ms = int((time.perf_counter() - start_time) * 1000)

            self.add_to_history("user", user_input)
            self.add_to_history("assistant", final_answer)

            return AgentResult(
                final_answer=final_answer,
                success=False,
                total_tokens=total_tokens,
                last_prompt_tokens=last_prompt_tokens,
                trace_id=trace_id,
                total_duration_ms=total_duration_ms,
            )

        except Exception as e:
            logger.exception("Processing failed")
            error_msg = f"抱歉，处理失败: {e}"
            self.add_to_history("user", user_input)
            self.add_to_history("assistant", error_msg)

            return AgentResult(
                final_answer=error_msg,
                success=False,
                trace_id=trace_id,
                total_duration_ms=0,
            )

    # --- 工具缓存管理 ---

    async def _load_and_cache_tools(self) -> None:
        """预加载 MCP 工具并缓存展开后的工具列表"""
        # 预加载 MCP（连接服务器、获取 subtools、注册执行器）
        all_tools = self.registry.list_all()
        mcp_tools = [t for t in all_tools if t.execution.type == ToolExecutionType.MCP]
        await self._mcp_service.preload(
            mcp_tools=mcp_tools,
            executor_registry=self.executor_registry,
        )

        # 缓存展开后的工具列表
        expanded, openai, tool_map = self._tool_service.build_expanded_tools(
            registry=self.registry,
            mcp_tools_cache=self._mcp_service.tools_cache,
        )
        self._expanded_tools_cache = expanded
        self._openai_tools_cache = openai
        self._tool_map_cache = tool_map

    async def _refresh_tool_caches(self) -> None:
        """刷新工具缓存（Find-Tools 注册新工具后调用）

        重新加载 MCP 连接和工具列表，更新所有 Agent 的缓存。
        """
        logger.info("Refreshing tool caches after new tools registered...")

        # 重新预加载 MCP（可能有新的 MCP 服务器）
        all_tools = self.registry.list_all()
        mcp_tools = [t for t in all_tools if t.execution.type == ToolExecutionType.MCP]
        await self._mcp_service.preload(
            mcp_tools=mcp_tools,
            executor_registry=self.executor_registry,
        )

        # 重建缓存
        expanded, openai, tool_map = self._tool_service.build_expanded_tools(
            registry=self.registry,
            mcp_tools_cache=self._mcp_service.tools_cache,
        )
        self._expanded_tools_cache = expanded
        self._openai_tools_cache = openai
        self._tool_map_cache = tool_map

        # 更新 EvolveAgent 的工具缓存（下次路由决策时能看到新工具）
        if self._evolve_agent:
            self._evolve_agent._expanded_tools = expanded

        logger.info("Tool caches refreshed: %d expanded tools", len(expanded))

    # --- 生命周期管理 ---

    async def close(self) -> None:
        """关闭智能体（委托给 MCPService 关闭连接）"""
        logger.info("Closing QDAgent...")
        await self._mcp_service.close()
        if self._memory_service:
            self._memory_service.close()
        logger.info("QDAgent closed")

    # --- 内置工具注册 ---

    async def _register_builtin_tools(self) -> None:
        """注册内置工具执行器"""
        from ..tools.builtins import echo
        self.executor_registry.register_function("echo", echo)

        from ..tools.search import (
            serper_search,
            tavily_search,
        )
        self.executor_registry.register_function("serper_search", serper_search)
        self.executor_registry.register_function("tavily_search", tavily_search)

        # 工具注册 function — LLM 可直接调用管理工具箱
        from ..tools.builtin_register import (
            tool_register_cli,
            tool_register_mcp,
            tool_register_skill,
            tool_register_http,
            register_builtin_function_tools,
        )
        self.executor_registry.register_function("tool_register_cli", tool_register_cli)
        self.executor_registry.register_function("tool_register_mcp", tool_register_mcp)
        self.executor_registry.register_function("tool_register_skill", tool_register_skill)
        self.executor_registry.register_function("tool_register_http", tool_register_http)

        # 将4个工具注册 function 持久化到数据库（scope=builtin）
        register_builtin_function_tools(self.registry)

        logger.info("Registered builtin tool executors")

    # --- 历史管理 ---

    def add_to_history(self, role: str, content: str) -> None:
        """添加消息到会话历史"""
        self.context.add_to_history(role, content)

    def clear_history(self) -> None:
        """清空会话历史"""
        self.context.clear_history()

    def get_history(self) -> list[dict[str, str]]:
        """获取会话历史"""
        return self.context.get_history()

    # --- 记忆管理 ---

    def _init_memory_service(self) -> None:
        """初始化长期记忆服务"""
        if not self.config.memory:
            logger.info("Memory config not set, skipping memory service init")
            return

        try:
            from ..memory.service import MemoryService
            self._memory_service = MemoryService(self.config.memory)
            logger.info("Memory service initialized (db: %s)", self.config.memory.db_path)
        except Exception as e:
            logger.warning("Failed to initialize memory service: %s", e)
            self._memory_service = None

    def _auto_save_memory(self, question: str, answer: str, token_count: int = 0) -> None:
        """QA 完成后自动写入长期记忆"""
        if not self._memory_service:
            return
        try:
            self._memory_service.save(
                question=question,
                answer=answer,
                session_id=self._session_id,
                source="evolve",
                model=self.llm.current_model,
                token_count=token_count,
            )
            logger.info("Auto-saved QA to memory (session=%s)", self._session_id[:8] if self._session_id else "?")
        except Exception:
            logger.exception("Auto-save memory failed")
