"""
配置加载器
"""
import os
from pathlib import Path
from typing import Any
from typing_extensions import Self
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProviderConfig(BaseModel):
    """LLM 提供商配置"""
    api_key: str
    base_url: str = "https://integrate.api.nvidia.com/v1"
    model: str | None = None
    timeout: int = 120000
    max_retries: int = 3


class LLMConfig(BaseModel):
    """LLM 配置"""
    default_provider: str = "nvidia"
    providers: dict[str, LLMProviderConfig] = Field(default_factory=dict)
    two_phase_enabled: bool = True
    tool_threshold: int = 50
    phase_one_tools: list[str] = Field(
        default_factory=lambda: ["meta.direct", "meta.find_tools", "search.web"]
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


class Config(BaseSettings):
    """主配置类"""
    model_config = SettingsConfigDict(
        env_prefix="QD_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    system_name: str = "qd-agents"
    system_version: str = "0.1.0"
    environment: str = "development"

    llm: LLMConfig = Field(default_factory=LLMConfig)
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
                template_dir=base_dir / "qd_agents" / "prompts" / "templates",
            ),
            storage=StorageConfig(
                data_dir=data_dir,
                traces_dir=data_dir / "traces",
                audit_dir=data_dir / "audit",
            ),
            observability=ObservabilityConfig(
                log_file_path=data_dir / "logs" / "app.log",
            ),
        )


def load_config(base_dir: Path | None = None, env_file: Path | None = None) -> Config:
    """
    加载配置

    Args:
        base_dir: 项目根目录
        env_file: .env 文件路径

    Returns:
        配置对象
    """
    if env_file is None and base_dir is not None:
        env_file = base_dir / ".env"

    if env_file and env_file.exists():
        load_dotenv(env_file)

    config = Config.with_defaults(base_dir=base_dir)

    # 从环境变量加载 NVIDIA 配置
    nvidia_api_key = os.getenv("NVIDIA_API_KEY")
    if nvidia_api_key and not config.llm.providers.get("nvidia"):
        config.llm.providers["nvidia"] = LLMProviderConfig(
            api_key=nvidia_api_key,
            base_url=os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        )

    return config
