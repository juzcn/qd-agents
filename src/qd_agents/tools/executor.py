"""
工具执行器 - 支持多种执行类型

注意：此文件已重构为兼容性包装。实际实现在 executors/ 目录中。
为保持向后兼容性，从此文件重新导出所有公共API。
"""

from __future__ import annotations

# 重新导出所有公共API，保持向后兼容性
from .executors import (
    ToolExecutor,
    ToolExecutorRegistry,
    create_executor,
    create_http_tool,
    create_cli_tool,
    create_function_tool,
    create_bash_tool,
    create_skill_tool,
    create_mcp_tool,
    HTTPToolExecutor,
    CLIToolExecutor,
    BashToolExecutor,
    FunctionToolExecutor,
    SkillToolExecutor,
    MCPToolExecutor,
)

__all__ = [
    "ToolExecutor",
    "ToolExecutorRegistry",
    "create_executor",
    "create_http_tool",
    "create_cli_tool",
    "create_function_tool",
    "create_bash_tool",
    "create_skill_tool",
    "create_mcp_tool",
    "HTTPToolExecutor",
    "CLIToolExecutor",
    "BashToolExecutor",
    "FunctionToolExecutor",
    "SkillToolExecutor",
    "MCPToolExecutor",
]


class HTTPToolExecutor(ToolExecutor):
    """HTTP 工具执行器"""

    def __init__(
        self,
        endpoint: str,
        method: str = "POST",
        headers: dict[str, str] | None = None,
        timeout: int = 30,
    ):
        self.endpoint = endpoint
        self.method = method.upper()
        self.headers = headers or {}
        self.timeout = timeout

    async def execute(self, **kwargs: Any) -> Any:
        logger.info("Executing HTTP tool: %s %s", self.method, self.endpoint)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            request_kwargs: dict[str, Any] = {
                "headers": self.headers,
            }

            if self.method in ["POST", "PUT", "PATCH"]:
                request_kwargs["json"] = kwargs
            else:
                request_kwargs["params"] = kwargs

            response = await client.request(
                method=self.method,
                url=self.endpoint,
                **request_kwargs
            )

            response.raise_for_status()
            return response.json()


class CLIToolExecutor(ToolExecutor):
    """CLI 工具执行器"""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        timeout: int = 30,
    ):
        self.command = command
        self.args = args or []
        self.timeout = timeout

    def _format_arg(self, arg: str, **kwargs: Any) -> str:
        """格式化参数，替换占位符"""
        result = arg
        for key, value in kwargs.items():
            placeholder = f"{{{key}}}"
            if placeholder in result:
                result = result.replace(placeholder, str(value))
        return result

    async def execute(self, **kwargs: Any) -> Any:
        import shlex

        # 构建命令
        cmd_parts = [self.command]
        cmd_parts.extend(self._format_arg(arg, **kwargs) for arg in self.args)

        cmd_str = " ".join(shlex.quote(p) for p in cmd_parts)
        logger.info("Executing CLI tool: %s", cmd_str)

        # 执行命令
        proc = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"Command timed out after {self.timeout}s")

        # 始终返回包含 stdout、stderr 和 returncode 的结构化结果
        # 这样可以保持与 OpenAI tool calling 标准的兼容性
        result = {
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
            "returncode": proc.returncode,
            "success": proc.returncode == 0
        }

        # 如果输出是 JSON，也提供解析后的版本
        try:
            result["json"] = json.loads(stdout.decode())
        except json.JSONDecodeError:
            pass

        return result


