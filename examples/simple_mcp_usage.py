#!/usr/bin/env python3
"""
简单 MCP 工具使用示例

演示如何创建、注册和使用 MCP 天气工具的最小示例
"""

import asyncio
import json
from pathlib import Path
import sys

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.qd_agents.registry import ToolRegistry, Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType
from src.qd_agents.tools.executor import ToolExecutorRegistry, create_executor


async def main() -> None:
    """主函数"""
    print("🔧 创建 MCP 天气工具的最小示例")
    print("=" * 50)

    # 1. 创建工具注册表
    db_path = Path("data/tools.db")
    registry = ToolRegistry(db_path)

    # 2. 创建 MCP 天气工具定义
    weather_tool = Tool(
        id="weather.get_current_weather",
        name="get_current_weather",
        description="获取指定城市的当前天气信息",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名称，如 'Beijing'"},
                "country": {"type": "string", "description": "国家代码，如 'CN'，可选"},
            },
            "required": ["city"],
        },
        execution=ToolExecutionConfig(
            type=ToolExecutionType.MCP,
            server="weather",
            tool="get_current_weather",
            transport="sse",  # 使用 SSE HTTP 模式
            endpoint="http://localhost:8000",  # MCP 服务器地址
            timeout=30,
        ),
        metadata=ToolMetadata(
            category="weather",
            tags=["weather", "mcp", "current"],
        ),
    )

    # 3. 注册工具
    registry.register(weather_tool)
    print(f"✅ 已注册工具: {weather_tool.id}")
    print(f"   描述: {weather_tool.description}")
    print()

    # 4. 获取工具并创建执行器
    retrieved_tool = registry.get("weather.get_current_weather")
    if not retrieved_tool:
        print("❌ 工具检索失败")
        return

    print(f"✅ 成功检索到工具: {retrieved_tool.name}")
    print()

    # 5. 创建执行器注册表
    executor_registry = ToolExecutorRegistry()

    # 6. 获取执行器
    executor = executor_registry.get_executor(retrieved_tool)
    print(f"✅ 创建执行器: {type(executor).__name__}")
    print()

    # 7. 执行工具（演示模式）
    print("🚀 执行 MCP 天气工具（演示模式）")
    print("注意: 实际执行需要启动 MCP 天气服务器")
    print()

    try:
        # 由于 MCP 服务器未运行，这里会触发简化实现
        result = await executor.execute(city="Beijing", country="CN")
        print(f"✅ 执行结果:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except NotImplementedError as e:
        print(f"⚠️  工具执行需要 MCP 服务器: {e}")
        print()
        print("📋 启动 MCP 天气服务器的步骤:")
        print("1. 安装: `uv add mcp-weather-server`")
        print("2. 启动服务器:")
        print("   - stdio 模式: 集成到 Claude Desktop")
        print("   - HTTP SSE 模式: `mcp-weather-server --mode sse --port 8000`")
        print("3. 更新工具配置中的 endpoint")
    except Exception as e:
        print(f"❌ 执行错误: {e}")

    print()
    print("=" * 50)
    print("✅ 示例完成")
    print()
    print("📝 后续步骤:")
    print("1. 实际启动 MCP 天气服务器")
    print("2. 更新工具定义中的 endpoint")
    print("3. 实现完整的 MCP 客户端（使用 mcp 库）")
    print("4. 将工具集成到主应用程序中")


if __name__ == "__main__":
    asyncio.run(main())