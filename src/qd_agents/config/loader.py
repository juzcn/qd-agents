"""
配置加载器 - JSON 文件读写逻辑

Pydantic 模型定义在 config/models.py 中。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .models import Config, RuntimeConfig

logger = logging.getLogger(__name__)


def _dict_to_config(data: dict[str, Any], base_dir: Path | None = None) -> Config:
    """将字典转换为 Config 对象"""
    if base_dir is None:
        base_dir = Path.cwd()

    # tools_credentials 已迁移到 runtime.json，从 config.json 数据中移除
    data.pop('tools_credentials', None)

    # 转换 Path 字段
    if 'memory' in data and data['memory']:
        mem = data['memory']
        mem['db_path'] = base_dir / mem['db_path'] if mem.get('db_path') else None
        if mem.get('model_path'):
            mem['model_path'] = base_dir / mem['model_path']

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
    """保存配置到 config.json"""
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
    """加载配置"""
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
    """加载运行时配置（runtime.json）

    如果 runtime.json 不存在，尝试从 config.json 迁移 tools_credentials。
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
    """保存运行时配置到 runtime.json"""
    if base_dir is None:
        base_dir = Path.cwd()

    if runtime_file is None:
        runtime_file = base_dir / "runtime.json"

    data = runtime_config.model_dump()

    with open(runtime_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)