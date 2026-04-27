"""
召回服务 — 向量 + 关键词混合检索，RRF 融合

结果按时间倒序（越新越优先），排除当前 session。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import MemoryRecord, MemoryStore

logger = logging.getLogger(__name__)


class RecallService:
    """混合召回服务"""

    def __init__(
        self,
        store: MemoryStore,
        top_k: int = 5,
        similarity_threshold: float = 0.7,
        hybrid_search: bool = True,
        max_recall_results: int = 5,
    ) -> None:
        self._store = store
        self._top_k = top_k
        self._similarity_threshold = similarity_threshold
        self._hybrid_search = hybrid_search
        self._max_recall_results = max_recall_results

    def search(
        self,
        query_vec: bytes,
        query_text: str,
        exclude_session: str = "",
    ) -> list[MemoryRecord]:
        """混合召回：向量 + 关键词，RRF 融合，按时间倒序"""
        # 向量召回
        vec_results = self._store.search_vec(
            query_vec,
            top_k=self._top_k,
            exclude_session=exclude_session,
        )

        if not self._hybrid_search:
            return vec_results[:self._max_recall_results]

        # 关键词召回
        kw_results = self._store.search_keyword(
            query_text,
            top_k=self._top_k,
            exclude_session=exclude_session,
        )

        # RRF 融合
        merged = self._rrf_merge(vec_results, kw_results)
        return merged[:self._max_recall_results]

    def _rrf_merge(
        self,
        vec_results: list[MemoryRecord],
        kw_results: list[MemoryRecord],
        k: int = 60,
    ) -> list[MemoryRecord]:
        """Reciprocal Rank Fusion 融合两路结果，按融合分数降序"""
        scores: dict[str, float] = {}

        for rank, record in enumerate(vec_results):
            scores[record.id] = scores.get(record.id, 0.0) + 1.0 / (k + rank + 1)

        for rank, record in enumerate(kw_results):
            scores[record.id] = scores.get(record.id, 0.0) + 1.0 / (k + rank + 1)

        # 按分数降序，同分按时间倒序
        id_to_record: dict[str, MemoryRecord] = {}
        for record in vec_results + kw_results:
            if record.id not in id_to_record:
                id_to_record[record.id] = record

        sorted_ids = sorted(
            scores.keys(),
            key=lambda rid: (-scores[rid], id_to_record[rid].created_at),
            reverse=False,
        )
        # 二次排序：分数降序为主，时间倒序为辅
        sorted_ids = sorted(
            scores.keys(),
            key=lambda rid: (-scores[rid], -(self._parse_timestamp(id_to_record[rid].created_at))),
        )

        return [id_to_record[rid] for rid in sorted_ids]

    @staticmethod
    def _parse_timestamp(iso_str: str) -> float:
        """ISO 时间字符串转可比较的数值"""
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(iso_str)
            return dt.timestamp()
        except (ValueError, TypeError):
            return 0.0
