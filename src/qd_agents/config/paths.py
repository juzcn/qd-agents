"""集中路径解析 — 从 config 派生的路径统一在此处理"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from qd_agents.config.models import Config


def resolve_template_dir(config: Config) -> Path:
    """解析提示词模板目录，config.prompts 未配置时使用内置默认路径。"""
    if config.prompts and config.prompts.template_dir:
        return Path(config.prompts.template_dir)
    return Path(__file__).parent.parent / "prompts" / "templates"
