"""
智能 MCP 验证器模块

使用 LLM 自主验证 MCP 服务。
"""

import json
import logging
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Optional

from qd_agents.llm import LLMClient
from qd_agents.registry import Tool
from qd_agents.tools.executors.mcp import MCPToolExecutor
from rich.console import Console

from .template_renderer import MCPTemplateRenderer

logger = logging.getLogger(__name__)


class SmartMCPValidator:
    """智能 MCP 验证器，使用 LLM 自主验证 MCP 服务"""

    def __init__(self, llm_client: LLMClient, console: Console, template_renderer: MCPTemplateRenderer = None):
        self.llm_client = llm_client
        self.console = console

        # 初始化模板渲染器
        if template_renderer is None:
            from pathlib import Path
            template_dir = Path(__file__).parent / "templates"
            self.template_renderer = MCPTemplateRenderer(template_dir)
        else:
            self.template_renderer = template_renderer

    async def validate_mcp_service(self, mcp_server_dir: Path, analysis: Dict[str, Any], tool_definition: Tool) -> bool:
        """
        使用 LLM 智能验证 MCP 服务

        Args:
            mcp_server_dir: MCP 服务器目录
            analysis: 技能分析结果
            tool_definition: 工具定义

        Returns:
            验证是否通过
        """
        self.console.print("[blue][INFO][/] 开始智能验证 MCP 服务...")

        # 收集验证信息
        validation_info = {
            "skill_name": analysis.get('name', 'unknown'),
            "skill_description": analysis.get('description', ''),
            "parameters": analysis.get('parameters', []),
            "mcp_server_dir": str(mcp_server_dir),
            "tool_definition": {
                "name": tool_definition.name,
                "description": tool_definition.description,
                "parameters": tool_definition.parameters,
            }
        }

        # 读取生成的 MCP 服务器文件
        try:
            # 读取主要文件
            files_to_read = [
                "scripts/main.py",
                "pyproject.toml",
                "README.md",
                "test/validate.py",
                "requirements.txt",
                "scripts/__init__.py"
            ]

            file_contents = {}
            for filename in files_to_read:
                file_path = mcp_server_dir / filename
                if file_path.exists():
                    try:
                        content = file_path.read_text(encoding='utf-8')[:3000]  # 限制长度
                        file_contents[filename] = content
                    except Exception as e:
                        self.console.print(f"[dim]无法读取文件 {filename}: {e}[/]")

            validation_info["generated_files"] = file_contents

        except Exception as e:
            self.console.print(f"[yellow][WARN][/] 读取生成文件时出错: {e}")

        # 使用 LLM 分析生成的代码
        validation_result = await self._analyze_with_llm(validation_info)

        # 显示验证结果
        if validation_result.get("is_valid", False):
            self.console.print("[green][OK][/] 智能验证通过")
            self.console.print(f"[dim]验证反馈: {validation_result.get('feedback', '无反馈')}[/]")
            return True
        else:
            self.console.print("[yellow][WARN][/] 智能验证发现问题")
            self.console.print(f"[dim]问题: {validation_result.get('issues', '未知问题')}[/]")
            self.console.print(f"[dim]建议: {validation_result.get('suggestions', '无建议')}[/]")
            return False

    async def _analyze_with_llm(self, validation_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        使用 LLM 分析生成的 MCP 服务

        Args:
            validation_info: 验证信息

        Returns:
            验证结果
        """
        # 使用模板渲染系统提示词和用户提示词
        system_prompt = self.template_renderer.render_template('validation_system.j2', validation_info)
        user_prompt = self.template_renderer.render_template('validation_prompt.j2', validation_info)

        messages = [
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ]

        try:
            response = await self.llm_client.chat(
                messages=messages,
                temperature=0.3,
                max_tokens=2000
            )

            content = response.choices[0].message.content
            # 尝试从响应中提取 JSON
            import re
            json_match = re.search(r'```json\n(.*?)\n```', content, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # 尝试直接解析整个内容
                json_str = content

            result = json.loads(json_str)
            return result

        except Exception as e:
            self.console.print(f"[yellow][WARN][/] LLM 验证分析失败: {e}")
            # 返回默认结果
            return {
                "is_valid": False,
                "feedback": f"验证分析失败: {e}",
                "issues": ["LLM 分析失败"],
                "suggestions": ["检查 LLM 配置和网络连接"],
                "confidence": 0.0
            }

