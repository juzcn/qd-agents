"""
MCP 服务器管理命令

负责注册和管理 MCP (Model Context Protocol) 服务器。
"""
import asyncio
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

import typer
from rich.console import Console

from qd_agents.config import load_config
from qd_agents.registry import ToolRegistry
from qd_agents.models.tool import Tool, ToolExecutionConfig, ToolMetadata
from qd_agents.tools.executors import create_mcp_tool, extract_mcp_servers_config


logger = logging.getLogger(__name__)


def _detect_package_version(command: str, args: list[str]) -> tuple[str | None, str | None]:
    """检测 MCP 工具包的版本和安装源

    Returns:
        (version, install_source) — 版本号和安装源（如 npm/pip 包名）
    """
    install_source = None
    version = None

    # 从 args 中提取包名
    if command in ("npx", "npm") and args:
        # npx -y @scope/package → 提取 @scope/package
        filtered = [a for a in args if a not in ("-y", "--yes", "--")]
        if filtered:
            install_source = filtered[0]
    elif command in ("uvx", "pip") and args:
        # uvx package-name → 提取 package-name
        filtered = [a for a in args if not a.startswith("-")]
        if filtered:
            install_source = filtered[0]

    if not install_source:
        return None, None

    # 尝试获取已安装版本
    try:
        if command in ("npx", "npm"):
            # npm list 输出格式: @scope/package@1.2.3
            result = subprocess.run(
                ["npm", "list", "-g", install_source, "--depth=0", "--json"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                deps = data.get("dependencies", {})
                if install_source in deps:
                    version = deps[install_source].get("version")
        elif command in ("uvx", "pip"):
            # pip show 输出格式: Version: 1.2.3
            result = subprocess.run(
                ["pip", "show", install_source],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if line.startswith("Version:"):
                        version = line.split(":", 1)[1].strip()
                        break
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass

    return version, install_source


async def mcp_add_async(
    console: Console,
    name: str,
    server: str,
    transport: str = "stdio",
    command: Optional[str] = None,
    args: Optional[str] = None,
    url: Optional[str] = None,
    config_file: Optional[Path] = None,
    base_dir: Optional[Path] = None,
    json_file: Optional[Path] = None,
) -> None:
    """
    添加 MCP 服务器

    Args:
        console: Rich 控制台对象
        name: 工具名称（必需）
        server: MCP服务器标识（必需）
        transport: 传输模式 ("stdio", "sse", "streamable-http")
        command: stdio 模式下的命令
        args: stdio 模式下的参数（JSON 字符串或逗号分隔）
        url: SSE 或 streamable-http 模式的 URL
        config_file: 配置文件路径
        base_dir: 基础目录
        json_file: JSON配置文件路径（如果提供，将从文件读取配置）
    """
    # 如果提供了 JSON 文件，从中读取配置
    json_config = {}
    if json_file:
        if not json_file.exists():
            console.print(f"[red][ERROR][/] JSON 文件不存在: {json_file}")
            return
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                json_config = json.load(f)
        except json.JSONDecodeError as e:
            console.print(f"[red][ERROR][/] JSON 文件解析失败: {e}")
            return
        except Exception as e:
            console.print(f"[red][ERROR][/] 读取 JSON 文件失败: {e}")
            return

    # 从 JSON 配置中提取 MCP 服务器配置
    extracted_config = {}
    config_server_name = None  # 从配置中提取的服务器名

    if json_config:
        # 使用辅助函数提取服务器配置字典
        servers, first_server_name = extract_mcp_servers_config(json_config)

        if servers:
            # 保存配置中的服务器名
            config_server_name = first_server_name

            # 使用 server 参数作为键查找配置
            if server and server in servers:
                extracted_config = servers[server]
                # 如果命令行提供了 server 参数且匹配，使用它
                config_server_name = server
            else:
                # 如果没有匹配的 server，使用第一个服务器
                if first_server_name:
                    extracted_config = servers[first_server_name]
                # 命令行提供的 server 不匹配，所以使用配置中的服务器名
                # config_server_name 已经是 first_server_name
        else:
            # 扁平配置格式
            extracted_config = json_config

    # 合并参数：JSON 配置作为默认值，命令行参数优先级更高
    # 如果命令行没有提供 name，尝试使用配置中的服务器名
    final_name = name if name is not None else (config_server_name or extracted_config.get("name"))
    # 总是使用配置中的服务器名，确保与配置一致，如果都没有则使用命令行提供的 server
    final_server = config_server_name or extracted_config.get("server") or server
    final_transport = transport if transport != "stdio" or "transport" not in extracted_config else extracted_config.get("transport", "stdio")
    final_command = command if command is not None else extracted_config.get("command")
    final_args = args if args is not None else extracted_config.get("args")
    final_url = url if url is not None else extracted_config.get("url")
    # 获取环境变量配置，并添加完整的 JSON 配置用于 MCP 库解析
    final_env = extracted_config.get("env") or {}
    if not isinstance(final_env, dict):
        final_env = {}
    # 将完整的 JSON 配置存储为环境变量，供 MCP 库使用
    final_env = final_env.copy()
    final_env["__mcp_config__"] = json.dumps(json_config, ensure_ascii=False)

    # 验证必需参数
    if not final_name:
        console.print("[red][ERROR][/] 工具名称不能为空")
        return
    if not final_server:
        console.print("[red][ERROR][/] MCP服务器标识不能为空")
        return

    # 如果 args 是列表（来自 JSON），将其转换为 JSON 字符串以便后续解析
    if final_args and isinstance(final_args, list):
        final_args = json.dumps(final_args, ensure_ascii=False)

    config = load_config(base_dir=base_dir, config_file=config_file)

    db_path = config.tool_registry.db_path if config.tool_registry else Path("data/tools.db")
    registry = ToolRegistry(db_path=db_path)

    # 解析 args 参数
    parsed_args: list[str] = []
    if final_args:
        # 首先去除可能的额外引号或空格
        args_str = final_args.strip()

        # 调试：输出接收到的args值
        logger.debug(f"Received args string: {repr(args_str)}")

        # 尝试多种解析方式
        try:
            # 方法1：尝试直接解析为JSON数组
            parsed_args = json.loads(args_str)
            if not isinstance(parsed_args, list):
                raise ValueError("args must be a JSON array")
            logger.debug(f"Successfully parsed as JSON array: {parsed_args}")
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug(f"Failed to parse as JSON: {e}")

            # 方法2：如果看起来像数组（以 [ 开头，以 ] 结尾）但不是有效JSON
            if args_str.startswith('[') and args_str.endswith(']'):
                # 移除方括号并尝试解析内容
                inner = args_str[1:-1].strip()
                logger.debug(f"Extracted inner content: {repr(inner)}")

                if inner:
                    # 尝试解析内部内容
                    try:
                        # 如果内部是JSON字符串（可能有引号）
                        if (inner.startswith('"') and inner.endswith('"')) or \
                           (inner.startswith("'") and inner.endswith("'")):
                            # 去除引号
                            inner = inner[1:-1]
                            parsed_args = [inner]
                        else:
                            # 尝试按逗号分割
                            parsed_args = [item.strip() for item in inner.split(",") if item.strip()]
                    except Exception:
                        # 如果所有方法都失败，将整个内容作为单个参数
                        parsed_args = [inner]
            else:
                # 方法3：尝试按逗号分割
                parsed_args = [arg.strip() for arg in args_str.split(",") if arg.strip()]

        # 如果解析后结果为空，但原始args非空，将其作为单个参数
        if not parsed_args and args_str:
            parsed_args = [args_str]

        logger.debug(f"Final parsed args: {parsed_args}")

    # 检测版本和安装源
    version, install_source = None, None
    if final_command and parsed_args:
        version, install_source = _detect_package_version(final_command, parsed_args)

    # 创建 MCP 工具
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
        source_path=str(json_file) if json_file else final_server,
        version=version,
        install_source=install_source,
    )

    # 注册工具
    tool_id = registry.register(tool)

    console.print(f"[green][OK][/] 已注册 MCP 服务器: {final_name} ({tool_id})")
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


def mcp_add(
    console: Console,
    name: str = typer.Argument(..., help="工具名称（必需）"),
    server: str = typer.Argument(..., help="MCP服务器标识（必需）"),
    transport: str = typer.Option("stdio", "--transport", "-t", help="传输模式: stdio, sse, streamable-http"),
    command: Optional[str] = typer.Option(None, "--command", "-c", help="stdio 模式下的命令"),
    args: Optional[str] = typer.Option(None, "--args", "-a", help="stdio 模式下的参数 (JSON 数组或逗号分隔)"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="SSE 或 streamable-http 模式的 URL"),
    config_file: Optional[Path] = typer.Option(None, "--config", help="配置文件路径"),
    base_dir: Optional[Path] = typer.Option(None, "--base-dir", "-d", help="基础目录"),
    json_file: Optional[Path] = typer.Option(None, "--json", "-j", help="JSON配置文件路径（如果提供，将从文件读取配置）"),
) -> None:
    """添加 MCP 服务器"""
    asyncio.run(mcp_add_async(
        console=console,
        name=name,
        server=server,
        transport=transport,
        command=command,
        args=args,
        url=url,
        config_file=config_file,
        base_dir=base_dir,
        json_file=json_file,
    ))
