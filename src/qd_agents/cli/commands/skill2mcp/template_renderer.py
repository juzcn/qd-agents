"""
MCP 模板渲染器

使用 Jinja2 渲染 MCP 服务器模板。
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from jinja2 import Environment, FileSystemLoader, Template, select_autoescape

logger = logging.getLogger(__name__)


class MCPTemplateRenderer:
    """MCP 模板渲染器"""

    def __init__(self, template_dir: Path):
        """
        初始化模板渲染器

        Args:
            template_dir: 模板目录路径
        """
        self.template_dir = template_dir
        self.env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

        # 添加自定义过滤器
        self.env.filters['tojson'] = lambda x: json.dumps(x, ensure_ascii=False)
        self.env.filters['python_value'] = self._python_value_filter
        self.env.filters['python_repr'] = lambda x: repr(x) if x is not None else 'None'

    def render_template(self, template_name: str, context: Dict[str, Any]) -> str:
        """
        渲染模板

        Args:
            template_name: 模板文件名（支持 .j2 扩展名）
            context: 模板上下文变量

        Returns:
            渲染后的字符串
        """
        if not template_name.endswith(".j2"):
            template_name = template_name + ".j2"

        template = self.env.get_template(template_name)
        return template.render(**context)

    def render_main_py(self, analysis: Dict[str, Any], tool_definition: Any) -> str:
        """
        渲染主 Python 文件

        Args:
            analysis: 技能分析结果
            tool_definition: 工具定义

        Returns:
            渲染后的 main.py 内容
        """
        skill_name = analysis.get('name', 'unknown_skill')
        description = analysis.get('description', '')
        parameters = analysis.get('parameters', [])
        invocation_command = analysis.get('invocation_command', 'python skill_wrapper.py')

        # 调整调用命令为相对于技能目录的路径
        if invocation_command and isinstance(invocation_command, str):
            # 如果包含 skills/<skill_name>/ 前缀，将其转换为相对于技能目录的路径
            skill_prefix = f"skills/{skill_name}/"
            if skill_prefix in invocation_command:
                # 提取scripts/后的部分
                scripts_part = invocation_command.split(skill_prefix)[1]
                # 确保使用正确的Python命令（python而不是python3）
                if invocation_command.startswith('python3 '):
                    invocation_command = 'python ' + scripts_part
                elif invocation_command.startswith('python '):
                    invocation_command = 'python ' + scripts_part
                else:
                    invocation_command = 'python ' + scripts_part

        # 处理参数类型映射和默认值转换
        processed_params = []
        for param in parameters:
            param_type = param.get('type', 'string')
            # 映射到 Python 类型
            py_type = self._map_to_py_type(param_type)
            processed_param = param.copy()
            processed_param['py_type'] = py_type

            # 转换默认值为 Python 表达式
            if 'default' in processed_param:
                processed_param['default'] = self._convert_default_to_python(processed_param['default'], param_type)

            processed_params.append(processed_param)

        # 准备模板上下文
        context = {
            'skill_name': skill_name,
            'package_name': skill_name.lower().replace('_', '-'),
            'skill_description': description,
            'parameters': processed_params,
            'invocation_command': invocation_command,
            'skill_name_pascal': self._to_pascal_case(skill_name),
            'skill_name_upper': skill_name.upper().replace('-', '_'),
            'tool_parameters': tool_definition.parameters if hasattr(tool_definition, 'parameters') else {},
        }

        return self.render_template('main.py', context)

    def render_pyproject_toml(self, analysis: Dict[str, Any]) -> str:
        """
        渲染 pyproject.toml 文件

        Args:
            analysis: 技能分析结果

        Returns:
            渲染后的 pyproject.toml 内容
        """
        skill_name = analysis.get('name', 'unknown_skill')

        context = {
            'skill_name': skill_name,
            'package_name': skill_name.lower().replace('_', '-'),
        }

        return self.render_template('pyproject.toml', context)

    def render_readme_md(self, analysis: Dict[str, Any]) -> str:
        """
        渲染 README.md 文件

        Args:
            analysis: 技能分析结果

        Returns:
            渲染后的 README.md 内容
        """
        skill_name = analysis.get('name', 'unknown_skill')
        description = analysis.get('description', '')
        parameters = analysis.get('parameters', [])

        context = {
            'skill_name': skill_name,
            'package_name': skill_name.lower().replace('_', '-'),
            'skill_description': description,
            'parameters': parameters,
        }

        return self.render_template('README.md', context)

    def render_validate_py(self, analysis: Dict[str, Any]) -> str:
        """
        渲染验证脚本

        Args:
            analysis: 技能分析结果

        Returns:
            渲染后的 validate.py 内容
        """
        skill_name = analysis.get('name', 'unknown_skill')

        context = {
            'skill_name': skill_name,
        }

        return self.render_template('validate.py', context)

    def _map_to_py_type(self, param_type: str) -> str:
        """将参数类型映射到 Python 类型"""
        param_type_lower = param_type.lower()

        # 处理复杂类型如 list[obj], list[str] 等
        if param_type_lower.startswith('list[') or param_type_lower.startswith('array['):
            # 提取内部类型
            inner_type = param_type_lower.split('[')[1].rstrip(']')
            if inner_type in ['str', 'string', 'int', 'integer', 'float', 'number', 'bool', 'boolean']:
                inner_py_type = self._simple_type_mapping(inner_type)
                return f'List[{inner_py_type}]'
            else:
                return 'List[Any]'

        # 处理字典类型如 dict[str, int]
        if param_type_lower.startswith('dict[') or param_type_lower.startswith('object['):
            return 'Dict[str, Any]'

        # 简单类型映射
        return self._simple_type_mapping(param_type_lower)

    def _simple_type_mapping(self, param_type: str) -> str:
        """简单类型映射"""
        type_mapping = {
            'str': 'str',
            'string': 'str',
            'int': 'int',
            'integer': 'int',
            'float': 'float',
            'number': 'float',
            'bool': 'bool',
            'boolean': 'bool',
            'list': 'List[Any]',
            'array': 'List[Any]',
            'dict': 'Dict[str, Any]',
            'object': 'Dict[str, Any]',
            'json': 'Any',
            'any': 'Any'
        }
        return type_mapping.get(param_type, 'str')

    def _to_pascal_case(self, name: str) -> str:
        """将烤肉串或蛇形命名转换为帕斯卡命名"""
        return ''.join(word.capitalize() for word in name.replace('-', '_').split('_'))

    def _python_value_filter(self, value: Any) -> Any:
        """将值转换为 Python 字面量"""
        if value is None:
            return None

        if isinstance(value, str):
            # 尝试解析 JSON
            try:
                parsed = json.loads(value)
                # 如果解析结果是字符串，检查是否是 'true'/'false'
                if isinstance(parsed, str):
                    lower = parsed.lower()
                    if lower == 'true':
                        return True
                    if lower == 'false':
                        return False
                return parsed
            except json.JSONDecodeError:
                # 检查字符串是否为布尔值
                lower = value.lower()
                if lower == 'true':
                    return True
                if lower == 'false':
                    return False
                # 返回原字符串
                return value

        # 其他类型直接返回
        return value

    def _convert_default_to_python(self, default_value: Any, param_type: str) -> Any:
        """将默认值转换为 Python 表达式"""
        if default_value is None:
            return None

        # 首先使用 _python_value_filter 转换值
        converted = self._python_value_filter(default_value)

        # 对于布尔类型，确保是布尔值
        param_type_lower = param_type.lower()
        if param_type_lower in ['bool', 'boolean']:
            if isinstance(converted, str):
                lower = converted.lower()
                if lower == 'true':
                    return True
                elif lower == 'false':
                    return False
            # 如果已经是布尔值或其他类型，直接返回
            return converted

        # 对于数字类型，尝试转换为数字
        if param_type_lower in ['int', 'integer', 'float', 'number']:
            try:
                if param_type_lower in ['int', 'integer']:
                    return int(converted)
                else:
                    return float(converted)
            except (ValueError, TypeError):
                # 转换失败，返回原值
                return converted

        # 对于其他类型，返回转换后的值
        return converted