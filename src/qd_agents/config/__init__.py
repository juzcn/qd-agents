"""
配置管理模块
"""
from .models import (
    Config,
    RuntimeConfig,
    LLMProviderConfig,
    SearchProviderConfig,
    BaiduSearchConfig,
    SearchConfig,
    LLMConfig,
    ToolRegistryConfig,
    ExecutionConfig,
    ToolCredentialConfig,
    ToolsCredentialsConfig,
    PromptsConfig,
    StorageConfig,
    ObservabilityConfig,
    SystemConfig,
)
from .loader import load_config, load_runtime_config, save_config, save_runtime_config

# 全局配置实例
_config: Config | None = None


def get_config() -> Config | None:
    """获取全局配置实例"""
    return _config


def set_config(config: Config) -> None:
    """设置全局配置实例"""
    global _config
    _config = config


__all__ = [
    # 主配置类
    "Config",
    "RuntimeConfig",
    # 加载/保存
    "load_config",
    "load_runtime_config",
    "save_config",
    "save_runtime_config",
    # 全局配置
    "get_config",
    "set_config",
    # 子配置模型
    "LLMProviderConfig",
    "SearchProviderConfig",
    "BaiduSearchConfig",
    "SearchConfig",
    "LLMConfig",
    "ToolRegistryConfig",
    "ExecutionConfig",
    "ToolCredentialConfig",
    "ToolsCredentialsConfig",
    "PromptsConfig",
    "StorageConfig",
    "ObservabilityConfig",
    "SystemConfig",
]