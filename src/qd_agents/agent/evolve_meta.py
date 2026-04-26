"""
Evolve 元Agent — 自主进化智能体

EvolveMetaAgent 是一个真正的自主 agent：
- 持有完整对话上下文（system prompt + history + tool results）
- 通过 function calling 直接调用工具（内置元工具能力）
- 自己思考、自己调工具、自己观察结果、自己决定下一步
- 终止条件：LLM 不返回 tool_calls，或达到最大迭代次数

不再依赖 ToolCallingMetaAgent 作为子 Agent，工具调用是自身具备的能力。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from ..llm import LLMClient
from ..context import ContextManager, ContextCompressor
from ..models import EvolveResult, AskUserInfo, DelegateInfo
from ..registry import Tool, ToolExecutionType
from ..tools import ToolExecutorRegistry
from ..utils.parsing import extract_json_from_llm_output
from .base import MetaAgent, MetaAgentInput, MetaAgentOutput, StepCallback

logger = logging.getLogger(__name__)


class EvolveMetaAgent(MetaAgent):
    """Evolve 自主进化元Agent

    通过 function calling 直接调用工具，持有完整对话上下文。
    """

    name = "evolve"

    def __init__(
        self,
        llm_client: LLMClient,
        context_manager: ContextManager,
        executor_registry: ToolExecutorRegistry | None = None,
        tool_registry: Any = None,
        openai_tools: list[dict[str, Any]] | None = None,
        tool_map: dict[str, Tool] | None = None,
        expanded_tools: list[Tool] | None = None,
        temperature: float = 0.3,
        max_iterations: int = 10,
        on_step: StepCallback | None = None,
        cancel_event: asyncio.Event | None = None,
        compressor: ContextCompressor | None = None,
    ):
        self.llm = llm_client
        self.context = context_manager
        self.executor_registry = executor_registry
        self.tool_registry = tool_registry
        self._openai_tools = openai_tools or []
        self._tool_map = tool_map or {}
        self._expanded_tools = expanded_tools or []
        self.temperature = temperature
        self.max_iterations = max_iterations
        self._on_step = on_step
        self._cancel_event = cancel_event
        self._compressor = compressor

    async def run(self, input: MetaAgentInput) -> MetaAgentOutput:
        """
        执行自主进化循环。

        input.context 需包含：
          - tools: list[Tool]  可用工具列表
          - observations: list[str]  前几轮观察结果（可选）
          - openai_tools: list[dict]  OpenAI 格式工具定义（可选，覆盖默认）
          - tool_map: dict[str, Tool]  工具映射（可选，覆盖默认）
        """
        start_time = time.perf_counter()

        # 工具来源：优先使用 context 传入的，否则使用构造时的默认
        expanded_tools = input.context.get("expanded_tools") or self._expanded_tools or []
        openai_tools = input.context.get("openai_tools") or self._openai_tools or []
        tool_map = input.context.get("tool_map") or self._tool_map or {}
        observations = input.context.get("observations", [])

        # 确保 bash 工具可用（SKILL 工具需要）
        openai_tools, tool_map = self._ensure_bash_available(openai_tools, tool_map)

        # 构建初始消息
        messages = self.context.build_evolve_messages(
            user_input=input.user_message,
            tools=expanded_tools,
            history=input.history,
            observations=observations,
        )

        self.llm.meta_agent_name = self.name

        iteration = 0
        total_tokens = 0
        last_prompt_tokens = 0

        while iteration < self.max_iterations:
            # 检查取消信号
            if self._cancel_event and self._cancel_event.is_set():
                logger.info("Evolve loop cancelled by user")
                latency_ms = int((time.perf_counter() - start_time) * 1000)
                return MetaAgentOutput(
                    output="已取消",
                    output_type="text",
                    success=False,
                    messages=messages,
                    model=self.llm.current_model,
                    total_tokens=total_tokens,
                    last_prompt_tokens=last_prompt_tokens,
                    latency_ms=latency_ms,
                    iterations=iteration,
                )

            iteration += 1

            # 压缩历史中的旧 tool_result（保留最近 keep_recent_results 轮完整）
            if self._compressor:
                messages = self._compressor.compress_old_results(messages, iteration)

            response = await self.llm.chat(
                messages=messages,
                tools=openai_tools,
                tool_choice="auto",
                temperature=self.temperature,
            )

            choice = response.choices[0]
            assistant_message = choice.message

            if hasattr(response, "usage") and response.usage:
                total_tokens += response.usage.total_tokens
                last_prompt_tokens = response.usage.prompt_tokens

            # 追加 assistant 消息
            assistant_dict: dict[str, Any] = {
                "role": "assistant",
                "content": assistant_message.content or "",
            }
            if hasattr(assistant_message, "tool_calls") and assistant_message.tool_calls:
                assistant_dict["tool_calls"] = assistant_message.tool_calls
            messages.append(assistant_dict)

            # 终止条件：LLM 不返回 tool_calls
            if not assistant_message.tool_calls:
                content = assistant_message.content or ""
                latency_ms = int((time.perf_counter() - start_time) * 1000)

                # 尝试解析为 EvolveResult（ask_user/delegate 等特殊输出）
                evolve_result = self._try_parse_evolve_result(content)

                if evolve_result and evolve_result.action in ("ask_user", "delegate"):
                    return MetaAgentOutput(
                        output=evolve_result,
                        output_type="evolve_result",
                        success=True,
                        messages=messages,
                        model=self.llm.current_model,
                        total_tokens=total_tokens,
                        last_prompt_tokens=last_prompt_tokens,
                        latency_ms=latency_ms,
                        iterations=iteration,
                    )

                # 普通文本回答
                final_output = content.strip() if content.strip() else "抱歉，无法生成回答"
                return MetaAgentOutput(
                    output=final_output,
                    output_type="text",
                    success=True,
                    messages=messages,
                    model=self.llm.current_model,
                    total_tokens=total_tokens,
                    last_prompt_tokens=last_prompt_tokens,
                    latency_ms=latency_ms,
                    iterations=iteration,
                )

            # 执行工具调用
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_input = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    tool_input = {"raw": tool_call.function.arguments}

                # SKILL 工具渐进式披露：将 SKILL.md 注入系统提示词
                skill_tool = self._find_skill_tool(tool_name, tool_map)
                if skill_tool:
                    skill_md = self.context._load_skill_md(
                        skill_tool.dependencies.get("skill_dir_name", skill_tool.name)
                    ) or ""
                    if skill_md:
                        logger.info("Injecting SKILL.md into system prompt: %s (progressive disclosure)", tool_name)
                        self._emit_step(iteration, event="skill_load", tool_name=tool_name, detail=tool_name)
                        messages = self._inject_skill_into_system_prompt(messages, tool_name, skill_md)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": f"已加载技能 {tool_name} 的用法指南到系统提示词。请仔细阅读系统提示词中「技能指南: {tool_name}」的 Usage 部分，严格按照 Usage 给出的命令格式，使用 execute_bash 执行。",
                        })
                    else:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": f"技能 {tool_name} 的 SKILL.md 未找到。",
                        })
                    continue

                # 记录 LLM 生成的工具调用命令
                if tool_name == "execute_bash":
                    command = tool_input.get("command", tool_input.get("code", ""))
                    logger.info("LLM generated command [%s]: %s", tool_name, command)
                    self._emit_step(iteration, event="tool_call", tool_name=tool_name, command=command)
                else:
                    logger.info("LLM generated tool call [%s]: %s", tool_name, json.dumps(tool_input, ensure_ascii=False))
                    self._emit_step(iteration, event="tool_call", tool_name=tool_name)

                tool_result = await self._execute_tool(tool_name, tool_input, tool_map, expanded_tools)

                # 回调：工具执行结果
                result_summary = tool_result[:200] if len(tool_result) > 200 else tool_result
                self._emit_step(iteration, event="tool_result", tool_name=tool_name, result_summary=result_summary)

                # 上下文压缩：大结果预写临时文件 + 预生成摘要
                # 本轮仍展示完整结果，下一轮 compress_old_results 会替换为摘要
                if self._compressor and len(tool_result) > self._compressor.config.result_threshold:
                    await self._compressor.compress_result(tool_name, tool_call.id, tool_result)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                })

        # 达到最大迭代次数
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        return MetaAgentOutput(
            output="达到最大工具调用迭代次数，请简化您的问题。",
            output_type="text",
            success=False,
            messages=messages,
            model=self.llm.current_model,
            total_tokens=total_tokens,
            last_prompt_tokens=last_prompt_tokens,
            latency_ms=latency_ms,
            iterations=iteration,
        )

    def _try_parse_evolve_result(self, content: str) -> EvolveResult | None:
        """尝试解析为 EvolveResult，用于识别 ask_user/delegate 等特殊输出"""
        try:
            json_str = extract_json_from_llm_output(content)
            result_dict = json.loads(json_str)
            if "route" in result_dict and "action" not in result_dict:
                result_dict["action"] = result_dict.pop("route")
            result = EvolveResult(**result_dict)
            # 只有 ask_user 和 delegate 需要特殊处理
            if result.action in ("ask_user", "delegate"):
                return result
            return None
        except (json.JSONDecodeError, ValueError):
            return None

    def _emit_step(
        self,
        iteration: int,
        event: str,
        tool_name: str = "",
        command: str = "",
        result_summary: str = "",
        detail: str = "",
    ) -> None:
        """触发步骤回调"""
        if self._on_step:
            self._on_step({
                "iteration": iteration,
                "max_iterations": self.max_iterations,
                "event": event,
                "tool_name": tool_name,
                "command": command,
                "result_summary": result_summary,
                "detail": detail,
            })

    async def _execute_tool(
        self,
        tool_name: str,
        tool_input: dict,
        tool_map: dict[str, Tool],
        expanded_tools: list[Tool] | None = None,
    ) -> str:
        """执行单个工具调用并返回结果字符串"""
        tool = tool_map.get(tool_name)

        if not tool and self.tool_registry:
            tool = self.tool_registry.get(tool_name) or self.tool_registry.get_by_name(tool_name)

        if not tool:
            return f"工具未找到: {tool_name}"

        if not self.executor_registry:
            return f"工具执行器不可用: {tool_name}"

        try:
            logger.info("Executing tool: %s (id: %s)", tool.name, tool.id)
            executor = self.executor_registry.get_executor(tool)

            # 当执行 execute_bash 时，合并 SKILL 工具的 env
            if tool_name == "execute_bash" and expanded_tools and hasattr(executor, 'env'):
                skill_env = {}
                for t in expanded_tools:
                    if t.execution.type == ToolExecutionType.SKILL and t.execution.env:
                        skill_env.update(t.execution.env)
                if skill_env:
                    executor.env = {**(executor.env or {}), **skill_env}

            if tool.execution.type == ToolExecutionType.MCP:
                tool_input_with_name = {"tool_name": tool.name, **tool_input}
                tool_result = await executor.execute(**tool_input_with_name)
            else:
                tool_result = await executor.execute(**tool_input)
            return self._format_tool_result(tool_result)
        except Exception as e:
            logger.exception("Tool execution failed")
            return f"工具调用失败: {e}"

    def _inject_skill_into_system_prompt(
        self,
        messages: list[dict[str, Any]],
        skill_name: str,
        skill_md: str,
    ) -> list[dict[str, Any]]:
        """将 SKILL.md 注入系统提示词，返回更新后的 messages"""
        if not messages or messages[0].get("role") != "system":
            return messages

        skill_section = f"\n\n## 技能指南: {skill_name}\n\n{skill_md}"
        messages[0]["content"] = messages[0]["content"] + skill_section
        logger.info("System prompt updated: injected SKILL.md for %s (%d chars added)", skill_name, len(skill_section))
        logger.info("System prompt appended content:\n%s", skill_section)
        return messages

    def _find_skill_tool(
        self,
        tool_name: str,
        tool_map: dict[str, Tool],
    ) -> Tool | None:
        """检查工具名是否对应一个 SKILL 工具"""
        tool = tool_map.get(tool_name)
        if tool and tool.execution.type == ToolExecutionType.SKILL:
            return tool
        if self.tool_registry:
            tool = self.tool_registry.get(tool_name) or self.tool_registry.get_by_name(tool_name)
            if tool and tool.execution.type == ToolExecutionType.SKILL:
                return tool
        return None

    def _ensure_bash_available(
        self,
        openai_tools: list[dict],
        tool_map: dict[str, Tool],
    ) -> tuple[list[dict], dict[str, Tool]]:
        """确保 execute_bash 在 openai_tools 中可用（evolve agent 的元工具）"""
        existing_names = {t.get("function", {}).get("name") for t in openai_tools if "function" in t}
        if "execute_bash" in existing_names:
            return openai_tools, tool_map

        if self.tool_registry:
            bash_tool = self.tool_registry.get("execute_bash")
            if bash_tool and bash_tool.name not in existing_names:
                openai_tools.append(bash_tool.to_openai_function())
                tool_map[bash_tool.name] = bash_tool
                logger.info("Adding execute_bash to openai_tools (evolve meta-tool)")

        return openai_tools, tool_map

    @staticmethod
    def _format_tool_result(tool_result: Any) -> str:
        """将工具执行结果格式化为字符串"""
        if isinstance(tool_result, str):
            return tool_result
        if hasattr(tool_result, "text"):
            return tool_result.text
        if isinstance(tool_result, list):
            text_parts = []
            for item in tool_result:
                if hasattr(item, "text"):
                    text_parts.append(item.text)
                elif hasattr(item, "type") and getattr(item, "type", None) == "text":
                    text_parts.append(getattr(item, "text", str(item)))
                else:
                    text_parts.append(str(item))
            return "\n\n".join(text_parts) if text_parts else ""
        try:
            return json.dumps(tool_result, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(tool_result)