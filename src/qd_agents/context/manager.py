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
from pathlib import Path
from typing import Any

from ..prompts import PromptLoader
from ..registry import Tool, ToolExecutionType


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
        self._tool_use_cache: dict[tuple, str] = {}    # 缓存工具调用提示词
        self._judge_cache: dict[tuple, str] = {}       # 缓存判断提示词
        self._coding_cache: dict[tuple, str] = {}      # 缓存代码生成提示词
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

    def _build_skill_injection(self, tools: list[Tool]) -> str:
        """
        为 SKILL 类型的工具构建 SKILL.md 注入文本（按需注入）。

        仅注入 judge 选中的 SKILL 工具对应的 SKILL.md，
        统一通过 tools 列表过滤，不区分有脚本/无脚本。

        Args:
            tools: 当前请求选中的工具列表（由 judge 过滤后）

        Returns:
            注入到 system prompt 的文本，无 SKILL 工具时返回空字符串
        """
        skill_sections = []

        for tool in tools:
            if tool.execution.type != ToolExecutionType.SKILL:
                continue

            skill_dir_name = tool.dependencies.get("skill_dir_name", tool.name)
            skill_body = self._load_skill_md(skill_dir_name)
            if skill_body:
                skill_sections.append(
                    f"## 技能指南: {tool.name}\n\n{skill_body}"
                )

        if not skill_sections:
            return ""

        return "\n\n---\n\n" + "\n\n".join(skill_sections)



    def build_tool_use_messages(
        self,
        user_input: str,
        tools: list[Tool],
        search_web_available: bool = False,
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        """
        构建工具调用消息（优化的单阶段提示词）

        对于 SKILL 类型的工具，会自动注入对应的 SKILL.md 正文到系统提示词。

        Args:
            user_input: 当前用户输入
            tools: 可用工具列表
            search_web_available: search.web 工具是否可用
            history: 会话历史（如果不传则使用内部存储的历史）

        Returns:
            完整的消息列表
        """
        # 构建缓存键：使用工具ID列表和search_web_available
        tool_ids = tuple(sorted(tool.id for tool in tools))
        cache_key = (tool_ids, search_web_available)

        # 检查缓存
        if cache_key in self._tool_use_cache:
            system_prompt = self._tool_use_cache[cache_key]
            logger.debug("Using cached system prompt for tool_use")
        else:
            if self.prompts:
                system_prompt = self.prompts.render(
                    "tool_use",
                    tools=tools,
                    search_web_available=search_web_available,
                )
            else:
                # 回退到硬编码
                if search_web_available:
                    system_prompt = (
                        "你是一个智能助手，可以调用工具帮助用户。\n"
                        "如果用户的问题需要实时信息或外部知识，请优先使用 search.web 工具进行网络搜索。\n"
                        "注意：我们是在Windows环境下工作。"
                    )
                else:
                    system_prompt = "你是一个智能助手，可以调用工具帮助用户。\n注意：我们是在Windows环境下工作。"

            # 缓存结果
            self._tool_use_cache[cache_key] = system_prompt
            logger.debug(f"Cached system prompt for {len(tools)} tools, search_web_available={search_web_available}")

        # 为 SKILL 类型工具注入 SKILL.md 正文
        skill_injection = self._build_skill_injection(tools)
        if skill_injection:
            system_prompt = system_prompt + skill_injection

        return self._build_messages(
            system_prompt=system_prompt,
            user_input=user_input,
            history=history,
        )

    def build_judge_messages(
        self,
        user_input: str,
        tools: list[Tool],
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        """
        构建路由判断消息

        Args:
            user_input: 当前用户输入
            tools: 可用工具列表
            history: 会话历史

        Returns:
            完整的消息列表
        """
        # 构建缓存键
        tool_ids = tuple(sorted(tool.id for tool in tools))
        cache_key = tool_ids

        # 检查缓存
        if cache_key in self._judge_cache:
            system_prompt = self._judge_cache[cache_key]
            logger.debug("Using cached system prompt for judge")
        else:
            if self.prompts:
                system_prompt = self.prompts.render(
                    "judge",
                    tools=tools,
                )
            else:
                # 回退到硬编码
                tools_info = "\n".join(
                    f"- {getattr(t, 'name', str(t))}: {getattr(t, 'description', '')[:100]}"
                    for t in tools[:20]
                )
                system_prompt = f"""你是一个路由判断助手。分析用户的问题，决定应该由哪个路径处理。

可用工具:
{tools_info or '暂无'}

路由选项:
1. direct - 直接回答（基于知识，不需要工具）
2. tool_use - 简单工具调用（1-3个工具）
3. coding - 复杂工具编排（需要条件判断、循环等）

返回JSON: {{"route": "direct|tool_use|coding", "reasoning": "...", "direct_answer": "..."}}"""

            self._judge_cache[cache_key] = system_prompt
            logger.debug(f"Cached system prompt for judge with {len(tools)} tools")

        return self._build_messages(
            system_prompt=system_prompt,
            user_input=user_input,
            history=history,
        )

    def build_coding_messages(
        self,
        user_input: str,
        tools: list[Tool],
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        """
        构建代码生成消息

        Args:
            user_input: 当前用户输入
            tools: 可用工具列表
            history: 会话历史

        Returns:
            完整的消息列表
        """
        # 构建缓存键
        tool_ids = tuple(sorted(tool.id for tool in tools))
        cache_key = tool_ids

        # 检查缓存
        if cache_key in self._coding_cache:
            system_prompt = self._coding_cache[cache_key]
            logger.debug("Using cached system prompt for coding")
        else:
            if self.prompts:
                system_prompt = self.prompts.render(
                    "coding",
                    tools=tools,
                )
            else:
                # 回退到硬编码
                tools_info = "\n".join(
                    f"- {getattr(t, 'name', str(t))}: {getattr(t, 'description', '')}"
                    for t in tools
                )
                system_prompt = f"""你是一个代码生成助手。根据用户的需求，生成Python代码来编排工具调用。

可用工具函数:
{tools_info or '暂无'}

要求:
1. 使用 await 调用异步工具
2. 结果赋值给 result 变量
3. 只使用列出的工具"""

            self._coding_cache[cache_key] = system_prompt
            logger.debug(f"Cached system prompt for coding with {len(tools)} tools")

        return self._build_messages(
            system_prompt=system_prompt,
            user_input=user_input,
            history=history,
        )

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
                "你是一个技能分析助手。分析 SKILL.md 的内容，识别技能的参数定义和工具依赖。\n\n"
                f"## 已注册工具\n{tools_info}\n\n"
                "请返回 JSON 格式的分析结果，包含 name, description, parameters, tool_deps, success, failure_reason 字段。"
            )

        return self._build_messages(
            system_prompt=system_prompt,
            user_input=skill_md,
        )

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
