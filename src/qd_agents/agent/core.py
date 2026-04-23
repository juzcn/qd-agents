"""
核心智能体实现

QDAgent 是 Agent 容器 + 资源管理器：
- 管理共享资源（MCP 连接、工具缓存、执行引擎、历史等）
- 注册和切换可用 Agent
- 将 process() 委托给当前 Agent
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from ..config import Config
from ..llm import LLMClient
from ..registry import ToolRegistry, Tool, ToolExecutionType, ToolMetadata
from ..prompts import PromptLoader
from ..execution import ExecutionEngine
from ..tools import ToolExecutorRegistry
from ..tools.executors.mcp import MCPToolExecutor
from ..utils import RetryConfig, RetryExecutor, CircuitBreaker, CircuitBreakerConfig
from ..context import ContextManager
from .base import Agent, AgentResult
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

        # 初始化上下文管理器
        self.context = context_manager or ContextManager(prompt_loader=prompt_loader)

        # 注册的 Agent 集合
        self._agents: dict[str, Agent] = {}
        self._current_agent: Agent | None = None

        # 初始化重试和熔断
        self._setup_retry_and_circuit_breaker()

        # MCP 工具缓存
        self._mcp_tools_cache: dict[str, list[Tool]] = {}
        self._mcp_executors_cache: dict[str, Any] = {}

        # 展开工具缓存
        self._expanded_tools_cache: list[Tool] | None = None
        self._openai_tools_cache: list[dict[str, Any]] | None = None
        self._tool_map_cache: dict[str, Tool] = {}

    def _setup_retry_and_circuit_breaker(self) -> None:
        """配置重试和熔断器"""
        self.retry_config = RetryConfig(
            max_attempts=self.config.execution.max_attempts,
            backoff_strategy=self.config.execution.backoff_strategy,
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

        self.retry_executor = RetryExecutor(
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

    # --- MCP 管理（委托给 MCPToolManager）---

    async def _preload_mcp_tools(self) -> None:
        """预加载 MCP 工具（会话开始时连接 MCP 服务器）"""
        logger.info("Preloading MCP tools...")

        all_tools = self.registry.list_all()
        mcp_tools = [t for t in all_tools if t.execution.type == ToolExecutionType.MCP]

        if not mcp_tools:
            logger.info("No MCP tools found to preload")
            return

        server_configs = {}
        for tool in mcp_tools:
            exec_config = tool.execution
            server_key = exec_config.server or ""
            if not server_key:
                logger.warning(f"MCP tool {tool.id} has no server configuration, skipping")
                continue
            if server_key in server_configs:
                continue

            server_configs[server_key] = {
                'type': ToolExecutionType.MCP,
                'server': server_key,
                'transport': exec_config.transport or "stdio",
                'command': exec_config.command,
                'args': exec_config.args,
                'endpoint': exec_config.endpoint,
                'headers': exec_config.headers,
                'env': exec_config.env or {},
                'timeout': exec_config.timeout,
                'tool': None,
            }

        if not server_configs:
            logger.warning("No valid MCP server configurations found")
            return

        async def connect_server(server_key: str, config: dict):
            try:
                subtools, executor = await self._get_mcp_server_tools(config)
                if subtools:
                    logger.info(f"Preloaded MCP server '{server_key}' with {len(subtools)} tools")
                else:
                    logger.warning(f"Failed to preload MCP server '{server_key}'")
            except Exception as e:
                logger.error(f"Error preloading MCP server '{server_key}': {e}")

        tasks = [
            connect_server(server_key, config)
            for server_key, config in server_configs.items()
        ]

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _cache_expanded_tools(self) -> None:
        """缓存展开后的工具列表和OpenAI格式工具"""
        logger.info("Caching expanded tools...")

        all_tools = self.registry.list_all()

        expanded_tools = []
        for tool in all_tools:
            if tool.execution.type != ToolExecutionType.MCP:
                expanded_tools.append(tool)
                continue

            exec_config = tool.execution
            server_key = exec_config.server or ""
            if server_key in self._mcp_tools_cache:
                mcp_subtools = self._mcp_tools_cache[server_key]
                if mcp_subtools:
                    expanded_tools.extend(mcp_subtools)
                else:
                    logger.warning(f"Cached MCP tools for server '{server_key}' is empty, keeping original tool")
                    expanded_tools.append(tool)
            else:
                logger.warning(f"MCP server '{server_key}' not in cache, keeping original tool")
                expanded_tools.append(tool)

        openai_tools = []
        search_web = self.registry.get("search.web")
        if search_web:
            openai_tools.append(search_web.to_openai_function())
            expanded_tools = [t for t in expanded_tools if t.id != "search.web"]

        openai_tools.extend([t.to_openai_function() for t in expanded_tools])

        self._expanded_tools_cache = expanded_tools
        self._openai_tools_cache = openai_tools
        self._tool_map_cache = {t.name: t for t in expanded_tools}

        logger.info(f"Cached {len(expanded_tools)} expanded tools and {len(openai_tools)} OpenAI tools")

    async def close(self) -> None:
        """关闭智能体（会话结束时调用）"""
        logger.info("Closing QDAgent...")

        for server_key, executor in self._mcp_executors_cache.items():
            try:
                await executor.close()
                logger.info(f"Closed MCP connection to server: {server_key}")
            except (RuntimeError, GeneratorExit, asyncio.CancelledError, BaseException) as e:
                logger.debug(f"Ignoring error closing MCP connection to server {server_key}: {type(e).__name__}: {e}")
            except Exception as e:
                logger.error(f"Error closing MCP connection to server {server_key}: {e}")

        self._mcp_tools_cache.clear()
        self._mcp_executors_cache.clear()
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

    async def _get_mcp_server_tools(self, server_config: dict) -> tuple[list[Tool], Any]:
        """获取 MCP 服务器的工具列表和执行器"""
        server_key = server_config.get('server', '')
        if not server_key:
            return [], None

        if server_key in self._mcp_tools_cache:
            logger.debug(f"Using cached MCP tools for server: {server_key}")
            return self._mcp_tools_cache[server_key], self._mcp_executors_cache.get(server_key)

        try:
            executor = MCPToolExecutor(
                server=server_key,
                transport=server_config.get('transport', 'stdio'),
                command=server_config.get('command'),
                args=server_config.get('args', []),
                url=server_config.get('endpoint'),
                headers=server_config.get('headers', {}),
                env=server_config.get('env', {}),
                timeout=server_config.get('timeout', 30),
            )

            default_connect_timeout = 5
            if "filesystem" in server_key.lower() or server_config.get('command') in ["npx", "node"]:
                default_connect_timeout = 15

            connect_timeout = server_config.get('timeout', default_connect_timeout)
            try:
                await asyncio.wait_for(executor._ensure_connected(), timeout=connect_timeout)
            except asyncio.TimeoutError:
                logger.error(f"MCP server {server_key} connection timeout after {connect_timeout}s")
                try:
                    await executor.close()
                except Exception:
                    pass
                return [], None
            except asyncio.CancelledError:
                logger.warning(f"MCP server {server_key} connection cancelled")
                try:
                    await executor.close()
                except Exception:
                    pass
                return [], None
            except Exception as e:
                logger.error(f"MCP server {server_key} connection failed: {e}")
                try:
                    await executor.close()
                except Exception:
                    pass
                return [], None

            mcp_tools = executor.get_cached_tools()

            if not mcp_tools:
                logger.warning(f"MCP server {server_key} returned no tools")
                try:
                    await executor.close()
                except Exception:
                    pass
                return [], None

            subtools = []
            for mcp_tool_name, mcp_tool in mcp_tools.items():
                subtool_id = f"mcp.{server_key}.{mcp_tool_name}"
                parameters = _extract_mcp_tool_parameters(mcp_tool)
                exec_config = server_config.copy()
                exec_config['tool'] = mcp_tool_name

                subtool = Tool(
                    id=subtool_id,
                    name=mcp_tool_name,
                    description=mcp_tool.description if hasattr(mcp_tool, 'description') else f"MCP tool: {mcp_tool_name}",
                    parameters=parameters,
                    execution=exec_config,
                    metadata=ToolMetadata(
                        category="mcp",
                        tags=[mcp_tool_name, 'mcp-subtool', server_key],
                    ),
                )
                subtools.append(subtool)

            self._mcp_tools_cache[server_key] = subtools
            self._mcp_executors_cache[server_key] = executor

            for subtool in subtools:
                self.executor_registry.register_executor(subtool.id, executor)

            logger.info(f"Connected to MCP server {server_key} and cached {len(subtools)} subtools")
            return subtools, executor

        except Exception as e:
            logger.error(f"Failed to connect to MCP server {server_key}: {e}")
            return [], None


def _extract_mcp_tool_parameters(mcp_tool: Any) -> dict[str, Any]:
    """从 mcp.Tool 对象提取参数 schema"""
    if hasattr(mcp_tool, 'input_schema'):
        input_schema = mcp_tool.input_schema
        if isinstance(input_schema, dict):
            return input_schema

    return {
        "type": "object",
        "properties": {
            "arguments": {
                "type": "object",
                "description": "工具参数",
                "additionalProperties": True,
            }
        },
        "required": ["arguments"],
    }