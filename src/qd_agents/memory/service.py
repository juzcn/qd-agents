"""
MemoryService — 长期记忆统一入口

对外提供 save() 和 recall() 两个核心接口。
LLM 通过 recall_memory 工具调用 recall()，QA 完成后自动调用 save()。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..config.models import MemoryConfig
from .embedder import BaseEmbedder, create_embedder
from .recall import RecallService
from .store import MemoryRecord, MemoryStore

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class MemoryService:
    """长期记忆服务 — 统一入口"""

    def __init__(self, config: MemoryConfig) -> None:
        self._config = config

        # 嵌入引擎
        model_path = config.model_path
        if model_path is None or model_path.is_dir():
            model_path = (model_path or Path(".")) / config.embedding_model
        self._embedder: BaseEmbedder = create_embedder(
            backend=config.embedding_backend,
            model_path=model_path,
            model_name=config.embedding_model,
            vec_dim=config.vec_dim,
            hf_token=config.hf_token,
        )

        # 存储层
        self._store = MemoryStore(config.db_path, vec_dim=config.vec_dim)

        # 召回服务
        self._recall = RecallService(
            store=self._store,
            top_k=config.top_k,
            similarity_threshold=config.similarity_threshold,
            hybrid_search=config.hybrid_search,
            max_recall_results=config.max_recall_results,
        )

    def save(
        self,
        question: str,
        answer: str,
        session_id: str = "",
        tags: list[str] | None = None,
        source: str = "evolve",
        model: str = "",
        token_count: int = 0,
    ) -> str:
        """保存一条 QA 记忆"""
        question_vec = self._embedder.embed(question)
        return self._store.save(
            question=question,
            answer=answer,
            question_vec=question_vec,
            session_id=session_id,
            tags=tags,
            source=source,
            model=model,
            token_count=token_count,
        )

    def recall(
        self,
        query: str,
        exclude_session: str = "",
    ) -> list[MemoryRecord]:
        """召回相关历史记忆（排除当前 session）"""
        query_vec = self._embedder.embed(query)
        results = self._recall.search(
            query_vec=query_vec,
            query_text=query,
            exclude_session=exclude_session,
        )

        if not results:
            return []

        # 截断到 max_recall_chars
        total_chars = 0
        filtered: list[MemoryRecord] = []
        for record in results:
            display = record.format_display()
            if total_chars + len(display) > self._config.max_recall_chars:
                break
            filtered.append(record)
            total_chars += len(display)

        return filtered

    def format_recall_result(self, records: list[MemoryRecord]) -> str:
        """格式化召回结果为 LLM 可读文本"""
        if not records:
            return "未找到相关的历史记忆。"

        parts = [record.format_display() for record in records]
        return "找到以下相关历史记忆：\n\n" + "\n\n".join(parts)

    def count(self) -> int:
        return self._store.count()

    def close(self) -> None:
        self._embedder.close()
        self._store.close()
