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
import sys
from pathlib import Path
from typing import Any, Optional

import yaml
from rich.console import Console

from qd_agents.config import load_config, load_runtime_config, save_runtime_config
from qd_agents.cli.managers import setup_configuration
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
        meta = yaml.safe_load(frontmatter)
        # 附加正文内容
        meta["_skill_body"] = content[end + 3:].strip()
        return meta
    except yaml.YAMLError as e:
        logger.warning("Failed to parse SKILL.md frontmatter in %s: %s", skill_dir, e)
        return None


def _has_python_scripts(skill_dir: Path) -> bool:
    """检查 skill 目录是否有 Python 脚本。"""
    scripts_dir = skill_dir / "scripts"
    return scripts_dir.exists() and bool(list(scripts_dir.glob("*.py")))


def _extract_parameters_from_md(skill_body: str) -> dict | None:
    """从 SKILL.md 正文的 Request Parameters 表格提取 JSON Schema。

    解析 markdown 表格中的参数定义，构建 OpenAI function calling 格式的 parameters schema。
    """
    if not skill_body:
        return None

    # 查找 "Request Parameters" 或 "## Request Parameters" 段落
    param_section_match = re.search(
        r'(?:##\s*)?Request\s+Parameters?\s*\n',
        skill_body,
        re.IGNORECASE,
    )
    if not param_section_match:
        return None

    # 从匹配位置开始，找到下一个 ## 标题或文档结束
    start = param_section_match.end()
    next_section = re.search(r'\n##\s', skill_body[start:])
    end = start + next_section.start() if next_section else len(skill_body)
    section_text = skill_body[start:end]

    # 解析 markdown 表格行
    # 格式: | Param | Type | Required | Default | Description |
    properties = {}
    required = []

    for line in section_text.strip().split('\n'):
        line = line.strip()
        if not line.startswith('|'):
            continue
        # 跳过表头分隔行 |---|---|
        if re.match(r'^\|[\s\-|]+\|$', line):
            continue

        cells = [c.strip() for c in line.split('|')[1:-1]]  # 去掉首尾空元素
        if len(cells) < 4:
            continue

        param_name = cells[0]
        param_type_raw = cells[1].lower()
        is_required = cells[2].lower() in ('yes', 'true', 'required')
        # default_val = cells[3] if len(cells) > 3 else None
        description = cells[4] if len(cells) > 4 else (cells[3] if len(cells) > 3 else "")

        # 跳过表头行
        if param_name.lower() in ('param', 'parameter', 'name', '参数'):
            continue

        # 类型映射
        prop = {"description": description}
        if 'str' in param_type_raw or 'string' in param_type_raw:
            prop["type"] = "string"
        elif 'int' in param_type_raw or 'integer' in param_type_raw or 'number' in param_type_raw:
            prop["type"] = "integer"
        elif 'bool' in param_type_raw:
            prop["type"] = "boolean"
        elif 'list' in param_type_raw or 'array' in param_type_raw:
            prop["type"] = "array"
        elif 'obj' in param_type_raw or 'dict' in param_type_raw:
            prop["type"] = "object"
        else:
            prop["type"] = "string"  # 默认 string

        properties[param_name] = prop
        if is_required:
            required.append(param_name)

    if not properties:
        return None

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _get_skills_dir(base_dir: Optional[Path] = None) -> Path:
    """获取 skills 目录路径。"""
    if base_dir:
        return base_dir / "tools" / SKILLS_DIR_NAME
    return Path("tools") / SKILLS_DIR_NAME


# 环境变量名到 tools_credentials 中工具名的映射
_ENV_TO_TOOL_NAME_MAP: dict[str, str] = {
    "BAIDU_API_KEY": "baidu_search",
    "SERPER_API_KEY": "serper_search",
    "TAVILY_API_KEY": "tavily_search",
}


def _env_var_to_tool_name(env_var: str) -> str:
    """将环境变量名转换为 tools_credentials 中的工具名。"""
    if env_var in _ENV_TO_TOOL_NAME_MAP:
        return _ENV_TO_TOOL_NAME_MAP[env_var]

    if env_var.endswith("_API_KEY"):
        return env_var[:-len("_API_KEY")].lower()

    return env_var.lower()


