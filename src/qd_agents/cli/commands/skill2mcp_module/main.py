"""
技能转 MCP 主模块

包含主要的异步和同步入口函数。
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from qd_agents.config import load_config
from qd_agents.llm import LLMClient, create_client
from qd_agents.registry import ToolRegistry

from .analyzer import SkillAnalyzer
from .generator import MCPToolGenerator, MCPServerGenerator
from .validator import SmartMCPValidator

logger = logging.getLogger(__name__)


async def skill2mcp_async(
    console: Console,
    skill_path: Path,
    output_dir: Optional[Path] = None,
    register: bool = False,
    config_file: Optional[Path] = None,
    base_dir: Optional[Path] = None,
) -> None:
    """
    将技能转换为 MCP 工具

    Args:
        console: Rich 控制台对象
        skill_path: 技能目录路径
        output_dir: 输出目录（可选）
        register: 是否注册到工具注册表
        config_file: 配置文件路径
        base_dir: 基础目录
    """
    # 验证技能路径
    if not skill_path.exists():
        console.print(f"[red][ERROR][/] 技能路径不存在: {skill_path}")
        return

    if not skill_path.is_dir():
        console.print(f"[red][ERROR][/] 技能路径必须是目录: {skill_path}")
        return

    console.print(f"[blue][INFO][/] 分析技能: {skill_path.name}")

    # 加载配置
    config = load_config(base_dir=base_dir, config_file=config_file)

    # 创建 LLM 客户端 - 使用与 chat 功能一致的配置方式
    try:
        # 获取默认 LLM 提供者配置
        llm_config = config.llm
        default_provider = getattr(llm_config, 'default_provider', 'nvidia')
        providers = getattr(llm_config, 'providers', {})

        if not providers:
            console.print("[yellow][WARN][/] 配置中没有 LLM 提供者设置")
            return

        # 获取默认提供者配置
        provider_config = providers.get(default_provider)
        if not provider_config:
            # 尝试获取第一个可用的提供者
            provider_name = next(iter(providers.keys()))
            provider_config = providers[provider_name]
            default_provider = provider_name

        if not provider_config or not getattr(provider_config, 'api_key', None):
            console.print(f"[yellow][WARN][/] 提供者 {default_provider} 缺少 API 密钥")
            return

        # 创建 LLM 客户端
        llm_client = LLMClient(
            api_key=provider_config.api_key,
            base_url=getattr(provider_config, 'base_url', 'https://integrate.api.nvidia.com/v1'),
            model_names=getattr(provider_config, 'models', None),
            timeout=getattr(provider_config, 'timeout', 120.0),
            max_retries=getattr(provider_config, 'max_retries', 3),
        )

        # 如果启用了自动发现且没有预定义模型，则发现模型
        if getattr(provider_config, 'auto_discover', True) and not getattr(provider_config, 'models', None):
            with console.status("[dim]正在发现可用模型...[/]"):
                await llm_client.discover_models()

        console.print(f"[dim]使用 LLM 提供者: {default_provider}[/]")

    except Exception as e:
        console.print(f"[red][ERROR][/] 初始化 LLM 客户端失败: {e}")
        # 回退到旧的配置读取方式
        console.print("[dim]尝试回退到配置文件读取...[/]")
        try:
            # 尝试从 config.json 直接读取 NVIDIA API 密钥
            config_path = config_file or base_dir / "config.json" if base_dir else Path("config.json")
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                    api_key = config_data.get('llm', {}).get('providers', {}).get('nvidia', {}).get('api_key')

            if not api_key:
                import os
                api_key = os.getenv('NVIDIA_API_KEY')

            if not api_key:
                console.print("[yellow][WARN][/] 未找到 API 密钥，无法进行分析")
                return

            llm_client = create_client(api_key=api_key, auto_discover=False)
            console.print("[dim]使用回退配置[/]")
        except Exception as fallback_error:
            console.print(f"[red][ERROR][/] 回退配置也失败: {fallback_error}")
            return

    # 分析技能
    analyzer = SkillAnalyzer(llm_client)
    try:
        analysis = await analyzer.analyze_skill(skill_path)
        console.print(f"[green][OK][/] 技能分析完成")

        # 显示分析结果
        console.print(Panel.fit(
            json.dumps(analysis, indent=2, ensure_ascii=False),
            title="技能分析结果",
            border_style="blue"
        ))

    except Exception as e:
        console.print(f"[red][ERROR][/] 技能分析失败: {e}")
        return
    finally:
        await llm_client.close()

    # 生成 MCP 工具定义
    generator = MCPToolGenerator(base_dir or Path.cwd())
    tool = generator.generate_tool_definition(analysis)

    console.print(f"[green][OK][/] MCP 工具定义生成完成")

    # 显示工具定义
    tool_dict = {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
        "execution": {
            "type": tool.execution.type.value,
            "server": tool.execution.server,
            "transport": tool.execution.transport,
            "command": tool.execution.command,
            "args": tool.execution.args
        }
    }

    console.print(Panel.fit(
        json.dumps(tool_dict, indent=2, ensure_ascii=False),
        title="MCP 工具定义",
        border_style="green"
    ))

    # 保存到文件（如果指定了输出目录）
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{tool.name}.json"

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(tool_dict, f, indent=2, ensure_ascii=False)

        console.print(f"[green][OK][/] 工具定义已保存到: {output_file}")

        # 生成完整的 MCP 服务器
        try:
            server_generator = MCPServerGenerator(base_dir or Path.cwd(), skill_path)
            mcp_server_dir = server_generator.generate_server(analysis, tool)
            console.print(f"[green][OK][/] MCP 服务器已生成到: {mcp_server_dir}")

            # 生成验证脚本
            server_generator.generate_validation_script(analysis, tool)
            console.print(f"[green][OK][/] 验证脚本已生成")

            # 显示后续步骤
            try:
                relative_path = mcp_server_dir.relative_to(base_dir or Path.cwd())
            except ValueError:
                relative_path = mcp_server_dir

            package_name = analysis.get('name', skill_path.name).lower().replace('_', '-')

            console.print(Panel.fit(
                f"""## 后续步骤

1. 进入 MCP 服务器目录:
   cd {relative_path}

2. 安装依赖:
   uv pip install -e .

3. 运行服务器:
   uv run {package_name} -m {package_name}.main

4. 运行验证测试:
   python -m test.validate

服务器使用 stdio 协议，可以通过 MCP 客户端连接。""",
                title="MCP 服务器已就绪",
                border_style="cyan"
            ))

        except Exception as e:
            console.print(f"[yellow][WARN][/] 生成 MCP 服务器时出错: {e}")
            console.print("[dim]继续生成工具定义...[/]")

    # 注册到工具注册表（如果请求）
    if register:
        try:
            db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
            registry = ToolRegistry(db_path=db_path)

            tool_id = registry.register(tool)
            console.print(f"[green][OK][/] 工具已注册: {tool.name} (ID: {tool_id})")
        except Exception as e:
            console.print(f"[red][ERROR][/] 工具注册失败: {e}")


def skill2mcp(
    console: Console,
    skill_path: Path = typer.Argument(..., help="技能目录路径"),
    output_dir: Optional[Path] = typer.Option(None, "--output", "-o", help="输出目录"),
    register: bool = typer.Option(False, "--register", "-r", help="注册到工具注册表"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c", help="配置文件路径"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
) -> None:
    """将技能转换为 MCP 工具"""
    asyncio.run(skill2mcp_async(
        console=console,
        skill_path=skill_path,
        output_dir=output_dir,
        register=register,
        config_file=config_file,
        base_dir=base_dir,
    ))