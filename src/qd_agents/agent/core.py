"""
核心智能体实现
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any

from ..config import Config
from ..llm import LLMClient
from ..registry import ToolRegistry, Tool
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

    async def initialize(self) -> None:
        """初始化智能体"""
        logger.info("Initializing QDAgent...")

        # 发现 LLM 模型
        if not self.llm.current_model:
            await self.llm.discover_models(top_k=5)

        # 注册内置工具
        await self._register_builtin_tools()

        logger.info("QDAgent initialized. Models: %s", self.llm._model_names)

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
            web_search,
            meta_direct,
            meta_find_tools,
            meta_coding_tool_use,
            meta_step_down,
        )
        self.executor_registry.register_function("serper_search", serper_search)
        self.executor_registry.register_function("tavily_search", tavily_search)
        self.executor_registry.register_function("baidu_search", baidu_search)
        self.executor_registry.register_function("web_search", web_search)
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
            # 执行调度 - 传递历史消息（不包含当前这条用户输入，因为会在 orchestrator 中添加）
            # 历史消息只包含之前的 user/assistant 对话
            history = self.context.get_history()
            orch_result = await self.orchestrator.orchestrate(
                user_input=user_input,
                session_id=session_id,
                trace_id=trace_id,
                history=history[:-1] if history else None,
            )

            # 执行工具/代码
            final_output = await self._execute_orchestration(orch_result)

            # 添加到历史
            self.add_to_history("assistant", final_output)

            total_duration = int((time.perf_counter() - start_time) * 1000)

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

        # 默认返回
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
