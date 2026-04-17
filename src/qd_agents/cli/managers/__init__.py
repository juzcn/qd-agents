"""
CLI 管理器模块
"""

from .llm_client import LLMClientManager
from .configuration import setup_configuration
from .tool_registration import auto_register_pdf_skill

__all__ = [
    "LLMClientManager",
    "setup_configuration",
    "auto_register_pdf_skill",
]