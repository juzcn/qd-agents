"""
NVIDIA 模型池 - 动态获取模型列表，建立 fallback 机制
"""

import os
from typing import List, Optional, Dict, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from dotenv import load_dotenv
from openai import OpenAI
from openai.types.model import Model as OpenAIModel

from ..utils.debug import debug_print, debug_step


# 从多个可能的位置加载 .env
def _find_and_load_env() -> None:
    """查找并加载 .env 文件"""
    # 可能的 .env 位置
    possible_paths = [
        # 当前工作目录
        ".env",
        # 当前文件的各级父目录
        os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"),
        os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
    ]

    for path in possible_paths:
        if os.path.exists(path):
            load_dotenv(path)
            return


_find_and_load_env()


class ModelType(Enum):
    """模型类型"""
    CHAT = "chat"
    TEXT = "text"
    EMBEDDING = "embedding"


@dataclass
class ModelInfo:
    """模型信息"""
    id: str
    name: str
    model_type: ModelType
    context_window: int = 0
    is_free: bool = True
    priority: int = 0  # 优先级，数字越小越优先

    @classmethod
    def from_openai_model(cls, model: OpenAIModel) -> "ModelInfo":
        """从 OpenAI Model 对象创建"""
        model_id = model.id

        # 判断模型类型
        model_type = ModelType.CHAT
        if "embedding" in model_id.lower():
            model_type = ModelType.EMBEDDING

        # 提取模型名称（去掉前缀）
        name = model_id
        if "/" in name:
            name = name.split("/")[-1]

        # 估算优先级（基于模型名称判断流行度）
        priority = _calculate_priority(model_id)

        return cls(
            id=model_id,
            name=name,
            model_type=model_type,
            priority=priority
        )


def _calculate_priority(model_id: str) -> int:
    """
    根据模型ID计算优先级（越小越优先）

    基于模型流行度和能力评分：
    - Llama 3 / 3.1 系列最优先
    - Gemma 2 系列
    - Mistral / Mixtral 系列
    - Qwen 系列
    """
    model_lower = model_id.lower()

    # Llama 3.1 (最新最强)
    if "llama-3.1" in model_lower or "llama3.1" in model_lower:
        if "70b" in model_lower:
            return 1
        if "8b" in model_lower:
            return 2
        return 3

    # Llama 3
    if "llama-3" in model_lower or "llama3" in model_lower:
        if "70b" in model_lower:
            return 4
        if "8b" in model_lower:
            return 5
        return 6

    # Gemma 2
    if "gemma-2" in model_lower or "gemma2" in model_lower:
        if "27b" in model_lower:
            return 7
        if "9b" in model_lower:
            return 8
        return 9

    # Mixtral / Mistral
    if "mixtral" in model_lower:
        if "8x22b" in model_lower:
            return 10
        if "8x7b" in model_lower:
            return 11
        return 12

    if "mistral" in model_lower:
        if "large" in model_lower:
            return 13
        if "7b" in model_lower:
            return 14
        return 15

    # Qwen
    if "qwen" in model_lower:
        if "72b" in model_lower or "max" in model_lower:
            return 16
        if "32b" in model_lower or "14b" in model_lower:
            return 17
        if "7b" in model_lower:
            return 18
        return 19

    # 其他模型
    return 99


