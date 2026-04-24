"""
配置加载器 - 支持 JSON 配置文件
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from typing_extensions import Self
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


from enum import Enum



class LLMProviderConfig(BaseModel):
    """LLM 提供商配置"""
    api_key: str
    base_url: str = "https://integrate.api.nvidia.com/v1"
    models: list[str] = Field(default_factory=list)
    timeout: int = 120000
    max_retries: int = 3
    auto_discover: bool = True


class SearchProviderConfig(BaseModel):
    """搜索提供商配置"""
    api_key: str = ""


class BaiduSearchConfig(BaseModel):
    """百度搜索配置"""
    api_key_1: str = ""
    api_key_2: str = ""


class SearchConfig(BaseModel):
    """搜索配置"""
    serper: SearchProviderConfig = Field(default_factory=SearchProviderConfig)
    tavily: SearchProviderConfig = Field(default_factory=SearchProviderConfig)
    baidu: BaiduSearchConfig = Field(default_factory=BaiduSearchConfig)


class LLMConfig(BaseModel):
    """LLM 配置"""
    default_provider: str = "nvidia"
    default_model: str | None = None
    providers: dict[str, LLMProviderConfig] = Field(default_factory=dict)
    mode: str = "tool-use"  # 向后兼容，实际使用 default_agent
    default_agent: str = "tool-use"
    tool_threshold: int = 50


class ToolRegistryConfig(BaseModel):
    """Tool Registry 配置"""
    db_path: Path
    sqlite_vec_enabled: bool = True
    embedding_model: str = "hf_KimChen_bge-m3-q4_k_m.gguf"
    model_path: Path | None = None
    top_k: int = 10
    similarity_threshold: float = 0.7
    hybrid_search: bool = True


class ExecutionConfig(BaseModel):
    """执行引擎配置"""
    default_timeout: int = 30000
    per_tool_timeout: dict[str, int] = Field(default_factory=dict)
    max_attempts: int = 3
    backoff_strategy: str = "exponential_with_jitter"
    initial_delay_ms: int = 1000
    max_delay_ms: int = 30000
    circuit_breaker_enabled: bool = True
    circuit_breaker_error_rate: float = 0.5
    circuit_breaker_min_requests: int = 10
    circuit_breaker_half_open_timeout: int = 30000
    code_exec_allowed_modules: list[str] = Field(
        default_factory=lambda: ["math", "datetime", "json", "re", "collections", "itertools"]
    )
    code_exec_blocked_builtins: list[str] = Field(
        default_factory=lambda: ["eval", "exec", "__import__", "open"]
    )
    code_exec_timeout: int = 30000


class ToolCredentialConfig(BaseModel):
    """单个外部工具的凭证配置"""
    api_key: str = ""


class ToolsCredentialsConfig(BaseModel):
    """外部工具凭证配置（CLI/MCP/Skill 等工具的 API key）"""
    # 每个工具一个子配置，key 为工具名（如 baidu_search）
    tools: dict[str, ToolCredentialConfig] = Field(default_factory=dict)

    def get_api_key(self, tool_name: str) -> str | None:
        """获取指定工具的 API key"""
        cred = self.tools.get(tool_name)
        if cred and cred.api_key:
            return cred.api_key
        return None

    def set_api_key(self, tool_name: str, api_key: str) -> None:
        """设置指定工具的 API key"""
        if tool_name in self.tools:
            self.tools[tool_name].api_key = api_key
        else:
            self.tools[tool_name] = ToolCredentialConfig(api_key=api_key)


class PromptsConfig(BaseModel):
    """提示词配置"""
    template_dir: Path
    default_language: str = "zh-CN"
    cache_enabled: bool = True
    cache_ttl: int = 3600



class StorageConfig(BaseModel):
    """存储配置"""
    data_dir: Path
    traces_dir: Path
    audit_dir: Path
    trace_retention_days: int = 30
    audit_retention_days: int = 90


class ObservabilityConfig(BaseModel):
    """可观测性配置"""
    log_level: str = "INFO"
    log_output: list[str] = Field(default_factory=lambda: ["file"])
    log_session_dir: Path = Path(".")
    log_rotation: str = "daily"
    log_retention_days: int = 30
    log_external_api: bool = False
    log_immediate_flush: bool = True
    tracing_enabled: bool = True
    tracing_exporter: str = "otlp"
    tracing_endpoint: str = ""
    tracing_sample_rate: float = 1.0
    metrics_enabled: bool = True
    metrics_exporter: str = "prometheus"
    metrics_port: int = 9090
    log_file_path: Path = Path(".")


class SystemConfig(BaseModel):
    """系统配置"""
    name: str = "qd-agents"
    version: str = "1.0.0"
    environment: str = "development"


class RuntimeConfig(BaseModel):
    """运行时配置 — 由 CLI 命令自动读写，存储在 runtime.json"""
    tools_credentials: ToolsCredentialsConfig = Field(default_factory=ToolsCredentialsConfig)


class Config(BaseSettings):
    """主配置类 — 静态系统配置，存储在 config.json"""
    model_config = SettingsConfigDict(
        env_prefix="QD_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    system: SystemConfig = Field(default_factory=SystemConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    tool_registry: ToolRegistryConfig | None = None
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    prompts: PromptsConfig | None = None

    storage: StorageConfig | None = None
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)



    @classmethod
    def with_defaults(cls, base_dir: Path | None = None) -> Self:
        """使用默认路径创建配置"""
        if base_dir is None:
            base_dir = Path.cwd()

        data_dir = base_dir / "data"
        model_path = base_dir / "hf_KimChen_bge-m3-q4_k_m.gguf"

        return cls(
            tool_registry=ToolRegistryConfig(
                db_path=data_dir / "tools.db",
                model_path=model_path if model_path.exists() else None,
            ),
            prompts=PromptsConfig(
                template_dir=base_dir / "src" / "qd_agents" / "prompts" / "templates",
            ),
            storage=StorageConfig(
                data_dir=data_dir,
                traces_dir=data_dir / "traces",
                audit_dir=data_dir / "audit",
            ),
        )

def _dict_to_config(data: dict[str, Any], base_dir: Path | None = None) -> Config:
    """将字典转换为 Config 对象"""
    if base_dir is None:
        base_dir = Path.cwd()

    # tools_credentials 已迁移到 runtime.json，从 config.json 数据中移除
    data.pop('tools_credentials', None)

    # 转换 Path 字段
    if 'tool_registry' in data and data['tool_registry']:
        tr = data['tool_registry']
        tr['db_path'] = base_dir / tr['db_path'] if tr.get('db_path') else None
        if tr.get('model_path'):
            tr['model_path'] = base_dir / tr['model_path']

    if 'prompts' in data and data['prompts']:
        data['prompts']['template_dir'] = base_dir / data['prompts']['template_dir']

    if 'storage' in data and data['storage']:
        s = data['storage']
        s['data_dir'] = base_dir / s['data_dir']
        s['traces_dir'] = base_dir / s['traces_dir']
        s['audit_dir'] = base_dir / s['audit_dir']

    if 'observability' in data and data['observability'].get('log_session_dir'):
        data['observability']['log_session_dir'] = base_dir / data['observability']['log_session_dir']

    # 向后兼容：支持旧的 'model' 字段
    if 'llm' in data and 'providers' in data['llm']:
        # 确保 default_model 存在
        if 'default_model' not in data['llm']:
            data['llm']['default_model'] = None

        for name, provider_data in data['llm']['providers'].items():
            if 'model' in provider_data and provider_data['model'] and 'models' not in provider_data:
                provider_data['models'] = [provider_data['model']]
            provider_data.pop('model', None)

            # 设置默认值：nvidia 默认 auto_discover=true，其他默认 false
            if name == 'nvidia':
                provider_data.setdefault('auto_discover', True)
            else:
                provider_data.setdefault('auto_discover', False)

    # 确保default_agent字段存在（向后兼容：从mode字段迁移）
    if 'llm' in data:
        if 'default_agent' not in data['llm'] and data['llm'].get('mode'):
            data['llm']['default_agent'] = data['llm']['mode']

    return Config(**data)


def _convert_paths(obj: Any, base_dir: Path) -> Any:
    """递归转换字典中的 Path 对象为相对路径字符串"""
    if isinstance(obj, Path):
        try:
            return str(obj.relative_to(base_dir))
        except ValueError:
            return str(obj)
    if isinstance(obj, dict):
        return {k: _convert_paths(v, base_dir) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_paths(v, base_dir) for v in obj]
    return obj


def _config_to_dict(config: Config, base_dir: Path | None = None) -> dict[str, Any]:
    """将 Config 对象转换为可 JSON 序列化的字典"""
    if base_dir is None:
        base_dir = Path.cwd()

    data = config.model_dump()
    return _convert_paths(data, base_dir)


def save_config(
    config: Config,
    base_dir: Path | None = None,
    config_file: Path | None = None,
) -> None:
    """
    保存配置到 config.json

    Args:
        config: 配置对象
        base_dir: 项目根目录
        config_file: config.json 文件路径
    """
    if base_dir is None:
        base_dir = Path.cwd()

    if config_file is None:
        config_file = base_dir / "config.json"

    config_data = _config_to_dict(config, base_dir)

    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, ensure_ascii=False, indent=2)


def load_config(
    base_dir: Path | None = None,
    config_file: Path | None = None,
) -> Config:
    """
    加载配置

    Args:
        base_dir: 项目根目录
        config_file: config.json 文件路径

    Returns:
        配置对象
    """
    from . import set_config

    if base_dir is None:
        base_dir = Path.cwd()

    # 尝试加载 config.json
    if config_file is None:
        config_file = base_dir / "config.json"

    if config_file and config_file.exists():
        with open(config_file, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        config = _dict_to_config(config_data, base_dir)
    else:
        # 如果没有 config.json，使用默认配置
        config = Config.with_defaults(base_dir=base_dir)

    # 设置全局配置
    set_config(config)
    return config


def load_runtime_config(
    base_dir: Path | None = None,
    runtime_file: Path | None = None,
) -> RuntimeConfig:
    """
    加载运行时配置（runtime.json）

    如果 runtime.json 不存在，尝试从 config.json 迁移 tools_credentials。

    Args:
        base_dir: 项目根目录
        runtime_file: runtime.json 文件路径

    Returns:
        运行时配置对象
    """
    if base_dir is None:
        base_dir = Path.cwd()

    if runtime_file is None:
        runtime_file = base_dir / "runtime.json"

    if runtime_file.exists():
        with open(runtime_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return RuntimeConfig(**data)

    # runtime.json 不存在，尝试从 config.json 迁移
    config_file = base_dir / "config.json"
    if config_file.exists():
        with open(config_file, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        if 'tools_credentials' in config_data:
            logger.info("Migrating tools_credentials from config.json to runtime.json")
            runtime_config = RuntimeConfig(**config_data['tools_credentials'])
            save_runtime_config(runtime_config, base_dir=base_dir, runtime_file=runtime_file)

            # 从 config.json 中移除 tools_credentials
            del config_data['tools_credentials']
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)

            return runtime_config

    return RuntimeConfig()


def save_runtime_config(
    runtime_config: RuntimeConfig,
    base_dir: Path | None = None,
    runtime_file: Path | None = None,
) -> None:
    """
    保存运行时配置到 runtime.json

    Args:
        runtime_config: 运行时配置对象
        base_dir: 项目根目录
        runtime_file: runtime.json 文件路径
    """
    if base_dir is None:
        base_dir = Path.cwd()

    if runtime_file is None:
        runtime_file = base_dir / "runtime.json"

    data = runtime_config.model_dump()

    with open(runtime_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
