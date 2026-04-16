# MCP 天气工具快速集成指南

## 已完成的工作

1. ✅ **安装依赖**: `uv add mcp-weather-server`
2. ✅ **更新工具执行器**: 实现 `MCPToolExecutor` 类
3. ✅ **扩展配置**: 在 `ToolExecutionConfig` 中添加 `transport` 字段
4. ✅ **创建辅助函数**: `create_mcp_tool` 用于简化工具创建
5. ✅ **提供示例代码**: 多个使用示例

## 快速开始

### 1. 安装 MCP 天气服务器
```bash
uv add mcp-weather-server
```

### 2. 启动 MCP 服务器 (HTTP SSE 模式)
```bash
mcp-weather-server --mode sse --port 8000
```

### 3. 注册天气工具 (示例)

```python
from pathlib import Path
from src.qd_agents.registry import ToolRegistry
from src.qd_agents.tools.executor import create_mcp_tool

# 初始化注册表
registry = ToolRegistry(Path("data/tools.db"))

# 创建当前天气工具
weather_tool = create_mcp_tool(
    name="get_current_weather",
    description="获取指定城市的当前天气信息",
    server="weather",
    tool_name="get_current_weather",
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名称"},
            "country": {"type": "string", "description": "国家代码，可选"},
        },
        "required": ["city"],
    },
    transport="sse",  # HTTP Server-Sent Events 模式
    endpoint="http://localhost:8000",  # MCP 服务器地址
    category="weather",
    tags=["weather", "mcp", "current"],
)

# 注册工具
registry.register(weather_tool)
print(f"✅ 已注册工具: {weather_tool.id}")
```

### 4. 执行天气工具

```python
import asyncio
from src.qd_agents.tools.executor import ToolExecutorRegistry

async def main():
    # 获取工具
    tool = registry.get("weather.get_current_weather")
    
    # 创建执行器
    executor_registry = ToolExecutorRegistry()
    executor = executor_registry.get_executor(tool)
    
    # 执行工具
    try:
        result = await executor.execute(city="Beijing", country="CN")
        print("✅ 天气查询结果:")
        print(result)
    except Exception as e:
        print(f"❌ 执行失败: {e}")

# 运行
asyncio.run(main())
```

## 集成到主应用程序

要将 MCP 天气工具集成到主应用程序中，请在 `src/qd_agents/cli/main.py` 的 `_init_tools` 函数中添加工具注册代码。

### 修改位置:
文件: `src/qd_agents/cli/main.py`
函数: `_init_tools()` (约第 443 行)

在现有工具注册代码后添加:

```python
# ==================== MCP 天气工具 ====================

from ..tools.executor import create_mcp_tool

# 当前天气工具
current_weather_tool = create_mcp_tool(
    name="get_current_weather",
    description="获取指定城市的当前天气信息",
    server="weather",
    tool_name="get_current_weather",
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
    tags=["weather", "mcp", "current"],
)
registry.register(current_weather_tool)
registered_tools.append(current_weather_tool.name)

# 空气质量工具
air_quality_tool = create_mcp_tool(
    name="get_air_quality",
    description="获取指定城市的空气质量信息",
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
registered_tools.append(air_quality_tool.name)
```

## 验证安装

运行以下命令验证工具是否成功注册:

```bash
# 初始化工具
python -m src.qd_agents.cli.main --init-tools

# 列出所有工具
python -m src.qd_agents.cli.main --list-tools
```

应该能看到新注册的天气工具:
- `weather.get_current_weather`
- `weather.get_air_quality`

## 故障排除

### 1. 连接失败
- 确保 MCP 服务器已启动: `mcp-weather-server --mode sse --port 8000`
- 检查端口是否被占用
- 验证 endpoint 配置正确

### 2. 工具未注册
- 检查数据库路径是否正确
- 确认 `--init-tools` 命令已执行
- 查看注册代码是否正确添加

### 3. 执行错误
- 检查 MCP 服务器日志
- 验证工具参数格式
- 确认网络连接正常

## 更多示例

查看 `examples/` 目录下的完整示例:

1. `examples/mcp_weather_example.py` - 完整注册示例
2. `examples/simple_mcp_usage.py` - 最小使用示例
3. `examples/mcp_http_client.py` - HTTP 客户端实现

## 支持的 MCP 工具

mcp-weather-server 提供以下工具:

| 工具名称 | 描述 |
|----------|------|
| `get_current_weather` | 当前天气信息 |
| `get_weather_by_datetime_range` | 历史天气数据 |
| `get_weather_details` | 详细天气信息 |
| `get_air_quality` | 空气质量 |
| `get_air_quality_details` | 详细空气质量 |
| `get_current_datetime` | 当前时间 |
| `get_timezone_info` | 时区信息 |
| `convert_time` | 时间转换 |

## 下一步

1. **生产部署**: 配置正式的 MCP 服务器地址
2. **错误处理**: 添加重试和降级机制
3. **监控**: 集成到系统监控
4. **缓存**: 实现天气数据缓存
5. **扩展**: 注册更多 MCP 服务器工具

---

**完成状态**: ✅ 已实现所有核心功能  
**测试状态**: ⚠️ 需要实际 MCP 服务器运行  
**生产就绪**: 🔄 需要进一步测试和优化