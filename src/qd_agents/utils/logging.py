"""
日志配置
"""
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog


class ImmediateFlushFileHandler(logging.FileHandler):
    """立即刷新的文件处理器，方便在 VS Code 中实时查看日志"""

    def emit(self, record):
        """重写 emit 方法，确保每次写入后立即刷新到磁盘"""
        try:
            # 1. 格式化记录
            msg = self.format(record)

            # 2. 直接写入（不使用父类的 emit，避免任何缓冲）
            stream = self.stream
            if stream:
                stream.write(msg + self.terminator)

                # 3. 强制刷新 Python 缓冲区
                stream.flush()

                # 4. 强制操作系统将数据写入磁盘
                if hasattr(stream, 'fileno'):
                    import os
                    try:
                        os.fsync(stream.fileno())
                    except (OSError, AttributeError):
                        # 如果 fsync 失败，至少确保刷新了
                        pass

        except Exception:
            self.handleError(record)

    def flush(self):
        """重写 flush 方法，确保彻底刷新到磁盘"""
        if self.stream and hasattr(self.stream, 'flush'):
            self.stream.flush()
            # 强制同步到磁盘
            if hasattr(self.stream, 'fileno'):
                import os
                try:
                    os.fsync(self.stream.fileno())
                except (OSError, AttributeError):
                    pass


def generate_session_log_path(log_dir: Path, trace_id: Optional[str] = None) -> Path:
    """
    生成会话日志文件路径

    Args:
        log_dir: 日志目录
        trace_id: 可选的追踪 ID，自动生成 UUID

    Returns:
        日志文件路径
    """
    if trace_id is None:
        trace_id = str(uuid.uuid4())

    # 取 trace_id 前 8 位作为短标识
    short_id = trace_id[:8]

    # 生成文件名：YYYYMMDD_HHMMSS_shortid.log
    now = datetime.now()
    filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{short_id}.log"

    return log_dir / filename


def setup_logging(
    level: str = "INFO",
    log_format: str = "console",
    log_file: Optional[Path] = None,
) -> None:
    """
    配置结构化日志

    Args:
        level: 日志级别
        log_format: 日志格式 (console/json)
        log_file: 日志文件路径
    """
    # 配置标准库 logging
    handlers = []

    if log_file:
        # 确保目录存在
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = ImmediateFlushFileHandler(log_file, encoding='utf-8')
        handlers.append(file_handler)
    else:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        format="%(message)s",
        handlers=handlers,
        level=getattr(logging, level.upper()),
    )

    # 配置 structlog
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(
            structlog.dev.ConsoleRenderer(colors=True)
        )

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def setup_session_logging(
    log_dir: Path,
    level: str = "INFO",
    log_format: str = "json",
    trace_id: Optional[str] = None,
) -> tuple[Path, str]:
    """
    配置会话日志（仅输出到文件）

    Args:
        log_dir: 日志目录
        level: 日志级别
        log_format: 日志格式 (console/json)
        trace_id: 可选的追踪 ID

    Returns:
        (日志文件路径, trace_id)
    """
    if trace_id is None:
        trace_id = str(uuid.uuid4())

    log_file = generate_session_log_path(log_dir, trace_id)
    setup_logging(level=level, log_format=log_format, log_file=log_file)

    # 设置 trace_id 上下文
    structlog.contextvars.bind_contextvars(trace_id=trace_id)

    return log_file, trace_id


__all__ = [
    "ImmediateFlushFileHandler",
    "generate_session_log_path",
    "setup_logging",
    "setup_session_logging",
]
