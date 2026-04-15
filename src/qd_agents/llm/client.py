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
                logger.info("Calling model: %s", use_model)

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
                    logger.debug("Response: %s", response.model_dump_json() if hasattr(response, 'model_dump_json') else str(response))  # type: ignore

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

        stream = await self._client.chat.completions.create(
            model=use_model,
            messages=messages,
            temperature=temperature,
            max_completion_tokens=max_tokens,
            tools=tools,
            stream=True,
        )

        async for chunk in stream:
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
