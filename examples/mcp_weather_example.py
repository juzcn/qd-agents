#!/usr/bin/env python3
"""
MCP 天气工具示例

演示如何注册和使用 mcp-weather-server 中的 MCP 天气工具

先决条件：
1. 安装 mcp-weather-server: `uv add mcp-weather-server`
2. 启动 MCP 天气服务器（例如使用 HTTP 模式）:
   - stdio 模式: 直接集成到 Claude Desktop 等客户端
   - HTTP 模式: `mcp-weather-server --mode sse --port 8000`
"""

import asyncio
from pathlib import Path
import sys

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.qd_agents.registry import ToolRegistry
from src.qd_agents.tools.executor import create_mcp_tool


def register_weather_tools(registry: ToolRegistry) -> None:
    """注册所有 MCP 天气工具"""

    # 注意：实际使用需要根据 MCP 服务器的部署方式配置 endpoint 和 transport

    # 1. 获取当前天气
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
        transport="sse",  # 或 "stdio", "streamable-http"
        endpoint="http://localhost:8000",  # 如果使用 HTTP 模式
        category="weather",
        tags=["weather", "mcp", "current"],
    )
    registry.register(current_weather_tool)
    print(f"✅ 已注册工具: {current_weather_tool.id}")

    # 2. 获取天气详情
    weather_details_tool = create_mcp_tool(
        name="get_weather_details",
        description="获取指定城市的详细天气信息（结构化 JSON 输出）",
        server="weather",
        tool_name="get_weather_details",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名称"},
                "country": {"type": "string", "description": "国家代码，可选"},
            },
            "required": ["city"],
        },
        transport="sse",
        endpoint="http://localhost:8000",
        category="weather",
        tags=["weather", "mcp", "details"],
    )
    registry.register(weather_details_tool)
    print(f"✅ 已注册工具: {weather_details_tool.id}")

    # 3. 获取空气质量
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
        endpoint="http://localhost:8000",
        category="air-quality",
        tags=["air-quality", "mcp", "pollution"],
    )
    registry.register(air_quality_tool)
    print(f"✅ 已注册工具: {air_quality_tool.id}")

    # 4. 获取当前时间
    current_time_tool = create_mcp_tool(
        name="get_current_datetime",
        description="获取指定时区的当前日期和时间",
        server="weather",
        tool_name="get_current_datetime",
        parameters={
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "description": "时区，如 'Asia/Shanghai'，可选"},
            },
            "required": [],
        },
        transport="sse",
        endpoint="http://localhost:8000",
        category="time",
        tags=["time", "mcp", "datetime"],
    )
    registry.register(current_time_tool)
    print(f"✅ 已注册工具: {current_time_tool.id}")

    # 5. 时间转换
    convert_time_tool = create_mcp_tool(
        name="convert_time",
        description="将时间从一个时区转换到另一个时区",
        server="weather",
        tool_name="convert_time",
        parameters={
            "type": "object",
            "properties": {
                "datetime": {"type": "string", "description": "日期时间字符串，格式如 '2026-04-16T10:30:00'"},
                "from_timezone": {"type": "string", "description": "源时区，如 'UTC'"},
                "to_timezone": {"type": "string", "description": "目标时区，如 'Asia/Shanghai'"},
            },
            "required": ["datetime", "from_timezone", "to_timezone"],
        },
        transport="sse",
        endpoint="http://localhost:8000",
        category="time",
        tags=["time", "mcp", "conversion"],
    )
    registry.register(convert_time_tool)
    print(f"✅ 已注册工具: {convert_time_tool.id}")


async def demonstrate_tool_usage() -> None:
    """演示如何使用已注册的 MCP 工具"""

    # 初始化工具注册表
    db_path = Path("data/tools.db")
    registry = ToolRegistry(db_path)

    # 注册天气工具
    print("🔧 正在注册 MCP 天气工具...")
    register_weather_tools(registry)
    print("✅ 所有工具注册完成")
    print()

    # 列出所有工具
    print("📋 已注册的工具列表:")
    all_tools = registry.list_all()
    for tool in all_tools:
        print(f"  - {tool.id}: {tool.description}")
    print()

    # 搜索天气相关的工具
    print("🔍 搜索 'weather' 相关的工具:")
    weather_tools = registry.search("weather", top_k=5)
    for tool in weather_tools:
        print(f"  - {tool.id}: {tool.description}")
    print()

    # 注意：实际执行需要启动 MCP 天气服务器
    # 这里只是演示注册过程
    print("📝 使用说明:")
    print("1. 启动 MCP 天气服务器:")
    print("   - stdio 模式: 集成到 Claude Desktop 配置中")
    print("   - HTTP SSE 模式: `mcp-weather-server --mode sse --port 8000`")
    print("2. 更新工具注册的 endpoint 为实际服务器地址")
    print("3. 使用 registry.get('weather.get_current_weather') 获取工具定义")
    print("4. 使用 ToolExecutorRegistry 创建执行器并执行")


def main() -> None:
    """主函数"""
    print("=" * 60)
    print("MCP 天气工具注册示例")
    print("=" * 60)
    print()

    # 同步函数调用异步演示
    asyncio.run(demonstrate_tool_usage())


if __name__ == "__main__":
    main()