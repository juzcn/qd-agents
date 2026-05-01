"""环境变量解析 — 工具注册和执行共享

环境变量策略：从 runtime.json tools_credentials 读取，有则用，无则从 os.environ 取，再无则空字符串。
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 环境变量名到 tools_credentials 中工具名的映射
ENV_TO_TOOL_NAME_MAP: dict[str, str] = {
    "BAIDU_API_KEY": "baidu_search",
    "SERPER_API_KEY": "serper_search",
    "TAVILY_API_KEY": "tavily_search",
}


def env_var_to_tool_name(env_var: str) -> str:
    """将环境变量名转换为 tools_credentials 中的工具名。"""
    if env_var in ENV_TO_TOOL_NAME_MAP:
        return ENV_TO_TOOL_NAME_MAP[env_var]
    if env_var.endswith("_API_KEY"):
        return env_var[: -len("_API_KEY")].lower()
    return env_var.lower()


def resolve_env_vars_noninteractive(
    env_vars: list[str], base_dir: Optional[Path] = None
) -> dict[str, str]:
    """从 runtime.json tools_credentials 解析环境变量，无交互。

    策略：runtime.json 有值则用，否则从 os.environ 取，再无则空字符串。
    """
    from qd_agents.config import load_runtime_config

    env: dict[str, str] = {}
    runtime_config = load_runtime_config(base_dir=base_dir)

    for var in env_vars:
        tool_name = env_var_to_tool_name(var)
        api_key_value = runtime_config.tools_credentials.get_api_key(tool_name)
        if api_key_value:
            env[var] = api_key_value
        else:
            env[var] = os.environ.get(var, "")

    return env
