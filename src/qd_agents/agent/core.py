"""QDAgent — 智能体核心编排器

QDAgent 是用户交互的入口，负责：
1. 初始化所有组件（LLM、Registry、Memory、MCP、Context）
2. 创建 EvolveAgent 和子 Agent
3. 管理会话历史和工具缓存
4. 将用户输入传递给 EvolveAgent 执行

在新架构下，QDAgent 不再包含硬编码的路由逻辑。
路由决策完全由 EvolveAgent 通过 delegate 工具自主完成。
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
from ..context import ContextManager
from ..models.tool import Tool, ToolExecutionType
from ..registry import ToolRegistry
from ..prompts import PromptLoader
from ..tools import ToolExecutorRegistry
from ..services import MCPService, ToolService
from ..utils import RetryConfig, RetryExecutor, CircuitBreaker, CircuitBreakerConfig, BackoffStrategy
from .base import AgentResult, StepCallback, AskUserCallback
from .chat import EvolveAgent
from .use_tool import UseToolAgent
from .find_tools import FindToolsAgent

if TYPE_CHECKING:
    from ..memory.service import MemoryService


logger = logging.getLogger(__name__)


class QDAgent:
    """智能体核心编排器

    管理组件生命周期，将用户输入传递给 EvolveAgent 执行。
    路由决策由 EvolveAgent 通过 delegate 工具自主完成。
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

        # Agent 实例
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

        # 创建 Agent 实例
        self._use_tool_agent = UseToolAgent(
            llm_client=self.llm,
            tool_registry=self.registry,
            context_manager=self.context,
            executor_registry=self.executor_registry,
            max_iterations=self.config.execution.max_use_tool_iterations,
            expanded_tool_map=self._tool_map_cache,
            context_window_size=self._get_context_window_size(),
            context_summarizer_threshold=self.config.execution.context_summarizer_threshold,
        )

        self._find_tools_agent = FindToolsAgent(
            llm_client=self.llm,
            tool_registry=self.registry,
            context_manager=self.context,
            executor_registry=self.executor_registry,
            max_iterations=self.config.execution.max_find_tools_iterations,
            context_window_size=self._get_context_window_size(),
            context_summarizer_threshold=self.config.execution.context_summarizer_threshold,
        )

        self._evolve_agent = EvolveAgent(
            llm_client=self.llm,
            tool_registry=self.registry,
            context_manager=self.context,
            executor_registry=self.executor_registry,
            expanded_tools_cache=self._expanded_tools_cache,
            max_iterations=self.config.execution.max_iterations,
            memory_service=self._memory_service,
            session_id=self._session_id,
            use_tool_agent=self._use_tool_agent,
            find_tools_agent=self._find_tools_agent,
            refresh_callback=self._refresh_tool_caches,
            context_window_size=self._get_context_window_size(),
            context_summarizer_threshold=self.config.execution.context_summarizer_threshold,
        )

        logger.info("QDAgent initialized with EvolveAgent + Use-Tool + Find-Tools. Models: %s", self.llm._model_names)

    @property
    def agent(self) -> EvolveAgent | None:
        """获取 EvolveAgent"""
        return self._evolve_agent

    async def process(
        self,
        user_input: str,
        session_id: str | None = None,
        on_step: StepCallback | None = None,
    ) -> AgentResult:
        """处理用户输入

        将用户输入传递给 EvolveAgent 执行。
        EvolveAgent 通过 delegate 工具自主路由到子 Agent。
        """
        trace_id = str(uuid.uuid4())
        start_time = time.perf_counter()
        logger.info("Processing user input (trace_id: %s): %s", trace_id, user_input[:100])

        # 创建取消信号
        self._cancel_event = asyncio.Event()

        try:
            if self._evolve_agent is None:
                raise ValueError("Agent not initialized")

            # 获取当前历史
            conversation_history = self.context.get_history()

            # 执行 EvolveAgent（路由决策由模型通过 delegate 工具自主完成）
            result = await self._evolve_agent.execute(
                user_input=user_input,
                history=conversation_history,
                trace_id=trace_id,
                on_step=on_step,
                cancel_event=self._cancel_event,
            )

            # 自动保存记忆
            self._auto_save_memory(user_input, result.final_answer, result.total_tokens)

            # 记录 QA 到历史
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

    # --- 工具缓存管理 ---

    async def _load_and_cache_tools(self) -> None:
        """预加载 MCP 工具并缓存展开后的工具列表"""
        all_tools = self.registry.list_all()
        mcp_tools = [t for t in all_tools if t.execution.type == ToolExecutionType.MCP]
        await self._mcp_service.preload(
            mcp_tools=mcp_tools,
            executor_registry=self.executor_registry,
        )

        expanded, openai, tool_map = self._tool_service.build_expanded_tools(
            registry=self.registry,
            mcp_tools_cache=self._mcp_service.tools_cache,
        )
        self._expanded_tools_cache = expanded
        self._openai_tools_cache = openai
        self._tool_map_cache = tool_map

    async def _refresh_tool_caches(self) -> None:
        """刷新工具缓存（Find-Tools 注册新工具后调用）"""
        logger.info("Refreshing tool caches after new tools registered...")

        all_tools = self.registry.list_all()
        mcp_tools = [t for t in all_tools if t.execution.type == ToolExecutionType.MCP]
        await self._mcp_service.preload(
            mcp_tools=mcp_tools,
            executor_registry=self.executor_registry,
        )

        expanded, openai, tool_map = self._tool_service.build_expanded_tools(
            registry=self.registry,
            mcp_tools_cache=self._mcp_service.tools_cache,
        )
        self._expanded_tools_cache = expanded
        self._openai_tools_cache = openai
        self._tool_map_cache = tool_map

        # 更新 EvolveAgent 的工具缓存
        if self._evolve_agent:
            self._evolve_agent._expanded_tools = expanded

        # 更新 UseToolAgent 的工具映射
        if self._use_tool_agent:
            self._use_tool_agent._expanded_tool_map = tool_map

        logger.info("Tool caches refreshed: %d expanded tools", len(expanded))

    # --- 生命周期管理 ---

    async def close(self) -> None:
        """关闭智能体"""
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
            fetch,
        )
        self.executor_registry.register_function("serper_search", serper_search)
        self.executor_registry.register_function("tavily_search", tavily_search)
        self.executor_registry.register_function("fetch", fetch)

        from ..tools.builtin_register import (
            tool_register_cli,
            tool_register_mcp,
            tool_register_skill,
            tool_register_http,
            tool_register_code,
            register_builtin_function_tools,
            register_meta_function_tools,
            delegate,
            ask_user,
            context_summarizer,
            tools_list,
        )
        self.executor_registry.register_function("tool_register_cli", tool_register_cli)
        self.executor_registry.register_function("tool_register_mcp", tool_register_mcp)
        self.executor_registry.register_function("tool_register_skill", tool_register_skill)
        self.executor_registry.register_function("tool_register_http", tool_register_http)
        self.executor_registry.register_function("tool_register_code", tool_register_code)

        # 元工具
        self.executor_registry.register_function("delegate", delegate)
        self.executor_registry.register_function("ask_user", ask_user)
        self.executor_registry.register_function("context_summarizer", context_summarizer)
        self.executor_registry.register_function("tools_list", tools_list)

        register_builtin_function_tools(self.registry)
        register_meta_function_tools(self.registry)

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

    def _get_context_window_size(self) -> int:
        """从配置中获取当前模型的上下文窗口大小"""
        provider_name = self.config.llm.default_provider
        provider_config = self.config.llm.providers.get(provider_name)
        if provider_config:
            model_spec = provider_config.get_model_spec(self.llm.current_model)
            if model_spec and model_spec.context_length:
                return model_spec.context_length
        return 0

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
                source="chat",
                model=self.llm.current_model,
                token_count=token_count,
            )
            logger.info("Auto-saved QA to memory (session=%s)", self._session_id[:8] if self._session_id else "?")
        except Exception:
            logger.exception("Auto-save memory failed")