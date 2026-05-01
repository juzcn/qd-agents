"""工具注册错误类型"""


class ToolRegistrationError(Exception):
    """工具注册基础错误"""
    pass


class ToolNotFoundError(ToolRegistrationError):
    """工具源未找到（SKILL.md、MCP JSON 等）"""
    pass


class ToolValidationError(ToolRegistrationError):
    """工具参数或配置无效"""
    pass


class OpenAPISpecError(ToolRegistrationError):
    """OpenAPI spec 获取或解析失败"""
    pass
