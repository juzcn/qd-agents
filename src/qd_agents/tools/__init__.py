"""
工具模块

包含工具执行器、内置工具函数、工具注册函数。
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

# 内置工具函数
from .builtins import echo

# 搜索工具函数
from .search import serper_search, tavily_search, fetch

# 工具注册函数（供 LLM 调用管理工具箱）
from .builtin_register import (
    tool_register_cli,
    tool_register_mcp,
    tool_register_skill,
    tool_register_http,
    tool_register_code,
    delegate,
    ask_user,
    context_summarizer,
    tools_list,
    register_builtin_function_tools,
    register_meta_function_tools,
)

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
    # 内置工具函数
    "echo",
    # 搜索工具函数
    "serper_search",
    "tavily_search",
    "fetch",
    # 工具注册函数
    "tool_register_cli",
    "tool_register_mcp",
    "tool_register_skill",
    "tool_register_http",
    "tool_register_code",
    # 元工具函数
    "delegate",
    "ask_user",
    "context_summarizer",
    "tools_list",
    # 注册辅助
    "register_builtin_function_tools",
    "register_meta_function_tools",
]
