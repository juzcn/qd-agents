"""
嵌入引擎 — 基于 llama-cpp-python 加载 GGUF 模型生成向量

懒加载：首次调用 embed() 时才加载模型，避免不使用记忆功能时占用内存。
"""
from __future__ import annotations

import logging
import struct
from pathlib import Path

logger = logging.getLogger(__name__)


class Embedder:
    """GGUF 模型嵌入引擎"""

    def __init__(self, model_path: Path, vec_dim: int = 1024) -> None:
        self._model_path = model_path
        self._vec_dim = vec_dim
        self._model: object | None = None

    def _ensure_model(self) -> None:
        """懒加载模型"""
        if self._model is not None:
            return

        if not self._model_path.exists():
            raise FileNotFoundError(f"Embedding model not found: {self._model_path}")

        from llama_cpp import Llama

        logger.info("Loading embedding model: %s", self._model_path)
        self._model = Llama(
            model_path=str(self._model_path),
            embedding=True,
            n_ctx=512,
            verbose=False,
        )
        logger.info("Embedding model loaded (vec_dim=%d)", self._vec_dim)

    def embed(self, text: str) -> bytes:
        """生成文本的嵌入向量，返回 float32 bytes（可直接写入 sqlite-vec）"""
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
        """生成文本的嵌入向量，返回 float 列表"""
        self._ensure_model()

        result = self._model.create_embedding([text])  # type: ignore[union-attr]
        return result["data"][0]["embedding"]
