"""
配置数据模型 — 所有 Pydantic BaseModel 定义

从 config/loader.py 中提取，loader.py 只保留 JSON 加载/保存逻辑。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Self


class ModelSpecConfig(BaseModel):
    """模型规格配置 — 对应 config.json 中 models 列表的对象条目"""
    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
    )

    name: str
    context_length: int | None = Field(None, alias="contextWindow")
    max_output_tokens: int | None = Field(None, alias="maxTokens")
    reasoning: bool | None = None
    input: list[str] | None = None
    capabilities: list[str] | None = None


class LLMProviderConfig(BaseModel):
    """LLM 提供商配置"""
    api_key: str
    base_url: str = "https://integrate.api.nvidia.com/v1"
    models: list[str | ModelSpecConfig] = Field(default_factory=list)
    timeout: int = 120000
    max_retries: int = 3
    auto_discover: bool = True

    def get_model_names(self) -> list[str]:
        """提取所有模型名称（兼容 str 和 ModelSpecConfig 两种格式）"""
        return [m if isinstance(m, str) else m.name for m in self.models]

    def get_model_spec(self, model_name: str) -> ModelSpecConfig | None:
        """获取指定模型的规格配置"""
        for m in self.models:
            if isinstance(m, ModelSpecConfig) and m.name == model_name:
                return m
        return None


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
    tool_threshold: int = 50


class MemoryConfig(BaseModel):
    """长期记忆配置"""
    db_path: Path = Path("data/memory.db")
    embedding_backend: str = "llama_cpp"
    embedding_model: str = "hf_KimChen_bge-m3-q4_k_m.gguf"
    hf_token: str = ""
    hf_cache_dir: str = ""
    model_path: Path | None = None
    vec_dim: int = 1024
    top_k: int = 5
    similarity_threshold: float = 0.7
    hybrid_search: bool = True
    auto_save: bool = True
    max_recall_chars: int = 2000
    max_recall_results: int = 5


class ToolRegistryConfig(BaseModel):
    """Tool Registry 配置"""
    db_path: Path
    sqlite_vec_enabled: bool = True
    embedding_backend: str = "llama_cpp"
    embedding_model: str = "hf_KimChen_bge-m3-q4_k_m.gguf"
    hf_token: str = ""
    hf_cache_dir: str = ""
    model_path: Path | None = None
    top_k: int = 10
    similarity_threshold: float = 0.7
    hybrid_search: bool = True


class ExecutionConfig(BaseModel):
    """执行引擎配置"""
    default_timeout: int = 30000
    per_tool_timeout: dict[str, int] = Field(default_factory=dict)
    max_attempts: int = 3
    max_iterations: int = 10
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
    memory: MemoryConfig | None = None
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
            memory=MemoryConfig(
                db_path=data_dir / "memory.db",
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