"""
执行层 - 工具调用的实际执行
"""

from typing import Any, Dict, Callable, Optional, List
from dataclasses import dataclass
from enum import Enum


class ExecutionStatus(Enum):
    """执行状态"""
    SUCCESS = "success"
    ERROR = "error"
    NEED_CONFIRMATION = "need_confirmation"


@dataclass
class ExecutionResult:
    """执行结果"""
    status: ExecutionStatus
    data: Optional[Any] = None
    error_message: Optional[str] = None
    can_retry: bool = False
    confirmation_question: Optional[str] = None

    @classmethod
    def success(cls, data: Any) -> "ExecutionResult":
        """创建成功结果"""
        return cls(status=ExecutionStatus.SUCCESS, data=data)

    @classmethod
    def error(cls, message: str, can_retry: bool = False) -> "ExecutionResult":
        """创建错误结果"""
        return cls(
            status=ExecutionStatus.ERROR,
            error_message=message,
            can_retry=can_retry
        )

    @classmethod
    def need_confirmation(cls, question: str) -> "ExecutionResult":
        """创建需要确认的结果"""
        return cls(
            status=ExecutionStatus.NEED_CONFIRMATION,
            confirmation_question=question
        )


class Tool:
    """工具定义"""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: Callable[..., Any],
        require_confirmation: bool = False
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.require_confirmation = require_confirmation

    def to_schema(self) -> Dict[str, Any]:
        """转换为工具Schema"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class Executor:
    """
    执行层：实际执行工具调用
    确定性代码，不涉及LLM
    """

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register_tool(self, tool: Tool) -> None:
        """注册一个工具"""
        self._tools[tool.name] = tool

    def register_function(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: Callable[..., Any],
        require_confirmation: bool = False
    ) -> None:
        """
        注册一个函数作为工具

        Args:
            name: 工具名称
            description: 工具描述
            parameters: 参数Schema (JSON Schema格式)
            handler: 处理函数
            require_confirmation: 是否需要用户确认
        """
        tool = Tool(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            require_confirmation=require_confirmation
        )
        self.register_tool(tool)

    def get_tool(self, name: str) -> Optional[Tool]:
        """获取工具"""
        return self._tools.get(name)

    def list_tools(self) -> List[Tool]:
        """列出所有工具"""
        return list(self._tools.values())

    def list_tool_schemas(self) -> List[Dict[str, Any]]:
        """列出所有工具的Schema"""
        return [tool.to_schema() for tool in self._tools.values()]

    def execute(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        confirmed: bool = False
    ) -> ExecutionResult:
        """
        执行工具调用

        Args:
            tool_name: 工具名称
            parameters: 工具参数
            confirmed: 是否已获用户确认

        Returns:
            执行结果
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            return ExecutionResult.error(f"未知工具: {tool_name}")

        # 检查是否需要确认
        if tool.require_confirmation and not confirmed:
            return ExecutionResult.need_confirmation(
                f"是否确认执行工具 '{tool_name}'？"
            )

        try:
            # 执行工具
            result = tool.handler(**parameters)
            return ExecutionResult.success(result)
        except Exception as e:
            return ExecutionResult.error(
                f"执行工具 '{tool_name}' 时出错: {str(e)}",
                can_retry=True
            )
