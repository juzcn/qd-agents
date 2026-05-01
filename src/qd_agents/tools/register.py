"""工具注册 — 兼容性重导出层

所有注册逻辑已迁移至 tools/registrars/ 子包。
本文件仅保留 re-export 以兼容现有 import。
"""

from qd_agents.tools.registrars import (
    register_cli_tool,
    register_mcp_tool,
    register_skill_tool,
    register_http_tool,
)

# 共享工具函数重导出
from qd_agents.tools.openapi import parse_filter, fetch_openapi_spec
from qd_agents.tools.env import resolve_env_vars_noninteractive
from qd_agents.tools.llm_helpers import ensure_logging, parse_help_with_llm, run_add_skill_analyzer
from qd_agents.tools.skill_parsing import parse_skill_md
from qd_agents.tools.version import detect_package_version, detect_version_simple

__all__ = [
    "register_cli_tool",
    "register_mcp_tool",
    "register_skill_tool",
    "register_http_tool",
    "parse_filter",
    "fetch_openapi_spec",
    "resolve_env_vars_noninteractive",
    "ensure_logging",
    "parse_help_with_llm",
    "run_add_skill_analyzer",
    "parse_skill_md",
    "detect_package_version",
    "detect_version_simple",
]
