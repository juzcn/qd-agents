"""
模型管理命令

负责列出和显示可用模型。
"""

import asyncio
from pathlib import Path
from typing import Optional

from rich.console import Console

from qd_agents.config import load_config
from qd_agents.llm import LLMClient


async def list_models_async(
    console: Console,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
    provider: Optional[str] = None,
) -> None:
    """
    异步列出模型

    Args:
        console: Rich 控制台对象
        base_dir: 基础目录
        config_file: 配置文件路径
        provider: 指定提供商，如果为 None 则显示所有提供商
    """
    config = load_config(base_dir=base_dir, config_file=config_file)

    # 如果指定了 provider，只显示该 provider
    if provider:
        provider_names = [provider]
    else:
        # 否则显示所有 provider
        provider_names = list(config.llm.providers.keys())

    for provider_name in provider_names:
        provider_config = config.llm.providers.get(provider_name)
        if not provider_config or not provider_config.api_key:
            continue

        console.print(f"\n[bold]提供商: {provider_name}[/]")
        console.print(f"连接到: {provider_config.base_url}")

        # 如果配置了模型列表，直接显示
        if provider_config.models and not provider_config.auto_discover:
            console.print("\n[bold]已配置的模型:[/]")
            for model_name in provider_config.get_model_names():
                console.print(f"  - [cyan]{provider_name}/{model_name}[/]")
            continue

        # 否则从 API 获取
        console.print("\n正在获取模型列表...")

        try:
            async with LLMClient(
                api_key=provider_config.api_key,
                base_url=provider_config.base_url,
            ) as llm_client:
                # 使用 discover_models 获取模型池的模型
                if provider_config.auto_discover:
                    # 对于 auto_discover 的提供商，获取模型池的模型（Top 5）
                    models = await llm_client.discover_models(top_k=5)
                    console.print("[bold]可用模型 (模型池):[/]")
                    for model_name in models:
                        console.print(f"  - [cyan]{provider_name}/{model_name}[/]")
                else:
                    # 对于非 auto_discover 的提供商，直接显示所有模型
                    models_response = await llm_client._client.models.list()
                    console.print("[bold]可用模型:[/]")
                    for m in models_response.data:
                        console.print(f"  - [cyan]{provider_name}/{m.id}[/]")
                        if hasattr(m, "created") and m.created:
                            console.print(f"    创建时间: {m.created}", style="dim")
        except Exception as e:
            console.print(f"[red]获取模型列表失败: {e}[/]")