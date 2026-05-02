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

# 工具类型显示顺序和标签
_TYPE_ORDER = [
    (ToolExecutionType.SKILL, "skill"),
    (ToolExecutionType.MCP, "mcp"),
    (ToolExecutionType.CLI, "cli"),
    (ToolExecutionType.HTTP, "http"),
    (ToolExecutionType.BASH, "bash"),
    (ToolExecutionType.FUNCTION, "function"),
]

# scope 显示顺序
_SCOPE_ORDER = {"builtin": 0, "default": 1, "user": 2}


def _group_tools_by_type(tools: list[Tool]) -> dict[str, Any]:
    """将工具列表按 scope→type 分组，MCP 工具按 server 嵌套子工具。

    排序规则：先按 scope (builtin → default → user)，再按 type (skill → mcp → cli → http → bash → function)。

    Returns:
        {
            "scope_groups": [
                {"scope": "builtin", "non_mcp_types": [(label, [Tool, ...]), ...], "mcp_servers": [...]},
                {"scope": "default", ...},
                {"scope": "user", ...},
            ],
            # 兼容旧接口
            "mcp_servers": [...],
            "non_mcp_types": [...],
        }
    """
    # 按 scope 分桶
    by_scope: dict[str, list[Tool]] = {}
    for tool in tools:
        by_scope.setdefault(tool.scope, []).append(tool)

    scope_groups = []
    all_mcp_servers = []
    all_non_mcp_types = []

    for scope in sorted(by_scope, key=lambda s: _SCOPE_ORDER.get(s, 99)):
        scope_tools = by_scope[scope]
        by_type: dict[ToolExecutionType, list[Tool]] = {}
        mcp_servers: dict[str, list[Tool]] = {}

        for tool in scope_tools:
            t = tool.execution.type
            if t == ToolExecutionType.MCP:
                server = tool.execution.server or "unknown"
                mcp_servers.setdefault(server, []).append(tool)
            else:
                by_type.setdefault(t, []).append(tool)

        # MCP 服务器列表
        mcp_server_list = []
        for server_name, subtools in mcp_servers.items():
            mcp_server_list.append({
                "server_name": server_name,
                "server_desc": f"MCP server: {server_name}",
                "subtools": subtools,
            })

        # non-MCP 类型列表（按预定义顺序）
        non_mcp_types = []
        for exec_type, label in _TYPE_ORDER:
            if exec_type != ToolExecutionType.MCP and exec_type in by_type:
                non_mcp_types.append((label, by_type[exec_type]))

        scope_groups.append({
            "scope": scope,
            "non_mcp_types": non_mcp_types,
            "mcp_servers": mcp_server_list,
        })
        all_mcp_servers.extend(mcp_server_list)
        all_non_mcp_types.extend(non_mcp_types)

    return {
        "scope_groups": scope_groups,
        "mcp_servers": all_mcp_servers,
        "non_mcp_types": all_non_mcp_types,
    }


_SCOPE_LABELS = {"builtin": "内置", "default": "默认", "user": "用户安装"}


