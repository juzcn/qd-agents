"""工具模块

包含工具执行器、注册器、内置工具函数。
"""
from .executors import (
    ToolExecutor,
    ToolExecutorRegistry,
    create_executor,
    create_http_tool,
    create_function_tool,
    create_bash_tool,
    HTTPToolExecutor,
    BashToolExecutor,
    FunctionToolExecutor,
)

from .registrars import (
    register_cli_tool,
    register_http_tool,
    register_mcp_tool,
    register_skill_tool,
)

from .builtin_register import (
    tool_register_cli,
    tool_register_mcp,
    tool_register_skill,
    tool_register_http,
    delegate,
    ask_user,
    context_summarizer,
    tools_list,
    register_builtin_function_tools,
)

from .builtins import echo
from .search import serper_search, tavily_search, fetch
from .errors import ToolRegistrationError, ToolNotFoundError, ToolValidationError, OpenAPISpecError

__all__ = [
    # 执行器
    "ToolExecutor",
    "ToolExecutorRegistry",
    "create_executor",
    "create_http_tool",
    "create_function_tool",
    "create_bash_tool",
    "HTTPToolExecutor",
    "BashToolExecutor",
    "FunctionToolExecutor",
    # 注册器
    "register_cli_tool",
    "register_http_tool",
    "register_mcp_tool",
    "register_skill_tool",
    # 内置工具函数
    "echo",
    "serper_search",
    "tavily_search",
    "fetch",
    # 工具注册函数
    "tool_register_cli",
    "tool_register_mcp",
    "tool_register_skill",
    "tool_register_http",
    # 元工具函数
    "delegate",
    "ask_user",
    "context_summarizer",
    "tools_list",
    # 注册辅助
    "register_builtin_function_tools",
    # 错误类型
    "ToolRegistrationError",
    "ToolNotFoundError",
    "ToolValidationError",
    "OpenAPISpecError",
]