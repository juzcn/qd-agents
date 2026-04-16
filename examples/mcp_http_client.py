#!/usr/bin/env python3
"""
MCP HTTP 客户端示例

演示如何通过 HTTP 与 MCP 天气服务器通信
适用于 SSE 或 Streamable HTTP 模式
"""

import asyncio
import json
import httpx
import sseclient


async def call_mcp_tool_via_sse(
    endpoint: str,
    tool_name: str,
    params: dict,
    timeout: float = 30.0,
) -> dict:
    """
    通过 SSE (Server-Sent Events) 调用 MCP 工具

    Args:
        endpoint: MCP 服务器 SSE 端点，如 "http://localhost:8000/sse"
        tool_name: 工具名称，如 "get_current_weather"
        params: 工具参数
        timeout: 超时时间（秒）

    Returns:
        工具执行结果
    """
    # 注意：mcp-weather-server 的 SSE 端点可能需要特定的路径
    # 这里是一个通用的 SSE 调用示例

    print(f"🔗 连接到 SSE 端点: {endpoint}")
    print(f"🛠️  调用工具: {tool_name}")
    print(f"📦 参数: {json.dumps(params, indent=2)}")

    # 实际实现需要处理 SSE 流
    # 这里简化实现，直接使用 HTTP POST
    async with httpx.AsyncClient(timeout=timeout) as client:
        # 根据 MCP HTTP 协议发送请求
        response = await client.post(
            endpoint,
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": params,
                },
                "id": 1,
            }
        )

        response.raise_for_status()
        result = response.json()

        if "error" in result:
            raise RuntimeError(f"MCP 工具调用错误: {result['error']}")

        return result.get("result", {})


async def call_mcp_tool_via_streamable_http(
    endpoint: str,
    tool_name: str,
    params: dict,
    timeout: float = 30.0,
) -> dict:
    """
    通过 Streamable HTTP 调用 MCP 工具

    Args:
        endpoint: MCP 服务器 Streamable HTTP 端点
        tool_name: 工具名称
        params: 工具参数
        timeout: 超时时间（秒）

    Returns:
        工具执行结果
    """
    print(f"🔗 连接到 Streamable HTTP 端点: {endpoint}")
    print(f"🛠️  调用工具: {tool_name}")

    # Streamable HTTP 模式可能使用不同的协议
    # 这里简化实现，使用与 SSE 相同的方式
    return await call_mcp_tool_via_sse(endpoint, tool_name, params, timeout)


async def main() -> None:
    """主函数"""
    print("=" * 60)
    print("MCP HTTP 客户端示例")
    print("=" * 60)
    print()

    # 配置
    endpoint = "http://localhost:8000"  # MCP 服务器地址
    tool_name = "get_current_weather"
    params = {
        "city": "Beijing",
        "country": "CN",
    }

    print("📋 配置信息:")
    print(f"   服务器: {endpoint}")
    print(f"   工具: {tool_name}")
    print(f"   参数: {json.dumps(params, indent=4)}")
    print()

    try:
        # 尝试调用工具
        print("🚀 正在调用 MCP 工具...")
        result = await call_mcp_tool_via_sse(endpoint, tool_name, params)

        print("✅ 调用成功!")
        print("📊 结果:")
        print(json.dumps(result, indent=2, ensure_ascii=False))

    except httpx.ConnectError:
        print("❌ 连接失败: 无法连接到 MCP 服务器")
        print()
        print("💡 请确保 MCP 天气服务器已启动:")
        print("   1. 安装: `uv add mcp-weather-server`")
        print("   2. 启动 HTTP 模式: `mcp-weather-server --mode sse --port 8000`")
        print("   3. 等待服务器启动完成")
        print("   4. 重新运行此示例")
    except Exception as e:
        print(f"❌ 调用失败: {e}")
        print()
        print("💡 可能的解决方案:")
        print("   1. 检查服务器地址和端口是否正确")
        print("   2. 确认服务器支持 HTTP 模式")
        print("   3. 查看服务器日志以获取更多信息")

    print()
    print("=" * 60)
    print("📚 集成到工具注册表的步骤:")
    print("   1. 创建 MCP 工具定义 (使用 create_mcp_tool 函数)")
    print("   2. 注册到 ToolRegistry")
    print("   3. 实现 MCPToolExecutor 使用此 HTTP 客户端")
    print("   4. 通过 ToolExecutorRegistry 获取和执行工具")


if __name__ == "__main__":
    asyncio.run(main())