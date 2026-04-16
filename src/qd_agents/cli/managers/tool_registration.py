"""
工具注册管理

负责自动注册 MCP 工具和其他内置工具。
"""

from pathlib import Path
from typing import Optional

from rich.console import Console

from qd_agents.registry import ToolRegistry
from qd_agents.tools.executor import create_mcp_tool


async def auto_register_mcp_weather_tools(
    console: Console,
    tool_registry: ToolRegistry,
    endpoint: str = "http://localhost:8000",
) -> None:
    """
    自动注册 MCP 天气工具（如果尚未注册）

    Args:
        console: Rich 控制台对象，用于输出信息
        tool_registry: 工具注册表
        endpoint: MCP 服务器端点
    """
    # 检查当前天气工具是否已存在
    if not tool_registry.get("weather.get_current_weather"):
        current_weather_tool = create_mcp_tool(
            name="get_current_weather",
            description="获取指定城市的当前天气信息，包括温度、湿度、风速、天气描述等",
            server="weather",
            tool_name="get_current_weather",
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名称，如 'Beijing' 或 '上海'"},
                    "country": {"type": "string", "description": "国家代码，如 'CN'，可选"},
                    "latitude": {"type": "number", "description": "纬度，可选"},
                    "longitude": {"type": "number", "description": "经度，可选"},
                },
                "required": ["city"],
            },
            transport="sse",
            endpoint=endpoint,
            category="weather",
            tags=["weather", "mcp", "current"],
        )
        tool_registry.register(current_weather_tool)
        console.print("[dim]✅ 已自动注册工具: weather.get_current_weather[/]", style="dim")

    # 检查空气质量工具是否已存在
    if not tool_registry.get("weather.get_air_quality"):
        air_quality_tool = create_mcp_tool(
            name="get_air_quality",
            description="获取指定城市的空气质量信息，包括 PM2.5、PM10、臭氧、NO₂、CO 等级和健康建议",
            server="weather",
            tool_name="get_air_quality",
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名称"},
                    "country": {"type": "string", "description": "国家代码，可选"},
                },
                "required": ["city"],
            },
            transport="sse",
            endpoint=endpoint,
            category="air-quality",
            tags=["air-quality", "mcp", "pollution"],
        )
        tool_registry.register(air_quality_tool)
        console.print("[dim]✅ 已自动注册工具: weather.get_air_quality[/]", style="dim")