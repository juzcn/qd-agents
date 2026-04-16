"""
配置加载器 - 支持 JSON 配置文件
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from typing_extensions import Self
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


from enum import Enum


class AgentMode(str, Enum):
    """智能体工作模式"""
    TOOL_USE = "tool-use"
    CODE_PLAN = "code-plan"


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
    mode: AgentMode = AgentMode.TOOL_USE
    two_phase_enabled: bool = False
    tool_threshold: int = 50
    phase_one_tools: list[str] = Field(
        default_factory=lambda: ["search.web"]  # 仅保留搜索工具
    )


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


class PromptsConfig(BaseModel):
    """提示词配置"""
    template_dir: Path
    default_language: str = "zh-CN"
    cache_enabled: bool = True
    cache_ttl: int = 3600


class ObservabilityConfig(BaseModel):
    """可观测性配置"""
    log_level: str = "INFO"
    log_format: str = "json"
    log_output: list[str] = Field(default_factory=lambda: ["console"])
    log_file_path: Path | None = None
    log_session_dir: Path | None = None
    log_rotation: str = "daily"
    log_retention_days: int = 30
    tracing_enabled: bool = True
    tracing_exporter: str = "otlp"
    tracing_endpoint: str | None = None
    tracing_sample_rate: float = 1.0
    metrics_enabled: bool = True
    metrics_exporter: str = "prometheus"
    metrics_port: int = 9090


class StorageConfig(BaseModel):
    """存储配置"""
    data_dir: Path
    traces_dir: Path
    audit_dir: Path
    trace_retention_days: int = 30
    audit_retention_days: int = 90


class SecurityConfig(BaseModel):
    """安全配置"""
    require_confirmation_for: list[str] = Field(
        default_factory=lambda: ["destructive", "readwrite"]
    )
    audit_log_enabled: bool = True
    sensitive_fields: list[str] = Field(
        default_factory=lambda: ["api_key", "password", "secret"]
    )


class VersioningConfig(BaseModel):
    """版本管理配置"""
    default_version_strategy: str = "latest_active"
    deprecation_warning_days: int = 30
    auto_migrate: bool = False


class SystemConfig(BaseModel):
    """系统配置"""
    name: str = "qd-agents"
    version: str = "1.0.0"
    environment: str = "development"


class Config(BaseSettings):
    """主配置类"""
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
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    storage: StorageConfig | None = None
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    versioning: VersioningConfig = Field(default_factory=VersioningConfig)

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
            observability=ObservabilityConfig(
                log_file_path=data_dir / "logs" / "app.log",
                log_session_dir=Path("."),
            ),
        )


def _dict_to_config(data: dict[str, Any], base_dir: Path | None = None) -> Config:
    """将字典转换为 Config 对象"""
    if base_dir is None:
        base_dir = Path.cwd()

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

    if 'observability' in data and data['observability'].get('log_file_path'):
        data['observability']['log_file_path'] = base_dir / data['observability']['log_file_path']
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

    return Config(**data)


def _config_to_dict(config: Config, base_dir: Path | None = None) -> dict[str, Any]:
    """将 Config 对象转换为字典"""
    if base_dir is None:
        base_dir = Path.cwd()

    data = config.model_dump()

    # 转换 Path 字段为相对路径
    if data.get('tool_registry') and data['tool_registry'].get('db_path'):
        data['tool_registry']['db_path'] = str(Path(data['tool_registry']['db_path']).relative_to(base_dir))
    if data.get('tool_registry') and data['tool_registry'].get('model_path'):
        data['tool_registry']['model_path'] = str(Path(data['tool_registry']['model_path']).relative_to(base_dir))

    if data.get('prompts') and data['prompts'].get('template_dir'):
        data['prompts']['template_dir'] = str(Path(data['prompts']['template_dir']).relative_to(base_dir))

    if data.get('storage'):
        if data['storage'].get('data_dir'):
            data['storage']['data_dir'] = str(Path(data['storage']['data_dir']).relative_to(base_dir))
        if data['storage'].get('traces_dir'):
            data['storage']['traces_dir'] = str(Path(data['storage']['traces_dir']).relative_to(base_dir))
        if data['storage'].get('audit_dir'):
            data['storage']['audit_dir'] = str(Path(data['storage']['audit_dir']).relative_to(base_dir))

    if data.get('observability') and data['observability'].get('log_file_path'):
        data['observability']['log_file_path'] = str(Path(data['observability']['log_file_path']).relative_to(base_dir))
    if data.get('observability') and data['observability'].get('log_session_dir'):
        data['observability']['log_session_dir'] = str(Path(data['observability']['log_session_dir']).relative_to(base_dir))

    return data


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
