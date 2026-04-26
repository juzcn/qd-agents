"""
CLI 凭证工具函数

环境变量名到工具名的映射，供 skills 和 tools 命令共享。
"""

# 环境变量名到 tools_credentials 中工具名的映射
ENV_TO_TOOL_NAME_MAP: dict[str, str] = {
    "BAIDU_API_KEY": "baidu_search",
    "SERPER_API_KEY": "serper_search",
    "TAVILY_API_KEY": "tavily_search",
}


def env_var_to_tool_name(env_var: str) -> str:
    """将环境变量名转换为 tools_credentials 中的工具名。"""
    if env_var in ENV_TO_TOOL_NAME_MAP:
        return ENV_TO_TOOL_NAME_MAP[env_var]

    if env_var.endswith("_API_KEY"):
        return env_var[:-len("_API_KEY")].lower()

    return env_var.lower()