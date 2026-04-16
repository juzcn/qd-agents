"""
Skill 工具执行器

处理预置技能和工作流的工具执行器。
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from typing import Any

from .base import ToolExecutor
from qd_agents.registry import Tool, ToolExecutionConfig, ToolMetadata, ToolExecutionType


logger = logging.getLogger(__name__)


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