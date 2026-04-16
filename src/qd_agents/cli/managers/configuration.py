"""
配置管理

负责配置加载、数据目录设置和日志配置。
"""

from pathlib import Path
from typing import Optional, Tuple, Any

from rich.console import Console

from qd_agents.config import Config, load_config
from qd_agents.utils.logging import setup_session_logging


def setup_configuration(
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> Config:
    """
    加载配置并设置日志

    Args:
        console: Rich 控制台对象，用于输出信息
        base_dir: 基础目录
        config_file: 配置文件路径

    Returns:
        config: 加载的配置对象
    """
    config = load_config(base_dir=base_dir, config_file=config_file)

    # 确保数据目录存在（用于日志等）
    if config.storage:
        config.storage.data_dir.mkdir(parents=True, exist_ok=True)

    # 配置会话日志（仅文件输出）
    log_level = config.observability.log_level if config.observability else "INFO"
    log_format = config.observability.log_format if config.observability else "json"
    log_dir = config.observability.log_session_dir if (config.observability and config.observability.log_session_dir) else Path(".")

    log_file, trace_id = setup_session_logging(
        log_dir=log_dir,
        level=log_level,
        log_format=log_format,
    )

    console.print(f"[dim]日志文件: {log_file}[/]\n", style="dim")

    return config