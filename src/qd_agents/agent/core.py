"""
QDAgent 核心容器

负责资源管理和 EvolveAgent 的生命周期。
MCP 连接管理委托给 MCPService，工具注册/缓存委托给 ToolService。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import Config
from ..llm import LLMClient
from ..models.tool import Tool, ToolExecutionType, ToolMetadata
from ..registry import ToolRegistry
from ..prompts import PromptLoader
from ..tools import ToolExecutorRegistry
from ..tools.executors.mcp import MCPToolExecutor
from ..utils import RetryConfig, RetryExecutor, CircuitBreaker, CircuitBreakerConfig, BackoffStrategy
from ..context import ContextManager
from ..services import MCPService, ToolService
from .base import Agent, AgentResult, StepCallback
from .evolve import EvolveAgent

if TYPE_CHECKING:
    from ..memory.service import MemoryService

logger = logging.getLogger(__name__)


class QDAgent:
    """
    主智能体类 — 资源管理器 + EvolveAgent 容器

    管理工具注册、MCP 连接、上下文压缩等资源，
    将用户输入委托给 EvolveAgent 执行。
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

        # EvolveAgent（唯一 Agent）
        self._agent: EvolveAgent | None = None

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

        # 取消信号（Escape 键设置，EvolveAgent 循环中检查）
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

        # 预加载 MCP 工具
        await self._preload_mcp_tools()

        # 缓存展开后的工具列表
        await self._cache_expanded_tools()

        # 初始化长期记忆服务
        self._init_memory_service()

        # 创建 EvolveAgent
        self._agent = EvolveAgent(
            llm_client=self.llm,
            tool_registry=self.registry,
            context_manager=self.context,
            executor_registry=self.executor_registry,
            expanded_tools_cache=self._expanded_tools_cache,
            openai_tools_cache=self._openai_tools_cache,
            tool_map_cache=self._tool_map_cache,
            max_iterations=self.config.execution.max_iterations,
            memory_service=self._memory_service,
            session_id=self._session_id,
        )
        logger.info("EvolveAgent created")

        logger.info("QDAgent initialized. Models: %s", self.llm._model_names)

    @property
    def agent(self) -> EvolveAgent | None:
        """获取 EvolveAgent"""
        return self._agent

    async def process(
        self,
        user_input: str,
        session_id: str | None = None,
        on_step: StepCallback | None = None,
    ) -> AgentResult:
        """处理用户输入，委托给 EvolveAgent 执行。"""
        trace_id = str(uuid.uuid4())
        logger.info("Processing user input (trace_id: %s): %s", trace_id, user_input[:100])

        # 创建取消信号
        self._cancel_event = asyncio.Event()

        try:
            if self._agent is None:
                raise ValueError("Agent not initialized")

            # 获取当前历史（执行前的 Q&A 对）
            conversation_history = self.context.get_history()

            # 委托给 EvolveAgent
            result = await self._agent.execute(
                user_input=user_input,
                history=conversation_history,
                trace_id=trace_id,
                on_step=on_step,
                cancel_event=self._cancel_event,
            )

            # 执行后记录本轮 Q&A 对（中间过程不进入历史）
            self.add_to_history("user", user_input)
            self.add_to_history("assistant", result.final_answer)

            return result

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

    # --- MCP 管理（委托给 MCPService）---

    async def _preload_mcp_tools(self) -> None:
        """预加载 MCP 工具（委托给 MCPService）"""
        all_tools = self.registry.list_all()
        mcp_tools = [t for t in all_tools if t.execution.type == ToolExecutionType.MCP]

        await self._mcp_service.preload(
            mcp_tools=mcp_tools,
            executor_registry=self.executor_registry,
        )

    async def _cache_expanded_tools(self) -> None:
        """缓存展开后的工具列表和OpenAI格式工具"""
        expanded, openai, tool_map = self._tool_service.build_expanded_tools(
            registry=self.registry,
            mcp_tools_cache=self._mcp_service.tools_cache,
        )
        self._expanded_tools_cache = expanded
        self._openai_tools_cache = openai
        self._tool_map_cache = tool_map

    async def close(self) -> None:
        """关闭智能体（委托给 MCPService 关闭连接）"""
        logger.info("Closing QDAgent...")
        await self._mcp_service.close()
        if self._memory_service:
            self._memory_service.close()
        logger.info("QDAgent closed")

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
        )
        self.executor_registry.register_function("tool_register_cli", tool_register_cli)
        self.executor_registry.register_function("tool_register_mcp", tool_register_mcp)
        self.executor_registry.register_function("tool_register_skill", tool_register_skill)
        self.executor_registry.register_function("tool_register_http", tool_register_http)

        logger.info("Registered builtin tool executors")

    def add_to_history(self, role: str, content: str) -> None:
        """添加消息到会话历史"""
        self.context.add_to_history(role, content)

    def clear_history(self) -> None:
        """清空会话历史"""
        self.context.clear_history()

    def get_history(self) -> list[dict[str, str]]:
        """获取会话历史"""
        return self.context.get_history()

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