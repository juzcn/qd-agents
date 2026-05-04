"""Evolve Agent — 主循环

Evolve Agent 是智能体的主循环，遵循 MetaAgent 的感知-推理-行动模式。
它只拥有 delegate、ask_user、context_summarizer 三个内置工具，
通过 delegate 工具将任务路由到子 Agent（Use-Tool/Find-Tools/Coding）。
路由决策完全由模型基于提示词原则自主决定，框架层面没有硬编码分支。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from ..llm import LLMClient
from ..context import ContextManager
from ..models.tool import Tool
from ..registry import ToolRegistry
from ..tools import ToolExecutorRegistry
from .base import MetaAgent, AgentResult, StepCallback, AskUserCallback
from .use_tool import UseToolAgent
from .find_tools import FindToolsAgent

if TYPE_CHECKING:
    from ..memory.service import MemoryService


logger = logging.getLogger(__name__)


class EvolveAgent(MetaAgent):
    """Evolve Agent — 智能体主循环

    通过 delegate 工具将任务路由到子 Agent。
    系统提示词中加载所有工具的名称和描述，模型自主决定路由方向。
    """

    name = "evolve"
    description = "智能体主循环，通过 delegate 路由到子 Agent"

    DEFAULT_TASK_BACKGROUND = "你是一个主会话Agent，协调管理子Agent的工作"
    DEFAULT_TASK_REQUIREMENTS = "你的主要任务如下：你能依靠知识回答的时候，直接回答；需要使用和编排工具回答的，delegate to Use-Tool Agent；你发现没有合适的工具回答时，你delegate to Find-Tools Agent，按delegate的参数要求输出"
    DEFAULT_TOOL_LIST = ["delegate"]

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        context_manager: ContextManager,
        executor_registry: ToolExecutorRegistry | None = None,
        expanded_tools_cache: list[Tool] | None = None,
        max_iterations: int | None = None,
        on_step: StepCallback | None = None,
        memory_service: MemoryService | None = None,
        session_id: str = "",
        use_tool_agent: UseToolAgent | None = None,
        find_tools_agent: FindToolsAgent | None = None,
        ask_user_callback: AskUserCallback | None = None,
        refresh_callback: Any | None = None,
        context_window_size: int = 0,
        context_summarizer_threshold: float = 0.75,
        task_background: str = "",
        task_requirements: str = "",
    ):
        task_background = task_background or self.DEFAULT_TASK_BACKGROUND
        task_requirements = task_requirements or self.DEFAULT_TASK_REQUIREMENTS
        super().__init__(
            llm_client=llm_client,
            tool_registry=tool_registry,
            executor_registry=executor_registry,
            max_iterations=max_iterations or 20,
            on_step=on_step,
            ask_user_callback=ask_user_callback,
            context_window_size=context_window_size,
            context_summarizer_threshold=context_summarizer_threshold,
            task_background=task_background,
            task_requirements=task_requirements,
        )
        self.context = context_manager
        self._expanded_tools = expanded_tools_cache or []
        self._memory_service = memory_service
        self._session_id = session_id
        self._use_tool_agent = use_tool_agent
        self._find_tools_agent = find_tools_agent
        self._refresh_callback = refresh_callback

    async def execute(self, **kwargs) -> AgentResult:
        """执行主循环

        构建 Evolve Agent 的 messages 和工具，运行 MetaAgent 标准循环。
        模型通过 delegate/ask_user 工具与子 Agent 和用户交互。
        """
        user_input: str = kwargs.get("user_input", "")
        history: list[dict] = kwargs.get("history", [])
        on_step = kwargs.get("on_step")
        cancel_event = kwargs.get("cancel_event")
        if on_step:
            self._on_step = on_step
        if cancel_event:
            self._cancel_event = cancel_event

        trace_id = kwargs.get("trace_id", str(uuid.uuid4()))
        start_time = time.perf_counter()

        # 检查取消信号
        if self._cancel_event and self._cancel_event.is_set():
            return AgentResult(
                final_answer="已取消",
                success=False,
                trace_id=trace_id,
                total_duration_ms=0,
            )

        # 1. 构建 messages（含系统提示词 + 历史 + 用户输入）
        messages = self.context.build_evolve_messages(
            user_input=user_input,
            tools=self._expanded_tools,
            history=history,
            task_background=self._task_background,
            task_requirements=self._task_requirements,
            tool_list=self.DEFAULT_TOOL_LIST,
        )

        # 2. 构建内置工具 schema（delegate + ask_user + context_summarizer）
        openai_tools = self._build_meta_tools()
        tool_map = self._build_meta_tool_map()

        # 3. 设置 LLM 日志标识
        self.llm.meta_agent_name = self.name
        self.llm.reset_log_count(messages)

        # 4. 运行 MetaAgent 标准工具调用循环
        result = await self.run_loop(
            messages=messages,
            openai_tools=openai_tools,
            tool_map=tool_map,
            temperature=0.1,
        )

        total_duration_ms = int((time.perf_counter() - start_time) * 1000)

        return AgentResult(
            final_answer=result.final_answer,
            success=result.success,
            working_memory=result.working_memory,
            total_tokens=result.total_tokens,
            last_prompt_tokens=result.last_prompt_tokens,
            trace_id=trace_id,
            total_duration_ms=total_duration_ms,
        )

    # --- delegate 工具拦截 ---

    async def _handle_delegate(
        self,
        tool_call: Any,
        tool_input: dict,
        tool_map: dict[str, Tool],
        iteration: int,
        messages: list[dict],
    ) -> str:
        """拦截 delegate 工具调用，路由到子 Agent

        根据 agent 参数路由到 Use-Tool / Find-Tools / Coding。
        """
        agent_name = tool_input.get("agent", "Unknown")
        task = tool_input.get("task", "")
        task_background = tool_input.get("task_background", "")
        tool_names = tool_input.get("tools", [])

        self._emit_step(iteration, event="delegate_call", tool_name=agent_name, detail=task[:100])

        try:
            if agent_name == "Use-Tool":
                result = await self._delegate_to_use_tool(
                    task=task,
                    task_background=task_background,
                    tool_names=tool_names,
                    messages=messages,
                    iteration=iteration,
                )
            elif agent_name == "Find-Tools":
                result = await self._delegate_to_find_tools(
                    task=task,
                    task_background=task_background,
                    messages=messages,
                    iteration=iteration,
                )
            elif agent_name == "Coding":
                result = json.dumps({
                    "success": False,
                    "error": "Coding Agent 尚未实现",
                    "message": "请通过 Use-Tool Agent 使用 execute_bash 执行代码，或通过 Find-Tools 搜索可用的代码生成工具。",
                }, ensure_ascii=False)
            else:
                result = json.dumps({
                    "success": False,
                    "error": f"未知的子 Agent: {agent_name}",
                }, ensure_ascii=False)
        except Exception as e:
            logger.exception("delegate to %s failed", agent_name)
            result = json.dumps({
                "success": False,
                "error": f"委派到 {agent_name} 失败: {e}",
            }, ensure_ascii=False)

        result_summary = result[:200] if len(result) > 200 else result
        self._emit_step(iteration, event="delegate_result", tool_name=agent_name, result_summary=result_summary)

        return result

    async def _delegate_to_use_tool(
        self,
        task: str,
        task_background: str,
        tool_names: list[str],
        messages: list[dict],
        iteration: int,
    ) -> str:
        """委派到 Use-Tool Agent"""
        if not self._use_tool_agent:
            return json.dumps({"success": False, "error": "Use-Tool Agent 未初始化"}, ensure_ascii=False)

        result = await self._use_tool_agent.execute(
            task_background=task_background,
            task_description=task,
            tool_list=tool_names,
            messages=messages,
            trace_id=str(uuid.uuid4()),
            on_step=self._on_step,
            cancel_event=self._cancel_event,
        )

        return json.dumps({
            "success": result.success,
            "final_answer": result.final_answer,
            "tools_used": result.working_memory.get("tools_used", []) if result.working_memory else [],
        }, ensure_ascii=False)

    async def _delegate_to_find_tools(
        self,
        task: str,
        task_background: str,
        messages: list[dict],
        iteration: int,
    ) -> str:
        """委派到 Find-Tools Agent"""
        if not self._find_tools_agent:
            return json.dumps({"success": False, "error": "Find-Tools Agent 未初始化"}, ensure_ascii=False)

        result = await self._find_tools_agent.execute(
            task_background=task_background,
            task_description=task,
            messages=messages,
            trace_id=str(uuid.uuid4()),
            on_step=self._on_step,
            cancel_event=self._cancel_event,
        )

        # 如果发现新工具，刷新工具缓存
        found_tool_names = result.working_memory.get("found_tool_names", []) if result.working_memory else []
        if found_tool_names and self._refresh_callback:
            logger.info("Find-Tools discovered %d tools, refreshing caches: %s", len(found_tool_names), found_tool_names)
            try:
                await self._refresh_callback()
                # 更新 EvolveAgent 的工具列表
                self._expanded_tools = list(self._use_tool_agent._expanded_tool_map.values()) if self._use_tool_agent else self._expanded_tools
            except Exception as e:
                logger.error("Refresh callback failed: %s", e)

        return json.dumps({
            "success": result.success,
            "final_answer": result.final_answer,
            "found_tool_names": found_tool_names,
        }, ensure_ascii=False)

    # --- 内置工具构建 ---

    def _build_meta_tools(self) -> list[dict]:
        """构建 Evolve Agent 的内置工具 schema 列表

        包含：delegate、ask_user、context_summarizer
        """
        tools = []

        for name in ("delegate", "ask_user", "context_summarizer"):
            tool = self.registry.get(name) or self.registry.get_by_name(name)
            if not tool:
                raise RuntimeError(f"Builtin meta tool '{name}' not found in registry — agent not initialized properly")
            tools.append(tool.to_openai_function())

        return tools

    def _build_meta_tool_map(self) -> dict[str, Tool]:
        """构建内置工具的 tool_map

        delegate/ask_user/context_summarizer 不需要真正的 Tool 对象来执行
        （由 MetaAgent.run_loop() 拦截），但 tool_map 需要包含它们以避免
        execute_tool() 报 "工具未找到" 错误。
        """
        tool_map = {}
        for name in ("delegate", "ask_user", "context_summarizer"):
            tool = self.registry.get(name) or self.registry.get_by_name(name)
            if tool:
                tool_map[name] = tool
        return tool_map


