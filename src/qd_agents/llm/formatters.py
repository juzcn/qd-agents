"""
LLM 消息格式化工具

提供消息格式化、转义序列清理和日志格式化功能。
"""
from __future__ import annotations

import json
from typing import Any


def clean_escape_sequences(text: str) -> str:
    """
    清理常见的转义字符序列，提高可读性

    只处理控制字符的转义序列（\\n, \\r, \\t, \\\\, \\\", \\'），
    保留Unicode转义序列和其他转义序列，避免破坏原始内容。

    注意：替换顺序很重要，\\\\ 必须最先处理，否则会破坏其他替换结果。
    """
    if not isinstance(text, str):
        return str(text)

    # 替换顺序：\\\\ 必须最先，否则 \\n 中的 \\\\ 会先被匹配破坏
    replacements = [
        ('\\\\', '\\'),       # 反斜杠（必须最先）
        ('\\n', '\n'),        # 换行
        ('\\r', '\r'),        # 回车
        ('\\t', '\t'),        # 制表符
        ('\\"', '"'),         # 双引号
        ("\\'", "'"),         # 单引号
    ]

    cleaned = text
    for old, new in replacements:
        cleaned = cleaned.replace(old, new)

    return cleaned


def _try_format_json_string(text: str, indent: str = "          ") -> str:
    """
    尝试将字符串解析为 JSON 并 pretty-print。

    处理三种情况：
    1. 整个字符串是 JSON → 直接 pretty-print
    2. 前缀文本 + JSON（CLI 工具常见：日志行 + JSON 输出）→ 分离后分别渲染
    3. 非 JSON → 清理转义序列后返回原文
    """
    if not isinstance(text, str) or not text.strip():
        return text

    # 情况 1：整个字符串是 JSON
    try:
        parsed = json.loads(text)
        formatted = json.dumps(parsed, ensure_ascii=False, indent=2)
        lines = formatted.split("\n")
        return lines[0] + "\n" + "\n".join(
            indent + line for line in lines[1:]
        )
    except (json.JSONDecodeError, ValueError):
        pass

    # 情况 2：前缀文本 + JSON（按行查找 JSON 起始位置）
    cleaned = clean_escape_sequences(text)
    lines = cleaned.split("\n")
    json_start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("{", "[")):
            # 尝试从该行开始解析 JSON
            candidate = "\n".join(lines[i:])
            try:
                parsed = json.loads(candidate)
                json_start = i
                break
            except (json.JSONDecodeError, ValueError):
                continue

    if json_start is not None:
        prefix_lines = lines[:json_start]
        prefix_text = "\n".join(prefix_lines)
        candidate = "\n".join(lines[json_start:])
        parsed = json.loads(candidate)
        formatted = json.dumps(parsed, ensure_ascii=False, indent=2)
        json_lines = formatted.split("\n")
        json_text = json_lines[0] + "\n" + "\n".join(
            indent + line for line in json_lines[1:]
        )
        if prefix_text.strip():
            return prefix_text + "\n" + json_text
        return json_text

    # 情况 3：纯文本
    return cleaned


def _format_tool_result_value(value: Any, indent: str = "          ") -> str:
    """
    格式化 tool 角色返回结果中的值。

    对 stdout/stderr 等字符串字段，尝试解析内部 JSON 并 pretty-print；
    对 dict/list 等结构化字段，递归格式化。
    """
    if isinstance(value, str):
        return _try_format_json_string(value, indent)
    if isinstance(value, (dict, list)):
        formatted = json.dumps(value, ensure_ascii=False, indent=2)
        lines = formatted.split("\n")
        return lines[0] + "\n" + "\n".join(
            indent + line for line in lines[1:]
        )
    return str(value)


def format_content(content: Any) -> str:
    """格式化消息 content 字段，纯文本渲染"""
    if content is None:
        return "[no content]"

    if isinstance(content, str):
        return clean_escape_sequences(content)

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type", "unknown")
                if item_type == "text":
                    parts.append(clean_escape_sequences(item.get("text", "")))
                elif item_type == "image_url":
                    url = item.get("image_url", {})
                    parts.append(f"[image: {str(url)[:80]}]")
                else:
                    parts.append(json.dumps(item, ensure_ascii=False, default=str))
            else:
                parts.append(str(item))
        return "\n".join(parts)

    return str(content)


