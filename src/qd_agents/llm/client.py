"""LLM 客户端 - OpenAI 兼容 API"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from openai import AsyncOpenAI, APIError, APIStatusError, APITimeoutError

from .scoring import ModelInfo, get_top_models, calculate_model_score
from .logging import LLMLogger

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """LLM 错误基类"""
    pass


class AllModelsFailedError(LLMError):
    """所有模型都失败了"""
    pass


class LLMClient:
    """LLM 客户端 - 支持多模型 Fallback，支持 Chat Completions 和 Responses 两种 API 模式"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://integrate.api.nvidia.com/v1",
        model_names: list[str] | None = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        api_mode: str = "completion",
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
        self._api_mode = api_mode
        self._meta_agent_name: str = ""
        self._logger = LLMLogger()

    @property
    def api_mode(self) -> str:
        return self._api_mode

    @property
    def meta_agent_name(self) -> str:
        return self._meta_agent_name

    @meta_agent_name.setter
    def meta_agent_name(self, name: str):
        self._meta_agent_name = name

    @property
    def current_model(self) -> str:
        if self._model_names:
            return self._model_names[self._current_model_index]
        return ""

    @property
    def available_models(self) -> list[str]:
        return self._model_names.copy()

    def switch_model(self, model_name: str) -> bool:
        if model_name in self._model_names:
            self._current_model_index = self._model_names.index(model_name)
            logger.info("Switched to model: %s", model_name)
            return True
        logger.warning("Model not found: %s", model_name)
        return False

    async def discover_models(self, top_k: int = 5) -> list[str]:
        """发现并选择可用模型"""
        logger.info("Discovering available models...")

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
        return [
            "deepseek-ai/DeepSeek-V3",
            "deepseek-ai/DeepSeek-R1",
            "meta/llama-3.1-70b-instruct",
            "mistralai/mistral-large-2-instruct",
            "Qwen/Qwen2.5-72B-Instruct",
        ]

    def reset_log_count(self, messages: list[dict[str, Any]] | None = None) -> None:
        self._logger.reset_log_count(self._meta_agent_name, messages)

    def _failover_to_next_model(self) -> bool:
        if self._current_model_index + 1 < len(self._model_names):
            self._current_model_index += 1
            logger.warning("Failing over to model: %s", self.current_model)
            return True
        return False

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
        """聊天补全（带自动 Fallback）— 根据 api_mode 分派"""
        if self._api_mode == "response":
            return await self._call_responses(
                messages=messages, model=model, temperature=temperature,
                max_tokens=max_tokens, tools=tools, tool_choice=tool_choice,
                stream=stream,
            )
        return await self._call_completions(
            messages=messages, model=model, temperature=temperature,
            max_tokens=max_tokens, tools=tools, tool_choice=tool_choice,
            stream=stream,
        )

    async def _call_completions(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        stream: bool = False,
    ) -> Any:
        """Chat Completions API 调用（带自动 Fallback）"""
        last_exception: Exception | None = None
        start_index = self._current_model_index

        while True:
            use_model = model or self.current_model

            try:
                logger.info("Calling model: %s (MetaAgent: %s)", use_model, self._meta_agent_name or "unknown")
                self._logger.log_prompt(messages, self._meta_agent_name or "unknown")

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
                    self._logger.log_completion(response, use_model, self._meta_agent_name or "unknown")
                    self._logger.log_token_usage(getattr(response, 'usage', None), use_model)

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

    async def _call_responses(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        stream: bool = False,
    ) -> Any:
        """Responses API 调用（带自动 Fallback），返回 Chat Completions 兼容格式"""
        last_exception: Exception | None = None
        start_index = self._current_model_index

        while True:
            use_model = model or self.current_model

            try:
                logger.info("Calling model (responses): %s (MetaAgent: %s)", use_model, self._meta_agent_name or "unknown")
                self._logger.log_prompt(messages, self._meta_agent_name or "unknown")

                kwargs: dict[str, Any] = {
                    "model": use_model,
                    "input": messages,
                }
                if temperature != 0.7:
                    kwargs["temperature"] = temperature
                if max_tokens is not None:
                    kwargs["max_output_tokens"] = max_tokens
                if tools is not None:
                    kwargs["tools"] = tools
                if tool_choice is not None:
                    kwargs["tool_choice"] = tool_choice

                response = await self._client.responses.create(**kwargs)
                self._logger.log_token_usage(getattr(response, 'usage', None), use_model)

                return self._convert_response(response)

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

    @staticmethod
    def _convert_response(response: Any) -> Any:
        """将 Responses API 响应转换为 Chat Completions 兼容格式

        MetaAgent.run_loop() 期望 response.choices[0].message.content 和 .tool_calls。
        此方法从 Responses API 的 response.output 中提取相同结构。
        """
        from openai.types.chat import ChatCompletion, ChatCompletionMessage
        from openai.types.chat.chat_completion_message_tool_call import Function, ChatCompletionMessageFunctionToolCall

        content = ""
        tool_calls: list[ChatCompletionMessageFunctionToolCall] = []

        for item in (response.output or []):
            if getattr(item, 'type', None) == "message":
                for content_item in (item.content or []):
                    if getattr(content_item, 'type', None) == "output_text":
                        content += content_item.text or ""
            elif getattr(item, 'type', None) == "function_call":
                tool_calls.append(ChatCompletionMessageFunctionToolCall(
                    id=str(getattr(item, 'call_id', None) or getattr(item, 'id', '')),
                    function=Function(
                        name=item.name,
                        arguments=item.arguments,
                    ),
                    type="function",
                ))

        from typing import cast
        message = ChatCompletionMessage(
            role="assistant",
            content=content or None,
            tool_calls=cast(Any, tool_calls if tool_calls else None),
        )

        choices = [type('Choice', (), {
            'index': 0,
            'message': message,
            'finish_reason': 'tool_calls' if tool_calls else 'stop',
        })()]

        return ChatCompletion(
            id=response.id,
            choices=choices,
            created=response.created_at if hasattr(response, 'created_at') else 0,
            model=response.model,
            object="chat.completion",
            usage=response.usage,
        )

    def format_tool_result(self, tool_call_id: str, result: str) -> dict[str, Any]:
        """格式化工具执行结果，根据 api_mode 返回对应格式的消息 dict"""
        if self._api_mode == "response":
            return {
                "type": "function_call_output",
                "call_id": tool_call_id,
                "output": result,
            }
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result,
        }

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
        self._logger.log_prompt(messages, self._meta_agent_name or "unknown", is_stream=True)

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
                self._logger.log_token_usage(chunk.usage, use_model)
            yield chunk

    async def close(self) -> None:
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