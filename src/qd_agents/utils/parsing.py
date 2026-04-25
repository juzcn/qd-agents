"""
LLM 输出解析工具

从 LLM 返回的文本中提取 JSON 内容，支持 markdown 代码块和裸 JSON。
"""
from __future__ import annotations

import json
import re


def extract_json_from_llm_output(content: str) -> str:
    """从 LLM 输出中提取 JSON 字符串。

    按优先级尝试：
    1. ```json ... ``` 或 ``` ... ``` 代码块
    2. 最外层 { } 包裹的内容
    3. 原文返回
    """
    # 匹配 ```json ... ``` 或 ``` ... ```
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
    if json_match:
        return json_match.group(1).strip()

    # 尝试找到 { } 包裹的内容
    brace_start = content.find('{')
    brace_end = content.rfind('}')
    if brace_start != -1 and brace_end != -1:
        return content[brace_start:brace_end + 1]

    return content


def parse_json_from_llm_output(content: str) -> dict | None:
    """从 LLM 输出中提取并解析 JSON 字典。

    Returns:
        解析后的字典，失败返回 None
    """
    try:
        json_str = extract_json_from_llm_output(content)
        return json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return None