def format_tools_markdown(tools: list[Tool], *, show_type_tag: bool = True) -> str:
    """将工具列表渲染为 markdown，按 scope→type 排序。

    Args:
        tools: 工具列表
        show_type_tag: 是否在每行前显示 [type] 标签（chat 需要，add_skill 不需要）

    Returns:
        渲染后的 markdown 字符串
    """
    groups = _group_tools_by_type(tools)
    lines: list[str] = []

    for sg in groups["scope_groups"]:
        scope_label = _SCOPE_LABELS.get(sg["scope"], sg["scope"])
        lines.append(f"### {scope_label}")
        for type_label, type_tools in sg["non_mcp_types"]:
            for t in type_tools:
                if show_type_tag:
                    lines.append(f"- [{type_label}] **{t.name}**: {t.description}")
                else:
                    lines.append(f"- **{t.name}**: {t.description}")
        for server_info in sg["mcp_servers"]:
            lines.append(f"#### {server_info['server_name']} ({server_info['server_desc']})")
            for t in server_info["subtools"]:
                if show_type_tag:
                    lines.append(f"- [mcp] **{t.name}**: {t.description}")
                else:
                    lines.append(f"- **{t.name}**: {t.description}")

    return "\n".join(lines)


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
        self._chat_cache: dict[tuple, str] = {}     # 缓存进化判断提示词
        self._use_tool_cache: dict[tuple, str] = {}   # 缓存 use-tool 提示词
        self._find_tools_cache: dict[tuple, str] = {}  # 缓存 find-tools 提示词
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

    def build_chat_messages(
        self,
        user_input: str,
        tools: list[Tool],
        history: list[dict[str, str]] | None = None,
        observations: list[str] | None = None,
    ) -> list[dict[str, str]]:
        """
        构建自主进化决策消息

        初始只列出所有工具的 name+description（渐进式披露）。
        SKILL 工具的 SKILL.md 在 LLM 选择使用时由 ChatAgent 动态注入。

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
        if cache_key in self._chat_cache:
            system_prompt = self._chat_cache[cache_key]
            logger.debug("Using cached system prompt for chat")
        else:
            if self.prompts:
                system_prompt = self.prompts.render(
                    "chat",
                    tools=tools,
                    tools_section=format_tools_markdown(tools),
                    observations=[],
                    env_info=self._get_env_info(),
                )
            else:
                # 回退到硬编码
                env_info = self._get_env_info()
                tools_section = format_tools_markdown(tools)
                tools_info = "\n".join(tools_lines)
                system_prompt = f"""你是一个智能路由决策器。分析用户请求，决定如何处理。

## 运行环境

- **操作系统**：{env_info['os']}
- **Python**：{env_info['python_version']}（{env_info['python_path']}）
- **虚拟环境**：{env_info['venv_path']}
- **包管理器**：uv {env_info['uv_version']}
- **项目根目录**：{env_info['project_dir']}
- **工作目录**：{env_info['work_dir']}

## 路由决策规则

根据用户请求和可用工具，选择最合适的路由：

1. **direct-answer**：简单问题、问候语、常识性问题、不需要工具就能回答的问题
2. **use-tool**：当前工具箱中有合适的工具可以完成任务。你需要列出需要使用的工具名和编排逻辑
3. **find-tools**：任务需要工具，但当前工具箱中缺少必要的工具。你需要描述需要什么能力
4. **ask_user**：信息不足，需要向用户提问才能继续
5. **delegate**：任务需要用户亲自执行（如涉及敏感操作、需要人工判断等）

## 工具箱管理（自主进化）

成功使用新工具后，通过 `tool_register_*` 系列函数注册到工具箱以便复用——这是你"进化"的核心能力，工具越用越多，能力越来越强。

安装新工具：`uv add <package>` 安装到虚拟环境，`uvx <tool>` 临时运行。

## 可用工具

{tools_section or '暂无'}

## 输出格式

你必须输出一个 JSON 对象，格式如下：

```json
{{
  "route": "direct-answer | use-tool | find-tools | ask_user | delegate",
  "task_background": "任务的背景上下文",
  "task_description": "任务的具体描述",
  "tool_list": ["tool_name_1", "tool_name_2"],
  "orchestration_logic": "工具使用的编排逻辑描述",
  "direct_answer": "直接回答的内容（仅 direct-answer 路由）",
  "ask_user": {{ "question": "...", "options": ["..."], "reason": "..." }},
  "delegate": {{ "task": "...", "guide": "...", "reason": "..." }},
  "reflection": "对当前决策的反思"
}}
```

