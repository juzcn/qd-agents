"""
Skills 管理命令

负责注册和列出 skills 工具。
所有 skill 统一注册为 ToolExecutionType.SKILL 类型的工具：
- 有 Python 脚本的 skill：可被 LLM 调用执行
- 无 Python 脚本的 skill：SKILL.md 注入 system prompt，LLM 按指南操作
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

import yaml
from rich.console import Console

from qd_agents.config import load_config
from qd_agents.cli.managers import setup_configuration
from qd_agents.cli.utils.registry import get_tool_registry
from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType
from qd_agents.cli.utils.credentials import env_var_to_tool_name, resolve_env_vars
from qd_agents.cli.utils.registration import register_tool_and_report


logger = logging.getLogger(__name__)

SKILLS_DIR_NAME = "skills"


def _parse_skill_md(skill_dir: Path) -> dict | None:
    """解析 SKILL.md 的 YAML frontmatter，返回元数据字典。"""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None

    content = skill_md.read_text(encoding="utf-8")

    # 提取 --- 之间的 frontmatter
    if not content.startswith("---"):
        return None

    end = content.find("---", 3)
    if end == -1:
        return None

    frontmatter = content[3:end].strip()
    try:
        meta = yaml.safe_load(frontmatter)
        # 附加正文内容
        meta["_skill_body"] = content[end + 3:].strip()
        return meta
    except yaml.YAMLError as e:
        logger.warning("Failed to parse SKILL.md frontmatter in %s: %s", skill_dir, e)
        return None


def _get_skills_dir(base_dir: Optional[Path] = None) -> Path:
    """获取 skills 目录路径。"""
    if base_dir:
        return base_dir / "tools" / SKILLS_DIR_NAME
    return Path("tools") / SKILLS_DIR_NAME


def skill_add(
    console: Console,
    skill_name: str,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
    extra_env: Optional[list[str]] = None,
    default: bool = False,
    interactive: bool = True,
) -> None:
    """
    添加 skill 工具

    使用 AddSkillAnalyzer（LLM）分析 SKILL.md，识别参数定义和工具依赖。
    所有 skill 统一注册为 ToolExecutionType.SKILL 类型。
    """
    skills_dir = _get_skills_dir(base_dir)
    skill_dir = skills_dir / skill_name

    # 验证 skill 目录存在
    if not skill_dir.exists():
        console.print(f"[red][ERROR][/] Skill 目录不存在: {skill_dir}")
        console.print(f"  可用的 skills:")
        available = [d.name for d in skills_dir.iterdir() if d.is_dir()] if skills_dir.exists() else []
        if available:
            for name in sorted(available):
                console.print(f"    - {name}")
        else:
            console.print("    (无)")
        return

    # 解析 SKILL.md
    meta = _parse_skill_md(skill_dir)
    if meta is None:
        console.print(f"[red][ERROR][/] Skill 目录中未找到有效的 SKILL.md: {skill_dir}")
        return

    name = meta.get("name", skill_name)
    description = meta.get("description", f"Skill: {skill_name}")

    # 提取版本号：优先从 frontmatter 的 version 字段，其次从目录名（如 name-1.0.0）
    skill_version = meta.get("version")
    if not skill_version:
        version_match = re.search(r"-(\d+\.\d+(?:\.\d+)?)$", skill_name)
        if version_match:
            skill_version = version_match.group(1)

    # 加载配置并设置会话日志
    config = setup_configuration(console, base_dir=base_dir, config_file=config_file)

    # --- 使用 AddSkillAnalyzer 分析 SKILL.md ---
    console.print(f"  [dim]正在分析 SKILL.md（识别参数和工具依赖）...[/]")
    add_skill_result = _run_add_skill_analyzer(skill_dir, config, base_dir, console)

    if add_skill_result is None:
        console.print(f"  [yellow]AddSkillAnalyzer 不可用，使用默认依赖[/]")
        tool_deps = []
        skill_type = "tool_manual"
    elif not add_skill_result.success:
        console.print(f"[red][ERROR][/] AddSkill 分析失败: {add_skill_result.failure_reason}[/]")
        return
    else:
        tool_deps = add_skill_result.tool_deps
        skill_type = add_skill_result.skill_type
        console.print(f"  [green]AddSkill 分析完成[/]")
        console.print(f"    技能类型: {skill_type}")
        console.print(f"    工具依赖: {', '.join(tool_deps) if tool_deps else '无'}")

    # 处理 API key
    metadata_raw = meta.get("metadata", {})
    openclaw = metadata_raw.get("openclaw", {}) if isinstance(metadata_raw, dict) else {}
    requires = openclaw.get("requires", {}) if isinstance(openclaw, dict) else {}
    env_vars = requires.get("env", []) if isinstance(requires, dict) else []

    # 合并 --env 参数（变量名）
    if extra_env:
        env_vars = list(dict.fromkeys(extra_env + env_vars))

    env: dict[str, str] = {}
    if env_vars:
        env, _ = resolve_env_vars(env_vars, console, base_dir=base_dir, interactive=interactive)

    # 注册工具到数据库
    registry = get_tool_registry(config)

    tool = Tool(
        id=f"skill.{name}",
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.SKILL,
            env=env,
        ),
        scope="default" if default else "user",
        metadata=ToolMetadata(
            tags=["skill", name],
            version=skill_version,
        ),
        dependencies={
            "skill_type": skill_type,
            "tool_deps": tool_deps,
        },
        source_path=skill_name,
        local_path=skill_name,
    )

    tool_id = registry.register(tool)

    console.print(f"[green][OK][/] 已注册 Skill Tool: {name} ({tool_id})")
    console.print(f"  目录: {skill_dir}")
    if env_vars:
        console.print(f"  所需环境变量: {', '.join(env_vars)}")


def _run_add_skill_analyzer(
    skill_dir: Path,
    config: Any,
    base_dir: Optional[Path] = None,
    console: Console | None = None,
) -> Any:
    """运行 AddSkillAnalyzer 分析 SKILL.md，返回 AddSkillResult 或 None

    像 chat 一样完整初始化：启动 MCP server、创建 LLMClient、运行分析器、输出日志、关闭资源。
    """
    import asyncio

    try:
        from qd_agents.agent.add_skill import AddSkillAnalyzer
        from qd_agents.cli.managers import LLMClientManager
        from qd_agents.context import ContextManager
        from qd_agents.prompts import PromptLoader
    except ImportError as e:
        logger.warning("AddSkillAnalyzer 依赖不可用: %s", e)
        return None

    async def _run():
        # 1. 读取 SKILL.md 全文
        skill_md_path = skill_dir / "SKILL.md"
        skill_md_content = skill_md_path.read_text(encoding="utf-8")

        # 2. 初始化工具注册表和上下文
        tool_registry = get_tool_registry(config)

        prompt_loader = None
        if config.prompts and config.prompts.template_dir:
            prompt_loader = PromptLoader(template_dir=Path(config.prompts.template_dir))

        context_manager = ContextManager(prompt_loader=prompt_loader, base_dir=base_dir)

        # 3. 初始化 LLM 客户端和 QDAgent（启动 MCP server）
        provider_name = config.llm.default_provider
        _console = console or Console()
        llm_manager = LLMClientManager(_console, config, tool_registry, prompt_loader, context_manager)
        if not await llm_manager.initialize(provider_name):
            logger.warning("LLMClientManager 初始化失败")
            return None

        try:
            # 4. 收集所有已注册工具（含 MCP subtools）
            all_tools = tool_registry.list_all()

            # 5. 创建并运行 AddSkillAnalyzer
            analyzer = AddSkillAnalyzer(
                llm_client=llm_manager.llm_client,
                context_manager=context_manager,
            )

            result = await analyzer.analyze(skill_md=skill_md_content, tools=all_tools)
            return result

        finally:
            # 6. 关闭资源（MCP server 等）
            await llm_manager.close()

    try:
        return asyncio.run(_run())
    except Exception as e:
        logger.warning("AddSkillAnalyzer 执行失败: %s", e)
        return None
