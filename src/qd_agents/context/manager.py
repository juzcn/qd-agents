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


def format_tools_markdown(tools: list[Tool], *, show_type_tag: bool = True, detail: bool = False) -> str:
    """将工具列表渲染为 markdown，按 scope→type 排序。

    Args:
        tools: 工具列表
        show_type_tag: 是否在每行前显示 [type] 标签（chat 需要，add_skill 不需要）
        detail: 是否显示工具参数详情（子 Agent 需要，Evolve Agent 不需要）

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
                if detail:
                    lines.append(_format_tool_params(t))
        for server_info in sg["mcp_servers"]:
            lines.append(f"#### {server_info['server_name']} ({server_info['server_desc']})")
            for t in server_info["subtools"]:
                if show_type_tag:
                    lines.append(f"- [mcp] **{t.name}**: {t.description}")
                else:
                    lines.append(f"- **{t.name}**: {t.description}")
                if detail:
                    lines.append(_format_tool_params(t))

    return "\n".join(lines)


def _format_tool_params(tool: Tool) -> str:
    """格式化单个工具的参数详情，直接输出 JSON schema"""
    import json

    params = tool.parameters or {}
    props = params.get("properties", {})
    required = params.get("required", [])
    if not props:
        return "  - 参数: 无"

    # MCP 壳工具只有 tool_name + arguments 路由参数，不展示
    if tool.execution.type == ToolExecutionType.MCP and set(props.keys()) == {"tool_name", "arguments"}:
        return "  - 参数: 无（MCP 壳工具，通过 subtool 调用）"

    # 直接输出每个参数的 JSON schema 片段
    parts = []
    for pname, pdef in props.items():
        req_flag = "必填" if pname in required else "可选"
        parts.append(f"  - `{pname}` ({req_flag}): {json.dumps(pdef, ensure_ascii=False)}")
    return "\n".join(parts)


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
        task_background: str = "",
        task_requirements: str = "",
        tool_list: list[str] | None = None,
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
            if not self.prompts:
                raise RuntimeError("PromptLoader 未初始化，无法渲染 evolve 模板")
            # tool_list 指定需要渲染 detail schema 的工具名，默认只渲染 delegate
            detail_names = set(tool_list) if tool_list else {"delegate"}
            detail_tools = [t for t in tools if t.name in detail_names]
            system_prompt = self.prompts.render(
                "evolve",
                tools=tools,
                tools_section=format_tools_markdown(tools),
                tools_detail_section=format_tools_markdown(detail_tools, detail=True),
                observations=[],
                env_info=self._get_env_info(),
                task_background=task_background,
                task_requirements=task_requirements,
            )

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
        if not self.prompts:
            raise RuntimeError("PromptLoader 未初始化，无法渲染 add_skill 模板")
        system_prompt = self.prompts.render(
            "add_skill",
            tools=tools,
            tools_section=format_tools_markdown(tools, show_type_tag=False),
        )

        return self._build_messages(
            system_prompt=system_prompt,
            user_input=skill_md,
        )

    def build_use_tool_task_message(
        self,
        *,
        task_background: str = "",
        task_description: str = "",
        orchestration_logic: str = "",
        tools: list[Tool] | None = None,
        tools_detail_section: str = "",
    ) -> str:
        """构建 Use-Tool 子循环的 task message 内容

        Args:
            task_background: 任务背景上下文
            task_description: 任务具体描述
            orchestration_logic: 工具编排逻辑描述
            tools: 任务可用的工具列表
            tools_detail_section: 工具详情（含 SKILL.md），由调用方构建

        Returns:
            task message 内容字符串
        """
        if not self.prompts:
            raise RuntimeError("PromptLoader 未初始化，无法渲染 use_tool 模板")
        return self.prompts.render(
            "use_tool",
            task_background=task_background,
            task_description=task_description,
            orchestration_logic=orchestration_logic,
            tools=tools or [],
            tools_detail_section=tools_detail_section,
            env_info=self._get_env_info(),
        )

    def build_find_tools_task_message(
        self,
        *,
        task_background: str = "",
        task_description: str = "",
        builtin_tools: list[Tool],
    ) -> str:
        """构建 Find-Tools 子循环的 task message 内容

        Args:
            task_background: 任务背景上下文
            task_description: 任务具体描述
            builtin_tools: 当前工具箱中所有工具

        Returns:
            task message 内容字符串
        """
        env_info = self._get_env_info()
        builtin_tool_groups = _group_tools_by_type(builtin_tools)

        if not self.prompts:
            raise RuntimeError("PromptLoader 未初始化，无法渲染 find_tools 模板")

        return self.prompts.render(
            "find_tools",
            task_background=task_background,
            task_description=task_description,
            builtin_tool_groups=builtin_tool_groups,
            tools_detail_section=format_tools_markdown(builtin_tools, detail=True),
            env_info=env_info,
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
