"""
嵌入引擎 — 支持 llama-cpp-python 和 sentence-transformers 两种后端

懒加载：首次调用 embed() 时才加载模型，避免不使用记忆功能时占用内存。
"""
from __future__ import annotations

import ctypes
import logging
import os
import struct
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)

# 空日志回调 — 替代 llama.cpp 默认的 C→Python 日志回调，从根源避免 ctypes callback 异常。
# 必须是模块级变量，防止被 GC 回收后 C 层访问无效内存导致 access violation。
_NOOP_LOG_CB = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)(
    lambda level, msg, user_data: None
)


class BaseEmbedder(ABC):
    """嵌入引擎抽象基类"""

    @abstractmethod
    def embed(self, text: str) -> bytes:
        """生成文本的嵌入向量，返回 float32 bytes（可直接写入 sqlite-vec）"""

    @abstractmethod
    def embed_as_list(self, text: str) -> list[float]:
        """生成文本的嵌入向量，返回 float 列表"""

    @abstractmethod
    def close(self) -> None:
        """释放模型资源"""


class LlamaCppEmbedder(BaseEmbedder):
    """GGUF 模型嵌入引擎 — llama-cpp-python 后端"""

    def __init__(self, model_path: Path, vec_dim: int = 1024, n_ctx: int = 8192) -> None:
        self._model_path = model_path
        self._vec_dim = vec_dim
        self._n_ctx = n_ctx
        self._model: object | None = None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return

        if not self._model_path.exists():
            raise FileNotFoundError(f"Embedding model not found: {self._model_path}")

        import llama_cpp

        logger.info("Loading embedding model (llama_cpp): %s", self._model_path)

        llama_cpp.llama_log_set(_NOOP_LOG_CB, ctypes.c_void_p(0))

        self._model = llama_cpp.Llama(
            model_path=str(self._model_path),
            embedding=True,
            n_ctx=self._n_ctx,
            verbose=False,
        )
        logger.info("Embedding model loaded (vec_dim=%d, n_ctx=%d)", self._vec_dim, self._n_ctx)

    def close(self) -> None:
        if self._model is not None:
            try:
                del self._model
            except Exception:
                pass
            self._model = None

    def embed(self, text: str) -> bytes:
        self._ensure_model()
        result = self._model.create_embedding([text])  # type: ignore[union-attr]
        vec = result["data"][0]["embedding"]

        if len(vec) != self._vec_dim:
            logger.warning(
                "Embedding dimension mismatch: expected %d, got %d",
                self._vec_dim, len(vec),
            )

        return struct.pack(f"{len(vec)}f", *vec)

    def embed_as_list(self, text: str) -> list[float]:
        self._ensure_model()
        result = self._model.create_embedding([text])  # type: ignore[union-attr]
        return result["data"][0]["embedding"]


class SentenceTransformersEmbedder(BaseEmbedder):
    """sentence-transformers 嵌入引擎 — 支持 BAAI/bge-m3 等 HuggingFace 模型"""

    def __init__(self, model_name: str = "BAAI/bge-m3", vec_dim: int = 1024, hf_token: str = "", hf_cache_dir: str = "") -> None:
        self._model_name = model_name
        self._vec_dim = vec_dim
        self._hf_token = hf_token
        self._hf_cache_dir = hf_cache_dir
        self._model: object | None = None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return

        # 在 import 前设置 HuggingFace 环境变量，避免模型下载到默认 cache 或每次联网检查
        if self._hf_cache_dir:
            os.environ["HF_HOME"] = self._hf_cache_dir
        if self._hf_token:
            os.environ["HF_TOKEN"] = self._hf_token
        # 已有本地模型时跳过在线检查
        os.environ["HF_HUB_OFFLINE"] = "1"

        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model (sentence_transformers): %s", self._model_name)
        kwargs = {}
        if self._hf_token:
            kwargs["token"] = self._hf_token
        self._model = SentenceTransformer(self._model_name, **kwargs)
        logger.info("Embedding model loaded (vec_dim=%d)", self._vec_dim)

    def close(self) -> None:
        if self._model is not None:
            try:
                del self._model
            except Exception:
                pass
            self._model = None

    def embed(self, text: str) -> bytes:
        self._ensure_model()
        vec: list[float] = self._model.encode(text).tolist()  # type: ignore[union-attr]

        if len(vec) != self._vec_dim:
            logger.warning(
                "Embedding dimension mismatch: expected %d, got %d",
                self._vec_dim, len(vec),
            )

        return struct.pack(f"{len(vec)}f", *vec)

    def embed_as_list(self, text: str) -> list[float]:
        self._ensure_model()
        return self._model.encode(text).tolist()  # type: ignore[union-attr]


def create_embedder(
    backend: str = "llama_cpp",
    model_path: Path | None = None,
    model_name: str = "BAAI/bge-m3",
    vec_dim: int = 1024,
    n_ctx: int = 8192,
    hf_token: str = "",
    hf_cache_dir: str = "",
) -> BaseEmbedder:
    """工厂函数 — 根据后端配置创建嵌入引擎"""
    if backend == "sentence_transformers":
        return SentenceTransformersEmbedder(model_name=model_name, vec_dim=vec_dim, hf_token=hf_token, hf_cache_dir=hf_cache_dir)
    if backend == "llama_cpp":
        if model_path is None:
            raise ValueError("llama_cpp backend requires model_path")
        return LlamaCppEmbedder(model_path=model_path, vec_dim=vec_dim, n_ctx=n_ctx)
    raise ValueError(f"Unknown embedding backend: {backend!r}")


# 向后兼容别名
Embedder = LlamaCppEmbedder
