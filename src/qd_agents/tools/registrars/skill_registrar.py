"""Skill 工具注册"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from qd_agents.config.loader import load_config
from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolExecutionType, ToolMetadata
from qd_agents.tools.env import resolve_env_vars_noninteractive
from qd_agents.tools.skill_parsing import parse_skill_md
from qd_agents.tools.llm_helpers import run_add_skill_analyzer
from qd_agents.tools.errors import ToolNotFoundError, ToolValidationError
from qd_agents.tools.registrars.base import save_tool

logger = logging.getLogger(__name__)


def register_skill_tool(
    skill_name: str,
    *,
    extra_env: list[str] | None = None,
    default: bool = False,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> Tool:
    """注册 Skill 工具（纯逻辑）。

    Args:
        skill_name: skill 目录名（在 tools/skills/ 下）
        extra_env: 额外环境变量名列表
        default: 是否为默认工具

    Returns:
        注册后的 Tool 对象
    """
    skills_dir = Path("tools/skills")
    skill_dir = skills_dir / skill_name

    if not skill_dir.exists():
        raise ToolNotFoundError(f"Skill 目录不存在: {skill_dir}")

    # 解析 SKILL.md
    meta = parse_skill_md(skill_dir)
    if meta is None:
        raise ToolValidationError(f"Skill 目录中未找到有效的 SKILL.md: {skill_dir}")

    name = meta.get("name", skill_name)
    description = meta.get("description", f"Skill: {skill_name}")

    # 提取版本号
    skill_version = meta.get("version")
    if not skill_version:
        version_match = re.search(r"-(\d+\.\d+(?:\.\d+)?)$", skill_name)
        if version_match:
            skill_version = version_match.group(1)

    # 环境变量
    metadata_raw = meta.get("metadata", {})
    openclaw = metadata_raw.get("openclaw", {}) if isinstance(metadata_raw, dict) else {}
    requires = openclaw.get("requires", {}) if isinstance(openclaw, dict) else {}
    env_vars = requires.get("env", []) if isinstance(requires, dict) else []

    if extra_env:
        env_vars = list(dict.fromkeys(extra_env + env_vars))

    env: dict[str, str] = {}
    if env_vars:
        env = resolve_env_vars_noninteractive(env_vars, base_dir)

    # 运行 AddSkillAnalyzer（如果可用）
    tool_deps: list[str] = []
    skill_type = "tool_manual"
    config = load_config(base_dir=base_dir, config_file=config_file)
    add_skill_result = run_add_skill_analyzer(skill_dir, config, base_dir)
    if add_skill_result is not None and add_skill_result.success:
        tool_deps = add_skill_result.tool_deps
        skill_type = add_skill_result.skill_type

    tool = Tool(
        id=f"skill.{name}",
        name=name,
        description=description,
        parameters={"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.SKILL,
            env=env,
        ),
        scope="default" if default else "user",
        metadata=ToolMetadata(
            tags=["skill", name],
            version=skill_version,
        ),
        dependencies={"skill_type": skill_type, "tool_deps": tool_deps},
        source_path=skill_name,
        local_path=skill_name,
    )

    return save_tool(tool, base_dir, config_file)


def extract_registration_args(tool: Tool) -> dict:
    """从已注册的 Tool 提取重注册所需的参数"""
    skill_name = tool.source_path or tool.local_path or tool.name
    env_names = list(tool.execution.env.keys()) if tool.execution.env else None
    return {
        "skill_name": skill_name,
        "extra_env": env_names,
        "default": tool.scope == "default",
    }