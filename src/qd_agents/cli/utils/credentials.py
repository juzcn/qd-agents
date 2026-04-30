"""
CLI 凭证工具函数

环境变量名到工具名的映射，供 skills 和 tools 命令共享。
"""

import os
from pathlib import Path
from typing import Optional, List

from rich.console import Console

from qd_agents.config import load_runtime_config, save_runtime_config

# 环境变量名到 tools_credentials 中工具名的映射
ENV_TO_TOOL_NAME_MAP: dict[str, str] = {
    "BAIDU_API_KEY": "baidu_search",
    "SERPER_API_KEY": "serper_search",
    "TAVILY_API_KEY": "tavily_search",
}


def env_var_to_tool_name(env_var: str) -> str:
    """将环境变量名转换为 tools_credentials 中的工具名。"""
    if env_var in ENV_TO_TOOL_NAME_MAP:
        return ENV_TO_TOOL_NAME_MAP[env_var]

    if env_var.endswith("_API_KEY"):
        return env_var[:-len("_API_KEY")].lower()

    return env_var.lower()


def resolve_env_vars(
    env_vars: List[str],
    console: Console,
    base_dir: Optional[Path] = None,
    interactive: bool = True,
) -> tuple[dict[str, str], bool]:
    """从 runtime.json tools_credentials 解析环境变量。

    Args:
        env_vars: 需要解析的环境变量名列表
        console: Rich 控制台
        base_dir: 基础目录
        interactive: 是否交互式输入缺失的 API Key

    Returns:
        (env_dict, runtime_changed) — env_dict 映射 var_name → value，
        runtime_changed 表示 runtime.json 是否被修改。
    """
    env: dict[str, str] = {}
    runtime_changed = False
    runtime_config = load_runtime_config(base_dir=base_dir)

    for var in env_vars:
        tool_name = env_var_to_tool_name(var)
        api_key_value = runtime_config.tools_credentials.get_api_key(tool_name)
        if api_key_value:
            env[var] = api_key_value
            console.print(f"  [dim]{var}[/]: 从 runtime.json (tools_credentials.{tool_name}) 加载")
        else:
            if interactive:
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
            else:
                env[var] = os.environ.get(var, "")

    if runtime_changed:
        save_runtime_config(runtime_config, base_dir=base_dir)
        console.print("  [dim]runtime.json 已更新[/]")

    return env, runtime_changed