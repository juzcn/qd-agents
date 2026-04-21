"""
Code-Plan Mode 调度器

实现五步循环渐进式披露架构：
1. 判断是否需要工具
2. 规划（直接调用或生成方案）
3. 代码生成
4. 沙盒执行
5. 生成回答
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from ..llm import LLMClient
from ..registry import ToolRegistry, Tool, ToolExecutionType, ToolMetadata
from ..prompts import PromptLoader
from ..context import ContextManager
from ..execution import ExecutionEngine, ExecutionResult
from ..tools.executors.mcp import MCPToolExecutor


logger = logging.getLogger(__name__)


class StepStatus(str, Enum):
    """步骤状态"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class StepType(str, Enum):
    """步骤类型"""
    JUDGE = "judge"  # 判断是否需要工具
    PLAN = "plan"    # 规划（直接调用或生成方案）
    CODE_GEN = "code_gen"  # 代码生成
    EXECUTE = "execute"  # 沙盒执行
    ANSWER = "answer"  # 生成回答


@dataclass
class WorkingMemoryItem:
    """工作记忆项"""
    id: str
    type: str  # tool_list | natural_language_plan | code | execution_result | user_preference
    content: Any
    step: StepType
    dependencies: List[str] = field(default_factory=list)  # 依赖的其他条目 ID
    timestamp: datetime = field(default_factory=datetime.utcnow)
    valid: bool = True  # 是否仍有效（被用户介入标记无效时设为 False）


@dataclass
class CodePlanResult:
    """Code-Plan 模式调度结果"""
    trace_id: str
    user_input: str
    session_id: str | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    final_output: Any = None
    final_status: str = "pending"
    total_latency_ms: int = 0
    steps: List[Dict[str, Any]] = field(default_factory=list)  # 五步循环的详细步骤
    working_memory_snapshot: List[WorkingMemoryItem] = field(default_factory=list)  # 工作记忆快照
    execution_result: Optional[ExecutionResult] = None  # 执行结果


