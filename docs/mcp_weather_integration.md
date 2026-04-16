# MCP 天气工具集成指南

本文档介绍如何将 `mcp-weather-server` 集成到 QD Agents 的工具注册表中。

## 1. 概述

`mcp-weather-server` 是一个符合 MCP (Model Context Protocol) 标准的天气信息服务器，提供：
- 当前天气信息
- 天气预报
- 空气质量数据
- 时间和时区服务

## 2. 安装

```bash
uv add mcp-weather-server
```

这将自动安装以下依赖：
- `mcp>=1.0.0` (项目已有依赖)
- `httpx>=0.28.1`
- `python-dateutil>=2.8.2`
- `six` (兼容库)

## 3. MCP 服务器启动

### 3.1 支持的模式

1. **stdio 模式** (默认): 集成到 Claude Desktop 等客户端
   ```
   # 在 cline_mcp_settings.json 中配置
   ```

2. **SSE 模式** (HTTP Server-Sent Events):
   ```
   mcp-weather-server --mode sse --port 8000
   ```

3. **Streamable HTTP 模式**:
   ```
   mcp-weather-server --mode streamable-http --port 8000
   ```

### 3.2 Docker 部署
```bash
docker run -p 8000:8000 dog830228/mcp_weather_server:latest
```

## 4. 工具注册

### 4.1 工具列表

| 工具名称 | 描述 | 参数 |
|---------|------|------|
| `get_current_weather` | 获取当前天气信息 | `city` (必需), `country`, `latitude`, `longitude` |
| `get_weather_by_datetime_range` | 获取历史天气数据 | `city`, `start_date`, `end_date` |
| `get_weather_details` | 获取详细天气信息 | `city`, `country` |
| `get_air_quality` | 获取空气质量 | `city`, `country` |
| `get_air_quality_details` | 获取详细空气质量 | `city`, `country` |
| `get_current_datetime` | 获取当前时间 | `timezone` (可选) |
| `get_timezone_info` | 获取时区信息 | `timezone` |
| `convert_time` | 时间转换 | `datetime`, `from_timezone`, `to_timezone` |

### 4.2 注册代码示例

在 `src/qd_agents/cli/main.py` 的 `_init_tools` 函数中添加：

```python
# ==================== MCP 天气工具 ====================

# get_current_weather
current_weather_tool = Tool(
    id="weather.get_current_weather",
    name="get_current_weather",
    description="获取指定城市的当前天气信息，包括温度、湿度、风速、天气描述等",
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
    execution=ToolExecutionConfig(
        type=ToolExecutionType.MCP,
        server="weather",
        tool="get_current_weather",
        transport="sse",  # 或 "stdio", "streamable-http"
        endpoint="http://localhost:8000",  # 如果使用 HTTP 模式
        timeout=30,
    ),
    metadata=ToolMetadata(
        category="weather",
        tags=["weather", "mcp", "current"],
    ),
)
registry.register(current_weather_tool)
registered_tools.append(current_weather_tool.name)

# 类似地注册其他天气工具...
```

### 4.3 使用辅助函数

更简单的方法是使用 `create_mcp_tool` 辅助函数：

```python
from ..tools.executor import create_mcp_tool

# 注册当前天气工具
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
```

## 5. MCP 工具执行器

### 5.1 执行器实现

已更新 `MCPToolExecutor` 类以支持多种传输模式：

```python
class MCPToolExecutor(ToolExecutor):
    """MCP 工具执行器"""
    
    def __init__(self, server: str, tool: str, transport: str = "stdio", endpoint: str | None = None):
        self.server = server
        self.tool = tool
        self.transport = transport
        self.endpoint = endpoint
    
    async def execute(self, **kwargs):
        if self.transport in ["sse", "streamable-http"]:
            return await self._execute_http(**kwargs)
        else:
            # stdio 模式需要完整的 MCP 客户端实现
            return await self._execute_simplified(**kwargs)
```

### 5.2 HTTP 模式调用

```python
async def _execute_http(self, **kwargs):
    """通过 HTTP 执行 MCP 工具"""
    import httpx
    
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            self.endpoint,
            json={
                "method": f"tools/{self.tool}/call",
                "params": kwargs,
            }
        )
        response.raise_for_status()
        return response.json().get("result", {})
```

## 6. 配置更新

### 6.1 更新 ToolExecutionConfig

在 `src/qd_agents/registry/registry.py` 中添加 `transport` 字段：

```python
class ToolExecutionConfig(BaseModel):
    """工具执行配置"""
    type: ToolExecutionType
    endpoint: str | None = None
    method: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    timeout: int = 30
    module: str | None = None
    function: str | None = None
    skill_id: str | None = None
    server: str | None = None
    tool: str | None = None
    transport: str = "stdio"  # 新增字段：MCP 传输模式
```

### 6.2 更新执行器创建逻辑

在 `src/qd_agents/tools/executor.py` 中更新：

```python
elif exec_config.type == ToolExecutionType.MCP:
    if not exec_config.server or not exec_config.tool:
        raise ValueError("MCP tool requires server and tool")
    return MCPToolExecutor(
        server=exec_config.server,
        tool=exec_config.tool,
        transport=exec_config.transport,  # 传递 transport
        endpoint=exec_config.endpoint,     # 传递 endpoint
    )
```

## 7. 使用示例

### 7.1 注册工具

```python
from src.qd_agents.registry import ToolRegistry
from src.qd_agents.tools.executor import create_mcp_tool

# 初始化注册表
registry = ToolRegistry(Path("data/tools.db"))

# 注册天气工具
weather_tool = create_mcp_tool(
    name="get_current_weather",
    description="获取当前天气信息",
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
    tags=["weather", "mcp"],
)

registry.register(weather_tool)
```

### 7.2 执行工具

```python
from src.qd_agents.tools.executor import ToolExecutorRegistry

# 获取工具
tool = registry.get("weather.get_current_weather")

# 创建执行器
executor_registry = ToolExecutorRegistry()
executor = executor_registry.get_executor(tool)

# 执行
result = await executor.execute(city="Beijing", country="CN")
print(result)
```

## 8. 测试

运行示例代码：

```bash
# 启动 MCP 天气服务器（如果需要）
mcp-weather-server --mode sse --port 8000 &

# 运行示例
python examples/simple_mcp_usage.py
```

## 9. 注意事项

1. **服务器部署**: MCP 服务器需要独立运行，工具注册表只负责配置和调用
2. **传输模式**: 根据部署环境选择合适的传输模式
3. **错误处理**: 实现完整的错误处理和重试机制
4. **安全性**: 在生产环境中配置适当的访问控制
5. **性能**: 考虑连接池和超时设置

## 10. 相关文件

- `src/qd_agents/tools/executor.py` - MCP 工具执行器实现
- `src/qd_agents/registry/registry.py` - 工具注册表定义
- `examples/mcp_weather_example.py` - 完整示例
- `examples/simple_mcp_usage.py` - 简化示例
- `examples/mcp_http_client.py` - HTTP 客户端示例

## 11. 后续扩展

1. **完整的 MCP 客户端**: 实现支持 stdio 模式的 MCP 客户端
2. **工具自动发现**: 自动发现 MCP 服务器提供的工具
3. **负载均衡**: 支持多个 MCP 服务器实例
4. **监控告警**: 集成到系统的监控体系
5. **缓存优化**: 实现天气数据的本地缓存

---

**版本**: 1.0  
**更新日期**: 2026-04-16  
**状态**: 已实现，待集成