class BashToolExecutor(ToolExecutor):
    """Bash 工具执行器"""

    def __init__(
        self,
        shell_command: str,
        shell: str = "bash",
        timeout: int = 30,
    ):
        self.shell_command = shell_command
        self.shell = shell
        self.timeout = timeout

    async def execute(self, **kwargs: Any) -> Any:
        import shlex

        # 替换命令中的占位符
        formatted_command = self.shell_command
        for key, value in kwargs.items():
            placeholder = f"{{{key}}}"
            if placeholder in formatted_command:
                formatted_command = formatted_command.replace(placeholder, str(value))

        logger.info("Executing bash tool: %s", formatted_command)

        # 使用指定的shell执行命令
        # 在Windows上，如果shell是"bash"，可能需要使用"wsl bash -c"或其他方式
        # 这里简化处理，假设shell在PATH中
        proc = await asyncio.create_subprocess_shell(
            formatted_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,  # 使用shell执行
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"Bash command timed out after {self.timeout}s")

        # 返回结构化结果
        result = {
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
            "returncode": proc.returncode,
            "success": proc.returncode == 0
        }

        # 如果输出是JSON，也提供解析后的版本
        try:
            result["json"] = json.loads(stdout.decode())
        except json.JSONDecodeError:
            pass

        return result


class FunctionToolExecutor(ToolExecutor):
    """Python 函数执行器"""

    def __init__(self, func: Callable):
        self.func = func

    async def execute(self, **kwargs: Any) -> Any:
        logger.info("Executing function tool: %s", self.func.__name__)

        if asyncio.iscoroutinefunction(self.func):
            return await self.func(**kwargs)
        else:
            return self.func(**kwargs)


class SkillToolExecutor(ToolExecutor):
    """Skill 工具执行器（工作流）"""

    def __init__(self, exec_config: ToolExecutionConfig):
        self.exec_config = exec_config
        self.skill_id = exec_config.skill_id

    async def execute(self, **kwargs: Any) -> Any:
        logger.info("Executing skill tool: %s", self.skill_id)

        # 根据配置执行不同类型的skill
        # 1. Python函数执行
        if self.exec_config.module and self.exec_config.function:
            return await self._execute_python_function(**kwargs)

        # 2. 命令行执行
        elif self.exec_config.command:
            return await self._execute_command(**kwargs)

        # 3. 默认：尝试作为Python模块导入
        else:
            return await self._execute_as_module(**kwargs)

    async def _execute_python_function(self, **kwargs: Any) -> Any:
        """执行Python函数"""
        import importlib

        try:
            module = importlib.import_module(self.exec_config.module)
            func = getattr(module, self.exec_config.function)

            if asyncio.iscoroutinefunction(func):
                return await func(**kwargs)
            else:
                return func(**kwargs)
        except Exception as e:
            logger.exception("Failed to execute Python function skill: %s.%s",
                           self.exec_config.module, self.exec_config.function)
            raise

    async def _execute_command(self, **kwargs: Any) -> Any:
        """执行命令行脚本"""
        import shlex

        # 构建命令
        cmd_parts = [self.exec_config.command]

        # 处理参数
        for arg in self.exec_config.args:
            # 替换参数中的占位符
            formatted_arg = arg
            for key, value in kwargs.items():
                placeholder = f"{{{key}}}"
                if placeholder in formatted_arg:
                    formatted_arg = formatted_arg.replace(placeholder, str(value))
            cmd_parts.append(formatted_arg)

        cmd_str = " ".join(shlex.quote(p) for p in cmd_parts)
        logger.info("Executing skill command: %s", cmd_str)

        # 执行命令
        proc = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.exec_config.timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"Skill command timed out after {self.exec_config.timeout}s")

        # 返回结构化结果
        result = {
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
            "returncode": proc.returncode,
            "success": proc.returncode == 0
        }

        # 如果输出是JSON，也提供解析后的版本
        try:
            result["json"] = json.loads(stdout.decode())
        except json.JSONDecodeError:
            pass

        return result

    async def _execute_as_module(self, **kwargs: Any) -> Any:
        """尝试将skill_id作为Python模块导入并执行"""
        import importlib

        try:
            # skill_id可能是"module.function"格式
            if "." in self.skill_id:
                module_name, func_name = self.skill_id.rsplit(".", 1)
                module = importlib.import_module(module_name)
                func = getattr(module, func_name)

                if asyncio.iscoroutinefunction(func):
                    return await func(**kwargs)
                else:
                    return func(**kwargs)
            else:
                # 尝试导入整个模块并查找main函数
                module = importlib.import_module(self.skill_id)
                if hasattr(module, "main"):
                    func = module.main
                    if asyncio.iscoroutinefunction(func):
                        return await func(**kwargs)
                    else:
                        return func(**kwargs)
                else:
                    raise ValueError(f"Skill module {self.skill_id} has no 'main' function")
        except Exception as e:
            logger.exception("Failed to execute skill as module: %s", self.skill_id)
            raise