class CodePlanModeOrchestrator:
    """
    Code-Plan 模式调度器

    实现五步循环渐进式披露架构，支持复杂工作流编排。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        context_manager: ContextManager | None = None,
        prompt_loader: PromptLoader | None = None,
        execution_engine: ExecutionEngine | None = None,
        tool_threshold: int = 50,
    ):
        """
        初始化 Code-Plan 模式调度器

        Args:
            llm_client: LLM 客户端
            tool_registry: 工具注册中心
            context_manager: 上下文管理器
            prompt_loader: 提示词加载器
            execution_engine: 执行引擎
            tool_threshold: 工具数量阈值
        """
        self.llm = llm_client
        self.registry = tool_registry
        self.prompts = prompt_loader
        self.context = context_manager or ContextManager(prompt_loader=prompt_loader)
        self.execution = execution_engine or ExecutionEngine()
        self.tool_threshold = tool_threshold

        # 工作记忆存储
        self.working_memory: Dict[str, WorkingMemoryItem] = {}

        # MCP 工具缓存（与 ToolUseMode 共享）
        self._mcp_tools_cache: Dict[str, List[Tool]] = {}  # server -> list of subtools
        self._mcp_executors_cache: Dict[str, Any] = {}  # server -> MCPToolExecutor

        # 展开工具缓存
        self._expanded_tools_cache: Optional[List[Tool]] = None
        self._openai_tools_cache: Optional[List[Dict[str, Any]]] = None

        # 工具披露层级缓存
        self._tool_l0_cache: Optional[List[Dict[str, str]]] = None  # L0: 名称+描述
        self._tool_l1_cache: Optional[Dict[str, Dict[str, Any]]] = None  # L1: 工具ID -> L1详情

        # 上下文历史
        self._user_visible_history: List[Dict[str, str]] = []  # 用户可见历史
        self._process_history: List[Dict[str, Any]] = []  # 过程历史（五步循环内部记录）

    def set_mcp_cache(self, mcp_tools_cache: Dict[str, List[Tool]], mcp_executors_cache: Dict[str, Any]) -> None:
        """设置MCP缓存（当上层已预加载MCP工具时使用）"""
        self._mcp_tools_cache = mcp_tools_cache
        self._mcp_executors_cache = mcp_executors_cache
        logger.info(f"Set MCP cache from parent: {len(mcp_tools_cache)} servers, {sum(len(tools) for tools in mcp_tools_cache.values())} total tools")

    def set_expanded_tools_cache(self, expanded_tools: List[Tool], openai_tools: List[Dict[str, Any]]) -> None:
        """设置展开工具缓存（当上层已计算展开工具时使用）"""
        self._expanded_tools_cache = expanded_tools.copy() if expanded_tools else None
        self._openai_tools_cache = openai_tools.copy() if openai_tools else None
        logger.info(f"Set expanded tools cache from parent: {len(expanded_tools) if expanded_tools else 0} expanded tools, {len(openai_tools) if openai_tools else 0} OpenAI tools")

    async def initialize(self, skip_mcp_preload: bool = False, skip_tool_caching: bool = False) -> None:
        """初始化调度器，预加载 MCP 工具和缓存工具披露层级"""
        logger.info("Initializing CodePlanModeOrchestrator...")

        try:
            if not skip_mcp_preload:
                # 预加载 MCP 工具（允许失败，只记录日志）
                await self._preload_mcp_tools()

            if not skip_tool_caching:
                # 缓存展开后的工具列表和披露层级
                await self._cache_expanded_tools()
                await self._cache_tool_disclosure_levels()
            elif self._expanded_tools_cache is None or self._openai_tools_cache is None:
                # 即使skip_tool_caching为True，但如果缓存为空，仍然需要缓存
                logger.warning("skip_tool_caching is True but expanded tools cache is empty, caching anyway")
                await self._cache_expanded_tools()
                await self._cache_tool_disclosure_levels()
            else:
                logger.info("Skipping tool caching, using existing cache")

        except Exception as e:
            logger.warning(f"CodePlanModeOrchestrator initialization failed, but continuing: {e}")
            # 尝试缓存基础工具列表（即使MCP连接失败）
            try:
                await self._cache_expanded_tools()
                await self._cache_tool_disclosure_levels()
            except Exception as inner_e:
                logger.warning(f"Failed to cache tools and disclosure levels: {inner_e}")

        logger.info("CodePlanModeOrchestrator initialized")

    def _build_context_for_step(
        self,
        step_type: StepType,
        user_input: str,
        tool_list: Optional[List[str]] = None,
        working_memory_items: Optional[List[WorkingMemoryItem]] = None,
    ) -> Dict[str, Any]:
        """
        为步骤构建动态上下文

        Args:
            step_type: 步骤类型
            user_input: 用户输入
            tool_list: 工具列表（如果需要）
            working_memory_items: 工作记忆项

        Returns:
            上下文字典
        """
        context = {
            "user_input": user_input,
            "step_type": step_type,
        }

        # 根据步骤类型构建不同的上下文视图
        if step_type == StepType.JUDGE:
            # 第一步：用户输入 + 最近2-3轮用户可见历史 + 所有工具L0
            context["core"] = {
                "user_input": user_input,
                "recent_history": self._get_recent_user_visible_history(2),
                "tools_l0": self._tool_l0_cache or [],
            }
            context["support"] = {
                "earlier_history_summary": self._get_earlier_history_summary(),
            }

        elif step_type == StepType.PLAN:
            # 第二步：工具列表 + 工具L1详情 + 完整用户可见历史
            if tool_list:
                context["core"] = {
                    "tool_list": tool_list,
                    "tools_l1": self._get_tools_l1(tool_list),
                    "full_user_history": self._user_visible_history,
                }
                context["support"] = {
                    "relevant_working_memory": self._get_relevant_working_memory(
                        working_memory_items, ["judgment"]
                    ),
                    "earlier_history_summary": self._get_earlier_history_summary(),
                }

        elif step_type == StepType.CODE_GEN:
            # 第三步：自然语言方案 + 涉及工具的L1
            if working_memory_items:
                # 查找最近的自然语言方案
                plan_items = [item for item in working_memory_items if item.type == "plan"]
                if plan_items:
                    latest_plan = plan_items[-1]
                    context["core"] = {
                        "natural_language_plan": latest_plan.content.get("natural_language_plan", ""),
                        "tools_l1": self._get_tools_l1(tool_list or []),
                    }
                context["support"] = {
                    "user_input": user_input,
                    "code_gen_templates": "default",  # 如果有代码生成模板，可以添加
                }

        elif step_type == StepType.EXECUTE:
            # 第四步：代码本身 + 工具网关接口
            if working_memory_items:
                code_items = [item for item in working_memory_items if item.type == "code"]
                if code_items:
                    latest_code = code_items[-1]
                    context["core"] = {
                        "generated_code": latest_code.content,
                        "tool_gateway_interface": "available",  # 工具网关接口可用
                    }

        elif step_type == StepType.ANSWER:
            # 第五步：执行结果 + 用户原始问题 + 最近一轮用户可见历史
            if working_memory_items:
                execution_items = [item for item in working_memory_items if item.type == "execution_result"]
                if execution_items:
                    latest_execution = execution_items[-1]
                    context["core"] = {
                        "execution_result": latest_execution.content,
                        "user_original_input": user_input,
                        "recent_history": self._get_recent_user_visible_history(1),
                    }

        return context

    def _get_recent_user_visible_history(self, num_rounds: int) -> List[Dict[str, str]]:
        """获取最近N轮用户可见历史"""
        if not self._user_visible_history:
            return []
        return self._user_visible_history[-min(num_rounds * 2, len(self._user_visible_history)):]

    def _get_earlier_history_summary(self) -> str:
        """获取更早历史摘要"""
        if len(self._user_visible_history) <= 6:  # 如果只有最近3轮，不需要摘要
            return ""
        earlier_history = self._user_visible_history[:-6]
        # 简单摘要：返回对话轮数
        return f"Earlier conversation: {len(earlier_history) // 2} rounds"

    def _get_tools_l1(self, tool_list: List[str]) -> List[Dict[str, Any]]:
        """获取指定工具的L1详情"""
        if not self._tool_l1_cache:
            return []

        tools_l1 = []
        for tool_name in tool_list:
            # 查找工具
            tool = self.registry.get_by_name(tool_name)
            if tool and tool.id in self._tool_l1_cache:
                tools_l1.append(self._tool_l1_cache[tool.id])

        return tools_l1

    def _get_relevant_working_memory(
        self,
        working_memory_items: Optional[List[WorkingMemoryItem]],
        item_types: List[str]
    ) -> List[Any]:
        """获取相关工作记忆项"""
        if not working_memory_items:
            return []

        relevant_items = []
        for item in working_memory_items:
            if item.type in item_types and item.valid:
                relevant_items.append(item.content)

        return relevant_items

    def _add_to_user_visible_history(self, role: str, content: str) -> None:
        """添加到用户可见历史"""
        self._user_visible_history.append({"role": role, "content": content})

    def _add_to_process_history(self, step_type: StepType, step_data: Dict[str, Any]) -> None:
        """添加到过程历史"""
        self._process_history.append({
            "step_type": step_type,
            "timestamp": datetime.utcnow(),
            "data": step_data,
        })

    def _handle_step_failure(
        self,
        step_type: StepType,
        error: str,
        trace_id: str,
    ) -> Dict[str, Any]:
        """
        处理步骤失败

        Args:
            step_type: 失败的步骤类型
            error: 错误信息
            trace_id: 追踪ID

        Returns:
            恢复操作
        """
        logger.error(f"[{trace_id}] Step {step_type} failed: {error}")

        recovery_plan = {
            "step_type": step_type,
            "error": error,
            "timestamp": datetime.utcnow(),
            "recovery_actions": [],
        }

        # 根据步骤类型提供不同的恢复建议
        if step_type == StepType.JUDGE:
            recovery_plan["recovery_actions"] = [
                "重新分析用户问题，可能需要用户澄清需求",
                "检查工具列表是否完整",
                "尝试使用更简单的工具选择策略",
            ]
        elif step_type == StepType.PLAN:
            recovery_plan["recovery_actions"] = [
                "简化任务复杂度，尝试直接工具调用",
                "检查工具参数是否正确",
                "重新规划，避免复杂的条件分支",
            ]
        elif step_type == StepType.CODE_GEN:
            recovery_plan["recovery_actions"] = [
                "简化生成的代码逻辑",
                "检查工具调用格式是否正确",
                "添加更多错误处理代码",
            ]
        elif step_type == StepType.EXECUTE:
            recovery_plan["recovery_actions"] = [
                "检查代码语法错误",
                "验证工具参数格式",
                "降低执行复杂度，分步执行",
            ]
        elif step_type == StepType.ANSWER:
            recovery_plan["recovery_actions"] = [
                "重新生成回答，使用更简单的语言",
                "检查执行结果是否为空或异常",
            ]

        # 标记相关的工作记忆项为无效
        self._invalidate_dependent_working_memory(step_type)

        return recovery_plan

    def _invalidate_dependent_working_memory(self, failed_step_type: StepType) -> None:
        """使依赖失败步骤的工作记忆项无效"""
        invalidated_count = 0

        for item_id, item in list(self.working_memory.items()):
            # 如果项目依赖的步骤类型等于失败步骤类型
            if item.step == failed_step_type:
                item.valid = False
                invalidated_count += 1

            # 如果项目依赖其他无效的项目
            for dep_id in item.dependencies:
                dep_item = self.working_memory.get(dep_id)
                if dep_item and not dep_item.valid and item.valid:
                    item.valid = False
                    invalidated_count += 1
                    break

        if invalidated_count > 0:
            logger.info(f"Invalidated {invalidated_count} working memory items due to step {failed_step_type} failure")

    def _handle_user_intervention(
        self,
        user_message: str,
        trace_id: str,
    ) -> Dict[str, Any]:
        """
        处理用户介入

        Args:
            user_message: 用户介入消息
            trace_id: 追踪ID

        Returns:
            恢复计划
        """
        logger.info(f"[{trace_id}] User intervention: {user_message}")

        # 将用户消息添加到用户可见历史
        self._add_to_user_visible_history("user", user_message)

        recovery_plan = {
            "user_message": user_message,
            "timestamp": datetime.utcnow(),
            "actions": [
                "清空当前步骤的上下文视图",
                "重新从第一步开始（但可跳过部分步骤）",
                "保留工作记忆中尚未失效的中间产物",
                "重新构建上下文视图",
            ],
        }

        # 分析用户介入类型
        if "不要" in user_message or "不用" in user_message or "停止" in user_message:
            # 用户要求停止某些操作
            recovery_plan["type"] = "cancellation"
            recovery_plan["actions"].append("标记相关工具或操作为无效")

        elif "修改" in user_message or "改成" in user_message or "调整" in user_message:
            # 用户要求修改参数或方案
            recovery_plan["type"] = "modification"
            recovery_plan["actions"].append("更新相关参数并重新执行")

        elif "继续" in user_message or "接着" in user_message or "然后" in user_message:
            # 用户要求继续
            recovery_plan["type"] = "continuation"
            recovery_plan["actions"].append("从当前位置继续执行")

        else:
            # 其他类型的介入
            recovery_plan["type"] = "general"
            recovery_plan["actions"].append("重新分析用户意图")

        return recovery_plan

    def _optimize_working_memory(self) -> None:
        """优化工作记忆管理"""
        # 清理无效的工作记忆项（保留最近的一些用于调试）
        invalid_items = [item_id for item_id, item in self.working_memory.items() if not item.valid]

        # 保留最近3个无效项用于调试
        if len(invalid_items) > 3:
            items_to_remove = invalid_items[:-3]
            for item_id in items_to_remove:
                if item_id in self.working_memory:
                    del self.working_memory[item_id]
            logger.info(f"Cleaned up {len(items_to_remove)} invalid working memory items")

        # 检查依赖关系一致性
        self._validate_working_memory_dependencies()

    def _validate_working_memory_dependencies(self) -> None:
        """验证工作记忆依赖关系一致性"""
        for item_id, item in self.working_memory.items():
            for dep_id in item.dependencies:
                if dep_id not in self.working_memory:
                    logger.warning(f"Working memory item {item_id} has missing dependency: {dep_id}")
                    # 可以选择移除这个依赖或标记项目为无效
                    item.dependencies = [d for d in item.dependencies if d != dep_id]

    def _get_working_memory_snapshot(self) -> List[Dict[str, Any]]:
        """获取工作记忆快照（用于调试和恢复）"""
        snapshot = []
        for item_id, item in self.working_memory.items():
            snapshot.append({
                "id": item_id,
                "type": item.type,
                "step": item.step.value,
                "timestamp": item.timestamp.isoformat(),
                "valid": item.valid,
                "dependencies": item.dependencies,
                "content_summary": str(item.content)[:100] + ("..." if len(str(item.content)) > 100 else ""),
            })
        return snapshot

    def _restore_working_memory_from_snapshot(self, snapshot: List[Dict[str, Any]]) -> None:
        """从快照恢复工作记忆"""
        self.working_memory.clear()
        for item_data in snapshot:
            # 创建WorkingMemoryItem对象
            item = WorkingMemoryItem(
                id=item_data["id"],
                type=item_data["type"],
                content=item_data.get("content", {}),  # 注意：完整内容可能不在快照中
                step=StepType(item_data["step"]),
                dependencies=item_data["dependencies"],
                timestamp=datetime.fromisoformat(item_data["timestamp"]),
                valid=item_data["valid"],
            )
            self.working_memory[item_data["id"]] = item

    async def close(self, skip_mcp_close: bool = False) -> None:
        """关闭调度器，终止所有 MCP 连接"""
        logger.info("Closing CodePlanModeOrchestrator...")

        if not skip_mcp_close:
            # 关闭所有 MCP 连接（如果缓存非空）
            if self._mcp_executors_cache:
                for server_key, executor in self._mcp_executors_cache.items():
                    try:
                        await executor.close()
                        logger.info(f"Closed MCP connection to server: {server_key}")
                    except Exception as e:
                        logger.error(f"Error closing MCP connection to server {server_key}: {e}")
            else:
                logger.info("MCP executors cache is empty, nothing to close")
        else:
            logger.info("Skipping MCP connection close (handled by parent)")

        # 清空缓存
        self._mcp_tools_cache.clear()
        self._mcp_executors_cache.clear()
        self._expanded_tools_cache = None
        self._openai_tools_cache = None
        self._tool_l0_cache = None
        self._tool_l1_cache = None
        logger.info("CodePlanModeOrchestrator closed")

    async def _preload_mcp_tools(self) -> None:
        """预加载 MCP 工具（会话开始时连接 MCP 服务器）"""
        # 实现与 ToolUseMode 类似，这里先留空，复用父类的缓存
        logger.info("MCP preloading handled by parent or already cached")

    async def _cache_expanded_tools(self) -> None:
        """缓存展开后的工具列表和OpenAI格式工具"""
        # 实现与 ToolUseMode 类似，这里先留空，复用父类的缓存
        logger.info("Tool caching handled by parent or already cached")

    async def _cache_tool_disclosure_levels(self) -> None:
        """缓存工具披露层级（L0和L1）"""
        logger.info("Caching tool disclosure levels...")

        # 获取所有工具
        all_tools = self.registry.list_all()

        # 构建 L0 缓存（名称 + 一句话描述）
        l0_list = []
        for tool in all_tools:
            l0_list.append({
                "name": tool.name,
                "description": tool.description,
                "id": tool.id,
            })
        self._tool_l0_cache = l0_list

        # 构建 L1 缓存（参数、输出格式、副作用类型）
        l1_dict = {}
        for tool in all_tools:
            l1_dict[tool.id] = {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "execution_type": tool.execution.type,
                "security": tool.security,
            }
        self._tool_l1_cache = l1_dict

        logger.info(f"Cached {len(l0_list)} L0 tools and {len(l1_dict)} L1 tools")

    async def orchestrate(
        self,
        user_input: str,
        session_id: str | None = None,
    ) -> CodePlanResult:
        """
        执行五步循环调度

        Args:
            user_input: 用户输入
            session_id: 会话 ID

        Returns:
            调度结果
        """
        trace_id = str(uuid.uuid4())
        start_time = datetime.utcnow()
        result = CodePlanResult(
            trace_id=trace_id,
            user_input=user_input,
            session_id=session_id,
            timestamp=start_time,
        )

        logger.info(f"Starting Code-Plan orchestration (trace_id: {trace_id}): {user_input[:100]}...")

        try:
            # 第一步：判断是否需要工具
            step1_result = await self._step_judge(user_input, trace_id)
            result.steps.append(step1_result)

            if step1_result.get("status") == StepStatus.FAILED:
                result.final_status = "failed"
                result.final_output = "第一步判断失败"
                return result

            # 检查是否需要工具
            needs_tools = step1_result.get("needs_tools", False)
            if not needs_tools:
                # 不需要工具，直接回复
                direct_answer = step1_result.get("direct_answer")
                result.final_output = direct_answer
                result.final_status = "completed"
                result.total_latency_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                return result

            # 获取工具列表
            tool_list = step1_result.get("tool_list", [])
            if not tool_list:
                result.final_output = "无法确定需要哪些工具，请提供更多信息。"
                result.final_status = "completed"
                result.total_latency_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
                return result

            # 第二步：规划（直接调用或生成方案）
            step2_result = await self._step_plan(user_input, tool_list, trace_id)
            result.steps.append(step2_result)

            if step2_result.get("status") == StepStatus.FAILED:
                result.final_status = "failed"
                result.final_output = "第二步规划失败"
                return result

            plan_type = step2_result.get("plan_type")  # "direct_call" or "natural_language"

            if plan_type == "direct_call":
                # 直接调用工具
                # 这里简化处理，实际应该执行工具调用
                # 为了兼容性，暂时跳转到第四步风格执行
                execution_plan = step2_result.get("execution_plan", {})
                # 执行直接调用
                # 这里需要执行工具调用，然后生成回答
                # 暂时简化处理
                result.final_output = "直接工具调用功能开发中，请使用自然语言方案模式。"
                result.final_status = "completed"

            else:  # natural_language
                # 获取自然语言方案
                natural_language_plan = step2_result.get("natural_language_plan", "")

                # 第三步：代码生成
                step3_result = await self._step_code_generation(user_input, natural_language_plan, tool_list, trace_id)
                result.steps.append(step3_result)

                if step3_result.get("status") == StepStatus.FAILED:
                    result.final_status = "failed"
                    result.final_output = "第三步代码生成失败"
                    return result

                generated_code = step3_result.get("generated_code", "")

                # 第四步：沙盒执行
                step4_result = await self._step_execute(user_input, generated_code, trace_id, session_id)
                result.steps.append(step4_result)
                result.execution_result = step4_result.get("execution_result")

                if step4_result.get("status") == StepStatus.FAILED:
                    result.final_status = "failed"
                    result.final_output = "第四步执行失败"
                    return result

                execution_output = step4_result.get("execution_output", "")

                # 第五步：生成回答
                step5_result = await self._step_answer(user_input, execution_output, trace_id)
                result.steps.append(step5_result)

                if step5_result.get("status") == StepStatus.FAILED:
                    result.final_status = "failed"
                    result.final_output = "第五步生成回答失败"
                    return result

                result.final_output = step5_result.get("final_answer", "")

            result.final_status = "completed"

        except Exception as e:
            logger.exception(f"Code-Plan orchestration failed: {e}")
            result.final_status = "failed"
            result.final_output = f"Code-Plan 调度失败: {e}"

        result.total_latency_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        # 保存工作记忆快照
        result.working_memory_snapshot = list(self.working_memory.values())

        logger.info(f"Code-Plan orchestration completed (trace_id: {trace_id}, status: {result.final_status}, latency: {result.total_latency_ms}ms)")
        return result

    async def _step_judge(self, user_input: str, trace_id: str) -> Dict[str, Any]:
        """第一步：判断是否需要工具"""
        step_id = str(uuid.uuid4())
        logger.info(f"[{trace_id}] Step 1: Judge - {step_id}")

        step_result = {
            "step_id": step_id,
            "step_type": StepType.JUDGE,
            "status": StepStatus.IN_PROGRESS,
            "start_time": datetime.utcnow(),
        }

        try:
            # 获取 L0 工具列表
            if self._tool_l0_cache is None:
                await self._cache_tool_disclosure_levels()

            l0_tools = self._tool_l0_cache or []

            # 构建提示词
            prompt = f"""你是一个智能助手，需要判断用户的问题是否需要使用工具来回答。

