"""
核心智能体实现
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any

from ..config import Config
from ..llm import LLMClient
from ..registry import ToolRegistry, Tool, ToolExecutionType, ToolMetadata
from ..prompts import PromptLoader
from ..execution import ExecutionEngine
from ..orchestrator import TwoPhaseOrchestrator, OrchestrationResult
from ..tools import ToolExecutor, ToolExecutorRegistry, create_executor
from ..utils import RetryConfig, RetryExecutor, CircuitBreaker, CircuitBreakerConfig
from ..context import ContextManager


logger = logging.getLogger(__name__)


def _format_search_result(result: dict[str, Any]) -> str:
    """格式化搜索结果为易读的文本（不直接使用 Tavily 的英文 answer）"""
    try:
        # 提取搜索结果列表（不使用 Tavily 的 answer）
        results = result.get("results", [])
        if results:
            output = ""
            for i, r in enumerate(results[:5], 1):
                title = r.get("title", "")
                snippet = r.get("snippet", r.get("content", ""))
                link = r.get("url", r.get("link", ""))
                if title:
                    output += f"\n[{i}] {title}"
                    if snippet:
                        output += f"\n    {snippet}"
                    if link:
                        output += f"\n    {link}"
            return output.strip()

        # 其他格式
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return json.dumps(result, ensure_ascii=False)


def _format_references(result: dict[str, Any]) -> str:
    """格式化参考链接"""
    results = result.get("results", [])
    if not results:
        return ""

    output = "参考链接：\n"
    for i, r in enumerate(results[:3], 1):
        title = r.get("title", "")
        link = r.get("url", r.get("link", ""))
        if title and link:
            output += f"{i}. {title}: {link}\n"
    return output.strip()


class AgentResult:
    """智能体处理结果"""

    def __init__(
        self,
        trace_id: str,
        final_output: str,
        orchestration_result: OrchestrationResult | None = None,
        execution_result: Any = None,
        total_duration_ms: int = 0,
    ):
        self.trace_id = trace_id
        self.final_output = final_output
        self.orchestration_result = orchestration_result
        self.execution_result = execution_result
        self.total_duration_ms = total_duration_ms


class QDAgent:
    """
    主智能体类

    整合所有组件，提供完整的智能体功能
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
        """
        初始化智能体

        Args:
            config: 配置对象
            llm_client: LLM 客户端
            tool_registry: 工具注册中心
            prompt_loader: 提示词加载器
            execution_engine: 执行引擎
            context_manager: 上下文管理器
        """
        self.config = config
        self.llm = llm_client
        self.registry = tool_registry
        self.prompts = prompt_loader
        self.execution = execution_engine or ExecutionEngine()
        self.executor_registry = ToolExecutorRegistry()

        # 初始化上下文管理器
        self.context = context_manager or ContextManager(prompt_loader=prompt_loader)

        # 初始化调度器
        self.orchestrator = TwoPhaseOrchestrator(
            llm_client=llm_client,
            tool_registry=tool_registry,
            context_manager=self.context,
            prompt_loader=prompt_loader,
            tool_threshold=config.llm.tool_threshold,
        )

        # 初始化重试和熔断
        self._setup_retry_and_circuit_breaker()

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

        # MCP 工具缓存
        self._mcp_tools_cache: dict[str, list[Tool]] = {}  # server -> list of subtools
        self._mcp_executors_cache: dict[str, Any] = {}  # server -> MCPToolExecutor

        # 展开工具缓存（避免每次调用都展开MCP工具）
        self._expanded_tools_cache: list[Tool] | None = None
        self._openai_tools_cache: list[dict[str, Any]] | None = None
        self._tool_map_cache: dict[str, Tool] = {}  # tool_name -> Tool object

    async def initialize(self) -> None:
        """初始化智能体"""
        logger.info("Initializing QDAgent...")

        # 发现 LLM 模型
        if not self.llm.current_model:
            await self.llm.discover_models(top_k=5)

        # 注册内置工具
        await self._register_builtin_tools()

        # 预加载 MCP 工具（在会话开始时连接 MCP 服务器）
        await self._preload_mcp_tools()

        # 初始化调度器
        await self.orchestrator.initialize()

        logger.info("QDAgent initialized. Models: %s", self.llm._model_names)

    async def _preload_mcp_tools(self) -> None:
        """预加载 MCP 工具（会话开始时连接 MCP 服务器）"""
        logger.info("Preloading MCP tools...")

        # 获取所有 MCP 工具
        all_tools = self.registry.list_all()
        mcp_tools = [t for t in all_tools if t.execution.type == ToolExecutionType.MCP]

        if not mcp_tools:
            logger.info("No MCP tools found to preload")
            return

        # 为每个 MCP 服务器连接并缓存工具
        for tool in mcp_tools:
            exec_config = tool.execution
            server_config = {
                'type': ToolExecutionType.MCP,
                'server': exec_config.server or "",
                'transport': exec_config.transport or "stdio",
                'command': exec_config.command,
                'args': exec_config.args,
                'endpoint': exec_config.endpoint,
                'headers': exec_config.headers,
                'timeout': exec_config.timeout,
                'tool': None,
            }

            # 连接 MCP 服务器并缓存工具
            try:
                subtools, executor = await self._get_mcp_server_tools(server_config)
                if subtools:
                    logger.info(f"Preloaded MCP server '{exec_config.server}' with {len(subtools)} tools")
                else:
                    logger.warning(f"Failed to preload MCP server '{exec_config.server}'")
            except Exception as e:
                logger.error(f"Error preloading MCP server '{exec_config.server}': {e}")

        # 缓存展开后的工具列表（避免每次调用都展开）
        await self._cache_expanded_tools()

    async def _cache_expanded_tools(self) -> None:
        """缓存展开后的工具列表和OpenAI格式工具"""
        logger.info("Caching expanded tools...")

        # 获取所有工具
        all_tools = self.registry.list_all()

        # 展开 MCP 工具（使用缓存）
        expanded_tools = []
        for tool in all_tools:
            if tool.execution.type != ToolExecutionType.MCP:
                # 非 MCP 工具直接添加
                expanded_tools.append(tool)
                continue

            # 检查缓存中是否有该服务器的工具
            exec_config = tool.execution
            server_key = exec_config.server or ""
            if server_key in self._mcp_tools_cache:
                mcp_subtools = self._mcp_tools_cache[server_key]
                if mcp_subtools:
                    # 添加所有子工具
                    expanded_tools.extend(mcp_subtools)
                else:
                    # 缓存为空，保留原始工具
                    logger.warning(f"Cached MCP tools for server '{server_key}' is empty, keeping original tool")
                    expanded_tools.append(tool)
            else:
                # 缓存中没有该服务器的工具，保留原始工具
                logger.warning(f"MCP server '{server_key}' not in cache, keeping original tool")
                expanded_tools.append(tool)

        # 构建 OpenAPI 格式工具
        openai_tools = []
        # 优先添加 search.web 工具（如果可用）
        search_web = self.registry.get("search.web")
        if search_web:
            openai_tools.append(search_web.to_openai_function())
            # 从 expanded_tools 中移除 search.web 工具，避免重复
            expanded_tools = [t for t in expanded_tools if t.id != "search.web"]

        # 添加其他工具
        openai_tools.extend([t.to_openai_function() for t in expanded_tools])

        # 缓存结果
        self._expanded_tools_cache = expanded_tools
        self._openai_tools_cache = openai_tools

        # 构建工具映射（用于快速查找）
        self._tool_map_cache = {t.name: t for t in expanded_tools}

        logger.info(f"Cached {len(expanded_tools)} expanded tools and {len(openai_tools)} OpenAI tools")

    async def close(self) -> None:
        """关闭智能体（会话结束时调用）"""
        logger.info("Closing QDAgent...")

        # 关闭所有 MCP 连接
        for server_key, executor in self._mcp_executors_cache.items():
            try:
                await executor.close()
                logger.info(f"Closed MCP connection to server: {server_key}")
            except Exception as e:
                logger.error(f"Error closing MCP connection to server {server_key}: {e}")

        # 关闭调度器
        await self.orchestrator.close()

        # 清空缓存
        self._mcp_tools_cache.clear()
        self._mcp_executors_cache.clear()
        logger.info("QDAgent closed")

    async def _register_builtin_tools(self) -> None:
        """注册内置工具执行器（工具定义已通过 tools init 命令注册到数据库）"""
        # 注册 echo 工具
        from .builtins import echo
        self.executor_registry.register_function("echo", echo)

        # 注册搜索工具函数
        from .builtin_tools import (
            serper_search,
            tavily_search,
            baidu_search,
            meta_direct,
            meta_find_tools,
            meta_coding_tool_use,
            meta_step_down,
        )
        self.executor_registry.register_function("serper_search", serper_search)
        self.executor_registry.register_function("tavily_search", tavily_search)
        self.executor_registry.register_function("baidu_search", baidu_search)
        # 注册元工具执行器（占位实现）
        self.executor_registry.register_function("meta_direct", meta_direct)
        self.executor_registry.register_function("meta_find_tools", meta_find_tools)
        self.executor_registry.register_function("meta_coding_tool_use", meta_coding_tool_use)
        self.executor_registry.register_function("meta_step_down", meta_step_down)

        logger.info("Registered builtin tool executors")

    def add_to_history(self, role: str, content: str) -> None:
        """
        添加消息到会话历史

        Args:
            role: 角色 (user/assistant/system/tool)
            content: 内容
        """
        self.context.add_to_history(role, content)

    def clear_history(self) -> None:
        """清空会话历史"""
        self.context.clear_history()

    def get_history(self) -> list[dict[str, str]]:
        """获取会话历史"""
        return self.context.get_history()

    async def process(
        self,
        user_input: str,
        session_id: str | None = None,
    ) -> AgentResult:
        """
        处理用户输入

        Args:
            user_input: 用户输入
            session_id: 会话 ID

        Returns:
            处理结果
        """
        import time
        start_time = time.perf_counter()
        trace_id = str(uuid.uuid4())

        logger.info("Processing user input (trace_id: %s): %s", trace_id, user_input[:100])

        # 添加用户输入到历史
        self.add_to_history("user", user_input)

        try:
            # 使用 OpenAI Tool Calling 规范直接处理
            # 获取历史消息（不包含当前这条用户输入，因为会在循环中添加）
            history = self.context.get_history()
            conversation_history = []
            if history:
                # 转换历史消息格式
                for msg in history[:-1]:  # 排除最后一条用户输入
                    conversation_history.append({
                        "role": msg["role"],
                        "content": msg["content"]
                    })

            # 执行 OpenAI Tool Calling 循环
            final_output, full_messages = await self._openai_tool_calling_loop(
                user_input=user_input,
                session_id=session_id,
                trace_id=trace_id,
                history=conversation_history,
            )

            # 添加到历史
            self.add_to_history("assistant", final_output)

            total_duration = int((time.perf_counter() - start_time) * 1000)

            # 创建简化的调度结果（保持向后兼容）
            from ..orchestrator import OrchestrationResult
            orch_result = OrchestrationResult(
                trace_id=trace_id,
                session_id=session_id,
                user_input=user_input,
                final_output=final_output,
                final_status="completed",
                total_latency_ms=total_duration,
                messages=full_messages,
            )

            return AgentResult(
                trace_id=trace_id,
                final_output=final_output,
                orchestration_result=orch_result,
                total_duration_ms=total_duration,
            )

        except Exception as e:
            logger.exception("Processing failed")
            error_msg = f"抱歉，处理失败: {e}"
            self.add_to_history("assistant", error_msg)

            total_duration = int((time.perf_counter() - start_time) * 1000)

            return AgentResult(
                trace_id=trace_id,
                final_output=error_msg,
                total_duration_ms=total_duration,
            )

    async def _execute_orchestration(self, orch_result: OrchestrationResult) -> str:
        """执行调度结果"""
        # 如果已经有最终输出，直接返回
        if orch_result.final_output:
            return str(orch_result.final_output)

        if not orch_result.phase_two:
            return "没有生成执行计划"

        phase_two = orch_result.phase_two

        # 处理 coding_tool_use
        if phase_two.tool_choice == "coding_tool_use" and phase_two.generated_code:
            logger.info("Executing generated code")
            try:
                exec_result = await self.execution.execute_code(
                    code=phase_two.generated_code,
                    session_id=orch_result.session_id,
                    trace_id=orch_result.trace_id,
                )
                return f"代码执行结果: {exec_result.final_output}"
            except Exception as e:
                return f"代码执行失败: {e}"

        # 处理 step_down
        if phase_two.tool_choice == "step_down":
            reason = phase_two.tool_input.get("reason", "unknown")
            message = phase_two.tool_input.get("message", "")
            return f"[降级处理] {reason}: {message}"

        # 处理普通工具调用
        tool_name = phase_two.tool_choice
        if tool_name and tool_name not in ["direct", "find_tools", "coding_tool_use", "step_down"]:
            # 先尝试通过 ID 查找，再通过名称查找
            tool = self.registry.get(tool_name) or self.registry.get_by_name(tool_name)
            if tool:
                try:
                    logger.info("Executing tool: %s (id: %s)", tool.name, tool.id)
                    executor = self.executor_registry.get_executor(tool)
                    tool_input = phase_two.tool_input
                    result = await executor.execute(**tool_input)

                    # 对于搜索工具，按照 OpenAI tool calling 标准流程：
                    # 1. 获取工具执行结果
                    # 2. 将结果传给 LLM，让 LLM 用用户的语言总结回答
                    if tool.id.startswith("search."):
                        return await self._summarize_search_results(
                            user_input=orch_result.user_input,
                            search_result=result,
                        )

                    # 对于其他工具（如天气工具），也按照 OpenAI tool calling 标准流程
                    # 让 LLM 用用户的语言总结回答
                    logger.info("Calling _summarize_tool_result for tool: %s (id: %s), result type: %s",
                               tool.name, tool.id, type(result).__name__)
                    return await self._summarize_tool_result(
                        user_input=orch_result.user_input,
                        tool_name=tool.name,
                        tool_result=result,
                    )
                except Exception as e:
                    logger.exception("Tool execution failed")
                    return f"工具调用失败: {e}"
            else:
                logger.warning("Tool not found: %s", tool_name)
                return f"工具未找到: {tool_name}"

        # 默认返回 - 不应该到达这里，如果到达说明有逻辑错误
        logger.warning("Reached default return in _execute_orchestration, tool_choice: %s", phase_two.tool_choice if phase_two else None)
        return "处理完成"

    async def _summarize_search_results(
        self,
        user_input: str,
        search_result: dict[str, Any],
    ) -> str:
        """让 LLM 根据搜索结果用中文总结回答"""
        # 格式化搜索结果为文本
        formatted_results = _format_search_result(search_result)

        messages = [
            {
                "role": "system",
                "content": "你是一个专业的助手。请根据以下搜索结果，用中文回答用户的问题。回答要准确、客观，使用搜索结果中的信息。"
            },
            {
                "role": "user",
                "content": f"用户问题: {user_input}\n\n搜索结果:\n{formatted_results}"
            }
        ]

        try:
            response = await self.llm.chat(
                messages=messages,
                temperature=0.7,
            )
            answer = response.choices[0].message.content or "抱歉，无法生成回答"

            # 添加参考链接
            references = _format_references(search_result)
            if references:
                answer += "\n\n" + references

            return answer
        except Exception as e:
            logger.exception("Failed to summarize search results")
            # 如果 LLM 总结失败，返回格式化的搜索结果
            return _format_search_result(search_result)

    async def _summarize_tool_result(
        self,
        user_input: str,
        tool_name: str,
        tool_result: Any,
    ) -> str:
        """让 LLM 根据工具执行结果用中文总结回答"""
        logger.info("Summarizing tool result for tool: %s, result type: %s",
                   tool_name, type(tool_result).__name__)

        # 格式化工具结果为文本
        try:
            if isinstance(tool_result, dict):
                formatted_result = json.dumps(tool_result, ensure_ascii=False, indent=2)
                logger.debug("Tool result is dict, keys: %s", list(tool_result.keys()))
            else:
                formatted_result = str(tool_result)
                logger.debug("Tool result is not dict: %s", formatted_result[:200])
        except Exception as e:
            formatted_result = str(tool_result)
            logger.warning("Failed to format tool result: %s", e)

        messages = [
            {
                "role": "system",
                "content": f"你是一个专业的助手。请根据以下{tool_name}工具的执行结果，用中文回答用户的问题。回答要自然、友好，使用工具结果中的信息。"
            },
            {
                "role": "user",
                "content": f"用户问题: {user_input}\n\n工具执行结果:\n{formatted_result}"
            }
        ]

        try:
            logger.info("Calling LLM to summarize tool result for tool: %s", tool_name)
            response = await self.llm.chat(
                messages=messages,
                temperature=0.7,
            )
            answer = response.choices[0].message.content or "抱歉，无法生成回答"
            logger.info("LLM summarization successful for tool: %s", tool_name)
            return answer
        except Exception as e:
            logger.exception("Failed to summarize tool result for tool: %s", tool_name)
            # 如果 LLM 总结失败，返回格式化的工具结果
            return f"工具调用结果: {formatted_result}"

    async def _get_mcp_server_tools(self, server_config: dict) -> tuple[list[Tool], Any]:
        """
        获取 MCP 服务器的工具列表和执行器

        Args:
            server_config: MCP服务器配置字典，包含server, transport, command等字段

        Returns:
            (工具列表, MCP执行器)
        """
        from ..tools.executors.mcp import MCPToolExecutor

        server_key = server_config.get('server', '')
        if not server_key:
            return [], None

        # 检查缓存
        if server_key in self._mcp_tools_cache:
            logger.debug(f"Using cached MCP tools for server: {server_key}")
            return self._mcp_tools_cache[server_key], self._mcp_executors_cache.get(server_key)

        try:
            # 创建 MCP 执行器
            executor = MCPToolExecutor(
                server=server_key,
                transport=server_config.get('transport', 'stdio'),
                command=server_config.get('command'),
                args=server_config.get('args', []),
                url=server_config.get('endpoint'),  # 复用 endpoint 作为 URL
                headers=server_config.get('headers', {}),
                timeout=server_config.get('timeout', 30),
            )

            # 连接到服务器（异步上下文管理器）
            # 注意：这里我们不使用 async with，因为我们要保持连接打开
            # 设置连接超时（使用配置的超时或默认5秒）
            connect_timeout = server_config.get('timeout', 5)
            try:
                await asyncio.wait_for(executor._ensure_connected(), timeout=connect_timeout)
            except asyncio.TimeoutError:
                logger.error(f"MCP server {server_key} connection timeout after {connect_timeout}s")
                return [], None
            except asyncio.CancelledError:
                logger.warning(f"MCP server {server_key} connection cancelled")
                # 不重新抛出，直接返回空结果
                return [], None
            except Exception as e:
                logger.error(f"MCP server {server_key} connection failed: {e}")
                return [], None

            # 获取服务器提供的所有工具
            mcp_tools = executor.get_cached_tools()

            if not mcp_tools:
                logger.warning(f"MCP server {server_key} returned no tools")
                return [], None

            # 为每个 MCP 工具创建独立的 Tool 对象
            subtools = []
            for mcp_tool_name, mcp_tool in mcp_tools.items():
                # 创建子工具 ID
                subtool_id = f"mcp.{server_key}.{mcp_tool_name}"

                # 提取参数 schema
                parameters = self._extract_mcp_tool_parameters(mcp_tool)

                # 创建执行配置，包含具体工具名
                exec_config = server_config.copy()
                exec_config['tool'] = mcp_tool_name

                # 创建 Tool 对象
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

            # 缓存结果
            self._mcp_tools_cache[server_key] = subtools
            self._mcp_executors_cache[server_key] = executor

            # 为每个子工具注册执行器到工具执行器注册表
            for subtool in subtools:
                self.executor_registry.register_executor(subtool.id, executor)

            logger.info(f"Connected to MCP server {server_key} and cached {len(subtools)} subtools")
            return subtools, executor

        except Exception as e:
            logger.error(f"Failed to connect to MCP server {server_key}: {e}")
            return [], None

    async def _expand_mcp_tools(self, tools: list[Tool]) -> list[Tool]:
        """
        展开 MCP 工具

        对于 MCP 类型的工具，连接到 MCP 服务器，获取所有可用的工具，
        并将它们作为独立的工具返回。

        使用缓存的 MCP 工具列表，避免重复连接。

        Args:
            tools: 原始工具列表

        Returns:
            展开后的工具列表（MCP 工具被替换为服务器提供的具体工具）
        """
        expanded_tools = []

        for tool in tools:
            if tool.execution.type != ToolExecutionType.MCP:
                # 非 MCP 工具直接添加
                expanded_tools.append(tool)
                continue

            # 获取 MCP 服务器配置
            exec_config = tool.execution
            server_key = exec_config.server or ""

            if not server_key:
                logger.warning(f"MCP tool {tool.id} has no server configuration, keeping original tool")
                expanded_tools.append(tool)
                continue

            # 检查缓存中是否有该服务器的工具
            if server_key in self._mcp_tools_cache:
                # 使用缓存的子工具
                mcp_subtools = self._mcp_tools_cache[server_key]
                if mcp_subtools:
                    # 添加所有子工具
                    expanded_tools.extend(mcp_subtools)
                    logger.debug(f"Expanded MCP tool {tool.id} into {len(mcp_subtools)} subtools (from cache)")
                else:
                    # 缓存为空，保留原始工具
                    logger.warning(f"Cached MCP tools for server '{server_key}' is empty, keeping original tool")
                    expanded_tools.append(tool)
            else:
                # 缓存中没有该服务器的工具，这可能意味着预加载失败
                # 为了兼容性，保留原始工具
                logger.warning(f"MCP server '{server_key}' not in cache, preload may have failed, keeping original tool")
                expanded_tools.append(tool)

        return expanded_tools

    def _extract_mcp_tool_parameters(self, mcp_tool: Any) -> dict[str, Any]:
        """
        从 mcp.Tool 对象提取参数 schema

        Args:
            mcp_tool: mcp.Tool 对象

        Returns:
            参数 schema 字典
        """
        # 尝试从 input_schema 提取
        if hasattr(mcp_tool, 'input_schema'):
            input_schema = mcp_tool.input_schema
            if isinstance(input_schema, dict):
                # 直接使用 input_schema
                return input_schema

        # 如果没有 input_schema，尝试从其他属性提取
        # 或者返回一个通用的 schema
        return {
            "type": "object",
            "properties": {
                "arguments": {
                    "type": "object",
                    "description": "工具参数",
                    "additionalProperties": True
                }
            },
            "required": ["arguments"]
        }

    async def _openai_tool_calling_loop(
        self,
        user_input: str,
        session_id: str | None = None,
        trace_id: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        OpenAI Tool Calling 规范循环

        遵循 OpenAI Tool Calling 规范：
        1. 获取所有工具（展开 MCP 工具）
        2. 构建消息
        3. 调用 LLM
        4. 如果 LLM 返回 tool_calls，执行工具
        5. 将工具执行结果作为 tool 消息添加到对话
        6. 重复直到 LLM 不再调用工具

        Args:
            user_input: 用户输入
            session_id: 会话 ID
            trace_id: 追踪 ID
            history: 会话历史

        Returns:
            (最终输出, 完整消息历史)
        """
        import json

        # 使用缓存的工具列表（避免每次调用都展开MCP工具）
        if self._expanded_tools_cache is None or self._openai_tools_cache is None:
            logger.warning("Expanded tools cache is empty, trying to rebuild...")
            await self._cache_expanded_tools()

        # 使用缓存的工具列表
        expanded_tools = self._expanded_tools_cache or []
        openai_tools = self._openai_tools_cache or []
        tool_map = self._tool_map_cache or {}

        # 构建初始消息
        messages = []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        # 最大循环次数限制
        max_iterations = 10
        iteration = 0

        while iteration < max_iterations:
            iteration += 1

            # 调用 LLM - 始终提供工具列表，遵循 OpenAI Tool Calling 规范
            response = await self.llm.chat(
                messages=messages,
                tools=openai_tools,
                tool_choice="auto",
            )

            choice = response.choices[0]
            assistant_message = choice.message

            # 添加 assistant 消息到历史
            messages.append({
                "role": "assistant",
                "content": assistant_message.content,
                "tool_calls": assistant_message.tool_calls if hasattr(assistant_message, 'tool_calls') else None,
            })

            # 检查是否调用了工具
            if not assistant_message.tool_calls:
                # LLM 直接回复，没有调用工具
                final_output = assistant_message.content or "抱歉，无法生成回答"
                return final_output, messages

            # 执行所有工具调用
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_input = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_input = {"raw": tool_call.function.arguments}

                # 查找工具 - 首先从缓存的工具映射中查找
                tool = tool_map.get(tool_name)

                # 如果没找到，再从注册表中查找
                if not tool:
                    tool = self.registry.get(tool_name) or self.registry.get_by_name(tool_name)

                if not tool:
                    # 工具未找到，返回错误
                    tool_result = f"工具未找到: {tool_name}"
                else:
                    try:
                        # 执行工具
                        logger.info("Executing tool: %s (id: %s)", tool.name, tool.id)
                        executor = self.executor_registry.get_executor(tool)
                        # 对于MCP工具，确保传递tool_name参数
                        if tool.execution.type == ToolExecutionType.MCP:
                            # MCP执行器需要tool_name参数
                            tool_input_with_name = {"tool_name": tool.name, **tool_input}
                            tool_result = await executor.execute(**tool_input_with_name)
                        else:
                            tool_result = await executor.execute(**tool_input)
                        # 确保工具结果是字符串
                        if not isinstance(tool_result, str):
                            tool_result = json.dumps(tool_result, ensure_ascii=False)
                    except Exception as e:
                        logger.exception("Tool execution failed")
                        tool_result = f"工具调用失败: {e}"

                # 添加工具结果消息到对话
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                })

        # 达到最大迭代次数
        return "达到最大工具调用迭代次数，请简化您的问题。", messages
