"""
日志配置

提供会话日志、日志轮转、trace_id 注入等功能。
"""
import logging
import logging.handlers
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


class ImmediateFlushFileHandler(logging.FileHandler):
    """立即刷新的文件处理器，方便在 VS Code 中实时查看日志"""

    def __init__(self, filename, mode='a', encoding=None, delay=False, immediate_flush=True):
        super().__init__(filename, mode, encoding, delay)
        self.immediate_flush = immediate_flush

    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            if stream:
                stream.write(msg + self.terminator)
                stream.flush()
                if self.immediate_flush and hasattr(stream, 'fileno'):
                    import os
                    try:
                        os.fsync(self.stream.fileno())
                    except (OSError, AttributeError):
                        pass
        except Exception:
            self.handleError(record)

    def flush(self):
        if self.stream and hasattr(self.stream, 'flush'):
            self.stream.flush()
            if self.immediate_flush and hasattr(self.stream, 'fileno'):
                import os
                try:
                    os.fsync(self.stream.fileno())
                except (OSError, AttributeError):
                    pass


class ImmediateFlushRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """支持轮转的立即刷新文件处理器"""

    def __init__(self, filename, maxBytes=10*1024*1024, backupCount=5,
                 encoding=None, delay=False, immediate_flush=True):
        super().__init__(filename, maxBytes=maxBytes, backupCount=backupCount,
                         encoding=encoding, delay=delay)
        self.immediate_flush = immediate_flush

    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            if stream:
                stream.write(msg + self.terminator)
                stream.flush()
                if self.immediate_flush and hasattr(stream, 'fileno'):
                    import os
                    try:
                        os.fsync(self.stream.fileno())
                    except (OSError, AttributeError):
                        pass
        except Exception:
            self.handleError(record)

    def flush(self):
        if self.stream and hasattr(self.stream, 'flush'):
            self.stream.flush()
            if self.immediate_flush and hasattr(self.stream, 'fileno'):
                import os
                try:
                    os.fsync(self.stream.fileno())
                except (OSError, AttributeError):
                    pass


class TraceIdFilter(logging.Filter):
    """注入 trace_id 到日志记录中"""

    def __init__(self, trace_id: str = ""):
        super().__init__()
        self.trace_id = trace_id

    def filter(self, record):
        record.trace_id = self.trace_id
        return True


def generate_session_log_path(log_dir: Path, trace_id: Optional[str] = None) -> Path:
    """生成会话日志文件路径"""
    if trace_id is None:
        trace_id = str(uuid.uuid4())
    short_id = trace_id[:8]
    now = datetime.now()
    filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{short_id}.log"
    return log_dir / filename


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    log_external_api: bool = False,
    log_immediate_flush: bool = True,
    trace_id: Optional[str] = None,
    rotating: bool = False,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """配置标准库日志

    Args:
        level: 日志级别
        log_file: 日志文件路径
        log_external_api: 是否记录外部API调用日志
        log_immediate_flush: 是否立即刷新日志到磁盘
        trace_id: 会话追踪 ID，注入到每条日志中
        rotating: 是否启用日志轮转（默认 False，会话模式用单文件）
        max_bytes: 轮转文件最大字节数（默认 10MB）
        backup_count: 轮转保留文件数（默认 5）
    """
    handlers: list[logging.Handler] = []

    # 日志格式：包含 trace_id
    fmt = '%(asctime)s [%(levelname)s] %(trace_id)s %(name)s: %(message)s'
    formatter = logging.Formatter(fmt=fmt, datefmt='%Y-%m-%d %H:%M:%S')

    # trace_id 过滤器
    tid = trace_id or ""
    trace_filter = TraceIdFilter(tid)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler: logging.Handler
        if rotating:
            file_handler = ImmediateFlushRotatingFileHandler(
                log_file, maxBytes=max_bytes, backupCount=backup_count,
                encoding='utf-8', immediate_flush=log_immediate_flush,
            )
        else:
            file_handler = ImmediateFlushFileHandler(
                log_file, encoding='utf-8', immediate_flush=log_immediate_flush,
            )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(trace_filter)
        handlers.append(file_handler)
    else:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(trace_filter)
        handlers.append(console_handler)

    # 替换 root logger handlers
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)
    root.setLevel(getattr(logging, level.upper()))

    # 控制外部HTTP客户端日志级别
    if not log_external_api:
        for name in ("httpx", "httpcore", "urllib3"):
            logging.getLogger(name).setLevel(logging.WARNING)


def setup_session_logging(
    log_dir: Path,
    level: str = "INFO",
    trace_id: Optional[str] = None,
    log_external_api: bool = False,
    log_immediate_flush: bool = True,
) -> tuple[Path, str]:
    """配置会话日志（仅输出到文件）

    Returns:
        (日志文件路径, trace_id)
    """
    if trace_id is None:
        trace_id = str(uuid.uuid4())

    log_file = generate_session_log_path(log_dir, trace_id)
    setup_logging(
        level=level,
        log_file=log_file,
        log_external_api=log_external_api,
        log_immediate_flush=log_immediate_flush,
        trace_id=trace_id,
    )

    return log_file, trace_id


def setup_persistent_logging(
    log_file: Path,
    level: str = "INFO",
    log_external_api: bool = False,
    log_immediate_flush: bool = True,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> Path:
    """配置持久日志（带轮转，适用于 CLI 子命令等非会话场景）

    Args:
        log_file: 日志文件路径（如 data/logs/qd-agents.log）
        level: 日志级别
        max_bytes: 单个日志文件最大字节数
        backup_count: 保留的历史日志文件数

    Returns:
        日志文件路径
    """
    setup_logging(
        level=level,
        log_file=log_file,
        log_external_api=log_external_api,
        log_immediate_flush=log_immediate_flush,
        rotating=True,
        max_bytes=max_bytes,
        backup_count=backup_count,
    )
    return log_file


__all__ = [
    "ImmediateFlushFileHandler",
    "ImmediateFlushRotatingFileHandler",
    "TraceIdFilter",
    "generate_session_log_path",
    "setup_logging",
    "setup_session_logging",
    "setup_persistent_logging",
]