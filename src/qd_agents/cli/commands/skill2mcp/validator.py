"""
智能 MCP 验证器模块

使用 LLM 自主验证 MCP 服务。
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any

from qd_agents.llm import LLMClient
from qd_agents.registry import Tool
from rich.console import Console

logger = logging.getLogger(__name__)


class SmartMCPValidator:
    """智能 MCP 验证器，使用 LLM 自主验证 MCP 服务"""

    def __init__(self, llm_client: LLMClient, console: Console):
        self.llm_client = llm_client
        self.console = console

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
                "src/index.ts",
                "skill_wrapper.py",
                "package.json",
                "README.md"
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
        prompt = self._build_validation_prompt(validation_info)

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个代码审查专家。你的任务是分析生成的 MCP 服务器代码并提供验证反馈。\n"
                    "请检查以下方面：\n"
                    "1. 代码结构和完整性\n"
                    "2. 与原始技能的功能匹配度\n"
                    "3. 参数处理是否正确\n"
                    "4. 错误处理是否完善\n"
                    "5. 是否符合 MCP 协议标准\n"
                    "6. 是否存在明显的逻辑错误\n"
                    "\n"
                    "请以 JSON 格式回复，包含以下字段：\n"
                    "- is_valid: 布尔值，表示验证是否通过\n"
                    "- feedback: 验证反馈信息\n"
                    "- issues: 发现的问题列表（如果有）\n"
                    "- suggestions: 改进建议列表\n"
                    "- confidence: 验证置信度 (0-1)\n"
                )
            },
            {
                "role": "user",
                "content": prompt
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

    def _build_validation_prompt(self, validation_info: Dict[str, Any]) -> str:
        """构建验证提示词"""
        skill_name = validation_info.get('skill_name', 'unknown')
        skill_description = validation_info.get('skill_description', '')

        prompt = f"""请分析以下技能 '{skill_name}' 的 MCP 服务器实现：

## 技能信息
- 名称: {skill_name}
- 描述: {skill_description}

## 工具定义
{json.dumps(validation_info.get('tool_definition', {}), indent=2, ensure_ascii=False)}

## 生成的代码文件
"""

        file_contents = validation_info.get('generated_files', {})
        for filename, content in file_contents.items():
            prompt += f"\n=== 文件: {filename} ===\n{content}\n"

        prompt += """
基于以上信息，请验证生成的 MCP 服务器是否：
1. 正确实现了原始技能的功能
2. 正确处理所有参数
3. 符合 MCP 协议标准
4. 具有适当的错误处理
5. 代码结构合理

请提供详细的验证反馈。
"""

        return prompt