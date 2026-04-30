"""
CLI 工具管理命令

负责注册和管理 CLI 工具。
command 为完整命令行，自动拆分为可执行文件和参数。
添加时自动执行 --help 并用 LLM 解析参数为 JSON Schema。
"""
import asyncio
import json
import logging
import shlex
import subprocess
from pathlib import Path
from typing import Optional, List

from rich.console import Console

from qd_agents.config import load_config
from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType
from qd_agents.cli.utils.registry import get_tool_registry
from qd_agents.cli.utils.credentials import resolve_env_vars
from qd_agents.cli.utils.registration import register_tool_and_report
from qd_agents.utils.parsing import extract_json_from_llm_output

logger = logging.getLogger(__name__)

_PARSE_HELP_PROMPT = """\
You are a CLI help text parser. Given the --help output of a CLI command, generate a JSON Schema describing its parameters.

Rules:
- Output ONLY valid JSON, no markdown, no explanation
- The schema must follow this structure (example):
  {{ "type": "object", "properties": {{ "PARAM_NAME": {{ "type": "string", "description": "desc" }} }}, "required": [] }}
- Convert CLI flags (e.g. --verbose, -v) to snake_case property names (e.g. verbose)
- Positional arguments become required string properties
- Optional flags become optional string properties with default ""
- Boolean flags (--verbose) become type "boolean" with default false
- Numeric flags (--timeout) become type "integer" or "number"
- Include the description from the help text for each parameter

Help text:
```
{help_text}
```"""


def cli_add(
    console: Console,
    name: str,
    command: str,
    default: bool = False,
    extra_env: Optional[List[str]] = None,
    timeout: int = 300,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
    interactive: bool = True,
) -> None:
    """注册 CLI 工具。command 为完整命令行，自动拆分为可执行文件和参数。"""
    # 拆分命令行
    parts = shlex.split(command)
    if not parts:
        console.print("[red][ERROR][/] 命令不能为空")
        return
    executable = parts[0]
    args = parts[1:]

    # 验证命令存在并获取 --help 输出
    try:
        result = subprocess.run(
            [executable] + args + ["--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        console.print(f"[red][ERROR][/] 找不到命令: {executable}")
        return
    except subprocess.TimeoutExpired:
        console.print(f"[red][ERROR][/] `{executable} --help` 超时")
        return

    help_text = result.stdout or result.stderr
    if not help_text.strip():
        console.print(f"[red][ERROR][/] `{executable} {' '.join(args)} --help` 无输出")
        return
    parameters = _parse_help_with_llm(console, help_text, base_dir, config_file)

    # 处理环境变量
    env_names = extra_env or []
    env_dict: dict[str, str] = {}
    if env_names:
        resolved, _ = resolve_env_vars(env_names, console, base_dir=base_dir, interactive=interactive)
        env_dict.update(resolved)

    # 注册工具
    tool = Tool(
        id=f"cli.{name}",
        name=name,
        description=f"CLI tool: {name}",
        parameters=parameters,
        execution=ToolExecutionConfig(
            type=ToolExecutionType.CLI,
            command=executable,
            args=args,
            timeout=timeout,
            env=env_dict,
        ),
        scope="default" if default else "user",
        metadata=ToolMetadata(
            tags=["cli", name],
        ),
    )

    register_tool_and_report(tool, console, base_dir=base_dir, config_file=config_file)
    console.print(f"  命令: {executable} {' '.join(args)}".strip())
    console.print(f"  超时: {timeout}s")
    if env_names:
        console.print(f"  所需环境变量: {', '.join(env_names)}")


def _parse_help_with_llm(
    console: Console,
    help_text: str,
    base_dir: Optional[Path],
    config_file: Optional[Path],
) -> dict:
    """用 LLM 解析 --help 输出，生成 parameters JSON Schema。"""
    try:
        from qd_agents.cli.managers import LLMClientManager, setup_configuration
        from qd_agents.context import ContextManager
        from qd_agents.prompts import PromptLoader
    except ImportError as e:
        logger.warning("LLMClientManager 依赖不可用: %s", e)
        return _default_parameters()

    config = setup_configuration(console, base_dir=base_dir, config_file=config_file)
    tool_registry = get_tool_registry(config)

    prompt_loader = None
    if config.prompts and config.prompts.template_dir:
        prompt_loader = PromptLoader(template_dir=Path(config.prompts.template_dir))

    context_manager = ContextManager(prompt_loader=prompt_loader, base_dir=base_dir)

    async def _run():
        provider_name = config.llm.default_provider
        llm_manager = LLMClientManager(console, config, tool_registry, prompt_loader, context_manager)
        if not await llm_manager.initialize(provider_name):
            logger.warning("LLMClientManager 初始化失败")
            return None

        try:
            prompt = _PARSE_HELP_PROMPT.format(help_text=help_text)
            llm_manager.llm_client.meta_agent_name = "parse_help"

            response = await llm_manager.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
            content = response.choices[0].message.content or ""
            json_str = extract_json_from_llm_output(content)
            return json.loads(json_str)
        except Exception as e:
            logger.warning("LLM 解析 --help 失败: %s", e)
            return None
        finally:
            await llm_manager.close()

    try:
        result = asyncio.run(_run())
        if result and isinstance(result, dict) and "properties" in result:
            console.print("[dim]  已从 --help 解析参数[/]")
            return result
    except Exception as e:
        logger.warning("LLM 解析 --help 失败: %s", e)

    console.print("[yellow]  ⚠ 无法解析 --help，使用默认参数[/]")
    return _default_parameters()


def _default_parameters() -> dict:
    return {
        "type": "object",
        "properties": {
            "arguments": {
                "type": "string",
                "description": "传递给命令的参数",
            },
        },
        "required": ["arguments"],
    }