用户输入: {user_input}

可用工具列表（仅名称和描述）:
{json.dumps(l0_tools, ensure_ascii=False, indent=2)}

请判断：
1. 如果你可以用自己的知识直接回答这个问题，请直接给出回答。
2. 如果需要使用工具，请列出需要的工具名称列表（去重）。

请用以下JSON格式回复：
{{
    "needs_tools": true/false,
    "direct_answer": "如果不需要工具，这里是你直接的回答",
    "tool_list": ["tool_name1", "tool_name2", ...]  # 如果需要工具
}}
"""

            # 调用 LLM
            response = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )

            content = response.choices[0].message.content

            # 解析 JSON 响应
            try:
                judgment = json.loads(content)
                needs_tools = judgment.get("needs_tools", False)
                direct_answer = judgment.get("direct_answer", "")
                tool_list = judgment.get("tool_list", [])

                # 存储到工作记忆
                item_id = f"judgment_{step_id}"
                self.working_memory[item_id] = WorkingMemoryItem(
                    id=item_id,
                    type="judgment",
                    content=judgment,
                    step=StepType.JUDGE,
                )

                step_result.update({
                    "status": StepStatus.COMPLETED,
                    "needs_tools": needs_tools,
                    "direct_answer": direct_answer,
                    "tool_list": tool_list,
                    "llm_response": content,
                })

            except json.JSONDecodeError:
                logger.error(f"Failed to parse LLM response as JSON: {content}")
                step_result.update({
                    "status": StepStatus.FAILED,
                    "error": "Failed to parse LLM response as JSON",
                    "llm_response": content,
                })

        except Exception as e:
            logger.exception(f"Step 1 (Judge) failed: {e}")
            step_result.update({
                "status": StepStatus.FAILED,
                "error": str(e),
            })

        step_result["end_time"] = datetime.utcnow()
        duration = (step_result["end_time"] - step_result["start_time"]).total_seconds()
        step_result["duration_ms"] = int(duration * 1000)

        return step_result

    async def _step_plan(self, user_input: str, tool_list: List[str], trace_id: str) -> Dict[str, Any]:
        """第二步：规划（直接调用或生成方案）"""
        step_id = str(uuid.uuid4())
        logger.info(f"[{trace_id}] Step 2: Plan - {step_id}")

        step_result = {
            "step_id": step_id,
            "step_type": StepType.PLAN,
            "status": StepStatus.IN_PROGRESS,
            "start_time": datetime.utcnow(),
        }

        try:
            # 获取工具的 L1 详情
            if self._tool_l1_cache is None:
                await self._cache_tool_disclosure_levels()

            l1_tools_info = []
            for tool_name in tool_list:
                # 查找工具
                tool = self.registry.get_by_name(tool_name)
                if tool and tool.id in self._tool_l1_cache:
                    l1_tools_info.append(self._tool_l1_cache[tool.id])
                elif tool:
                    # 如果不在缓存中，构建 L1 信息
                    l1_tools_info.append({
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                        "execution_type": tool.execution.type,
                        "security": tool.security,
                    })

            # 构建提示词
            prompt = f"""你是一个智能规划器，需要根据用户的问题和可用工具来制定执行计划。

