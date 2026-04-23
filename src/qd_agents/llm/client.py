"""
LLM 客户端 - NVIDIA NIM API 兼容
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from openai import AsyncOpenAI, APIError, APIStatusError, APITimeoutError

from .scoring import ModelInfo, get_top_models, calculate_model_score
from .formatters import format_messages_for_logging, tool_calls_to_dicts


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
        self._meta_agent_name: str = ""
        self._meta_agent_message_counts: dict[str, int] = {}

    @property
    def meta_agent_name(self) -> str:
        """获取当前元Agent 名称"""
        return self._meta_agent_name

    @meta_agent_name.setter
    def meta_agent_name(self, name: str):
        """设置当前元Agent 名称"""
        self._meta_agent_name = name
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
        """切换到指定模型"""
        if model_name in self._model_names:
            self._current_model_index = self._model_names.index(model_name)
            logger.info("Switched to model: %s", model_name)
            return True
        logger.warning("Model not found: %s", model_name)
        return False

    async def discover_models(self, top_k: int = 5) -> list[str]:
        """发现并选择可用模型"""
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

            if top_k > 0:
                top_models = get_top_models(models, top_k=top_k)
            else:
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
        """切换到下一个模型"""
        if self._current_model_index + 1 < len(self._model_names):
            self._current_model_index += 1
            logger.warning("Failing over to model: %s", self.current_model)
            return True
        return False

    def _log_prompt(self, messages: list[dict[str, Any]], is_stream: bool = False) -> None:
        """记录 LLM 输入消息（增量日志）"""
        meta_name = self._meta_agent_name or "unknown"
        logged_count = self._get_logged_message_count()
        new_msg_count = len(messages) - logged_count
        prefix = "stream, " if is_stream else ""

        if new_msg_count > 0:
            formatted = format_messages_for_logging(messages, logged_count)
            logger.info(
                "LLM Prompt (%sMetaAgent: %s) [%d new messages]:\n%s",
                prefix, meta_name, new_msg_count, formatted,
            )
            self._update_logged_message_count(len(messages))
        else:
            logger.info("LLM Prompt (%sMetaAgent: %s): [no new messages]", prefix, meta_name)

    def _log_completion(self, response: Any, model: str) -> None:
        """记录 LLM 输出（非流式）"""
        if not response.choices:
            return

        message = response.choices[0].message
        completion_display: dict[str, Any] = {"role": "assistant"}
        if message.content:
            completion_display["content"] = message.content
        if hasattr(message, 'tool_calls') and message.tool_calls:
            completion_display["tool_calls"] = tool_calls_to_dicts(message.tool_calls)
        if not completion_display.get("content") and "tool_calls" not in completion_display:
            completion_display["content"] = "[no content or tool calls]"

        logger.info(
            "LLM Completion (MetaAgent: %s):\n%s",
            self._meta_agent_name or "unknown",
            format_messages_for_logging([completion_display]),
        )

    def _log_token_usage(self, usage: Any, model: str) -> None:
        """记录 token 使用情况"""
        if usage:
            logger.info(
                "Token usage - model: %s, prompt: %d, completion: %d, total: %d",
                model, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens,
            )

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

        Raises:
            AllModelsFailedError: 所有模型都失败时
        """
        last_exception: Exception | None = None
        start_index = self._current_model_index

        while True:
            use_model = model or self.current_model

            try:
                logger.info("Calling model: %s (MetaAgent: %s)", use_model, self._meta_agent_name or "unknown")
                self._log_prompt(messages)

                # Use max_tokens for broader API compatibility (xunfei etc.)
                # max_completion_tokens is OpenAI-only and causes 500 on other providers
                kwargs: dict[str, Any] = {
                    "model": use_model,
                    "messages": messages,
                    "temperature": temperature,
                    "stream": stream,
                }
                if max_tokens is not None:
                    kwargs["max_tokens"] = max_tokens
                if tools is not None:
                    kwargs["tools"] = tools
                if tool_choice is not None:
                    kwargs["tool_choice"] = tool_choice

                response = await self._client.chat.completions.create(**kwargs)

                if not stream:
                    logger.debug(
                        "Response: %s",
                        response.model_dump_json(ensure_ascii=False) if hasattr(response, 'model_dump_json') else str(response),
                    )
                    self._log_completion(response, use_model)
                    self._log_token_usage(getattr(response, 'usage', None), use_model)

                return response

            except (APIError, APIStatusError, APITimeoutError) as e:
                last_exception = e
                logger.warning("Model %s failed: %s", use_model, e)

                if not self._failover_to_next_model():
                    break
                if self._current_model_index == start_index:
                    break

            except Exception as e:
                last_exception = e
                logger.error("Unexpected error with model %s: %s", use_model, e)
                break

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
        """流式聊天补全"""
        use_model = model or self.current_model
        logger.info("Calling model (stream): %s (MetaAgent: %s)", use_model, self._meta_agent_name or "unknown")
        self._log_prompt(messages, is_stream=True)

        kwargs: dict[str, Any] = {
            "model": use_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools is not None:
            kwargs["tools"] = tools

        stream = await self._client.chat.completions.create(**kwargs)

        async for chunk in stream:
            if hasattr(chunk, 'usage') and chunk.usage:
                self._log_token_usage(chunk.usage, use_model)
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
    """创建并初始化 LLM 客户端"""
    client = LLMClient(
        api_key=api_key,
        base_url=base_url,
    )

    if auto_discover:
        asyncio.create_task(client.discover_models(top_k=top_k))

    return client