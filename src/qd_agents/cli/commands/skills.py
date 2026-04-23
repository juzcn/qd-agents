"""
Skills 管理命令

负责注册和列出 skills 工具。
"""

import json
import logging
from pathlib import Path
from typing import Optional

import yaml
from rich.console import Console
from rich.table import Table

from qd_agents.config import load_config
from qd_agents.registry import ToolRegistry, Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType


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
        return yaml.safe_load(frontmatter)
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
) -> None:
    """
    添加 skill 工具

    读取 tools/skills/<skill_name>/SKILL.md 的 frontmatter，
    将 skill 注册到工具注册中心。

    Args:
        console: Rich 控制台对象
        skill_name: skill 目录名（tools/skills/ 下的文件夹名）
        base_dir: 基础目录
        config_file: 配置文件路径
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
    metadata_raw = meta.get("metadata", {})

    # 提取环境变量需求
    openclaw = metadata_raw.get("openclaw", {}) if isinstance(metadata_raw, dict) else {}
    requires = openclaw.get("requires", {}) if isinstance(openclaw, dict) else {}
    env_vars = requires.get("env", []) if isinstance(requires, dict) else []
    bins = requires.get("bins", []) if isinstance(requires, dict) else []

    # 构建 env 字典（标记所需的环境变量）
    env: dict[str, str] = {}
    for var in env_vars:
        env[var] = ""  # 占位，实际值由运行时环境提供

    # 查找 scripts 目录下的脚本
    scripts_dir = skill_dir / "scripts"
    script_files = list(scripts_dir.glob("*.py")) if scripts_dir.exists() else []
    # 使用第一个 .py 脚本作为主入口
    main_script = script_files[0].relative_to(skill_dir.parent.parent) if script_files else None

    # 构建 shell_command
    if main_script:
        shell_command = f"python3 {main_script} '{{arguments}}'"
    else:
        shell_command = None

    # 构建 parameters schema
    parameters = {
        "type": "object",
        "properties": {
            "arguments": {"type": "string", "description": "JSON 格式的工具参数"},
        },
        "required": ["arguments"],
    }

    # 加载配置并注册
    config = load_config(base_dir=base_dir, config_file=config_file)
    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)

    tool = Tool(
        id=f"skill.{name}",
        name=name,
        description=description,
        parameters=parameters,
        execution=ToolExecutionConfig(
            type=ToolExecutionType.SKILL,
            command=str(main_script) if main_script else None,
            shell_command=shell_command,
            env=env,
        ),
        metadata=ToolMetadata(
            category="skills",
            tags=["skill", name] + ([Path(main_script).stem] if main_script else []),
        ),
    )

    tool_id = registry.register(tool)

    console.print(f"[green][OK][/] 已注册 Skill: {name} ({tool_id})")
    console.print(f"  目录: {skill_dir}")
    if main_script:
        console.print(f"  脚本: {main_script}")
    if env_vars:
        console.print(f"  所需环境变量: {', '.join(env_vars)}")
    if bins:
        console.print(f"  所需命令: {', '.join(bins)}")


def skill_list(
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> None:
    """
    列出已注册的 skill 工具

    Args:
        console: Rich 控制台对象
        base_dir: 基础目录
        config_file: 配置文件路径
    """
    config = load_config(base_dir=base_dir, config_file=config_file)
    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)

    all_tools = registry.list_all()
    skill_tools = [t for t in all_tools if t.execution.type == ToolExecutionType.SKILL]

    if not skill_tools:
        console.print("[yellow][WARN][/] 未找到已注册的 Skill 工具")
        return

    table = Table(title=f"已注册 Skill 工具 ({len(skill_tools)} 个)")
    table.add_column("名称", style="cyan")
    table.add_column("描述", style="dim", max_width=50)
    table.add_column("脚本", style="green")
    table.add_column("环境变量", style="magenta")
    table.add_column("ID", style="dim")

    for tool in skill_tools:
        env_str = ", ".join(k for k in tool.execution.env if k) or "-"
        script = tool.execution.command or "-"
        table.add_row(tool.name, tool.description, script, env_str, tool.id)

    console.print(table)
