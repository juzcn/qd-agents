"""
执行引擎 - 确定性执行
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """安全异常"""
    pass


class ExecutionStatus(str, Enum):
    """执行状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionStep(BaseModel):
    """执行步骤"""
    step: int
    tool_id: str | None = None
    tool_name: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    error: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_ms: int = 0
    status: ExecutionStatus = ExecutionStatus.PENDING


class ExecutionResult(BaseModel):
    """执行结果"""
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str | None = None
    user_input: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    status: ExecutionStatus = ExecutionStatus.PENDING
    steps: list[ExecutionStep] = Field(default_factory=list)
    final_output: Any = None
    error: str | None = None
    total_duration_ms: int = 0


class ExecutionEngine:
    """
    执行引擎

    负责确定性执行工具调用和生成的代码
    """

    def __init__(
        self,
        allowed_modules: list[str] | None = None,
        blocked_builtins: list[str] | None = None,
        timeout: int = 30000,
    ):
        """
        初始化执行引擎

        Args:
            allowed_modules: 允许导入的模块列表
            blocked_builtins: 禁用的内置函数列表
            timeout: 执行超时时间（毫秒）
        """
        self._tools: dict[str, Any] = {}
        self.allowed_modules = allowed_modules or ["math", "datetime", "json", "re", "collections", "itertools"]
        self.blocked_builtins = blocked_builtins or ["eval", "exec", "__import__", "open"]
        self.timeout = timeout

    def register_tool_func(self, tool_name: str, func: Any) -> None:
        """
        注册工具函数

        Args:
            tool_name: 工具名称
            func: 工具函数
        """
        self._tools[tool_name] = func

    async def execute_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        trace_id: str | None = None,
    ) -> tuple[Any, ExecutionStep]:
        """
        执行单个工具

        Args:
            tool_name: 工具名称
            tool_input: 工具输入
            trace_id: 追踪 ID

        Returns:
            (输出, 执行步骤)
        """
        step = ExecutionStep(
            step=1,
            tool_name=tool_name,
            input=tool_input,
            status=ExecutionStatus.RUNNING,
            start_time=datetime.utcnow(),
        )

        start_time = time.perf_counter()

        try:
            logger.info("Executing tool: %s", tool_name)

            if tool_name not in self._tools:
                raise ValueError(f"Tool not registered: {tool_name}")

            func = self._tools[tool_name]

            # 执行工具
            if hasattr(func, "__call__"):
                if hasattr(func, "__code__") and func.__code__.co_flags & 0x80:  # async
                    output = await func(**tool_input)
                else:
                    output = func(**tool_input)
            else:
                output = func

            step.output = output
            step.status = ExecutionStatus.COMPLETED
            logger.info("Tool %s executed successfully", tool_name)

        except Exception as e:
            logger.exception("Tool %s failed", tool_name)
            step.error = str(e)
            step.status = ExecutionStatus.FAILED
            raise

        finally:
            end_time = time.perf_counter()
            step.end_time = datetime.utcnow()
            step.duration_ms = int((end_time - start_time) * 1000)

        return step.output, step

    async def execute_code(
        self,
        code: str,
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> ExecutionResult:
        """
        执行生成的 Python 代码

        Args:
            code: Python 代码字符串
            session_id: 会话 ID
            trace_id: 追踪 ID

        Returns:
            执行结果
        """
        result = ExecutionResult(
            trace_id=trace_id or str(uuid.uuid4()),
            session_id=session_id,
            status=ExecutionStatus.RUNNING,
        )

        logger.info("Executing code (trace_id: %s)", result.trace_id)
        start_time = time.perf_counter()

        try:
            # 安全检查：检查代码中是否包含危险的关键字
            dangerous_keywords = ["eval", "exec", "__import__", "open", "subprocess", "os.system", "import os", "import sys"]
            for keyword in dangerous_keywords:
                if keyword in code:
                    raise SecurityError(f"Code contains dangerous keyword: {keyword}")

            # 准备安全的执行环境
            exec_globals: dict[str, Any] = {
                "__builtins__": self._create_restricted_builtins(),
            }

            # 自定义 __import__ 函数，限制导入
            def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
                # 检查是否在允许的模块列表中
                if name in self.allowed_modules:
                    # 如果是允许的模块，使用原始导入
                    import builtins
                    return builtins.__import__(name, globals, locals, fromlist, level)
                else:
                    raise ImportError(f"Import of module '{name}' is not allowed")

            # 将自定义的 __import__ 添加到内置函数
            exec_globals["__builtins__"]["__import__"] = safe_import

            # 注入已注册工具
            exec_globals.update(self._tools)

            # 执行代码
            exec_locals: dict[str, Any] = {}
            exec(code, exec_globals, exec_locals)

            # 获取返回值
            result.final_output = exec_locals.get("return") or exec_locals.get("result")
            result.status = ExecutionStatus.COMPLETED

            logger.info("Code executed successfully")

        except Exception as e:
            logger.exception("Code execution failed")
            result.error = str(e)
            result.status = ExecutionStatus.FAILED
            raise

        except ImportError as e:
            logger.exception("Import security violation: %s", e)
            result.error = f"Import security violation: {e}"
            result.status = ExecutionStatus.FAILED
            raise SecurityError(f"Security violation: {e}") from e
        except SecurityError as e:
            logger.exception("Security violation: %s", e)
            result.error = str(e)
            result.status = ExecutionStatus.FAILED
            raise
        except Exception as e:
            logger.exception("Code execution failed")
            result.error = str(e)
            result.status = ExecutionStatus.FAILED
            raise

        finally:
            end_time = time.perf_counter()
            result.total_duration_ms = int((end_time - start_time) * 1000)

        return result

    def _create_restricted_builtins(self) -> dict[str, Any]:
        """创建受限制的内置函数字典"""
        import builtins

        # 创建内置函数的副本
        restricted_builtins = {}
        for name in dir(builtins):
            if not name.startswith('_') or name == '__import__':
                continue
            # 跳过被禁用的内置函数
            if name in self.blocked_builtins:
                continue
            try:
                restricted_builtins[name] = getattr(builtins, name)
            except AttributeError:
                pass

        # 确保有基本的内置函数
        essential_builtins = [
            'print', 'len', 'str', 'int', 'float', 'bool', 'list', 'dict',
            'tuple', 'set', 'range', 'enumerate', 'zip', 'isinstance', 'type',
            'abs', 'round', 'min', 'max', 'sum', 'sorted', 'reversed'
        ]

        for name in essential_builtins:
            if name not in restricted_builtins:
                try:
                    restricted_builtins[name] = getattr(builtins, name)
                except AttributeError:
                    pass

        return restricted_builtins
