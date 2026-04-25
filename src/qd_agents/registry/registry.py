"""
工具注册表

管理工具的注册、查询和生命周期。
Tool 数据模型定义在 models.tool 中，本模块专注于注册表逻辑（SQLite 存储）。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ..models.tool import (
    Tool,
    ToolExecutionConfig,
    ToolExecutionType,
    ToolMetadata,
    ToolVersionStatus,
)

logger = logging.getLogger(__name__)


class ToolRegistry:
    """工具注册表 - SQLite 存储"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else Path("data/tools.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = self._init_db()

    def _init_db(self) -> sqlite3.Connection:
        """初始化数据库并返回连接"""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS tools (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                parameters_json TEXT NOT NULL,
                execution_json TEXT NOT NULL,
                security_tags TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                dependencies_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tools_category
            ON tools (json_extract(metadata_json, '$.category'))
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tools_version_status
            ON tools (json_extract(metadata_json, '$.version_status'))
        """)

        conn.commit()
        return conn

    def register(self, tool: Tool) -> str:
        """注册工具"""
        logger.info("Registering tool: %s", tool.id)

        with self._conn:
            self._conn.execute("""
                INSERT OR REPLACE INTO tools (
                    id, name, description, parameters_json, execution_json,
                    security_tags, metadata_json, dependencies_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tool.id,
                tool.name,
                tool.description,
                json.dumps(tool.parameters, ensure_ascii=False),
                tool.execution.model_dump_json(ensure_ascii=False),
                json.dumps(tool.security, ensure_ascii=False),
                tool.metadata.model_dump_json(ensure_ascii=False),
                json.dumps(tool.dependencies, ensure_ascii=False),
                tool.created_at.isoformat(),
                tool.updated_at.isoformat(),
            ))

        return tool.id

    def get(self, tool_id: str) -> Tool | None:
        """获取工具"""
        row = self._conn.execute(
            "SELECT * FROM tools WHERE id = ?", (tool_id,)
        ).fetchone()
        if row:
            return self._row_to_tool(row)
        return None

    def get_by_name(self, tool_name: str) -> Tool | None:
        """通过名称获取工具"""
        row = self._conn.execute(
            "SELECT * FROM tools WHERE name = ?", (tool_name,)
        ).fetchone()
        if row:
            return self._row_to_tool(row)
        return None

    def delete(self, tool_id: str) -> bool:
        """删除工具"""
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM tools WHERE id = ?", (tool_id,)
            )
            return cursor.rowcount > 0

    def clear_all(self) -> bool:
        """清空所有工具"""
        with self._conn:
            cursor = self._conn.execute("DELETE FROM tools")
            return cursor.rowcount > 0

    def list_all(
        self,
        category: str | None = None,
        version_status: ToolVersionStatus | None = None,
        limit: int | None = None,
    ) -> list[Tool]:
        """列出所有工具"""
        query = "SELECT * FROM tools WHERE 1=1"
        params: list[Any] = []

        if category:
            query += " AND json_extract(metadata_json, '$.category') = ?"
            params.append(category)

        if version_status:
            query += " AND json_extract(metadata_json, '$.version_status') = ?"
            params.append(version_status.value)

        query += " ORDER BY created_at DESC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_tool(row) for row in rows]

    def search(
        self,
        query: str,
        top_k: int = 10,
        similarity_threshold: float = 0.0,
    ) -> list[Tool]:
        """搜索工具（关键词搜索）"""
        keywords = query.lower().split()
        all_tools = self.list_all(version_status=ToolVersionStatus.ACTIVE)

        scored_tools: list[tuple[float, Tool]] = []
        for tool in all_tools:
            search_text = tool.get_search_text().lower()
            score = sum(1 for kw in keywords if kw in search_text)
            if score > 0:
                scored_tools.append((score, tool))

        scored_tools.sort(key=lambda x: x[0], reverse=True)
        return [tool for _, tool in scored_tools[:top_k]]

    def _row_to_tool(self, row: sqlite3.Row) -> Tool:
        """将数据库行转换为 Tool 对象"""
        return Tool(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            parameters=json.loads(row["parameters_json"]),
            execution=ToolExecutionConfig.model_validate_json(row["execution_json"]),
            security=json.loads(row["security_tags"]),
            metadata=ToolMetadata.model_validate_json(row["metadata_json"]),
            dependencies=json.loads(row["dependencies_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def __len__(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM tools").fetchone()
        return row[0]

    def __contains__(self, tool_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM tools WHERE id = ?", (tool_id,)
        ).fetchone()
        return row is not None

    def __iter__(self):
        return iter(self.list_all())

    def close(self) -> None:
        """关闭数据库连接"""
        self._conn.close()
