"""
模型评分与选择策略
"""
from __future__ import annotations

import re
from typing import NamedTuple


class ModelInfo(NamedTuple):
    """模型信息"""
    id: str
    name: str
    created: int | None = None
    owned_by: str | None = None
    capabilities: list[str] | None = None


# 模型系列优先级评分
SERIES_SCORES: dict[str, int] = {
    "deepseek": 42,
    "glm": 42,
    "qwen": 40,
    "minimax": 38,
    "llama": 35,
    "mistral": 35,
    "gemma": 35,
}

# 参数大小评分
PARAM_SIZE_PATTERNS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"72b|70b", re.IGNORECASE), 100),
    (re.compile(r"8x22b|8x19b", re.IGNORECASE), 90),
    (re.compile(r"34b|32b", re.IGNORECASE), 80),
    (re.compile(r"8x7b|13b|14b|12b", re.IGNORECASE), 70),
    (re.compile(r"8b", re.IGNORECASE), 60),
    (re.compile(r"7b", re.IGNORECASE), 50),
]


def is_chat_model(model: ModelInfo) -> bool:
    """判断是否为 chat 模型"""
    name = model.name.lower()

    # 1. 优先检查 capabilities 字段（如果模型对象有该字段）
    if model.capabilities:
        if isinstance(model.capabilities, list):
            # 如果 capabilities 是列表且包含 "chat"，返回 True
            if "chat" in model.capabilities:
                return True
            # 如果 capabilities 是列表但不包含 "chat"，返回 False
            return False
        elif isinstance(model.capabilities, str):
            # 如果 capabilities 是字符串且包含 "chat"，返回 True
            if "chat" in model.capabilities.lower():
                return True
            # 如果 capabilities 是字符串但不包含 "chat"，返回 False
            return False
        # 其他类型的 capabilities，回退到名称检查

    # 2. 检查名称规则
    chat_indicators = ["chat", "instruct", "conversation", "agent", "-it"]
    return any(indicator in name for indicator in chat_indicators)


def get_parameter_size_score(model_name: str) -> int:
    """获取参数大小评分"""
    name_lower = model_name.lower()
    for pattern, score in PARAM_SIZE_PATTERNS:
        if pattern.search(name_lower):
            return score
    return 40


def get_series_score(model_name: str) -> int:
    """获取模型系列评分"""
    name_lower = model_name.lower()
    for series, score in SERIES_SCORES.items():
        if series in name_lower:
            return score
    return 20


def calculate_model_score(model: ModelInfo) -> float:
    """
    计算模型综合得分

    总分 = 基础分 + 参数大小分 + 系列分 + 后缀加分

    Returns:
        模型得分，非 chat 模型返回 0.0
    """
    score = 0.0

    # 1. 只选择 chat 模型
    if not is_chat_model(model):
        return 0.0
    score += 1000

    # 2. 参数大小分
    score += get_parameter_size_score(model.name)

    # 3. 模型系列分
    score += get_series_score(model.name)

    # 4. instruct/chat 后缀加分
    name_lower = model.name.lower()
    if any(suffix in name_lower for suffix in ["instruct", "chat"]):
        score += 20

    return score


def get_top_models(models: list[ModelInfo], top_k: int = 5) -> list[ModelInfo]:
    """
    选择得分最高的 Top K 模型

    Args:
        models: 模型列表
        top_k: 返回的模型数量

    Returns:
        按得分降序排列的 Top K 模型
    """
    scored_models = [(calculate_model_score(m), m) for m in models]
    scored_models.sort(key=lambda x: x[0], reverse=True)

    # 过滤掉得分 0 的模型（非 chat 模型）
    filtered = [m for score, m in scored_models if score > 0]

    return filtered[:top_k]
