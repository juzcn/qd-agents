"""
配置管理模块
"""
from .loader import Config, load_config, save_config, AgentMode

# 全局配置实例
_config: Config | None = None


def get_config() -> Config | None:
    """获取全局配置实例"""
    return _config


def set_config(config: Config) -> None:
    """设置全局配置实例"""
    global _config
    _config = config


__all__ = ["Config", "load_config", "save_config", "get_config", "set_config", "AgentMode"]