用户输入: {user_input}

需要使用的工具（L1详情）:
{json.dumps(l1_tools_info, ensure_ascii=False, indent=2)}

请评估任务复杂度：
1. 如果只需要顺序调用1-3个工具且无分支/循环/并行，则选择直接调用（direct_call），生成结构化调用计划（JSON序列，指定工具和参数）。
2. 否则，选择自然语言方案（natural_language），描述步骤、依赖、条件、循环、异常处理。

请用以下JSON格式回复：
{{
    "plan_type": "direct_call" 或 "natural_language",
    "execution_plan": {{
        "steps": [
            {{
                "tool": "tool_name",
                "parameters": {{...}},
                "description": "步骤描述"
            }}
        ]
    }},
    "natural_language_plan": "如果选择自然语言方案，这里是详细的方案文本"
}}
"""

            # 调用 LLM
            response = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )

            content = response.choices[0].message.content

            # 解析 JSON 响应
            try:
                plan = json.loads(content)
                plan_type = plan.get("plan_type", "natural_language")
                execution_plan = plan.get("execution_plan", {})
                natural_language_plan = plan.get("natural_language_plan", "")

                # 存储到工作记忆
                item_id = f"plan_{step_id}"
                self.working_memory[item_id] = WorkingMemoryItem(
                    id=item_id,
                    type="plan",
                    content=plan,
                    step=StepType.PLAN,
                    dependencies=[f"judgment_*"],  # 依赖第一步的结果
                )

                step_result.update({
                    "status": StepStatus.COMPLETED,
                    "plan_type": plan_type,
                    "execution_plan": execution_plan,
                    "natural_language_plan": natural_language_plan,
                    "llm_response": content,
                })

            except json.JSONDecodeError:
                logger.error(f"Failed to parse LLM response as JSON: {content}")
                step_result.update({
                    "status": StepStatus.FAILED,
                    "error": "Failed to parse LLM response as JSON",
                    "llm_response": content,
                })

        except Exception as e:
            logger.exception(f"Step 2 (Plan) failed: {e}")
            step_result.update({
                "status": StepStatus.FAILED,
                "error": str(e),
            })

        step_result["end_time"] = datetime.utcnow()
        duration = (step_result["end_time"] - step_result["start_time"]).total_seconds()
        step_result["duration_ms"] = int(duration * 1000)

        return step_result

    async def _step_code_generation(self, user_input: str, natural_language_plan: str, tool_list: List[str], trace_id: str) -> Dict[str, Any]:
        """第三步：代码生成"""
        step_id = str(uuid.uuid4())
        logger.info(f"[{trace_id}] Step 3: Code Generation - {step_id}")

        step_result = {
            "step_id": step_id,
            "step_type": StepType.CODE_GEN,
            "status": StepStatus.IN_PROGRESS,
            "start_time": datetime.utcnow(),
        }

        try:
            # 获取工具的 L1 详情
            if self._tool_l1_cache is None:
                await self._cache_tool_disclosure_levels()

            l1_tools_info = []
            for tool_name in tool_list:
                # 查找工具
                tool = self.registry.get_by_name(tool_name)
                if tool and tool.id in self._tool_l1_cache:
                    l1_info = self._tool_l1_cache[tool.id]
                    l1_tools_info.append({
                        "name": tool.name,
                        "parameters": l1_info.get("parameters", {}),
                        "description": l1_info.get("description", ""),
                    })

            # 构建提示词
            prompt = f"""你是一个代码生成器，需要将自然语言方案转换为可执行的 Python 代码。

