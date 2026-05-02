"""
EvolveAgent — 自主进化 Agent

单循环架构：LLM 拥有所有工具，自主决策每一步。
从最小能力起步，通过交互逐渐成长。
"""
from .agent import EvolveAgent, EvolveResult
from .context import EvolveContextManager

__all__ = [
    "EvolveAgent",
    "EvolveResult",
    "EvolveContextManager",
]
