"""工具注册 — 按类型分发的注册器"""

from .cli_registrar import register_cli_tool, extract_registration_args as cli_extract_args
from .mcp_registrar import register_mcp_tool, extract_registration_args as mcp_extract_args
from .skill_registrar import register_skill_tool, extract_registration_args as skill_extract_args
from .http_registrar import register_http_tool, extract_registration_args as http_extract_args

__all__ = [
    "register_cli_tool",
    "register_mcp_tool",
    "register_skill_tool",
    "register_http_tool",
    "cli_extract_args",
    "mcp_extract_args",
    "skill_extract_args",
    "http_extract_args",
]