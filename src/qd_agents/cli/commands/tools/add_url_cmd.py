"""从 URL 自动安装工具命令"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from rich.console import Console

from qd_agents.config import load_config, load_runtime_config, save_runtime_config
from qd_agents.registry import ToolRegistry
from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType
from qd_agents.tools.executors import create_mcp_tool
from qd_agents.cli.utils.credentials import env_var_to_tool_name
from qd_agents.cli.utils.registry import get_tool_registry
from qd_agents.models.url_analyze import UrlAnalyzeResult

from .github import _fetch_github_version, _download_github_skill_dir

if TYPE_CHECKING:
    from qd_agents.cli.managers import LLMClientManager


def add_tool_from_url(
    console: Console,
    url: str,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
) -> None:
    """从 URL 自动安装工具

    支持 pypi 包、MCP 服务器、单个 skill、skillset（多 skill 集合）。
    用 LLM 分析 URL 内容，自动判断类型并注册。
    """
    import httpx

    try:
        from html2text import HTML2Text
    except ImportError:
        console.print("[red][ERROR][/] 需要 html2text 库，请运行: uv add html2text")
        return

    from qd_agents.cli.managers import LLMClientManager, setup_configuration
    from qd_agents.agent.url_analyzer import UrlAnalyzer
    from qd_agents.context import ContextManager
    from qd_agents.prompts import PromptLoader

    config = setup_configuration(console, base_dir=base_dir, config_file=config_file)
    registry = get_tool_registry(config)

    # 1. 获取 URL 内容
    console.print(f"正在获取 URL 内容: {url}")
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=30)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        console.print(f"[red][ERROR][/] 获取 URL 失败: {e}")
        return

    # HTML → markdown
    h2t = HTML2Text()
    h2t.ignore_links = False
    h2t.ignore_images = True
    h2t.body_width = 0
    content = h2t.handle(resp.text)

    if not content.strip():
        console.print("[red][ERROR][/] URL 内容为空")
        return

    console.print(f"已获取内容 ({len(content)} 字符)，正在分析...")

    # 2. 用 LLM 分析（通过 LLMClientManager 完整初始化）
    prompt_loader = None
    if config.prompts and config.prompts.template_dir:
        prompt_loader = PromptLoader(template_dir=Path(config.prompts.template_dir))

    context_manager = ContextManager(prompt_loader=prompt_loader, base_dir=base_dir)
    provider_name = config.llm.default_provider
    llm_manager = LLMClientManager(console, config, registry, prompt_loader, context_manager)

    async def _analyze_and_register():
        if not await llm_manager.initialize(provider_name):
            console.print("[red][ERROR][/] LLM 初始化失败")
            return None

        try:
            analyzer = UrlAnalyzer(llm_client=llm_manager.llm_client)
            result = await analyzer.analyze(url=url, content=content)

            if not result.success:
                console.print(f"[red][ERROR][/] 分析失败: {result.failure_reason}")
                return None

            console.print(f"分析结果: [cyan]{result.type}[/] — {result.name}: {result.description}")

            # 3. 执行前置安装
            if result.prereqs:
                console.print(f"\n[yellow]前置安装步骤 ({len(result.prereqs)} 个):[/]")
                for prereq in result.prereqs:
                    console.print(f"  执行: {prereq}")
                    try:
                        proc = subprocess.run(
                            prereq, shell=True, capture_output=True, text=True, timeout=120,
                        )
                        if proc.returncode != 0:
                            console.print(f"  [red]失败: {proc.stderr[:200]}[/]")
                        else:
                            console.print(f"  [green]成功[/]")
                    except subprocess.TimeoutExpired:
                        console.print(f"  [red]超时[/]")

            return result
        except Exception as e:
            console.print(f"[red][ERROR][/] 分析失败: {e}")
            return None
        finally:
            await llm_manager.close()

    # 2. 用 LLM 分析
    try:
        result = asyncio.run(_analyze_and_register())
    except Exception as e:
        console.print(f"[red][ERROR][/] 安装失败: {e}")
        return

    if not result:
        return

    # 版本号 fallback：LLM 未提取到版本且 URL 为 GitHub 项目时，从配置文件获取
    if not result.version and "github.com" in url:
        gh_version = _fetch_github_version(url)
        if gh_version:
            result.version = gh_version
            console.print(f"  [dim]从 GitHub 配置文件获取版本: {gh_version}[/]")

    # 4. 写入 env_vars 到 runtime.json（在 asyncio 之外，避免 input() 阻塞事件循环）
    if result.env_vars:
        _save_env_vars_to_runtime(console, result.env_vars, base_dir)

    # 5. 按类型注册（重新初始化 LLM 客户端用于 AddSkillAnalyzer）
    async def _register():
        if not await llm_manager.initialize(provider_name):
            console.print("[red][ERROR][/] LLM 初始化失败")
            return
        try:
            if result.type == "mcp":
                _register_mcp_from_url(console, registry, result, url)
            elif result.type == "skill":
                await _register_skill_from_url(
                    console, registry, result, url, base_dir, config_file,
                    llm_manager=llm_manager, all_tools=registry.list_all(),
                )
            elif result.type == "skillset":
                await _register_skillset_from_url(
                    console, registry, result, url, base_dir, config_file,
                    llm_manager=llm_manager,
                )
            elif result.type == "pypi":
                _register_pypi_from_url(console, registry, result, url)
            else:
                console.print(f"[red][ERROR][/] 未知类型: {result.type}")

            console.print(f"\n[green]安装完成[/]")
        finally:
            await llm_manager.close()

    try:
        asyncio.run(_register())
    except Exception as e:
        console.print(f"[red][ERROR][/] 注册失败: {e}")


def _save_env_vars_to_runtime(
    console: Console,
    env_vars: dict[str, str],
    base_dir: Optional[Path],
) -> None:
    """将 env_vars 写入 runtime.json 的 tools_credentials

    env_vars: key=环境变量名, value=获取说明
    如果 runtime.json 中已有该 key 的值则保留，否则提示用户输入。
    """
    runtime_config = load_runtime_config(base_dir=base_dir)
    runtime_changed = False

    for env_var, description in env_vars.items():
        tool_name = env_var_to_tool_name(env_var)
        existing = runtime_config.tools_credentials.get_api_key(tool_name)
        if existing:
            console.print(f"  [dim]{env_var}[/]: 从 runtime.json (tools_credentials.{tool_name}) 加载")
        else:
            console.print(f"  [yellow]{env_var}[/] 未配置 — {description}")
            # 检查是否在交互式终端中
            if sys.stdin.isatty():
                api_key_input = input(f"  {env_var}=").strip()
            else:
                # 非交互模式：检查环境变量
                api_key_input = os.environ.get(env_var, "")
                if api_key_input:
                    console.print(f"  [dim]从环境变量 {env_var} 获取[/]")
                else:
                    console.print(f"  [yellow]非交互模式，跳过输入。请稍后手动配置 runtime.json[/]")
                    continue

            if api_key_input:
                runtime_config.tools_credentials.set_api_key(tool_name, api_key_input)
                runtime_changed = True
                console.print(f"  [green]已将 {env_var} 写入 runtime.json (tools_credentials.{tool_name})[/]")
            else:
                console.print(f"  [yellow]警告: {env_var} 未设置，工具执行时可能失败[/]")

    if runtime_changed:
        save_runtime_config(runtime_config, base_dir=base_dir)
        console.print("  [dim]runtime.json 已更新[/]")


def _register_mcp_from_url(
    console: Console,
    registry: ToolRegistry,
    result: UrlAnalyzeResult,
    url: str,
) -> None:
    """注册 MCP 工具"""
    # 合并 mcp_env 和 env_vars 到 execution.env
    env = dict(result.mcp_env or {})
    for env_var in result.env_vars:
        if env_var not in env:
            env[env_var] = ""

    tool = create_mcp_tool(
        name=result.name,
        description=result.description or f"MCP server: {result.name}",
        server=result.name,
        transport=result.mcp_transport,
        command=result.mcp_command or None,
        args=result.mcp_args,
        env=env,
        source_path=url,
        version=result.version or None,
        install_source=result.install_source or None,
    )
    registry.register(tool)
    console.print(f"[green][OK][/] 已注册 MCP 工具: {result.name}")
    if result.env_vars:
        console.print(f"  所需环境变量: {', '.join(result.env_vars.keys())}")


async def _register_skill_from_url(
    console: Console,
    registry: ToolRegistry,
    result: UrlAnalyzeResult,
    url: str,
    base_dir: Optional[Path],
    config_file: Optional[Path],
    *,
    llm_manager: LLMClientManager,
    all_tools: list | None = None,
) -> None:
    """注册单个 skill 工具

    1. 保存 SKILL.md 到 tools/skills/<name>/SKILL.md
    2. 用 AddSkillAnalyzer 分析 SKILL.md 内容
    3. 注册为 SKILL 类型工具
    """
    from qd_agents.agent.add_skill import AddSkillAnalyzer

    skill_md = result.skill_md_content
    if not skill_md:
        console.print(f"[red][ERROR][/] skill {result.name} 没有 SKILL.md 内容")
        return

    # 保存 SKILL.md 到 tools/skills/<name>/
    skills_dir = Path("tools/skills") / result.name
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skills_dir / "SKILL.md"
    skill_file.write_text(skill_md, encoding="utf-8")
    console.print(f"  已保存 SKILL.md → {skill_file}")

    # 用 AddSkillAnalyzer 分析（复用 llm_manager 的 LLM 客户端）
    skill_result = None
    try:
        analyzer = AddSkillAnalyzer(
            llm_client=llm_manager.llm_client,
            context_manager=llm_manager.context_manager,
        )
        skill_result = await analyzer.analyze(
            skill_md=skill_md,
            tools=all_tools or [],
        )
    except Exception as e:
        console.print(f"  [yellow]Skill 分析失败，使用默认配置: {e}[/]")

    # 构建 env：合并 UrlAnalyzeResult.env_vars + AddSkillResult 的 env
    env: dict[str, str] = {}
    for env_var in result.env_vars:
        env[env_var] = ""

    skill_type = "tool_manual"
    if skill_result and skill_result.success:
        skill_type = skill_result.skill_type

    tool = Tool(
        id=f"skill.{result.name}",
        name=result.name,
        description=result.description or skill_md[:200],
        parameters={"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.SKILL,
            env=env,
        ),
        scope="user",
        metadata=ToolMetadata(
            tags=["skill", result.name],
            version=result.version or None,
            install_source=result.install_source or None,
        ),
        dependencies={"skill_type": skill_type},
        source_path=url,
        local_path=result.name,
    )
    registry.register(tool)
    console.print(f"[green][OK][/] 已注册 skill: {result.name} (type={skill_type})")
    if result.env_vars:
        console.print(f"  所需环境变量: {', '.join(result.env_vars.keys())}")


async def _register_skillset_from_url(
    console: Console,
    registry: ToolRegistry,
    result: UrlAnalyzeResult,
    url: str,
    base_dir: Optional[Path],
    config_file: Optional[Path],
    *,
    llm_manager: LLMClientManager,
) -> None:
    """注册 skillset 中的所有 skill

    对于 GitHub 仓库，使用 GitHub Contents API 下载每个 skill 文件夹的全部文件；
    对于非 GitHub URL，仅下载 SKILL.md。
    """
    import httpx

    if not result.skills:
        console.print("[red][ERROR][/] skillset 中没有找到子 skill")
        return

    all_tools = registry.list_all()
    console.print(f"\n发现 {len(result.skills)} 个 skill，逐个安装:\n")

    # 检测 GitHub 仓库路径（owner/repo），用于 API 调用
    gh_repo_match = re.match(r"https://github\.com/([^/]+/[^/]+)", url)
    gh_repo_path = gh_repo_match.group(1).rstrip("/") if gh_repo_match else None

    for skill_info in result.skills:
        console.print(f"  安装 skill: [cyan]{skill_info.name}[/]")

        # 构建单个 skill 的 UrlAnalyzeResult（继承 skillset 的 env_vars 和版本信息）
        skill_result = UrlAnalyzeResult(
            type="skill",
            name=skill_info.name,
            description=skill_info.description,
            skill_md_content="",
            env_vars=result.env_vars,
            version=result.version,
            install_source=result.install_source,
        )

        # 下载 skill 文件夹内容
        skill_dir = Path("tools/skills") / skill_info.name
        skill_dir.mkdir(parents=True, exist_ok=True)

        if gh_repo_path:
            # GitHub 仓库：用 API 下载整个文件夹
            downloaded = _download_github_skill_dir(console, gh_repo_path, skill_info.name, skill_dir, base_dir)
            if not downloaded:
                console.print(f"    [yellow]GitHub API 下载失败，回退到 SKILL.md 单文件下载[/]")
                if skill_info.skill_md_url:
                    try:
                        resp = httpx.get(skill_info.skill_md_url, follow_redirects=True, timeout=30)
                        resp.raise_for_status()
                        skill_result.skill_md_content = resp.text
                        (skill_dir / "SKILL.md").write_text(resp.text, encoding="utf-8")
                    except httpx.HTTPError as e:
                        console.print(f"    [red]下载 SKILL.md 失败: {e}[/]")
                        continue
                else:
                    console.print(f"    [yellow]没有 SKILL.md URL，跳过[/]")
                    continue
            else:
                # 从已下载的文件中读取 SKILL.md 内容
                skill_md_path = skill_dir / "SKILL.md"
                if skill_md_path.exists():
                    skill_result.skill_md_content = skill_md_path.read_text(encoding="utf-8")
                else:
                    console.print(f"    [yellow]下载的文件夹中没有 SKILL.md，跳过[/]")
                    continue
        else:
            # 非 GitHub URL：仅下载 SKILL.md
            if skill_info.skill_md_url:
                try:
                    resp = httpx.get(skill_info.skill_md_url, follow_redirects=True, timeout=30)
                    resp.raise_for_status()
                    skill_result.skill_md_content = resp.text
                except httpx.HTTPError as e:
                    console.print(f"    [red]下载 SKILL.md 失败: {e}[/]")
                    continue
            else:
                console.print(f"    [yellow]没有 SKILL.md URL，跳过[/]")
                continue

        await _register_skill_from_url(
            console, registry, skill_result, url, base_dir, config_file,
            llm_manager=llm_manager, all_tools=all_tools,
        )

    console.print(f"\n[green]skillset 安装完成: {len(result.skills)} 个 skill[/]")


def _register_pypi_from_url(
    console: Console,
    registry: ToolRegistry,
    result: UrlAnalyzeResult,
    url: str,
) -> None:
    """注册 pypi 包工具"""
    if result.install_command:
        console.print(f"  执行安装: {result.install_command}")
        try:
            proc = subprocess.run(
                result.install_command, shell=True, capture_output=True, text=True, timeout=120,
            )
            if proc.returncode != 0:
                console.print(f"  [red]安装失败: {proc.stderr[:200]}[/]")
                return
            console.print(f"  [green]安装成功[/]")
        except subprocess.TimeoutExpired:
            console.print(f"  [red]安装超时[/]")
            return

    # 合并 env_vars
    env = {}
    for env_var in result.env_vars:
        env[env_var] = ""

    tool = Tool(
        id=f"pypi.{result.name}",
        name=result.name,
        description=result.description or f"PyPI package: {result.package_name or result.name}",
        parameters={"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module=result.package_name or result.name,
            env=env,
        ),
        scope="user",
        metadata=ToolMetadata(
            tags=["pypi", result.name],
            version=result.version or None,
            install_source=result.install_source or None,
        ),
        source_path=url,
    )
    registry.register(tool)
    console.print(f"[green][OK][/] 已注册 pypi 工具: {result.name}")
    if result.env_vars:
        console.print(f"  所需环境变量: {', '.join(result.env_vars.keys())}")