def tool_calls_to_dicts(tool_calls: Any) -> list[dict[str, Any]]:
    """将 tool_calls 转为 dict 列表"""
    result = []
    for tc in tool_calls:
        if hasattr(tc, 'model_dump'):
            result.append(tc.model_dump())
        elif isinstance(tc, dict):
            result.append(tc)
        else:
            result.append({"raw": str(tc)})
    return result


def format_tool_call_text(tc: dict[str, Any]) -> str:
    """将单个 tool_call dict 格式化为纯文本行"""
    tc_id = tc.get("id", "")
    func = tc.get("function", {})
    name = func.get("name", "") if isinstance(func, dict) else ""
    arguments = func.get("arguments", "") if isinstance(func, dict) else ""

    # arguments 解码：如果它是 JSON 字符串，解析后纯文本渲染
    args_text = arguments
    if isinstance(arguments, str) and arguments.strip():
        try:
            args_parsed = json.loads(arguments)
            args_text = json.dumps(args_parsed, ensure_ascii=False, indent=2)
            # 缩进对齐
            args_lines = args_text.split("\n")
            args_text = "\n".join(
                args_lines[0] if k == 0 else "          " + line
                for k, line in enumerate(args_lines)
            )
        except (json.JSONDecodeError, ValueError):
            args_text = clean_escape_sequences(arguments)

    return f'{{"id": "{tc_id}", "function": {{"name": "{name}", "arguments": {args_text}}}}}'


def format_messages_for_logging(
    messages: list[dict[str, Any]],
    start_index: int = 0,
) -> str:
    """
    格式化消息用于日志记录，纯文本渲染，还原 messages 数组结构

    Args:
        messages: 消息列表
        start_index: 开始记录的消息索引（用于增量日志）
    """
    if not messages:
        return "[]"

    formatted_parts = []

    for i, msg in enumerate(messages):
        # 跳过已记录的消息
        if i < start_index:
            continue

        lines: list[str] = []
        role = msg.get("role", "unknown")
        lines.append(f'  [{i}] {{"role": "{role}"')

        # content — 纯文本渲染
        content = msg.get("content")
        if content is not None:
            # tool 角色的 content：解析外层 JSON 后逐字段格式化
            if role == "tool" and isinstance(content, str):
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict):
                        # 逐字段格式化，stdout/stderr 等字符串字段尝试解析内部 JSON
                        field_lines = ["{"]
                        field_items = list(parsed.items())
                        for k, (key, value) in enumerate(field_items):
                            formatted_value = _format_tool_result_value(value, "            ")
                            comma = "," if k < len(field_items) - 1 else ""
                            field_lines.append(f'          "{key}": {formatted_value}{comma}')
                        field_lines.append("        }")
                        text = "\n".join(field_lines)
                    else:
                        text = json.dumps(parsed, ensure_ascii=False, indent=2)
                        lines = text.split("\n")
                        text = lines[0] + "\n" + "\n".join(
                            "          " + line for line in lines[1:]
                        )
                except (json.JSONDecodeError, ValueError):
                    text = format_content(content)
            else:
                text = format_content(content)
            lines.append(f'      "content": {text}')

        # tool_calls — 纯文本渲染
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            lines.append('      "tool_calls": [')
            tc_list = tool_calls_to_dicts(tool_calls)
            for j, tc in enumerate(tc_list):
                tc_text = format_tool_call_text(tc)
                comma = "," if j < len(tc_list) - 1 else ""
                lines.append(f"        {tc_text}{comma}")
            lines.append("      ]")

        # tool 角色额外字段
        if role == "tool":
            tool_call_id = msg.get("tool_call_id")
            if tool_call_id:
                lines.append(f'      "tool_call_id": "{tool_call_id}"')
            tool_name = msg.get("name")
            if tool_name:
                lines.append(f'      "name": "{tool_name}"')

        lines.append("    }")
        formatted_parts.append("\n".join(lines))

    return "[\n" + ",\n".join(formatted_parts) + "\n]"