"""
上下文管理器

负责构建和管理 LLM 调用的完整上下文：
- 系统提示词（分阶段）
- 会话历史记录
- 工具描述
- SKILL.md 注入（仅对 SKILL 类型的工具）
"""
from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..prompts import PromptLoader
from ..models.tool import Tool, ToolExecutionType


logger = logging.getLogger(__name__)


class ContextManager:
    """
    上下文管理器

    统一管理 LLM 调用的上下文构建
    """

    def __init__(
        self,
        prompt_loader: PromptLoader | None = None,
        base_dir: Path | None = None,
    ):
        """
        初始化上下文管理器

        Args:
            prompt_loader: 提示词加载器
            base_dir: 项目根目录，用于定位 tools/skills/ 目录
        """
        self.prompts = prompt_loader
        self.base_dir = base_dir
        self._session_history: list[dict[str, str]] = []
        self._cached_system_prompt: str | None = None
        self._cached_tools: list[Any] | None = None
        self._evolve_cache: dict[tuple, str] = {}     # 缓存进化判断提示词
        self._skill_md_cache: dict[str, str] = {}      # 缓存 SKILL.md 正文

    def add_to_history(self, role: str, content: str) -> None:
        """
        添加消息到会话历史

        Args:
            role: 角色 (user/assistant/system/tool)
            content: 内容
        """
        self._session_history.append({"role": role, "content": content})

    def clear_history(self) -> None:
        """清空会话历史"""
        self._session_history.clear()

    def get_history(self) -> list[dict[str, str]]:
        """获取会话历史"""
        return self._session_history.copy()

    def _load_skill_md(self, skill_dir_name: str) -> str | None:
        """
        从 tools/skills/{skill_dir_name}/SKILL.md 读取正文内容。

        Args:
            skill_dir_name: skill 目录名（即 dependencies.skill_dir_name）

        Returns:
            SKILL.md 正文内容，不存在返回 None
        """
        if skill_dir_name in self._skill_md_cache:
            return self._skill_md_cache[skill_dir_name]

        # 构建路径：tools/skills/{skill_dir_name}/SKILL.md
        base = self.base_dir or Path(".")
        skill_md_path = base / "tools" / "skills" / skill_dir_name / "SKILL.md"

        if not skill_md_path.exists():
            logger.debug("SKILL.md not found: %s", skill_md_path)
            return None

        try:
            content = skill_md_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Failed to read SKILL.md for %s: %s", skill_dir_name, e)
            return None

        # 提取正文（跳过 YAML frontmatter）
        body = content
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                body = content[end + 3:].strip()

        self._skill_md_cache[skill_dir_name] = body
        logger.debug("Loaded SKILL.md for %s (%d chars)", skill_dir_name, len(body))
        return body

    def build_evolve_messages(
        self,
        user_input: str,
        tools: list[Tool],
        history: list[dict[str, str]] | None = None,
        observations: list[str] | None = None,
    ) -> list[dict[str, str]]:
        """
        构建自主进化决策消息

        初始只列出所有工具的 name+description（渐进式披露）。
        SKILL 工具的 SKILL.md 在 LLM 选择使用时由 EvolveAgent 动态注入。

        Args:
            user_input: 当前用户输入
            tools: 可用工具列表
            history: 会话历史
            observations: 前几轮观察结果

        Returns:
            完整的消息列表
        """
        # 构建缓存键（observations 不参与缓存，因为每轮不同）
        tool_ids = tuple(sorted(tool.id for tool in tools))
        cache_key = tool_ids

        # 检查缓存
        if cache_key in self._evolve_cache:
            system_prompt = self._evolve_cache[cache_key]
            logger.debug("Using cached system prompt for evolve")
        else:
            if self.prompts:
                system_prompt = self.prompts.render(
                    "evolve",
                    tools=tools,
                    observations=[],
                    env_info=self._get_env_info(),
                )
            else:
                # 回退到硬编码
                env_info = self._get_env_info()
                tools_info = "\n".join(
                    f"- {getattr(t, 'name', str(t))}: {getattr(t, 'description', '')[:150]}"
                    for t in tools[:30]
                )
                system_prompt = f"""你是一个自主进化的智能体。你能够自主思考、自主决策、自主行动，并在需要时向用户求助或请求协作。

你具备直接调用工具的能力——这是你的元工具，是你自身具备的能力。你可以自主决定调用哪些工具、观察结果、继续调用或给出最终答案。

自我认知：
- 系统提示词: src/qd_agents/prompts/templates/evolve.j2
- 运行配置: config.json
- 数据模型: src/qd_agents/models/evolve.py
- 决策逻辑: src/qd_agents/agent/evolve.py

运行环境：
- 操作系统: {env_info['os']}
- Python 版本: {env_info['python_version']}
- Python 路径: {env_info['python_path']}
- 虚拟环境: {env_info['venv_path']}
- 包管理器: uv {env_info['uv_version']}
- 项目根目录: {env_info['project_dir']}

自主循环：你可以多轮迭代：思考 → 调用工具 → 观察结果 → 继续或完成。不要猜测！不确定就先调工具获取信息。

可用工具:
{tools_info or '暂无'}

需要请求用户输入时输出: {{"action": "ask_user", "ask_user": {{"question": "...", "options": [...], "reason": "..."}}}}
需要委托用户执行时输出: {{"action": "delegate", "delegate": {{"task": "...", "guide": "...", "reason": "..."}}}}
其他情况直接用自然语言回答。"""

            self._evolve_cache[cache_key] = system_prompt
            logger.debug(f"Cached system prompt for evolve with {len(tools)} tools")

        # 构建 base messages（system + history + user_input）
        messages = self._build_messages(
            system_prompt=system_prompt,
            user_input=user_input,
            history=history,
        )

        # 如果有 observations，作为独立消息注入到 user_input 之前
        if observations:
            obs_text = "\n".join(f"- {obs}" for obs in observations)
            obs_message = {
                "role": "user",
                "content": f"## 前几轮观察结果\n\n{obs_text}\n\n请基于以上观察结果继续决策。如果信息已足够，给出最终答案；如果仍需更多信息，继续 observe。\n\n重要：你的回复必须是 JSON 格式，不要直接输出文本回答。",
            }
            # 插入到 user_input 之前（倒数第二个位置）
            messages.insert(-1, obs_message)

        return messages

    def build_add_skill_messages(
        self,
        skill_md: str,
        tools: list[Tool],
    ) -> list[dict[str, str]]:
        """
        构建 add-skill 分析消息

        Args:
            skill_md: SKILL.md 全文内容
            tools: 已注册工具列表

        Returns:
            完整的消息列表
        """
        if self.prompts:
            system_prompt = self.prompts.render(
                "add_skill",
                tools=tools,
            )
        else:
            tools_info = "\n".join(
                f"- **{t.name}**: {t.description[:120]}"
                for t in tools[:30]
            )
            system_prompt = (
                "你是一个技能分析助手。分析 SKILL.md 的内容，识别技能的工具依赖。\n\n"
                f"## 已注册工具\n{tools_info}\n\n"
                "请返回 JSON 格式的分析结果，包含 name, description, tool_deps, success, failure_reason 字段。"
            )

        return self._build_messages(
            system_prompt=system_prompt,
            user_input=skill_md,
        )

    @staticmethod
    def _get_env_info() -> dict[str, str]:
        """收集当前运行环境信息"""
        info = {
            "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "python_path": sys.executable,
            "venv_path": sys.prefix,
            "uv_version": "unknown",
            "project_dir": str(Path.cwd()),
            "work_dir": str(Path.cwd()),
        }
        try:
            result = subprocess.run(
                ["uv", "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                # uv 0.9.18 (xxx) → 0.9.18
                info["uv_version"] = result.stdout.strip().split()[1] if len(result.stdout.strip().split()) > 1 else result.stdout.strip()
        except Exception:
            pass
        return info

    def _build_messages(
        self,
        system_prompt: str,
        user_input: str,
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        """
        内部方法：构建完整消息列表

        格式：[system_prompt] + [history] + [current_user_input]

        Args:
            system_prompt: 系统提示词
            user_input: 当前用户输入
            history: 会话历史（可选）

        Returns:
            完整的消息列表
        """
        messages: list[dict[str, str]] = []

        # 1. 系统提示词
        messages.append({"role": "system", "content": system_prompt})

        # 2. 会话历史（使用传入的历史，或内部存储的历史）
        hist = history if history is not None else self._session_history
        if hist:
            messages.extend(hist)

        # 3. 当前用户输入
        messages.append({"role": "user", "content": user_input})

        return messages
