"""
上下文压缩器 — 工具结果"一次完整 + 后续摘要 + 文件指针"

核心思路：
- 工具结果首次出现时完整展示（LLM 本轮可直接使用）
- 写入临时文件保存完整内容
- 后续轮次替换为摘要 + 文件路径
- LLM 需要细节时自主决定读取临时文件或重新调用工具
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from ..config.models import ContextCompressionConfig
from ..llm import LLMClient

logger = logging.getLogger(__name__)


class ContextCompressor:
    """上下文压缩器：管理工具结果的压缩和临时文件"""

    def __init__(
        self,
        config: ContextCompressionConfig,
        llm_client: LLMClient,
        base_dir: Path | None = None,
    ):
        self.config = config
        self.llm = llm_client
        self.base_dir = base_dir or Path.cwd()
        self._temp_dir: Path | None = None
        # tool_call_id → (temp_file_path, original_content, compressed_content)
        self._compressed_results: dict[str, tuple[Path, str, str]] = {}

    @property
    def temp_dir(self) -> Path:
        """获取临时文件目录（懒创建）"""
        if self._temp_dir is None:
            self._temp_dir = self.base_dir / self.config.temp_dir
            self._temp_dir.mkdir(parents=True, exist_ok=True)
        return self._temp_dir

    def should_compress(self, result_content: str, is_recent: bool) -> bool:
        """判断是否需要压缩"""
        if not self.config.enabled:
            return False
        if is_recent:
            return False
        return len(result_content) > self.config.result_threshold

    async def compress_result(
        self,
        tool_name: str,
        tool_call_id: str,
        result_content: str,
    ) -> str:
        """压缩工具结果：写临时文件 + 生成摘要，返回压缩后的消息内容"""
        # 1. 写临时文件
        temp_path = self._write_temp_file(tool_call_id, result_content)

        # 2. 裸 LLM 调用生成摘要
        summary = await self._generate_summary(tool_name, result_content)

        # 3. 构建压缩后的消息内容
        compressed = f"[摘要: {summary}]\n完整结果已保存至: {temp_path}\n需要细节时可调用 read_text_file 读取该文件。"

        # 4. 缓存压缩结果
        self._compressed_results[tool_call_id] = (temp_path, result_content, compressed)

        logger.info(
            "Compressed tool result: %s (original %d chars → compressed %d chars, temp: %s)",
            tool_call_id, len(result_content), len(compressed), temp_path,
        )

        return compressed

    def compress_old_results(
        self,
        messages: list[dict[str, Any]],
        current_iteration: int,
    ) -> list[dict[str, Any]]:
        """在发送给 LLM 前，压缩历史中的旧 tool_result

        保留最近 keep_recent_results 轮的完整结果不压缩。
        """
        if not self.config.enabled:
            return messages

        # 找到所有 tool_result 消息的位置
        tool_result_indices = []
        for i, msg in enumerate(messages):
            if msg.get("role") == "tool" and msg.get("tool_call_id"):
                tool_result_indices.append(i)

        if not tool_result_indices:
            return messages

        # 保留最近 keep_recent_results 个不压缩
        keep_count = self.config.keep_recent_results
        compress_indices = tool_result_indices[:-keep_count] if keep_count > 0 else tool_result_indices

        for idx in compress_indices:
            msg = messages[idx]
            tool_call_id = msg.get("tool_call_id", "")
            content = msg.get("content", "")

            # 已经压缩过的，跳过
            if tool_call_id in self._compressed_results:
                _, _, compressed = self._compressed_results[tool_call_id]
                if content == compressed:
                    continue
                # 内容还没被替换为压缩版本，替换
                messages[idx] = {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": compressed,
                }
                continue

            # 短结果不需要压缩
            if len(content) <= self.config.result_threshold:
                continue

            # 需要压缩但还没生成摘要 — 先用简单截断 + 文件指针占位
            # 摘要会在下一轮异步生成后替换
            temp_path = self._write_temp_file(tool_call_id, content)
            truncated = content[:500] + f"\n...[截断，完整结果已保存至: {temp_path}]"
            self._compressed_results[tool_call_id] = (temp_path, content, truncated)
            messages[idx] = {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": truncated,
            }
            logger.info(
                "Quick-compressed old tool result: %s (%d chars → truncated, temp: %s)",
                tool_call_id, len(content), temp_path,
            )

        return messages

    def _write_temp_file(self, tool_call_id: str, result_content: str) -> Path:
        """将完整结果写入临时文件"""
        # 使用简短文件名（tool_call_id 可能很长）
        short_id = tool_call_id.replace("call_", "").replace("tool-", "")[:12]
        if not short_id:
            short_id = uuid.uuid4().hex[:12]
        filename = f"result_{short_id}.txt"
        temp_path = self.temp_dir / filename

        temp_path.write_text(result_content, encoding="utf-8")
        logger.debug("Wrote temp file: %s (%d chars)", temp_path, len(result_content))
        return temp_path

    async def _generate_summary(self, tool_name: str, result_content: str) -> str:
        """用裸 LLM 调用生成摘要"""
        # 截断结果内容（最多 8000 字符），避免摘要请求本身也超长
        truncated_content = result_content[:8000]
        if len(result_content) > 8000:
            truncated_content += f"\n...[共 {len(result_content)} 字符，已截断]"

        prompt = (
            f"请用1-2句话概括以下工具调用结果的关键信息，不要超过{self.config.summary_max_length}字符。"
            f"只输出摘要内容，不要任何前缀或解释。\n\n"
            f"工具名: {tool_name}\n结果:\n{truncated_content}"
        )

        try:
            response = await self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200,
            )
            summary = response.choices[0].message.content.strip()
            # 限制摘要长度
            if len(summary) > self.config.summary_max_length:
                summary = summary[:self.config.summary_max_length - 3] + "..."
            return summary
        except Exception as e:
            logger.warning("Failed to generate summary for %s: %s, using truncation", tool_name, e)
            # 回退：简单截断
            return result_content[:200].replace("\n", " ").strip() + "..."

    def cleanup_temp_files(self) -> None:
        """会话结束时清理临时文件"""
        for tool_call_id, (temp_path, _, _) in self._compressed_results.items():
            try:
                if temp_path.exists():
                    temp_path.unlink()
                    logger.debug("Cleaned up temp file: %s", temp_path)
            except OSError as e:
                logger.warning("Failed to cleanup temp file %s: %s", temp_path, e)

        self._compressed_results.clear()
        logger.info("Cleaned up %d temp files", len(self._compressed_results))