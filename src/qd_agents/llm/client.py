"""
LLM 客户端 - NVIDIA NIM API 兼容
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Literal

from openai import AsyncOpenAI, APIError, APIStatusError, APITimeoutError

from .scoring import ModelInfo, get_top_models, calculate_model_score


logger = logging.getLogger(__name__)


class LLMError(Exception):
    """LLM 错误基类"""
    pass


class AllModelsFailedError(LLMError):
    """所有模型都失败了"""
    pass


class LLMClient:
    """
    LLM 客户端 - 支持多模型 Fallback

    使用 NVIDIA NIM API（OpenAI 兼容格式）
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://integrate.api.nvidia.com/v1",
        model_names: list[str] | None = None,
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        """
        初始化 LLM 客户端

        Args:
            api_key: NVIDIA API Key
            base_url: API 基础 URL
            model_names: 预定义模型列表（如果不提供，会自动发现）
            timeout: 请求超时（秒）
            max_retries: 最大重试次数
        """
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )
        self._model_names: list[str] = model_names or []
        self._current_model_index = 0
        self._api_key = api_key
        self._base_url = base_url
        self._meta_agent_name: str = ""  # 当前使用的元Agent 名称
        self._meta_agent_message_counts: dict[str, int] = {}  # 每个元Agent 的消息计数器

    @property
    def meta_agent_name(self) -> str:
        """获取当前元Agent 名称"""
        return self._meta_agent_name

    @meta_agent_name.setter
    def meta_agent_name(self, name: str):
        """设置当前元Agent 名称"""
        self._meta_agent_name = name
        # 初始化该元Agent 的计数器（如果不存在）
        if name not in self._meta_agent_message_counts:
            self._meta_agent_message_counts[name] = 0

    def _get_logged_message_count(self) -> int:
        """获取当前元Agent 的已记录消息数量"""
        return self._meta_agent_message_counts.get(self._meta_agent_name, 0)

    def _update_logged_message_count(self, count: int):
        """更新当前元Agent 的已记录消息数量"""
        self._meta_agent_message_counts[self._meta_agent_name] = count

    @property
    def current_model(self) -> str:
        """获取当前使用的模型"""
        if self._model_names:
            return self._model_names[self._current_model_index]
        return ""

    @property
    def available_models(self) -> list[str]:
        """获取可用模型列表"""
        return self._model_names.copy()

    def switch_model(self, model_name: str) -> bool:
        """
        切换到指定模型

        Args:
            model_name: 模型名称

        Returns:
            是否切换成功
        """
        if model_name in self._model_names:
            self._current_model_index = self._model_names.index(model_name)
            logger.info("Switched to model: %s", model_name)
            return True
        logger.warning("Model not found: %s", model_name)
        return False

    async def discover_models(self, top_k: int = 5) -> list[str]:
        """
        发现并选择可用模型

        Args:
            top_k: 选择得分最高的 K 个模型

        Returns:
            模型名称列表
        """
        logger.info("Discovering available models from NVIDIA NIM API...")

        try:
            models_response = await self._client.models.list()
            models: list[ModelInfo] = []

            for m in models_response.data:
                models.append(ModelInfo(
                    id=m.id,
                    name=m.id,
                    created=getattr(m, "created", None),
                    owned_by=getattr(m, "owned_by", None),
                    capabilities=getattr(m, "capabilities", None),
                ))

            if not models:
                logger.warning("No models found, using fallback model list")
                return self._get_default_models()

            # 选择 Top K 模型，如果 top_k=0 则返回所有模型
            if top_k > 0:
                top_models = get_top_models(models, top_k=top_k)
            else:
                # 返回所有 chat 模型
                top_models = [m for m in models if calculate_model_score(m) > 0]

            self._model_names = [m.name for m in top_models]
            self._current_model_index = 0

            logger.info("Selected models: %s", self._model_names)
            return self._model_names

        except Exception as e:
            logger.error("Failed to discover models: %s", e)
            return self._get_default_models()

    def _get_default_models(self) -> list[str]:
        """获取默认模型列表"""
        return [
            "deepseek-ai/DeepSeek-V3",
            "deepseek-ai/DeepSeek-R1",
            "meta/llama-3.1-70b-instruct",
            "mistralai/mistral-large-2-instruct",
            "Qwen/Qwen2.5-72B-Instruct",
        ]

    def _failover_to_next_model(self) -> bool:
        """
        切换到下一个模型

        Returns:
            如果还有更多模型可用返回 True
        """
        if self._current_model_index + 1 < len(self._model_names):
            self._current_model_index += 1
            logger.warning(
                "Failing over to model: %s",
                self.current_model
            )
            return True
        return False

    def _clean_escape_sequences(self, text: str) -> str:
        """
        清理常见的转义字符序列，提高可读性

        只处理控制字符的转义序列（\\n, \\r, \\t, \\\\, \\\", \\'），
        保留Unicode转义序列和其他转义序列，避免破坏原始内容。

        注意：替换顺序很重要，\\\\ 必须最先处理，否则会破坏其他替换结果。

        Args:
            text: 包含转义字符的文本

        Returns:
            清理后的文本
        """
        if not isinstance(text, str):
            return str(text)

        # 替换顺序：\\\\ 必须最先，否则 \\n 中的 \\\\ 会先被匹配破坏
        replacements = [
            ('\\\\', '\\'),       # 反斜杠（必须最先）
            ('\\n', '\n'),        # 换行
            ('\\r', '\r'),        # 回车
            ('\\t', '\t'),        # 制表符
            ('\\"', '"'),         # 双引号
            ("\\'", "'"),         # 单引号
        ]

        cleaned = text
        for old, new in replacements:
            cleaned = cleaned.replace(old, new)

        return cleaned

    def _format_messages_for_logging(self, messages: list[dict[str, Any]], start_index: int = 0) -> str:
        """格式化消息用于日志记录，纯文本渲染，还原 messages 数组结构

        Args:
            messages: 消息列表
            start_index: 开始记录的消息索引（用于增量日志）
        """
        if not messages:
            return "[]"

        formatted_parts = []

        for i, msg in enumerate(messages):
            # 跳过已记录的消息
            if i < start_index:
                continue

            lines: list[str] = []
            role = msg.get("role", "unknown")
            lines.append(f'  [{i}] {{"role": "{role}"')

            # content — 纯文本渲染
            content = msg.get("content")
            if content is not None:
                # tool 角色的 content 尝试 JSON 格式化输出
                if role == "tool" and isinstance(content, str):
                    try:
                        parsed = json.loads(content)
                        text = json.dumps(parsed, ensure_ascii=False, indent=2)
                        # 缩进对齐
                        indented_lines = text.split("\n")
                        text = indented_lines[0] + "\n" + "\n".join(
                            "          " + line for line in indented_lines[1:]
                        )
                    except (json.JSONDecodeError, ValueError):
                        text = self._format_content(content)
                else:
                    text = self._format_content(content)
                lines.append(f'      "content": {text}')

            # tool_calls — 纯文本渲染
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                lines.append('      "tool_calls": [')
                tc_list = self._tool_calls_to_dicts(tool_calls)
                for j, tc in enumerate(tc_list):
                    tc_text = self._format_tool_call_text(tc)
                    comma = "," if j < len(tc_list) - 1 else ""
                    lines.append(f"        {tc_text}{comma}")
                lines.append("      ]")

            # tool 角色额外字段
            if role == "tool":
                tool_call_id = msg.get("tool_call_id")
                if tool_call_id:
                    lines.append(f'      "tool_call_id": "{tool_call_id}"')
                tool_name = msg.get("name")
                if tool_name:
                    lines.append(f'      "name": "{tool_name}"')

            lines.append("    }")
            formatted_parts.append("\n".join(lines))

        return "[\n" + ",\n".join(formatted_parts) + "\n]"

    def _format_content(self, content: Any) -> str:
        """格式化消息 content 字段，纯文本渲染"""
        if content is None:
            return "[no content]"

        if isinstance(content, str):
            return self._clean_escape_sequences(content)

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    item_type = item.get("type", "unknown")
                    if item_type == "text":
                        parts.append(self._clean_escape_sequences(item.get("text", "")))
                    elif item_type == "image_url":
                        url = item.get("image_url", {})
                        parts.append(f"[image: {str(url)[:80]}]")
                    else:
                        parts.append(json.dumps(item, ensure_ascii=False, default=str))
                else:
                    parts.append(str(item))
            return "\n".join(parts)

        return str(content)

    def _tool_calls_to_dicts(self, tool_calls: Any) -> list[dict[str, Any]]:
        """将 tool_calls 转为 dict 列表"""
        result = []
        for tc in tool_calls:
            if hasattr(tc, 'model_dump'):
                result.append(tc.model_dump())
            elif isinstance(tc, dict):
                result.append(tc)
            else:
                result.append({"raw": str(tc)})
        return result

    def _format_tool_call_text(self, tc: dict[str, Any]) -> str:
        """将单个 tool_call dict 格式化为纯文本行"""
        tc_id = tc.get("id", "")
        func = tc.get("function", {})
        name = func.get("name", "") if isinstance(func, dict) else ""
        arguments = func.get("arguments", "") if isinstance(func, dict) else ""

        # arguments 解码：如果它是 JSON 字符串，解析后纯文本渲染
        args_text = arguments
        if isinstance(arguments, str) and arguments.strip():
            try:
                args_parsed = json.loads(arguments)
                args_text = json.dumps(args_parsed, ensure_ascii=False, indent=2)
                # 缩进对齐
                args_lines = args_text.split("\n")
                args_text = "\n".join(
                    args_lines[0] if k == 0 else "          " + line
                    for k, line in enumerate(args_lines)
                )
            except (json.JSONDecodeError, ValueError):
                args_text = self._clean_escape_sequences(arguments)

        return f'{{"id": "{tc_id}", "function": {{"name": "{name}", "arguments": {args_text}}}}}'

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        stream: bool = False,
    ) -> Any:
        """
        聊天补全（带自动 Fallback）

        Args:
            messages: 消息列表
            model: 模型名称（不指定则使用当前模型）
            temperature: 温度参数
            max_tokens: 最大生成 token 数
            tools: 工具定义
            tool_choice: 工具选择策略
            stream: 是否流式输出

        Returns:
            OpenAI 响应对象

        Raises:
            AllModelsFailedError: 所有模型都失败时
        """
        last_exception: Exception | None = None
        start_index = self._current_model_index

        # 尝试所有模型，直到成功
        while True:
            use_model = model or self.current_model

            try:
                logger.info("Calling model: %s (MetaAgent: %s)", use_model, self._meta_agent_name or "unknown")
                # 记录输入 messages (INFO level) - 增量日志，只显示新增的消息
                logged_count = self._get_logged_message_count()
                new_msg_count = len(messages) - logged_count
                if new_msg_count > 0:
                    formatted_messages = self._format_messages_for_logging(messages, logged_count)
                    logger.info("LLM Prompt (MetaAgent: %s) [%d new messages]:\n%s", self._meta_agent_name or "unknown", new_msg_count, formatted_messages)
                    self._update_logged_message_count(len(messages))
                else:
                    logger.info("LLM Prompt (MetaAgent: %s): [no new messages]", self._meta_agent_name or "unknown")

                response = await self._client.chat.completions.create(
                    model=use_model,
                    messages=messages,
                    temperature=temperature,
                    max_completion_tokens=max_tokens,
                    tools=tools,
                    tool_choice=tool_choice,
                    stream=stream,
                )

                if not stream:
                    logger.debug("Response: %s", response.model_dump_json(ensure_ascii=False) if hasattr(response, 'model_dump_json') else str(response))  # type: ignore
                    # 记录输出 content (INFO level) — 与 Prompt 相同的纯文本格式
                    if response.choices and len(response.choices) > 0:
                        message = response.choices[0].message
                        completion_display = {"role": "assistant"}
                        if message.content:
                            completion_display["content"] = message.content
                        if hasattr(message, 'tool_calls') and message.tool_calls:
                            completion_display["tool_calls"] = self._tool_calls_to_dicts(message.tool_calls)
                        if not completion_display.get("content") and "tool_calls" not in completion_display:
                            completion_display["content"] = "[no content or tool calls]"
                        logger.info("LLM Completion (MetaAgent: %s):\n%s", self._meta_agent_name or "unknown", self._format_messages_for_logging([completion_display]))

                    # 记录 token 使用情况
                    if hasattr(response, 'usage') and response.usage:
                        logger.info(
                            "Token usage - model: %s, prompt: %d, completion: %d, total: %d",
                            use_model,
                            response.usage.prompt_tokens,
                            response.usage.completion_tokens,
                            response.usage.total_tokens
                        )

                return response

            except (APIError, APIStatusError, APITimeoutError) as e:
                last_exception = e
                logger.warning(
                    "Model %s failed: %s",
                    use_model,
                    e
                )

                # 尝试切换到下一个模型
                if not self._failover_to_next_model():
                    # 已试过所有模型
                    break

                # 如果回到了起点，也退出
                if self._current_model_index == start_index:
                    break

            except Exception as e:
                last_exception = e
                logger.error(
                    "Unexpected error with model %s: %s",
                    use_model,
                    e
                )
                break

        # 所有模型都失败了
        raise AllModelsFailedError(
            f"All models failed. Last error: {last_exception}"
        ) from last_exception

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[Any]:
        """
        流式聊天补全

        Yields:
            OpenAI 流式响应块
        """
        # 流式响应不自动重试（太复杂）
        use_model = model or self.current_model
        # 记录输入 messages (INFO level) - 增量日志
        logger.info("Calling model (stream): %s (MetaAgent: %s)", use_model, self._meta_agent_name or "unknown")
        logged_count = self._get_logged_message_count()
        new_msg_count = len(messages) - logged_count
        if new_msg_count > 0:
            formatted_messages = self._format_messages_for_logging(messages, logged_count)
            logger.info("LLM Prompt (stream, MetaAgent: %s) [%d new messages]:\n%s", self._meta_agent_name or "unknown", new_msg_count, formatted_messages)
            self._update_logged_message_count(len(messages))
        else:
            logger.info("LLM Prompt (stream, MetaAgent: %s): [no new messages]", self._meta_agent_name or "unknown")

        stream = await self._client.chat.completions.create(
            model=use_model,
            messages=messages,
            temperature=temperature,
            max_completion_tokens=max_tokens,
            tools=tools,
            stream=True,
        )

        async for chunk in stream:
            # 记录 token 使用情况（流式响应的最后一个 chunk 包含 usage）
            if hasattr(chunk, 'usage') and chunk.usage:
                logger.info(
                    "Token usage - model: %s, prompt: %d, completion: %d, total: %d",
                    use_model,
                    chunk.usage.prompt_tokens,
                    chunk.usage.completion_tokens,
                    chunk.usage.total_tokens
                )
            yield chunk

    async def close(self) -> None:
        """关闭客户端"""
        await self._client.close()

    async def __aenter__(self) -> "LLMClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()


def create_client(
    api_key: str,
    base_url: str = "https://integrate.api.nvidia.com/v1",
    auto_discover: bool = True,
    top_k: int = 5,
) -> LLMClient:
    """
    创建并初始化 LLM 客户端

    Args:
        api_key: NVIDIA API Key
        base_url: API 基础 URL
        auto_discover: 是否自动发现模型
        top_k: 自动发现时选择的 Top K 模型

    Returns:
        初始化后的 LLMClient
    """
    client = LLMClient(
        api_key=api_key,
        base_url=base_url,
    )

    if auto_discover:
        asyncio.create_task(client.discover_models(top_k=top_k))

    return client
