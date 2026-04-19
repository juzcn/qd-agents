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
                    "你是一个专业的技能分析专家，擅长将技能封装为 MCP 工具。\n\n"
                    "你的任务是：\n"
                    "1. 仔细分析技能文件，提取完整的技能信息\n"
                    "2. 准确识别参数类型和约束条件\n"
                    "3. 确定技能调用方式和依赖关系\n"
                    "4. 评估技能复杂度（简单包装 vs 复杂编排）\n\n"
                    "请以 JSON 格式回复，包含以下字段：\n"
                    "- name: 技能名称（字符串）\n"
                    "- description: 技能描述（字符串）\n"
                    "- parameters: 参数列表，每个参数包含：\n"
                    "  - name: 参数名称（字符串）\n"
                    "  - type: 参数类型（字符串：str, int, float, bool, list, dict, object, json）\n"
                    "  - required: 是否必需（布尔值）\n"
                    "  - description: 参数描述（字符串）\n"
                    "  - default: 默认值（如果存在，否则为 null）\n"
                    "- output_format: 输出格式描述（字符串）\n"
                    "- dependencies: 依赖关系列表（字符串数组）\n"
                    "- invocation_command: 调用命令（字符串，如 \"python3 script.py '{JSON}'\"）\n"
                    "- env_vars: 所需环境变量列表（字符串数组）\n"
                    "- binary_deps: 所需二进制文件列表（字符串数组）\n"
                    "- complexity: 技能复杂度评估（字符串：\"simple\" 或 \"complex\"）\n"
                    "- skill_type: 技能类型（字符串：\"command_line\", \"api_service\", \"script\", \"composite\"）\n"
                    "- has_config: 是否需要配置文件（布尔值）\n"
                    "- config_example: 配置示例（对象，如果 has_config 为 true）\n"
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
        prompt = f"请分析以下技能 '{skill_name}' 的文件内容，提取 MCP 工具所需的信息：\n\n"

        # 优先显示重要文件
        priority_files = []
        other_files = []

        for filename, content in file_contents.items():
            filename_lower = filename.lower()
            if filename_lower in ['skill.md', 'readme.md', '_meta.json', 'package.json', 'pyproject.toml']:
                priority_files.append((filename, content))
            elif filename_lower.endswith(('.py', '.js', '.ts', '.sh')):
                priority_files.append((filename, content))
            else:
                other_files.append((filename, content))

        # 显示优先级文件
        for filename, content in priority_files:
            prompt += f"=== 文件: {filename} ===\n{content}\n\n"

        # 显示其他文件
        for filename, content in other_files:
            prompt += f"=== 文件: {filename} ===\n{content}\n\n"

        prompt += """基于以上文件内容，请详细分析该技能并提取以下信息：

## 技能核心信息
1. 技能名称（name）：从 SKILL.md 的 frontmatter 中提取
2. 技能描述（description）：从 SKILL.md 的 frontmatter 中提取

## 参数分析
仔细分析 SKILL.md 中的参数表格，提取每个参数的：
- name: 参数名称
- type: 参数类型（str, int, float, bool, list, dict, object, json 等）
- required: 是否为必需参数（true/false）
- description: 参数描述
- default: 默认值（如果有）

注意处理复杂参数类型，如 list[obj]、obj 等，将其映射到合适的 JSON Schema 类型。

## 调用方式
从 Usage 部分提取调用命令（invocation_command），格式如：python3 script.py '<JSON>'

## 依赖关系
分析技能需要：
- env_vars: 所需环境变量列表
- binary_deps: 所需二进制文件列表
- dependencies: 其他依赖关系（如 Python 包）

## 技能复杂度评估
根据技能实现判断：
- 简单技能：直接命令调用即可完成
- 复杂技能：需要逻辑编排、多个步骤或条件判断

请以 JSON 格式回复，确保字段名与上述要求一致，类型正确。
"""

        return prompt