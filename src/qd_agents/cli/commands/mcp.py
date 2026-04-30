"""
MCP 服务器管理命令

负责注册和管理 MCP (Model Context Protocol) 服务器。
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from rich.console import Console

from qd_agents.config import load_config
from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolMetadata
from qd_agents.tools.executors import create_mcp_tool, extract_mcp_servers_config
from qd_agents.cli.utils.registry import get_tool_registry
from qd_agents.cli.utils.registration import register_tool_and_report
from qd_agents.cli.utils.credentials import resolve_env_vars
from qd_agents.cli.commands.tools.update_cmd import _detect_package_version


logger = logging.getLogger(__name__)


def mcp_add(
    console: Console,
    server: str,
    config_file: Optional[Path] = None,
    base_dir: Optional[Path] = None,
    default: bool = False,
    interactive: bool = True,
) -> None:
    """添加 MCP 服务器（从 JSON 文件读取配置）"""
    # 根据 server 名自动定位 JSON 文件
    json_file = Path("tools/mcp") / f"{server}.json"
    if not json_file.exists():
        console.print(f"[red][ERROR][/] JSON 配置文件不存在: {json_file}")
        return

    # 读取 JSON 配置
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            json_config = json.load(f)
    except json.JSONDecodeError as e:
        console.print(f"[red][ERROR][/] JSON 文件解析失败: {e}")
        return
    except Exception as e:
        console.print(f"[red][ERROR][/] 读取 JSON 文件失败: {e}")
        return

    # 提取服务器配置
    servers, first_server_name = extract_mcp_servers_config(json_config)

    if servers:
        # 用 server 参数匹配
        if server in servers:
            extracted_config = servers[server]
            config_server_name = server
        elif first_server_name:
            extracted_config = servers[first_server_name]
            config_server_name = first_server_name
        else:
            console.print(f"[red][ERROR][/] JSON 文件中未找到服务器配置")
            return
    else:
        # 扁平配置格式
        extracted_config = json_config
        config_server_name = server

    # 从配置中提取参数
    final_name = config_server_name or server
    final_server = config_server_name or server
    final_transport = extracted_config.get("transport", "stdio")
    final_command = extracted_config.get("command")
    final_args = extracted_config.get("args")
    final_url = extracted_config.get("url")
    env_raw = extracted_config.get("env")

    # 处理环境变量（与 skill_add 同样方式）
    env_names: list[str] = []
    env_fixed: dict[str, str] = {}
    if isinstance(env_raw, list):
        env_names = env_raw
    elif isinstance(env_raw, dict):
        for k, v in env_raw.items():
            env_fixed[k] = str(v)

    final_env: dict[str, str] = {}
    final_env.update(env_fixed)
    if env_names:
        resolved, _ = resolve_env_vars(env_names, console, base_dir=base_dir, interactive=interactive)
        final_env.update(resolved)

    final_env = final_env.copy()
    final_env["__mcp_config__"] = json.dumps(json_config, ensure_ascii=False)

    # 解析 args
    parsed_args: list[str] = []
    if final_args:
        if isinstance(final_args, list):
            parsed_args = final_args
        elif isinstance(final_args, str):
            try:
                parsed_args = json.loads(final_args)
                if not isinstance(parsed_args, list):
                    parsed_args = [final_args]
            except json.JSONDecodeError:
                parsed_args = [arg.strip() for arg in final_args.split(",") if arg.strip()]

    # 检测版本
    version, install_source = None, None
    if final_command and parsed_args:
        version, install_source = _detect_package_version(final_command, parsed_args)

    # 注册工具
    tool = create_mcp_tool(
        name=final_name,
        description=f"MCP server: {final_server}",
        server=final_server,
        transport=final_transport,
        command=final_command,
        args=parsed_args,
        url=final_url,
        env=final_env,
        parameters={
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "要执行的 MCP 工具名称"},
                "arguments": {"type": "object", "description": "工具参数", "additionalProperties": True},
            },
            "required": ["tool_name", "arguments"],
        },
        source_path=str(json_file),
        version=version,
        install_source=install_source,
        scope="default" if default else "user",
    )

    register_tool_and_report(tool, console, base_dir=base_dir, config_file=config_file)
    console.print(f"  服务器: {final_server}")
    console.print(f"  传输模式: {final_transport}")
    if final_command:
        console.print(f"  命令: {final_command}")
    if parsed_args:
        console.print(f"  参数: {parsed_args}")
    if final_url:
        console.print(f"  URL: {final_url}")
    if install_source:
        console.print(f"  安装源: {install_source}")
    if version:
        console.print(f"  版本: {version}")
    if env_names:
        console.print(f"  所需环境变量: {', '.join(env_names)}")
