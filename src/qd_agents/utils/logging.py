"""
日志配置
"""
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


class ImmediateFlushFileHandler(logging.FileHandler):
    """立即刷新的文件处理器，方便在 VS Code 中实时查看日志"""

    def __init__(self, filename, mode='a', encoding=None, delay=False, immediate_flush=True):
        """
        初始化文件处理器

        Args:
            filename: 日志文件名
            mode: 文件模式
            encoding: 文件编码
            delay: 是否延迟打开文件
            immediate_flush: 是否立即刷新到磁盘（包括fsync），默认为True
        """
        super().__init__(filename, mode, encoding, delay)
        self.immediate_flush = immediate_flush

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

                # 4. 强制操作系统将数据写入磁盘（如果启用立即刷新）
                if self.immediate_flush and hasattr(stream, 'fileno'):
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
            # 强制同步到磁盘（如果启用立即刷新）
            if self.immediate_flush and hasattr(self.stream, 'fileno'):
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
    log_file: Optional[Path] = None,
    log_external_api: bool = False,
    log_immediate_flush: bool = True,
) -> None:
    """
    配置标准库日志

    Args:
        level: 日志级别
        log_file: 日志文件路径
        log_external_api: 是否记录外部API调用日志
        log_immediate_flush: 是否立即刷新日志到磁盘，默认为True
    """
    # 配置标准库 logging
    handlers: list[logging.Handler] = []

    # 定义日志格式
    formatter = logging.Formatter(
        fmt='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    if log_file:
        # 确保目录存在
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = ImmediateFlushFileHandler(log_file, encoding='utf-8', immediate_flush=log_immediate_flush)
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    else:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        handlers.append(console_handler)

    # 强制替换 root logger 的 handlers（basicConfig 在已有 handler 时不生效）
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)
    root.setLevel(getattr(logging, level.upper()))

    # 控制外部HTTP客户端日志级别
    if not log_external_api:
        # 设置httpx和httpcore日志级别为WARNING，减少详细的HTTP请求日志
        httpx_logger = logging.getLogger("httpx")
        httpx_logger.setLevel(logging.WARNING)
        httpcore_logger = logging.getLogger("httpcore")
        httpcore_logger.setLevel(logging.WARNING)


def setup_session_logging(
    log_dir: Path,
    level: str = "INFO",
    trace_id: Optional[str] = None,
    log_external_api: bool = False,
    log_immediate_flush: bool = True,
) -> tuple[Path, str]:
    """
    配置会话日志（仅输出到文件）

    Args:
        log_dir: 日志目录
        level: 日志级别
        trace_id: 可选的追踪 ID
        log_external_api: 是否记录外部API调用日志
        log_immediate_flush: 是否立即刷新日志到磁盘，默认为True

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
        log_immediate_flush=log_immediate_flush
    )

    # trace_id 已包含在日志文件名中，无需额外上下文

    return log_file, trace_id


__all__ = [
    "ImmediateFlushFileHandler",
    "generate_session_log_path",
    "setup_logging",
    "setup_session_logging",
]