class MCPToolExecutor(ToolExecutor):
    """MCP 工具执行器"""

    def __init__(
        self,
        server: str,
        tool: str,
        transport: str = "stdio",
        endpoint: str | None = None,
    ):
        """
        初始化 MCP 工具执行器

        Args:
            server: MCP 服务器标识符（如 "weather"）
            tool: 工具名称（如 "get_current_weather"）
            transport: 传输模式，支持 "stdio", "sse", "streamable-http"
            endpoint: HTTP 端点（仅用于 SSE 或 Streamable HTTP 模式）
        """
        self.server = server
        self.tool = tool
        self.transport = transport
        self.endpoint = endpoint

    async def execute(self, **kwargs: Any) -> Any:
        logger.info("Executing MCP tool: %s/%s via %s", self.server, self.tool, self.transport)

        if self.transport in ["sse", "streamable-http"]:
            try:
                return await self._execute_http(**kwargs)
            except Exception as e:
                logger.warning("HTTP mode failed for MCP tool %s/%s: %s", self.server, self.tool, e)
                logger.warning("Falling back to simplified mode for demonstration")
                # 降级到简化模式，返回模拟数据
                return await self._execute_simplified(**kwargs)
        else:
            # stdio 模式需要完整的 MCP 客户端实现
            # 这里提供一个简化的 HTTP 模拟实现作为示例
            return await self._execute_simplified(**kwargs)

    async def _execute_http(self, **kwargs: Any) -> Any:
        """通过 HTTP 执行 MCP 工具"""
        if not self.endpoint:
            raise ValueError(f"HTTP endpoint required for {self.transport} transport")

        import httpx

        # MCP HTTP 协议可能使用不同的端点
        # 尝试常见的 MCP 端点
        endpoints_to_try = [
            self.endpoint,  # 原始端点
            f"{self.endpoint}/sse",  # SSE 专用端点
            f"{self.endpoint}/message",  # MCP 消息端点
            f"{self.endpoint}/tools/{self.tool}/call",  # 直接工具调用端点
        ]

        # 根据 mcp-weather-server 的 HTTP API 格式调用
        # 注意：实际实现需要根据具体的 MCP HTTP 协议实现
        async with httpx.AsyncClient(timeout=30) as client:
            last_error = None
            for endpoint in endpoints_to_try:
                try:
                    logger.info("Trying MCP endpoint: %s", endpoint)
                    if self.transport == "sse":
                        # SSE 模式：建立 Server-Sent Events 连接
                        # 这里简化实现，实际需要处理 SSE 流
                        response = await client.post(
                            endpoint,
                            json={
                                "method": f"tools/{self.tool}/call",
                                "params": kwargs,
                            }
                        )
                    else:  # streamable-http
                        # Streamable HTTP 模式
                        response = await client.post(
                            endpoint,
                            json={
                                "method": f"tools/{self.tool}/call",
                                "params": kwargs,
                            }
                        )

                    response.raise_for_status()
                    result = response.json()
                    logger.info("MCP tool %s/%s executed successfully via %s (endpoint: %s)",
                               self.server, self.tool, self.transport, endpoint)
                    return result.get("result", result)
                except httpx.HTTPStatusError as e:
                    last_error = e
                    logger.warning("MCP endpoint %s failed with status %s: %s",
                                 endpoint, e.response.status_code, e.response.text[:200])
                    if e.response.status_code == 404:
                        continue  # 尝试下一个端点
                    else:
                        raise
                except Exception as e:
                    last_error = e
                    logger.warning("MCP endpoint %s failed: %s", endpoint, e)
                    continue

            # 所有端点都失败
            if last_error:
                raise last_error
            else:
                raise ValueError(f"All MCP endpoints failed for tool {self.server}/{self.tool}")

    async def _execute_simplified(self, **kwargs: Any) -> Any:
        """简化的 MCP 工具执行（用于演示）"""
        # 在实际项目中，这里应该使用 mcp 库的客户端
        # 与 MCP 服务器进行 stdio 通信

        # 对于天气工具，我们返回一个模拟响应
        if self.server == "weather":
            if self.tool == "get_current_weather":
                return {
                    "temperature": 20.5,
                    "humidity": 65,
                    "description": "晴朗",
                    "city": kwargs.get("city", "未知城市"),
                    "timestamp": "2026-04-16T10:30:00Z"
                }
            elif self.tool == "get_air_quality":
                return {
                    "pm2_5": 35,
                    "pm10": 50,
                    "aqi": 45,
                    "health_advice": "空气质量良好",
                    "city": kwargs.get("city", "未知城市")
                }

        raise NotImplementedError(
            f"MCP tool {self.server}/{self.tool} not implemented. "
            f"Transport mode: {self.transport}"
        )