用户输入: {user_input}

自然语言方案: {natural_language_plan}

相关工具信息:
{json.dumps(l1_tools_info, ensure_ascii=False, indent=2)}

要求：
1. 生成的代码将在安全的沙盒环境中执行，所有已注册工具都作为同名函数可用
2. 实现方案中的顺序、并行（使用 asyncio 或 concurrent.futures）、条件、循环
3. 包含错误处理（try-except），捕获异常并记录错误信息
4. 不包含危险操作（禁止 os.system, eval, exec, __import__, open 等）
5. 输出结果以打印方式提供（如 print(json.dumps(result, ensure_ascii=False, indent=2))）
6. 工具调用格式：直接调用工具函数，如 result = tool_name(**parameters)
7. 如果是异步工具，请使用 await，如 result = await tool_name(**parameters)
8. 代码应包含必要的导入（如 json, asyncio, concurrent.futures 等）
9. 最终结果应存储在变量 result 中，或通过 print 输出

请只输出 Python 代码，不要包含其他解释或注释。
"""

            # 调用 LLM
            response = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )

            generated_code = response.choices[0].message.content

            # 存储到工作记忆
            item_id = f"code_{step_id}"
            self.working_memory[item_id] = WorkingMemoryItem(
                id=item_id,
                type="code",
                content=generated_code,
                step=StepType.CODE_GEN,
                dependencies=[f"plan_*"],  # 依赖第二步的结果
            )

            step_result.update({
                "status": StepStatus.COMPLETED,
                "generated_code": generated_code,
                "llm_response": generated_code,
            })

        except Exception as e:
            logger.exception(f"Step 3 (Code Generation) failed: {e}")
            step_result.update({
                "status": StepStatus.FAILED,
                "error": str(e),
            })

        step_result["end_time"] = datetime.utcnow()
        duration = (step_result["end_time"] - step_result["start_time"]).total_seconds()
        step_result["duration_ms"] = int(duration * 1000)

        return step_result

    async def _step_execute(self, user_input: str, generated_code: str, trace_id: str, session_id: str | None = None) -> Dict[str, Any]:
        """第四步：沙盒执行"""
        step_id = str(uuid.uuid4())
        logger.info(f"[{trace_id}] Step 4: Execute - {step_id}")

        step_result = {
            "step_id": step_id,
            "step_type": StepType.EXECUTE,
            "status": StepStatus.IN_PROGRESS,
            "start_time": datetime.utcnow(),
        }

        try:
            # 使用执行引擎执行代码
            # 注意：这里需要先注册工具到执行引擎
            # 为了简化，我们暂时只执行代码，不处理工具调用
            execution_result = await self.execution.execute_code(
                code=generated_code,
                session_id=session_id,
                trace_id=trace_id,
            )

            # 存储到工作记忆
            item_id = f"execution_{step_id}"
            self.working_memory[item_id] = WorkingMemoryItem(
                id=item_id,
                type="execution_result",
                content=execution_result,
                step=StepType.EXECUTE,
                dependencies=[f"code_*"],  # 依赖第三步的结果
            )

            step_result.update({
                "status": StepStatus.COMPLETED,
                "execution_result": execution_result,
                "execution_output": execution_result.final_output,
                "execution_status": execution_result.status,
            })

        except Exception as e:
            logger.exception(f"Step 4 (Execute) failed: {e}")
            step_result.update({
                "status": StepStatus.FAILED,
                "error": str(e),
                "execution_output": str(e),
            })

        step_result["end_time"] = datetime.utcnow()
        duration = (step_result["end_time"] - step_result["start_time"]).total_seconds()
        step_result["duration_ms"] = int(duration * 1000)

        return step_result

    async def _step_answer(self, user_input: str, execution_output: Any, trace_id: str) -> Dict[str, Any]:
        """第五步：生成回答"""
        step_id = str(uuid.uuid4())
        logger.info(f"[{trace_id}] Step 5: Answer - {step_id}")

        step_result = {
            "step_id": step_id,
            "step_type": StepType.ANSWER,
            "status": StepStatus.IN_PROGRESS,
            "start_time": datetime.utcnow(),
        }

        try:
            # 构建提示词
            prompt = f"""你是一个智能助手，需要根据执行结果生成友好、简洁的自然语言回答。

用户原始问题: {user_input}

执行结果: {execution_output}

要求：
1. 将结构化结果转换为友好、简洁的自然语言回答
2. 如果执行失败，解释原因并提供可操作建议
3. 不编造信息
4. 回答要直接、有用

请直接给出回答，不要包含其他内容。
"""

            # 调用 LLM
            response = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )

            final_answer = response.choices[0].message.content

            # 存储到工作记忆
            item_id = f"answer_{step_id}"
            self.working_memory[item_id] = WorkingMemoryItem(
                id=item_id,
                type="final_answer",
                content=final_answer,
                step=StepType.ANSWER,
                dependencies=[f"execution_*"],  # 依赖第四步的结果
            )

            step_result.update({
                "status": StepStatus.COMPLETED,
                "final_answer": final_answer,
                "llm_response": final_answer,
            })

        except Exception as e:
            logger.exception(f"Step 5 (Answer) failed: {e}")
            step_result.update({
                "status": StepStatus.FAILED,
                "error": str(e),
            })

        step_result["end_time"] = datetime.utcnow()
        duration = (step_result["end_time"] - step_result["start_time"]).total_seconds()
        step_result["duration_ms"] = int(duration * 1000)

        return step_result