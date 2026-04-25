"""
QDAgent 核心容器

负责 Agent 注册/切换、process 委托和历史管理。
MCP 连接管理委托给 MCPService，工具注册/缓存委托给 ToolService。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from ..config import Config
from ..llm import LLMClient
from ..models.tool import Tool, ToolExecutionType, ToolMetadata
from ..registry import ToolRegistry
from ..prompts import PromptLoader
from ..execution import ExecutionEngine
from ..tools import ToolExecutorRegistry
from ..tools.executors.mcp import MCPToolExecutor
from ..utils import RetryConfig, RetryExecutor, CircuitBreaker, CircuitBreakerConfig, BackoffStrategy
from ..context import ContextManager
from .base import Agent, AgentResult
from .mcp_service import MCPService
from .tool_service import ToolService
from .tool_use import ToolUseAgent

logger = logging.getLogger(__name__)


class QDAgent:
    """
    主智能体类 — Agent 容器 + 资源管理器

    不直接处理用户输入，而是委托给注册的 Agent。
    """

    def __init__(
        self,
        config: Config,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        prompt_loader: PromptLoader | None = None,
        execution_engine: ExecutionEngine | None = None,
        context_manager: ContextManager | None = None,
        base_dir: Path | None = None,
    ):
        self.config = config
        self.llm = llm_client
        self.registry = tool_registry
        self.prompts = prompt_loader
        self.execution = execution_engine or ExecutionEngine(
            allowed_modules=self.config.execution.code_exec_allowed_modules,
            blocked_builtins=self.config.execution.code_exec_blocked_builtins,
            timeout=self.config.execution.code_exec_timeout,
        )
        self.executor_registry = ToolExecutorRegistry()
        self.base_dir = base_dir

        # 初始化上下文管理器
        self.context = context_manager or ContextManager(
            prompt_loader=prompt_loader,
            base_dir=base_dir,
        )

        # 注册的 Agent 集合
        self._agents: dict[str, Agent] = {}
        self._current_agent: Agent | None = None

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

        # 将工具注册到执行引擎（用于 Code-Plan 模式）
        await self._register_tools_to_execution_engine()

        # 创建并注册 Agent
        self._register_agents()

        # 根据配置选择默认 Agent
        default_agent_name = self.config.llm.default_agent
        if default_agent_name in self._agents:
            self._current_agent = self._agents[default_agent_name]
            logger.info("Using default agent: %s", default_agent_name)
        else:
            # 降级到第一个可用 Agent
            if self._agents:
                first_name = next(iter(self._agents))
                self._current_agent = self._agents[first_name]
                logger.warning("Default agent '%s' not found, using '%s'", default_agent_name, first_name)
            else:
                logger.error("No agents registered!")

        logger.info("QDAgent initialized. Models: %s", self.llm._model_names)

    def _register_agents(self) -> None:
        """创建并注册所有可用 Agent"""
        # Tool Use Agent
        tool_use_agent = ToolUseAgent(
            llm_client=self.llm,
            tool_registry=self.registry,
            context_manager=self.context,
            executor_registry=self.executor_registry,
            expanded_tools_cache=self._expanded_tools_cache,
            openai_tools_cache=self._openai_tools_cache,
            tool_map_cache=self._tool_map_cache,
        )
        self._agents[tool_use_agent.name] = tool_use_agent
        logger.info("Registered agent: %s", tool_use_agent.name)

        # Code Plan Agent
        from .code_plan import CodePlanAgent
        code_plan_agent = CodePlanAgent(
            llm_client=self.llm,
            tool_registry=self.registry,
            context_manager=self.context,
            executor_registry=self.executor_registry,
            prompt_loader=self.prompts,
            expanded_tools_cache=self._expanded_tools_cache,
            openai_tools_cache=self._openai_tools_cache,
            tool_map_cache=self._tool_map_cache,
        )
        self._agents[code_plan_agent.name] = code_plan_agent
        logger.info("Registered agent: %s", code_plan_agent.name)

        # Evolve Agent
        from .evolve import EvolveAgent
        evolve_agent = EvolveAgent(
            llm_client=self.llm,
            tool_registry=self.registry,
            context_manager=self.context,
            expanded_tools_cache=self._expanded_tools_cache,
        )
        self._agents[evolve_agent.name] = evolve_agent
        logger.info("Registered agent: %s", evolve_agent.name)

    @property
    def registered_agents(self) -> dict[str, Agent]:
        """获取所有已注册的 Agent"""
        return self._agents

    @property
    def current_agent(self) -> Agent | None:
        """获取当前 Agent"""
        return self._current_agent

    @property
    def current_agent_name(self) -> str:
        """获取当前 Agent 名称"""
        return self._current_agent.name if self._current_agent else ""

    def switch_agent(self, agent_name: str) -> bool:
        """切换当前 Agent"""
        if agent_name not in self._agents:
            logger.warning("Agent '%s' not found", agent_name)
            return False

        self._current_agent = self._agents[agent_name]
        logger.info("Switched to agent: %s", agent_name)
        return True

    async def process(
        self,
        user_input: str,
        session_id: str | None = None,
    ) -> AgentResult:
        """处理用户输入，委托给当前 Agent 执行。"""
        trace_id = str(uuid.uuid4())
        logger.info("Processing user input (trace_id: %s): %s", trace_id, user_input[:100])

        # 添加用户输入到历史
        self.add_to_history("user", user_input)

        try:
            if self._current_agent is None:
                raise ValueError("No agent selected")

            # 获取历史（不包含当前这条用户输入，因为 Agent 内部会处理）
            history = self.context.get_history()
            conversation_history = []
            if history:
                for msg in history[:-1]:
                    conversation_history.append({
                        "role": msg["role"],
                        "content": msg["content"],
                    })

            # 委托给当前 Agent
            result = await self._current_agent.execute(
                user_input=user_input,
                history=conversation_history,
                trace_id=trace_id,
            )

            # 添加到历史
            self.add_to_history("assistant", result.final_answer)

            return result

        except Exception as e:
            logger.exception("Processing failed")
            error_msg = f"抱歉，处理失败: {e}"
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
        logger.info("QDAgent closed")

    async def _register_builtin_tools(self) -> None:
        """注册内置工具执行器"""
        from ..tools.builtins import echo
        self.executor_registry.register_function("echo", echo)

        from ..tools.builtin_search import (
            serper_search,
            tavily_search,
        )
        self.executor_registry.register_function("serper_search", serper_search)
        self.executor_registry.register_function("tavily_search", tavily_search)

        logger.info("Registered builtin tool executors")

    async def _register_tools_to_execution_engine(self) -> None:
        """将工具注册到执行引擎（用于 Code-Plan 模式）"""
        logger.info("Registering tools to execution engine...")

        from ..tools.builtins import echo
        self.execution.register_tool_func("echo", echo)

        from ..tools.builtin_search import serper_search, tavily_search
        self.execution.register_tool_func("serper_search", serper_search)
        self.execution.register_tool_func("tavily_search", tavily_search)

        all_tools = self.registry.list_all()
        for tool in all_tools:
            if tool.name in ["echo", "serper_search", "tavily_search"]:
                continue

            if tool.execution.type == ToolExecutionType.SKILL:
                continue

            try:
                executor = self.executor_registry.get_executor(tool)
                if executor:
                    async def tool_wrapper(**kwargs):
                        return await executor.execute(**kwargs)

                    self.execution.register_tool_func(tool.name, tool_wrapper)
                    logger.debug(f"Registered tool {tool.name} to execution engine")
            except Exception as e:
                logger.warning(f"Failed to register tool {tool.name} to execution engine: {e}")

        logger.info(f"Registered tools to execution engine")

    def add_to_history(self, role: str, content: str) -> None:
        """添加消息到会话历史"""
        self.context.add_to_history(role, content)

    def clear_history(self) -> None:
        """清空会话历史"""
        self.context.clear_history()

    def get_history(self) -> list[dict[str, str]]:
        """获取会话历史"""
        return self.context.get_history()
