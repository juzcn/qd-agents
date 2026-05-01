"""SKILL.md YAML frontmatter 解析"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_skill_md(skill_dir: Path) -> dict | None:
    """解析 SKILL.md 的 YAML frontmatter

    Args:
        skill_dir: Skill 目录路径

    Returns:
        包含 frontmatter 元数据和 _skill_body 的字典，解析失败返回 None
    """
    import yaml

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None

    content = skill_md.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return None

    end = content.find("---", 3)
    if end == -1:
        return None

    frontmatter = content[3:end].strip()
    try:
        meta = yaml.safe_load(frontmatter)
        meta["_skill_body"] = content[end + 3:].strip()
        return meta
    except yaml.YAMLError as e:
        logger.warning("Failed to parse SKILL.md frontmatter in %s: %s", skill_dir, e)
        return None
