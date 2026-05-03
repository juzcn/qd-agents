"""
记忆存储层 — SQLite + sqlite-vec 读写

memories 表存储 QA 记录，memory_vec 虚拟表存储问题向量索引。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import sqlite_vec  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


class MemoryRecord:
    """记忆记录"""

    __slots__ = (
        "id", "session_id", "question", "answer",
        "tags", "source", "model", "token_count",
        "created_at", "updated_at",
    )

    def __init__(
        self,
        id: str,
        session_id: str,
        question: str,
        answer: str,
        tags: list[str] | None = None,
        source: str = "",
        model: str = "",
        token_count: int = 0,
        created_at: str = "",
        updated_at: str = "",
    ) -> None:
        self.id = id
        self.session_id = session_id
        self.question = question
        self.answer = answer
        self.tags = tags or []
        self.source = source
        self.model = model
        self.token_count = token_count
        self.created_at = created_at
        self.updated_at = updated_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "question": self.question,
            "answer": self.answer,
            "tags": self.tags,
            "source": self.source,
            "model": self.model,
            "token_count": self.token_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def format_display(self) -> str:
        """格式化显示：日期 + QA"""
        date = self.created_at[:10] if self.created_at else "?"
        return f"[{date}] Q: {self.question}\nA: {self.answer}"


class MemoryStore:
    """记忆存储 — SQLite + sqlite-vec"""

    def __init__(self, db_path: str | Path, vec_dim: int = 1024) -> None:
        self._db_path = Path(db_path)
        self._vec_dim = vec_dim
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = self._init_db()

    def _init_db(self) -> sqlite3.Connection:
        """初始化数据库"""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                source TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                token_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(
                id TEXT PRIMARY KEY,
                question_vec float[{self._vec_dim}]
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_session
            ON memories (session_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_created
            ON memories (created_at)
        """)

        conn.commit()
        return conn

    def save(
        self,
        question: str,
        answer: str,
        question_vec: bytes,
        session_id: str = "",
        tags: list[str] | None = None,
        source: str = "chat",
        model: str = "",
        token_count: int = 0,
    ) -> str:
        """保存一条 QA 记忆"""
        now = datetime.now().isoformat()
        record_id = str(uuid.uuid4())

        with self._conn:
            self._conn.execute("""
                INSERT INTO memories (
                    id, session_id, question, answer,
                    tags, source, model, token_count,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record_id,
                session_id,
                question,
                answer,
                json.dumps(tags or [], ensure_ascii=False),
                source,
                model,
                token_count,
                now,
                now,
            ))

            self._conn.execute("""
                INSERT INTO memory_vec (id, question_vec)
                VALUES (?, ?)
            """, (record_id, question_vec))

        logger.info("Saved memory: %s (session=%s)", record_id[:8], session_id[:8] if session_id else "?")
        return record_id

    def search_vec(
        self,
        query_vec: bytes,
        top_k: int = 5,
        exclude_session: str = "",
    ) -> list[MemoryRecord]:
        """向量召回，排除当前 session，按时间倒序"""
        # sqlite-vec KNN 查询：需要子查询方式
        # 先通过 vec0 做向量搜索，再 JOIN memories 表
        if exclude_session:
            query_sql = """
                SELECT m.id, m.session_id, m.question, m.answer,
                       m.tags, m.source, m.model, m.token_count,
                       m.created_at, m.updated_at, v.distance
                FROM (
                    SELECT id, distance
                    FROM memory_vec
                    WHERE question_vec MATCH ? AND k = ?
                ) v
                JOIN memories m ON m.id = v.id
                WHERE m.session_id != ?
                ORDER BY v.distance, m.created_at DESC
            """
            params: list[Any] = [query_vec, top_k * 2, exclude_session]
        else:
            query_sql = """
                SELECT m.id, m.session_id, m.question, m.answer,
                       m.tags, m.source, m.model, m.token_count,
                       m.created_at, m.updated_at, v.distance
                FROM (
                    SELECT id, distance
                    FROM memory_vec
                    WHERE question_vec MATCH ? AND k = ?
                ) v
                JOIN memories m ON m.id = v.id
                ORDER BY v.distance, m.created_at DESC
                LIMIT ?
            """
            params = [query_vec, top_k, top_k]

        rows = self._conn.execute(query_sql, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def search_keyword(
        self,
        query: str,
        top_k: int = 5,
        exclude_session: str = "",
    ) -> list[MemoryRecord]:
        """关键词召回，排除当前 session，按时间倒序"""
        keywords = query.lower().split()
        if not keywords:
            return []

        conditions = []
        params: list[Any] = []
        for kw in keywords:
            conditions.append("(LOWER(m.question) LIKE ? OR LOWER(m.answer) LIKE ?)")
            params.extend([f"%{kw}%", f"%{kw}%"])

        query_sql = f"""
            SELECT m.id, m.session_id, m.question, m.answer,
                   m.tags, m.source, m.model, m.token_count,
                   m.created_at, m.updated_at
            FROM memories m
            WHERE ({" OR ".join(conditions)})
        """

        if exclude_session:
            query_sql += " AND m.session_id != ?"
            params.append(exclude_session)

        query_sql += " ORDER BY m.created_at DESC LIMIT ?"
        params.append(top_k)

        rows = self._conn.execute(query_sql, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            session_id=row["session_id"],
            question=row["question"],
            answer=row["answer"],
            tags=json.loads(row["tags"]),
            source=row["source"],
            model=row["model"],
            token_count=row["token_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        return row[0]

    def close(self) -> None:
        self._conn.close()