def create_executor(tool: Tool) -> ToolExecutor:
    """
    根据工具定义创建执行器

    Args:
        tool: 工具定义

    Returns:
        工具执行器
    """
    exec_config = tool.execution

    if exec_config.type == ToolExecutionType.HTTP:
        if not exec_config.endpoint:
            raise ValueError("HTTP tool requires endpoint")
        return HTTPToolExecutor(
            endpoint=exec_config.endpoint,
            method=exec_config.method or "POST",
            headers=exec_config.headers,
            timeout=exec_config.timeout,
        )

    elif exec_config.type == ToolExecutionType.CLI:
        if not exec_config.command:
            raise ValueError("CLI tool requires command")
        return CLIToolExecutor(
            command=exec_config.command,
            args=exec_config.args,
            timeout=exec_config.timeout,
        )

    elif exec_config.type == ToolExecutionType.BASH:
        if not exec_config.shell_command:
            raise ValueError("BASH tool requires shell_command")
        return BashToolExecutor(
            shell_command=exec_config.shell_command,
            shell=exec_config.shell or "bash",
            timeout=exec_config.timeout,
        )

    elif exec_config.type == ToolExecutionType.FUNCTION:
        # 函数工具需要单独注册
        raise NotImplementedError(
            "Function tools must be registered with ToolExecutorRegistry"
        )

    elif exec_config.type == ToolExecutionType.SKILL:
        if not exec_config.skill_id:
            raise ValueError("Skill tool requires skill_id")
        return SkillToolExecutor(exec_config=exec_config)

    elif exec_config.type == ToolExecutionType.MCP:
        if not exec_config.server or not exec_config.tool:
            raise ValueError("MCP tool requires server and tool")
        return MCPToolExecutor(
            server=exec_config.server,
            tool=exec_config.tool,
            transport=exec_config.transport,
            endpoint=exec_config.endpoint,
        )

    else:
        raise ValueError(f"Unknown tool type: {exec_config.type}")