**重要**：只输出 JSON，不要输出其他内容。确保 JSON 格式正确。"""

            self._chat_cache[cache_key] = system_prompt
            logger.debug(f"Cached system prompt for chat with {len(tools)} tools")

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
                "content": f"## 前几轮观察结果\n\n{obs_text}\n\n请基于以上观察结果继续决策。如果信息已足够，给出最终答案；如果仍需更多信息，继续调用工具。",
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
                tools_section=format_tools_markdown(tools, show_type_tag=False),
            )
        else:
            tools_section = format_tools_markdown(tools, show_type_tag=False)
            system_prompt = (
                "你是一个技能分析助手。分析 SKILL.md 的内容，识别技能的工具依赖。\n\n"
                f"## 已注册工具\n{tools_section}\n\n"
                "请返回 JSON 格式的分析结果，包含 name, description, tool_deps, success, failure_reason 字段。"
            )

        return self._build_messages(
            system_prompt=system_prompt,
            user_input=skill_md,
        )

    def build_use_tool_messages(
        self,
        job: Any,
        tools: list[Tool],
    ) -> list[dict[str, str]]:
        """构建 Use-Tool 子循环消息

        Args:
            job: Job 对象（含 task_background, task_description, orchestration_logic）
            tools: 本次任务需要的工具列表（带完整 schema）

        Returns:
            完整的消息列表
        """
        env_info = self._get_env_info()
        tool_groups = _group_tools_by_type(tools)

        # 收集 prompt 类型 SKILL 的 SKILL.md，预注入系统提示词
        skill_prompts: dict[str, str] = {}
        for t in tools:
            if t.execution.type == ToolExecutionType.SKILL:
                skill_type = t.dependencies.get("skill_type", "tool_manual")
                if skill_type == "prompt":
                    skill_md = self._load_skill_md(t.local_path or t.name) or ""
                    if skill_md:
                        skill_prompts[t.name] = skill_md

        # 构建缓存键
        tool_ids = tuple(sorted(t.id for t in tools))
        skill_keys = tuple(sorted(skill_prompts.keys()))
        cache_key = (tool_ids, skill_keys, job.task_description[:100])

        if cache_key in self._use_tool_cache:
            system_prompt = self._use_tool_cache[cache_key]
            logger.debug("Using cached system prompt for use-tool")
        else:
            if self.prompts:
                system_prompt = self.prompts.render(
                    "use_tool",
                    task_background=job.task_background,
                    task_description=job.task_description,
                    orchestration_logic=job.orchestration_logic,
                    tools=tools,
                    tool_groups=tool_groups,
                    skill_prompts=skill_prompts,
                    env_info=env_info,
                )
            else:
                # 回退到硬编码
                tools_lines = []
                for type_label, type_tools in tool_groups["non_mcp_types"]:
                    for t in type_tools:
                        tools_lines.append(f"- [{type_label}] **{t.name}**: {t.description}")
                for server_info in tool_groups["mcp_servers"]:
                    tools_lines.append(f"#### {server_info['server_name']} ({server_info['server_desc']})")
                    for t in server_info["subtools"]:
                        tools_lines.append(f"- [mcp] **{t.name}**: {t.description}")
                tools_info = "\n".join(tools_lines)

                skill_section = ""
                if skill_prompts:
                    parts = []
                    for name, md in skill_prompts.items():
                        parts.append(f"### {name}\n\n{md}")
                    skill_section = "\n## 技能指南\n\n" + "\n\n".join(parts)

                system_prompt = (
                    f"你是一个工具执行专家。根据任务描述和编排逻辑，使用提供的工具完成任务。\n\n"
                    f"## 任务背景\n\n{job.task_background}\n\n"
                    f"## 任务描述\n\n{job.task_description}\n\n"
                    f"## 编排逻辑\n\n{job.orchestration_logic}\n\n"
                    f"## 可用工具\n\n{tools_info or '暂无'}\n"
                    f"{skill_section}\n\n"
                    "## 执行规则\n\n"
                    "1. 按编排逻辑执行\n2. 观察后决策\n3. 完成后给出最终答案\n"
                )

            self._use_tool_cache[cache_key] = system_prompt
            logger.debug("Cached system prompt for use-tool with %d tools", len(tools))

        # 子循环独立，无会话历史
        messages: list[dict[str, str]] = []
        messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": job.task_description})

        return messages

    def build_find_tools_messages(
        self,
        job: Any,
        builtin_tools: list[Tool],
    ) -> list[dict[str, str]]:
        """构建 Find-Tools 子循环消息

        Args:
            job: Job 对象（含 task_background, task_description）
            builtin_tools: 当前工具箱中所有工具（用于展示已有工具，避免重复注册）

        Returns:
            完整的消息列表
        """
        env_info = self._get_env_info()
        builtin_tool_groups = _group_tools_by_type(builtin_tools)

        # 构建缓存键
        tool_ids = tuple(sorted(t.id for t in builtin_tools))
        cache_key = (tool_ids, job.task_description[:100])

        if cache_key in self._find_tools_cache:
            system_prompt = self._find_tools_cache[cache_key]
            logger.debug("Using cached system prompt for find-tools")
        else:
            if self.prompts:
                system_prompt = self.prompts.render(
                    "find_tools",
                    task_background=job.task_background,
                    task_description=job.task_description,
                    builtin_tool_groups=builtin_tool_groups,
                    env_info=env_info,
                )
            else:
                # 回退到硬编码
                tools_lines = []
                for type_label, type_tools in builtin_tool_groups["non_mcp_types"]:
                    for t in type_tools:
                        tools_lines.append(f"- [{type_label}] **{t.name}**: {t.description}")
                for server_info in builtin_tool_groups["mcp_servers"]:
                    tools_lines.append(f"#### {server_info['server_name']} ({server_info['server_desc']})")
                    for t in server_info["subtools"]:
                        tools_lines.append(f"- [mcp] **{t.name}**: {t.description}")
                tools_info = "\n".join(tools_lines)

                system_prompt = (
                    f"你是一个工具发现专家。根据任务需求，搜索、评估、安装并注册合适的工具。\n\n"
                    f"## 任务背景\n\n{job.task_background}\n\n"
                    f"## 任务描述\n\n{job.task_description}\n\n"
                    f"## 当前工具箱\n\n{tools_info or '暂无'}\n\n"
                    "## 可用操作\n\n"
                    "- serper_search / tavily_search: 搜索网络\n"
                    "- execute_bash: 执行 shell 命令（uv add 安装包）\n"
                    "- tool_register_cli/mcp/skill/http: 注册工具\n\n"
                    "## 输出\n\n"
                    "完成工具发现和注册后，列出所有新注册的工具名，格式：已注册工具: tool1, tool2, tool3"
                )

            self._find_tools_cache[cache_key] = system_prompt
            logger.debug("Cached system prompt for find-tools with %d builtin tools", len(builtin_tools))

        # 子循环独立，无会话历史
        messages: list[dict[str, str]] = []
        messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": job.task_description})

        return messages

    def build_skill_injected_system_prompt(
        self,
        system_prompt: str,
        skill_tools: list[Tool],
    ) -> str:
        """将 prompt 类型 SKILL 的 SKILL.md 预注入系统提示词

        在 Use-Tool 循环开始前调用，将所有 prompt 类型的 SKILL 指南
        直接注入系统提示词，避免循环中动态注入的复杂性。

        Args:
            system_prompt: 原始系统提示词
            skill_tools: SKILL 类型的工具列表

        Returns:
            注入后的系统提示词
        """
        for t in skill_tools:
            skill_type = t.dependencies.get("skill_type", "tool_manual")
            if skill_type == "prompt":
                skill_md = self._load_skill_md(t.local_path or t.name) or ""
                if skill_md:
                    system_prompt += f"\n\n## 技能指南: {t.name}\n\n{skill_md}"
                    logger.info("Pre-injected SKILL.md for %s into system prompt (%d chars)", t.name, len(skill_md))

        return system_prompt

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