def skill_add(
    console: Console,
    skill_name: str,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> None:
    """
    添加 skill 工具

    使用 AddSkillMetaAgent（LLM）分析 SKILL.md，识别参数定义和工具依赖。
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
    skill_body = meta.pop("_skill_body", "")
    has_scripts = _has_python_scripts(skill_dir)

    # 加载配置并设置会话日志
    config = setup_configuration(console, base_dir=base_dir, config_file=config_file)

    # --- 使用 AddSkillMetaAgent 分析 SKILL.md ---
    console.print(f"  [dim]正在分析 SKILL.md（识别参数和工具依赖）...[/]")
    add_skill_result = _run_add_skill_agent(skill_dir, config, base_dir, console)

    if add_skill_result is None:
        console.print(f"  [yellow]AddSkillMetaAgent 不可用，使用默认参数和依赖[/]")
        parameters = _extract_parameters_from_md(skill_body) or meta.get("parameters") or {
            "type": "object",
            "properties": {
                "arguments": {"type": "string", "description": "JSON 格式的工具参数"},
            },
            "required": ["arguments"],
        }
        tool_deps = []
    elif not add_skill_result.success:
        console.print(f"[red][ERROR][/] AddSkill 分析失败: {add_skill_result.failure_reason}[/]")
        return
    else:
        parameters = add_skill_result.parameters or _extract_parameters_from_md(skill_body) or {
            "type": "object",
            "properties": {
                "arguments": {"type": "string", "description": "JSON 格式的工具参数"},
            },
            "required": ["arguments"],
        }
        tool_deps = add_skill_result.tool_deps
        console.print(f"  [green]AddSkill 分析完成[/]")
        console.print(f"    参数: {', '.join(parameters.get('properties', {}).keys())}")
        console.print(f"    工具依赖: {', '.join(tool_deps) if tool_deps else '无'}")

    # 处理 API key（仅对有脚本的 skill）
    env: dict[str, str] = {}
    runtime_changed = False
    metadata_raw = meta.get("metadata", {})
    openclaw = metadata_raw.get("openclaw", {}) if isinstance(metadata_raw, dict) else {}
    requires = openclaw.get("requires", {}) if isinstance(openclaw, dict) else {}
    env_vars = requires.get("env", []) if isinstance(requires, dict) else []
    bins = requires.get("bins", []) if isinstance(requires, dict) else []

    if has_scripts and env_vars:
        runtime_config = load_runtime_config(base_dir=base_dir)
        for var in env_vars:
            tool_name = _env_var_to_tool_name(var)
            api_key_value = runtime_config.tools_credentials.get_api_key(tool_name)
            if api_key_value:
                env[var] = api_key_value
                console.print(f"  [dim]{var}[/]: 从 runtime.json (tools_credentials.{tool_name}) 加载")
            else:
                console.print(f"  [yellow]{var}[/] 未在 runtime.json 中配置，请输入 API Key:")
                api_key_input = input(f"  {var}=").strip()
                if api_key_input:
                    env[var] = api_key_input
                    runtime_config.tools_credentials.set_api_key(tool_name, api_key_input)
                    runtime_changed = True
                    console.print(f"  [green]已将 {var} 写入 runtime.json (tools_credentials.{tool_name})[/]")
                else:
                    env[var] = ""
                    console.print(f"  [yellow]警告: {var} 未设置，工具执行时可能失败[/]")

        if runtime_changed:
            save_runtime_config(runtime_config, base_dir=base_dir)
            console.print("  [dim]runtime.json 已更新[/]")

    # 查找脚本
    scripts_dir = skill_dir / "scripts"
    script_files = list(scripts_dir.glob("*.py")) if scripts_dir.exists() else []
    project_root = _get_skills_dir(base_dir).parent.parent
    main_script = script_files[0].relative_to(project_root) if script_files else None

    # 构建 shell_command（仅对有脚本的 skill）
    shell_command = None
    if main_script:
        python_cmd = "python" if sys.platform == "win32" else "python3"
        shell_command = f"{python_cmd} {main_script} '{{arguments}}'"

    # 注册工具到数据库
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
        dependencies={
            "skill_dir_name": skill_name,
            "tool_deps": tool_deps,
        },
    )

    tool_id = registry.register(tool)

    console.print(f"[green][OK][/] 已注册 Skill Tool: {name} ({tool_id})")
    console.print(f"  类型: SKILL（ToolExecutionType.SKILL）")
    console.print(f"  有执行脚本: {'是' if has_scripts else '否'}")
    console.print(f"  目录: {skill_dir}")
    if main_script:
        console.print(f"  脚本: {main_script}")
    if env_vars:
        console.print(f"  所需环境变量: {', '.join(env_vars)}")
    if bins:
        console.print(f"  所需命令: {', '.join(bins)}")

    # 显示参数 schema 信息
    param_names = list(parameters.get("properties", {}).keys())
    required_params = parameters.get("required", [])
    console.print(f"  参数: {', '.join(param_names)}")
    console.print(f"  必填: {', '.join(required_params) if required_params else '无'}")


def _run_add_skill_agent(
    skill_dir: Path,
    config: Any,
    base_dir: Optional[Path] = None,
    console: Console | None = None,
) -> Any:
    """运行 AddSkillMetaAgent 分析 SKILL.md，返回 AddSkillResult 或 None

    像 chat 一样完整初始化：启动 MCP server、创建 LLMClient、运行元Agent、输出日志、关闭资源。
    """
    import asyncio

    try:
        from qd_agents.agent.add_skill_meta import AddSkillMetaAgent
        from qd_agents.agent.base import MetaAgentInput
        from qd_agents.cli.managers import LLMClientManager
        from qd_agents.context import ContextManager
        from qd_agents.prompts import PromptLoader
    except ImportError as e:
        logger.warning("AddSkillMetaAgent 依赖不可用: %s", e)
        return None

    async def _run():
        # 1. 读取 SKILL.md 全文
        skill_md_path = skill_dir / "SKILL.md"
        skill_md_content = skill_md_path.read_text(encoding="utf-8")

        # 2. 初始化工具注册表和上下文
        db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
        tool_registry = ToolRegistry(db_path=db_path)

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

            # 5. 创建并运行 AddSkillMetaAgent
            agent = AddSkillMetaAgent(
                llm_client=llm_manager.llm_client,
                context_manager=context_manager,
            )

            meta_input = MetaAgentInput(
                user_message="分析 SKILL.md",
                history=[],
                context={
                    "skill_md": skill_md_content,
                    "tools": all_tools,
                },
            )

            output = await agent.run(meta_input)
            return output.output

        finally:
            # 6. 关闭资源（MCP server 等）
            await llm_manager.close()

    try:
        return asyncio.run(_run())
    except Exception as e:
        logger.warning("AddSkillMetaAgent 执行失败: %s", e)
        return None
