"""工具移除命令"""

from pathlib import Path
from typing import Optional

from rich.console import Console

from qd_agents.config import load_config, load_runtime_config, save_runtime_config
from qd_agents.models.tool import Tool
from qd_agents.cli.utils.credentials import env_var_to_tool_name
from qd_agents.cli.utils.registry import get_tool_registry


def remove_tools(
    console: Console,
    tool_identifier: str,
    base_dir: Optional[Path] = None,
    config_file: Optional[Path] = None,
    keep_credentials: bool = False,
) -> None:
    """移除已注册的工具（支持所有类型：function/cli/http/skill/mcp/bash）

    builtin 和 default 类别的工具受保护，不可删除。

    Args:
        console: Rich 控制台对象
        tool_identifier: 工具名称或 ID
        base_dir: 基础目录
        config_file: 配置文件路径
        keep_credentials: 是否保留工具凭证配置
    """
    config = load_config(base_dir=base_dir, config_file=config_file)
    registry = get_tool_registry(config)

    # 查找工具：先按 ID 查找，再按名称查找
    tool = registry.get(tool_identifier) or registry.get_by_name(tool_identifier)
    if not tool:
        console.print(f"[red][ERROR][/] 未找到工具: {tool_identifier}")
        return

    # 删除保护：builtin 和 default 不可删除
    scope = tool.scope
    if scope in ("builtin", "default"):
        console.print(f"[red][ERROR][/] 工具 {tool.name} 属于 [cyan]{scope}[/] 属性，受保护不可删除")
        if scope == "default":
            console.print("[dim]提示：默认工具可通过 `tools update` 更新版本[/]")
        return

    tool_type = tool.execution.type.value
    console.print(f"即将移除工具: [cyan]{tool.name}[/] (ID: {tool.id}, 类型: {tool_type})")

    # 从注册表删除
    success = registry.delete(tool.id)
    if not success:
        console.print(f"[red][ERROR][/] 移除工具失败: {tool.name}")
        return

    console.print(f"[green][OK][/] 已移除工具: {tool.name}")

    # 清理 runtime.json 中的对应配置
    if not keep_credentials and tool.execution.env:
        _cleanup_credentials(console, tool, base_dir)


def _cleanup_credentials(
    console: Console,
    tool: Tool,
    base_dir: Optional[Path],
) -> None:
    """清理工具对应的 credentials 配置（runtime.json）"""
    runtime_config = load_runtime_config(base_dir=base_dir)
    removed = []
    for env_var in tool.execution.env:
        tool_name = env_var_to_tool_name(env_var)
        if runtime_config.tools_credentials.tools and tool_name in runtime_config.tools_credentials.tools:
            del runtime_config.tools_credentials.tools[tool_name]
            removed.append(f"{env_var} (tools_credentials.{tool_name})")

    if removed:
        save_runtime_config(runtime_config, base_dir=base_dir)
        for item in removed:
            console.print(f"  [dim]已清理凭证配置: {item}[/]")