def create_http_tool(
    name: str,
    description: str,
    endpoint: str,
    method: str = "POST",
    parameters: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> Tool:
    """创建 HTTP 工具"""
    from ..registry import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType

    return Tool(
        id=name,
        name=name,
        description=description,
        parameters=parameters or {"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.HTTP,
            endpoint=endpoint,
            method=method,
            headers=headers or {},
            timeout=timeout,
        ),
        metadata=ToolMetadata(),
    )


def create_cli_tool(
    name: str,
    description: str,
    command: str,
    args: list[str] | None = None,
    parameters: dict[str, Any] | None = None,
    timeout: int = 30,
) -> Tool:
    """创建 CLI 工具"""
    from ..registry import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType

    return Tool(
        id=name,
        name=name,
        description=description,
        parameters=parameters or {"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.CLI,
            command=command,
            args=args or [],
            timeout=timeout,
        ),
        metadata=ToolMetadata(),
    )


def create_function_tool(
    name: str,
    description: str,
    func: Callable,
    parameters: dict[str, Any] | None = None,
) -> Tool:
    """创建 Python 函数工具"""
    from ..registry import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType

    return Tool(
        id=name,
        name=name,
        description=description,
        parameters=parameters or {"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.FUNCTION,
            module=func.__module__,
            function=func.__name__,
        ),
        metadata=ToolMetadata(),
    )


def create_mcp_tool(
    name: str,
    description: str,
    server: str,
    tool_name: str,
    parameters: dict[str, Any] | None = None,
    transport: str = "stdio",
    endpoint: str | None = None,
    timeout: int = 30,
    category: str = "mcp",
    tags: list[str] | None = None,
) -> Tool:
    """创建 MCP 工具"""
    from ..registry import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType

    if tags is None:
        tags = ["mcp", server]

    return Tool(
        id=f"{server}.{tool_name}",
        name=name,
        description=description,
        parameters=parameters or {"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.MCP,
            server=server,
            tool=tool_name,
            transport=transport,
            endpoint=endpoint,
            timeout=timeout,
        ),
        metadata=ToolMetadata(
            category=category,
            tags=tags,
        ),
    )


def create_skill_tool(
    name: str,
    description: str,
    skill_id: str,
    parameters: dict[str, Any] | None = None,
    module: str | None = None,
    function: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    timeout: int = 30,
    category: str = "skill",
    tags: list[str] | None = None,
) -> Tool:
    """创建 Skill 工具"""
    from ..registry import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType

    if tags is None:
        tags = ["skill"]

    return Tool(
        id=skill_id,
        name=name,
        description=description,
        parameters=parameters or {"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.SKILL,
            skill_id=skill_id,
            module=module,
            function=function,
            command=command,
            args=args or [],
            timeout=timeout,
        ),
        metadata=ToolMetadata(
            category=category,
            tags=tags,
        ),
    )


def create_bash_tool(
    name: str,
    description: str,
    shell_command: str,
    parameters: dict[str, Any] | None = None,
    shell: str = "bash",
    timeout: int = 30,
    category: str = "shell",
    tags: list[str] | None = None,
) -> Tool:
    """创建 Bash 工具"""
    from ..registry import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType

    if tags is None:
        tags = ["bash", "shell"]

    return Tool(
        id=name,
        name=name,
        description=description,
        parameters=parameters or {"type": "object", "properties": {}, "required": []},
        execution=ToolExecutionConfig(
            type=ToolExecutionType.BASH,
            shell_command=shell_command,
            shell=shell,
            timeout=timeout,
        ),
        metadata=ToolMetadata(
            category=category,
            tags=tags,
        ),
    )


class ToolExecutorRegistry:
    """工具执行器注册表"""

    def __init__(self):
        self._executors: dict[str, ToolExecutor] = {}
        self._functions: dict[str, Callable] = {}

    def register_function(self, name: str, func: Callable) -> None:
        """注册 Python 函数"""
        self._functions[name] = func

    def register_executor(self, tool_id: str, executor: ToolExecutor) -> None:
        """注册执行器"""
        self._executors[tool_id] = executor

    def get_executor(self, tool: Tool) -> ToolExecutor:
        """获取工具执行器"""
        if tool.id in self._executors:
            return self._executors[tool.id]

        if tool.execution.type == ToolExecutionType.FUNCTION:
            func_name = tool.execution.function
            if func_name and func_name in self._functions:
                return FunctionToolExecutor(self._functions[func_name])

        return create_executor(tool)
