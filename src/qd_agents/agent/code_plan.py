"""
CodePlan Agent — 基于三阶段路由的智能体

使用三个元Agent 实现：
1. JudgeMetaAgent - 路由判断
2. ToolCallingMetaAgent - 简单工具调用
3. CodingMetaAgent - 复杂工具编排
"""
from __future__ import annotations

import logging
from typing import Any

from ..llm import LLMClient
from ..registry import ToolRegistry
from ..context import ContextManager
from ..prompts import PromptLoader
from ..tools import ToolExecutorRegistry
from .base import Agent, AgentResult, MetaAgentInput
from .judge_meta import JudgeMetaAgent
from ..models import JudgeResult
from .tool_calling_meta import ToolCallingMetaAgent
from .coding_meta import CodingMetaAgent

logger = logging.getLogger(__name__)


class CodePlanAgent(Agent):
    """Code-Plan 智能体

    基于三阶段路由：
    - direct: 直接回答
    - tool_use: 简单工具调用
    - coding: 复杂工具编排
    """

    name = "code-plan"
    description = "智能路由Agent，支持直接回答、简单工具调用和复杂工具编排"

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        context_manager: ContextManager,
        executor_registry: ToolExecutorRegistry,
        prompt_loader: PromptLoader | None = None,
        expanded_tools_cache: list | None = None,
        openai_tools_cache: list[dict] | None = None,
        tool_map_cache: dict | None = None,
    ):
        self.llm = llm_client
        self.registry = tool_registry
        self.context = context_manager
        self.executor_registry = executor_registry
        self.prompts = prompt_loader

        # 工具缓存
        self._expanded_tools = expanded_tools_cache or []
        self._openai_tools = openai_tools_cache or []
        self._tool_map = tool_map_cache or {}

        # 创建元Agent
        self._judge = JudgeMetaAgent(
            llm_client=llm_client,
            context_manager=context_manager,
        )
        self._tool_calling = ToolCallingMetaAgent(
            llm_client=llm_client,
            context_manager=context_manager,
            executor_registry=executor_registry,
            tool_registry=tool_registry,
            openai_tools=openai_tools_cache,
            tool_map=tool_map_cache,
            expanded_tools=expanded_tools_cache,
        )
        self._coding = CodingMetaAgent(
            llm_client=llm_client,
            context_manager=context_manager,
            executor_registry=executor_registry,
        )

    async def execute(
        self,
        user_input: str,
        history: list[dict],
        **kwargs,
    ) -> AgentResult:
        """
        执行用户请求

        Args:
            user_input: 用户输入
            history: 会话历史
            **kwargs: 额外参数（如 trace_id）

        Returns:
            AgentResult
        """
        import time
        start_time = time.perf_counter()

        # 第一阶段：路由判断
        judge_input = MetaAgentInput(
            user_message=user_input,
            history=history,
            context={
                "tools": self._expanded_tools,
            },
        )

        judge_output = await self._judge.run(judge_input)
        judge_result: JudgeResult = judge_output.output

        logger.info(f"Judge result: route={judge_result.route}, reasoning={judge_result.reasoning}, tools={judge_result.tool_list}")

        # 根据路由执行
        if judge_result.route == "direct":
            # 直接回答
            final_answer = judge_result.direct_answer or "抱歉，我无法回答这个问题。"
            meta_traces = [judge_output]

        elif judge_result.route == "tool_use":
            # 简单工具调用 - 使用 judge 指定的工具 + 递归加载依赖
            all_needed = self._resolve_tool_deps(judge_result.tool_list)
            filtered_tools = self._filter_tools(list(all_needed))
            filtered_openai_tools = self._filter_openai_tools(list(all_needed))
            filtered_tool_map = self._filter_tool_map(list(all_needed))

            tool_input = MetaAgentInput(
                user_message=user_input,
                history=history,
                context={
                    "expanded_tools": filtered_tools,
                    "openai_tools": filtered_openai_tools,
                    "tool_map": filtered_tool_map,
                    "search_web_available": any(t.name == "search.web" for t in filtered_tools),
                },
            )

            tool_output = await self._tool_calling.run(tool_input)
            final_answer = tool_output.output
            meta_traces = [judge_output, tool_output]

        elif judge_result.route == "coding":
            # 复杂工具编排 - 使用 judge 指定的工具 + 递归加载依赖
            all_needed = self._resolve_tool_deps(judge_result.tool_list)
            filtered_tools = self._filter_tools(list(all_needed))
            filtered_openai_tools = self._filter_openai_tools(list(all_needed))
            filtered_tool_map = self._filter_tool_map(list(all_needed))

            coding_input = MetaAgentInput(
                user_message=user_input,
                history=history,
                context={
                    "tools": filtered_tools,
                    "tool_functions": await self._build_tool_functions(filtered_tools),
                },
            )

            coding_output = await self._coding.run(coding_input)
            final_answer = coding_output.output
            meta_traces = [judge_output, coding_output]

        else:
            final_answer = "抱歉，无法处理您的请求。"
            meta_traces = [judge_output]

        latency_ms = int((time.perf_counter() - start_time) * 1000)

        return AgentResult(
            final_answer=final_answer,
            success=True,
            meta_traces=meta_traces,
            total_duration_ms=latency_ms,
        )

    def _filter_tools(self, tool_names: list[str]) -> list:
        """根据工具名列表过滤工具"""
        if not tool_names:
            return self._expanded_tools
        return [t for t in self._expanded_tools if t.name in tool_names]

    def _resolve_tool_deps(self, tool_names: list[str]) -> set[str]:
        """递归解析工具依赖，返回所有需要的工具名（含去重）。

        当一个 SKILL 工具依赖其他工具时（dependencies["tool_deps"]），
        需要将依赖的工具也加入加载列表。支持递归：SKILL A 依赖 SKILL B，
        SKILL B 又依赖其他工具。
        """
        resolved = set(tool_names)
        queue = list(tool_names)
        while queue:
            name = queue.pop(0)
            tool = self._tool_map.get(name)
            if not tool:
                continue
            deps = tool.dependencies.get("tool_deps", [])
            for dep in deps:
                if dep not in resolved:
                    resolved.add(dep)
                    queue.append(dep)
                    logger.info("递归加载工具依赖: %s → %s", name, dep)
        return resolved

    def _filter_openai_tools(self, tool_names: list[str]) -> list[dict]:
        """根据工具名列表过滤 OpenAI 格式工具"""
        if not tool_names:
            return self._openai_tools
        return [t for t in self._openai_tools if t.get("function", {}).get("name") in tool_names]

    def _filter_tool_map(self, tool_names: list[str]) -> dict:
        """根据工具名列表过滤工具映射"""
        if not tool_names:
            return self._tool_map
        return {k: v for k, v in self._tool_map.items() if k in tool_names}

    async def _build_tool_functions(self, tools: list | None = None) -> dict[str, Any]:
        """构建工具函数映射（用于代码执行）"""
        # TODO: 实现工具函数的动态构建
        # 目前返回空字典，后续需要根据工具定义生成可调用的异步函数
        return {}
