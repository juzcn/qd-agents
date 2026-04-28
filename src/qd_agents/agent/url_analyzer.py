"""
URL 分析器 — 用 LLM 分析 URL 内容，判断工具类型并提取安装信息
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader

from ..models.url_analyze import UrlAnalyzeResult

if TYPE_CHECKING:
    from ..llm import LLMClient

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "prompts" / "templates"


class UrlAnalyzer:
    """URL 内容分析器"""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def analyze(self, url: str, content: str) -> UrlAnalyzeResult:
        """分析 URL 内容，返回工具类型和安装信息"""
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        template = env.get_template("url_analyze.j2")
        prompt = template.render(url=url, content=content)

        response = await self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )

        raw = response.choices[0].message.content or ""

        # 尝试从 LLM 输出中提取 JSON
        result = self._parse_result(raw)
        if result and result.success:
            logger.info("URL analyzed: %s → type=%s, name=%s", url, result.type, result.name)
        else:
            logger.warning("URL analysis failed or inconclusive: %s", url)

        return result or UrlAnalyzeResult(
            type="skill",
            name="unknown",
            success=False,
            failure_reason="Failed to parse LLM output",
        )

    @staticmethod
    def _parse_result(raw: str) -> UrlAnalyzeResult | None:
        """解析 LLM 输出为 UrlAnalyzeResult"""
        # 尝试提取 JSON
        json_str = raw
        if "```json" in json_str:
            json_str = json_str.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in json_str:
            json_str = json_str.split("```", 1)[1].split("```", 1)[0]

        json_str = json_str.strip()

        # 尝试找到 JSON 对象
        start = json_str.find("{")
        end = json_str.rfind("}") + 1
        if start >= 0 and end > start:
            json_str = json_str[start:end]

        try:
            data = json.loads(json_str)
            return UrlAnalyzeResult(**data)
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug("Failed to parse URL analysis result: %s", e)
            return None
