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
    HTTPToolExecutor,
    CLIToolExecutor,
    BashToolExecutor,
    FunctionToolExecutor,
)

__all__ = [
    "ToolExecutor",
    "ToolExecutorRegistry",
    "create_executor",
    "create_http_tool",
    "create_cli_tool",
    "create_function_tool",
    "create_bash_tool",
    "HTTPToolExecutor",
    "CLIToolExecutor",
    "BashToolExecutor",
    "FunctionToolExecutor",
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
