"""
Tool Registry - 工具注册中心
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


logger = logging.getLogger(__name__)


class ToolVersionStatus(str, Enum):
    """工具版本状态"""
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class ToolExecutionType(str, Enum):
    """工具执行类型"""
    FUNCTION = "function"
    CLI = "cli"
    HTTP = "http"
    MCP = "mcp"
    BASH = "bash"
    SKILL = "skill"


class ToolExecutionConfig(BaseModel):
    """工具执行配置"""
    type: ToolExecutionType
    endpoint: str | None = None
    method: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    timeout: int = 30
    module: str | None = None
    function: str | None = None
    server: str | None = None
    tool: str | None = None
    transport: str = "stdio"  # MCP传输模式：stdio, sse, streamable-http
    shell_command: str | None = None  # bash工具：完整的shell命令字符串
    shell: str = "bash"  # 使用的shell，默认为bash


class ToolMetadata(BaseModel):
    """工具元数据"""
    category: str = "utilities"
    tags: list[str] = Field(default_factory=list)
    version: str = "1.0.0"
    version_status: ToolVersionStatus = ToolVersionStatus.ACTIVE
    changelog: str = ""
    deprecated_since: str | None = None
    retirement_date: str | None = None
    replaces: str | None = None
    replaced_by: str | None = None


class Tool(BaseModel):
    """工具定义"""
    id: str
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    execution: ToolExecutionConfig
    security: list[str] = Field(default_factory=list)
    metadata: ToolMetadata = Field(default_factory=ToolMetadata)
    dependencies: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, v):
        if not v:
            return {
                "type": "object",
                "properties": {},
                "required": [],
            }
        return v

    def to_openai_function(self) -> dict[str, Any]:
        """转换为 OpenAI Function Calling 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def get_search_text(self) -> str:
        """获取用于检索的文本"""
        parts = [
            self.name,
            self.description,
            self.metadata.category,
            " ".join(self.metadata.tags),
        ]
        return " ".join(filter(None, parts))


class ToolRegistry:
    """
    工具注册中心

    使用 SQLite 存储工具定义
    """

    def __init__(self, db_path: Path | str):
        """
        初始化 Tool Registry

        Args:
            db_path: SQLite 数据库文件路径
        """
        self.db_path = Path(db_path)
        self._ensure_data_dir()
        self._init_database()

    def _ensure_data_dir(self) -> None:
        """确保数据目录存在"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_database(self) -> None:
        """初始化数据库表"""
        with self._get_connection() as conn:
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

    def register(self, tool: Tool) -> str:
        """
        注册工具

        Args:
            tool: 工具定义

        Returns:
            工具 ID
        """
        logger.info("Registering tool: %s", tool.id)

        with self._get_connection() as conn:
            conn.execute("""
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
            conn.commit()

        return tool.id

    def get(self, tool_id: str) -> Tool | None:
        """
        获取工具

        Args:
            tool_id: 工具 ID

        Returns:
            工具定义，不存在返回 None
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM tools WHERE id = ?",
                (tool_id,)
            ).fetchone()

            if row:
                return self._row_to_tool(row)

        return None

    def get_by_name(self, tool_name: str) -> Tool | None:
        """
        通过工具名称获取工具

        Args:
            tool_name: 工具名称

        Returns:
            工具定义，不存在返回 None
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM tools WHERE name = ?",
                (tool_name,)
            ).fetchone()

            if row:
                return self._row_to_tool(row)

        return None

    def delete(self, tool_id: str) -> bool:
        """
        删除工具

        Args:
            tool_id: 工具 ID

        Returns:
            是否删除成功
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM tools WHERE id = ?",
                (tool_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def clear_all(self) -> bool:
        """
        清空所有工具

        Returns:
            是否清空成功
        """
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM tools")
            conn.commit()
            return cursor.rowcount > 0

    def list_all(
        self,
        category: str | None = None,
        version_status: ToolVersionStatus | None = None,
        limit: int | None = None,
    ) -> list[Tool]:
        """
        列出所有工具

        Args:
            category: 按类别筛选
            version_status: 按版本状态筛选
            limit: 返回数量限制

        Returns:
            工具列表
        """
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

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_tool(row) for row in rows]

    def search(
        self,
        query: str,
        top_k: int = 10,
        similarity_threshold: float = 0.0,
    ) -> list[Tool]:
        """
        搜索工具（关键词搜索，暂不使用向量检索）

        Args:
            query: 搜索关键词
            top_k: 返回结果数量
            similarity_threshold: 相似度阈值

        Returns:
            工具列表
        """
        # 简单关键词搜索实现
        keywords = query.lower().split()
        all_tools = self.list_all(version_status=ToolVersionStatus.ACTIVE)

        scored_tools: list[tuple[float, Tool]] = []

        for tool in all_tools:
            search_text = tool.get_search_text().lower()
            score = sum(1 for kw in keywords if kw in search_text)
            if score > 0:
                scored_tools.append((score, tool))

        # 按分数排序
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
