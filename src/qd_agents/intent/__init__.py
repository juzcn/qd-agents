"""
意图对象模块：定义 Intent Schema 和相关工具
"""

from .schema import Intent, Constraints, Dependency, Meta
from .builder import IntentBuilder

__all__ = ["Intent", "Constraints", "Dependency", "Meta", "IntentBuilder"]