class NvidiaModelPool:
    """
    NVIDIA 模型池

    功能：
    - 启动时从 NVIDIA API 获取所有可用模型
    - 筛选出 5 个最强最流行的免费模型
    - 提供 fallback 机制：主模型失败时自动尝试下一个
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        pool_size: int = 5
    ):
        """
        初始化模型池

        Args:
            api_key: NVIDIA API Key，默认从环境变量 NVAPI_KEY 读取
            base_url: NVIDIA API Base URL，默认从环境变量 NVIDIA_BASE_URL 读取
            pool_size: 模型池大小（选择最强的 N 个模型）
        """
        self.api_key = api_key or os.getenv("NVAPI_KEY")
        self.base_url = base_url or os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
        self.pool_size = pool_size

        if not self.api_key:
            raise ValueError("NVAPI_KEY 未设置，请在环境变量或 .env 文件中配置")

        self._client: Optional[OpenAI] = None
        self._model_pool: List[ModelInfo] = []
        self._current_index = 0

        # 初始化
        self._init_client()
        self._fetch_and_build_pool()

    def _init_client(self) -> None:
        """初始化 OpenAI 客户端"""
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
        debug_step("NVIDIA", "OpenAI 客户端初始化完成")

    def _fetch_and_build_pool(self) -> None:
        """获取模型列表并构建模型池"""
        if self._client is None:
            raise RuntimeError("客户端未初始化")

        debug_step("NVIDIA", "从 API 获取模型列表...")

        # 获取所有可用模型
        models = self._client.models.list()

        debug_print("获取到的模型总数", len(models.data), style="cyan")

        # 筛选和排序
        chat_models: List[ModelInfo] = []

        for model in models.data:
            model_info = ModelInfo.from_openai_model(model)

            # 只保留聊天模型
            if model_info.model_type != ModelType.CHAT:
                continue

            # 只包含免费模型（NVIDIA 免费模型通常在 ID 中不包含 premium 等标识）
            # 这里假设所有列出的都是免费模型
            chat_models.append(model_info)

        # 按优先级排序
        chat_models.sort(key=lambda m: m.priority)

        # 选择前 pool_size 个
        self._model_pool = chat_models[:self.pool_size]

        debug_print("模型池已构建", [
            {"id": m.id, "priority": m.priority}
            for m in self._model_pool
        ], style="green")

    def get_current_model(self) -> ModelInfo:
        """获取当前使用的模型"""
        if not self._model_pool:
            raise RuntimeError("模型池为空")
        return self._model_pool[self._current_index]

    def get_all_models(self) -> List[ModelInfo]:
        """获取模型池中的所有模型"""
        return list(self._model_pool)

    def fallback(self) -> Optional[ModelInfo]:
        """
        切换到下一个模型（fallback）

        Returns:
            新的模型，如果没有更多模型则返回 None
        """
        if self._current_index + 1 < len(self._model_pool):
            self._current_index += 1
            new_model = self._model_pool[self._current_index]
            debug_step("Fallback", f"切换到模型: {new_model.id}")
            return new_model
        return None

    def reset(self) -> None:
        """重置到第一个模型"""
        self._current_index = 0

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        on_fallback: Optional[Callable[[ModelInfo, Exception], None]] = None
    ) -> str:
        """
        聊天补全（带自动 fallback）

        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大 token 数
            on_fallback: fallback 时的回调函数 (model, exception) -> None

        Returns:
            模型回复内容
        """
        if self._client is None:
            raise RuntimeError("客户端未初始化")

        last_exception: Optional[Exception] = None

        # 尝试模型池中的每个模型
        for attempt in range(len(self._model_pool)):
            model = self._model_pool[self._current_index]

            try:
                debug_step("调用模型", f"尝试使用: {model.id}")

                response = self._client.chat.completions.create(
                    model=model.id,
                    messages=messages,  # type: ignore
                    temperature=temperature,
                    max_tokens=max_tokens
                )

                content = response.choices[0].message.content or ""
                debug_print("模型响应", content, style="green")
                return content

            except Exception as e:
                last_exception = e
                debug_print(f"模型 {model.id} 调用失败", str(e), style="red")

                if on_fallback:
                    on_fallback(model, e)

                # 尝试 fallback 到下一个模型
                if self.fallback() is None:
                    break

        # 所有模型都失败了
        if last_exception:
            raise RuntimeError(f"所有模型都失败了。最后一个错误: {last_exception}") from last_exception
        raise RuntimeError("所有模型都失败了")
