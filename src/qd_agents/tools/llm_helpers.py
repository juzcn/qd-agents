"""LLM 辅助分析 — 工具注册中需要调用 LLM 的功能

parse_help_with_llm: 用 LLM 解析 --help 输出
run_add_skill_analyzer: 用 LLM 分析 SKILL.md 依赖
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

from qd_agents.config.loader import load_config
from qd_agents.llm import LLMClient

logger = logging.getLogger(__name__)


def ensure_logging(base_dir: Optional[Path] = None, config_file: Optional[Path] = None) -> None:
    """确保日志已配置（仅在 root logger 无 handler 时配置）"""
    import logging as _logging
    root = _logging.getLogger()
    if root.handlers:
        return
    config = load_config(base_dir=base_dir, config_file=config_file)
    from qd_agents.utils.logging import setup_session_logging
    log_level = config.observability.log_level if config.observability else "INFO"
    log_dir = config.observability.log_session_dir if (config.observability and config.observability.log_session_dir) else Path(".")
    log_external_api = config.observability.log_external_api if config.observability else False
    log_immediate_flush = config.observability.log_immediate_flush if config.observability else True
    log_file, trace_id = setup_session_logging(
        log_dir=log_dir,
        level=log_level,
        log_external_api=log_external_api,
        log_immediate_flush=log_immediate_flush,
    )
    logger.info("日志文件: %s", log_file)


def parse_help_with_llm(
    help_text: str, name: str, base_dir: Optional[Path], config_file: Optional[Path]
) -> dict | None:
    """用 LLM 解析 --help 输出

    Returns:
        包含 description 和 parameters 的字典。LLM 失败时返回默认值。
    """
    from qd_agents.utils.parsing import extract_json_from_llm_output
    from qd_agents.config.paths import resolve_template_dir
    from qd_agents.prompts import PromptLoader

    ensure_logging(base_dir, config_file)

    async def _run():
        config = load_config(base_dir=base_dir, config_file=config_file)
        provider_name = config.llm.default_provider
        provider_config = config.llm.providers.get(provider_name)
        if not provider_config or not provider_config.api_key:
            logger.warning("LLM provider %s 未配置 API key", provider_name)
            return None

        template_dir = resolve_template_dir(config)
        prompt_loader = PromptLoader(template_dir=template_dir)
        prompt = prompt_loader.render("add_cli.j2", help_text=help_text)

        llm = LLMClient(
            api_key=provider_config.api_key,
            base_url=provider_config.base_url,
            model_names=provider_config.get_model_names() or None,
        )
        try:
            logger.info("LLM 解析 --help: name=%s, provider=%s", name, provider_name)
            response = await llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
            content = response.choices[0].message.content or ""
            logger.info("LLM 响应: %s", content[:200])
            json_str = extract_json_from_llm_output(content)
            return json.loads(json_str)
        except Exception as e:
            logger.warning("LLM 解析 --help 失败: %s", e)
            return None
        finally:
            await llm.close()

    try:
        result = asyncio.run(_run())
        if result and isinstance(result, dict) and "parameters" in result:
            return result
    except Exception as e:
        logger.warning("LLM 解析 --help 失败: %s", e)

    return None


def run_add_skill_analyzer(
    skill_dir: Path, config: Any, base_dir: Optional[Path] = None
) -> Any:
    """运行 analyze_skill — 使用 LLMClient 直接调用"""
    from qd_agents.config.paths import resolve_template_dir
    from qd_agents.prompts import PromptLoader

    ensure_logging(base_dir, None)

    try:
        from qd_agents.agent.add_skill import analyze_skill
        from qd_agents.context import ContextManager
    except ImportError as e:
        logger.warning("analyze_skill 依赖不可用: %s", e)
        return None

    async def _run():
        skill_md_path = skill_dir / "SKILL.md"
        skill_md_content = skill_md_path.read_text(encoding="utf-8")

        from qd_agents.cli.utils.registry import get_tool_registry
        from qd_agents.services.tool_service import ToolService
        from qd_agents.services.mcp_service import MCPService
        from qd_agents.models.tool import ToolExecutionType
        tool_registry = get_tool_registry(config)

        # 使用与 chat 相同的工具加载方式（含 MCP subtools）
        tool_service = ToolService()
        mcp_service = MCPService()
        try:
            await mcp_service.preload(
                mcp_tools=[t for t in tool_registry.list_all() if t.execution.type == ToolExecutionType.MCP],
                executor_registry=None,
            )
            expanded_tools, _, _ = tool_service.build_expanded_tools(
                registry=tool_registry,
                mcp_tools_cache=mcp_service.tools_cache,
            )
        finally:
            await mcp_service.close()

        template_dir = resolve_template_dir(config)
        prompt_loader = PromptLoader(template_dir=template_dir)
        context_manager = ContextManager(prompt_loader=prompt_loader, base_dir=base_dir)

        provider_name = config.llm.default_provider
        provider_config = config.llm.providers.get(provider_name)
        if not provider_config or not provider_config.api_key:
            logger.warning("LLM provider %s 未配置 API key", provider_name)
            return None

        llm_client = LLMClient(
            api_key=provider_config.api_key,
            base_url=provider_config.base_url,
            model_names=provider_config.get_model_names() or None,
        )

        try:
            return await analyze_skill(
                skill_md=skill_md_content,
                tools=expanded_tools,
                llm_client=llm_client,
                context_manager=context_manager,
            )
        finally:
            await llm_client.close()

    try:
        return asyncio.run(_run())
    except Exception as e:
        logger.warning("analyze_skill 执行失败: %s", e)
        return None
