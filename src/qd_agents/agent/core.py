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


logger = logging.getLogger(__name__)


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
    ):
        """
        初始化智能体

        Args:
            config: 配置对象
            llm_client: LLM 客户端
            tool_registry: 工具注册中心
            prompt_loader: 提示词加载器
            execution_engine: 执行引擎
        """
        self.config = config
        self.llm = llm_client
        self.registry = tool_registry
        self.prompts = prompt_loader
        self.execution = execution_engine or ExecutionEngine()
        self.executor_registry = ToolExecutorRegistry()

        # 初始化调度器
        self.orchestrator = TwoPhaseOrchestrator(
            llm_client=llm_client,
            tool_registry=tool_registry,
            prompt_loader=prompt_loader,
            tool_threshold=config.llm.tool_threshold,
            two_phase_enabled=config.llm.two_phase_enabled,
        )

        # 初始化重试和熔断
        self._setup_retry_and_circuit_breaker()

        self._session_history: list[dict[str, str]] = []

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
        """注册内置工具"""
        # 注册一些示例工具
        from ..registry import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType

        # 示例：echo 工具
        echo_tool = Tool(
            id="util.echo",
            name="echo",
            description="回显输入的消息",
            parameters={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "要回显的消息"}
                },
                "required": ["message"],
            },
            execution=ToolExecutionConfig(
                type=ToolExecutionType.FUNCTION,
                module="qd_agents.agent.builtins",
                function="echo",
            ),
            metadata=ToolMetadata(
                category="utilities",
                tags=["echo", "utility"],
            ),
        )
        from .builtins import echo
        self.registry.register(echo_tool)
        self.executor_registry.register_function("echo", echo)

        logger.info("Registered builtin tools")

    def add_to_history(self, role: str, content: str) -> None:
        """
        添加消息到会话历史

        Args:
            role: 角色 (user/assistant/system/tool)
            content: 内容
        """
        self._session_history.append({"role": role, "content": content})

    def clear_history(self) -> None:
        """清空会话历史"""
        self._session_history.clear()

    def get_history(self) -> list[dict[str, str]]:
        """获取会话历史"""
        return self._session_history.copy()

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
            # 执行调度
            orch_result = await self.orchestrator.orchestrate(
                user_input=user_input,
                session_id=session_id,
                trace_id=trace_id,
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
        if tool_name and tool_name not in ["direct", "coding_tool_use", "step_down"]:
            tool = self.registry.get(tool_name)
            if tool:
                try:
                    executor = self.executor_registry.get_executor(tool)
                    tool_input = phase_two.tool_input
                    result = await executor.execute(**tool_input)
                    return f"工具调用结果: {json.dumps(result, ensure_ascii=False)}"
                except Exception as e:
                    return f"工具调用失败: {e}"

        # 默认返回
        return "处理完成"
