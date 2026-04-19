"""
技能分析器模块

使用 LLM 分析技能文件并将其转换为 MCP 工具。
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class SkillAnalyzer:
    """技能分析器，使用 LLM 解析技能文件"""

    def __init__(self, llm_client):
        self.llm_client = llm_client

    async def analyze_skill(self, skill_path: Path) -> Dict[str, Any]:
        """
        分析技能目录

        Args:
            skill_path: 技能目录路径

        Returns:
            技能分析结果，包括名称、描述、参数等
        """
        # 收集技能文件
        skill_files = self._collect_skill_files(skill_path)

        # 读取文件内容
        file_contents = {}
        for file_path in skill_files:
            try:
                content = file_path.read_text(encoding='utf-8')
                file_contents[file_path.name] = content[:5000]  # 限制长度
            except Exception as e:
                logger.warning(f"无法读取文件 {file_path}: {e}")

        # 使用 LLM 分析技能
        analysis = await self._analyze_with_llm(skill_path.name, file_contents)

        return analysis

    def _collect_skill_files(self, skill_path: Path) -> List[Path]:
        """收集技能目录下的所有相关文件"""
        files = []

        # 定义文本文件扩展名
        text_extensions = {
            '.py', '.sh', '.js', '.ts', '.json', '.md', '.txt', '.yml', '.yaml',
            '.toml', '.ini', '.cfg', '.conf', '.xml', '.html', '.css', '.sql'
        }

        # 排除的目录和文件
        excluded_dirs = {'.git', '__pycache__', 'node_modules', 'dist', 'build', 'venv'}
        excluded_files = {'.DS_Store', 'Thumbs.db'}

        # 递归遍历所有文件
        for file_path in skill_path.rglob("*"):
            if file_path.is_file():
                # 跳过排除的文件
                if file_path.name in excluded_files:
                    continue

                # 跳过排除的目录中的文件
                if any(excluded in file_path.parts for excluded in excluded_dirs):
                    continue

                # 检查文件大小（限制为 2MB）
                try:
                    if file_path.stat().st_size > 2 * 1024 * 1024:  # 2MB
                        continue
                except (OSError, IOError):
                    continue

                # 优先包含文本文件，但也包含其他可能相关的文件
                if file_path.suffix.lower() in text_extensions:
                    files.append(file_path)
                elif file_path.suffix == '' and file_path.name in ['SKILL.md', '_meta.json', 'Dockerfile', 'docker-compose.yml']:
                    # 无扩展名的重要文件
                    files.append(file_path)

        # 如果文件太多，按优先级排序并限制数量
        if len(files) > 50:
            # 按文件重要性排序
            priority_files = []
            other_files = []

            for file_path in files:
                name = file_path.name.lower()
                if name in ['skill.md', '_meta.json', 'readme.md', 'package.json', 'requirements.txt', 'pyproject.toml']:
                    priority_files.append(file_path)
                elif file_path.suffix in ['.py', '.js', '.ts']:
                    priority_files.append(file_path)
                else:
                    other_files.append(file_path)

            files = priority_files + other_files[:30]  # 总共最多50个文件

        return files

    async def _analyze_with_llm(self, skill_name: str, file_contents: Dict[str, str]) -> Dict[str, Any]:
        """
        使用 LLM 分析技能文件

        Args:
            skill_name: 技能名称
            file_contents: 文件内容字典

        Returns:
            技能分析结果
        """
        # 构建提示词
        prompt = self._build_analysis_prompt(skill_name, file_contents)

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个代码分析专家。你的任务是分析技能文件并提取以下信息：\n"
                    "1. 技能名称和描述\n"
                    "2. 输入参数（名称、类型、是否必需、描述、默认值）\n"
                    "3. 输出格式\n"
                    "4. 依赖关系（环境变量、二进制文件、Python包等）\n"
                    "5. 调用方式（命令行命令）\n"
                    "6. 任何其他重要信息\n"
                    "\n"
                    "请以 JSON 格式回复，包含以下字段：\n"
                    "- name: 技能名称\n"
                    "- description: 技能描述\n"
                    "- parameters: 参数列表，每个参数包含 name, type, required, description, default\n"
                    "- output_format: 输出格式描述\n"
                    "- dependencies: 依赖关系列表\n"
                    "- invocation_command: 调用命令（如 python script.py '{JSON}'）\n"
                    "- env_vars: 所需环境变量列表\n"
                    "- binary_deps: 所需二进制文件列表\n"
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

            analysis = json.loads(json_str)
            return analysis

        except Exception as e:
            logger.error(f"LLM 分析失败: {e}")
            raise

    def _build_analysis_prompt(self, skill_name: str, file_contents: Dict[str, str]) -> str:
        """构建分析提示词"""
        prompt = f"请分析以下技能 '{skill_name}' 的文件内容：\n\n"

        for filename, content in file_contents.items():
            prompt += f"=== 文件: {filename} ===\n{content}\n\n"

        prompt += (
            "基于以上文件内容，请提取技能信息。"
            "特别注意 SKILL.md 文件中的参数表格和示例。"
        )

        return prompt