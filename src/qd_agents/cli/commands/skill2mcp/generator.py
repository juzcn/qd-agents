"""
MCP 工具和服务器生成器模块

基于技能分析结果生成 MCP 工具定义和完整的 MCP 服务器。
"""

import json
import logging
import shutil
from pathlib import Path
from typing import Dict, Any, Optional

from qd_agents.registry import Tool, ToolMetadata, ToolExecutionType
from qd_agents.tools.executors import create_mcp_tool

logger = logging.getLogger(__name__)


class MCPToolGenerator:
    """MCP 工具生成器"""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def generate_tool_definition(self, analysis: Dict[str, Any]) -> Tool:
        """
        基于分析结果生成 MCP 工具定义

        Args:
            analysis: 技能分析结果

        Returns:
            MCP 工具定义
        """
        name = analysis.get('name', 'unknown_skill')
        description = analysis.get('description', '')

        # 构建参数 Schema
        parameters = self._build_parameters_schema(analysis)

        # 创建 MCP 工具
        # 注意：这里假设我们将创建一个通用的 MCP 服务器来包装技能
        # 实际实现中，可能需要生成一个专门的 MCP 服务器
        tool = create_mcp_tool(
            name=name,
            description=description,
            server=f"skill-wrapper-{name}",
            transport="stdio",
            command="python",
            args=[
                "-m", "qd_agents.tools.skill_wrapper",
                "--skill-name", name
            ],
            parameters=parameters,
        )

        return tool

    def _build_parameters_schema(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """构建 JSON Schema 参数定义"""
        parameters = analysis.get('parameters', [])

        properties = {}
        required = []

        for param in parameters:
            param_name = param.get('name', '')
            param_type = param.get('type', 'string')
            is_required = param.get('required', False)
            description = param.get('description', '')

            # 映射类型到 JSON Schema 类型
            json_schema_type = self._map_type_to_json_schema(param_type)

            properties[param_name] = {
                "type": json_schema_type,
                "description": description
            }

            # 添加默认值（如果有）
            if 'default' in param and param['default'] is not None:
                properties[param_name]['default'] = param['default']

            if is_required:
                required.append(param_name)

        # 如果参数为空，添加一个通用的参数
        if not properties:
            properties = {
                "input": {
                    "type": "object",
                    "description": "技能输入参数",
                    "additionalProperties": True
                }
            }
            required = ["input"]

        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False
        }

    def _map_type_to_json_schema(self, param_type: str) -> str:
        """将参数类型映射到 JSON Schema 类型"""
        type_mapping = {
            'str': 'string',
            'string': 'string',
            'int': 'integer',
            'integer': 'integer',
            'float': 'number',
            'number': 'number',
            'bool': 'boolean',
            'boolean': 'boolean',
            'list': 'array',
            'array': 'array',
            'dict': 'object',
            'object': 'object',
            'json': 'object'
        }

        return type_mapping.get(param_type.lower(), 'string')


class MCPServerGenerator:
    """MCP 服务器生成器，在 tools/mcp 目录下创建完整 MCP 服务器"""

    def __init__(self, base_dir: Path, skill_path: Path, output_dir: Optional[Path] = None):
        self.base_dir = base_dir
        self.skill_path = skill_path
        if output_dir:
            self.mcp_dir = output_dir
        else:
            self.mcp_dir = base_dir / "tools" / "mcp" / skill_path.name

    def generate_server(self, analysis: Dict[str, Any], tool_definition: Tool) -> Path:
        """
        生成完整的 MCP 服务器目录

        Args:
            analysis: 技能分析结果
            tool_definition: MCP 工具定义

        Returns:
            生成的 MCP 服务器目录路径
        """
        # 删除现有目录（如果存在）并重新创建
        if self.mcp_dir.exists():
            shutil.rmtree(self.mcp_dir)
        self.mcp_dir.mkdir(parents=True)

        # 生成 README.md
        self._generate_readme(analysis)

        # 生成 pyproject.toml (Python MCP 服务器)
        self._generate_pyproject_toml(analysis)

        # 生成 main.py (Python MCP 服务器实现)
        self._generate_main_py(analysis, tool_definition)

        # 注意：不再生成 skill_wrapper.py，MCP 服务器直接调用原始技能脚本

        # 生成 requirements.txt
        self._generate_requirements_txt(analysis)

        # 生成 __init__.py
        self._generate_init_py(analysis)

        # 生成 CLAUDE.md
        self._generate_claude_md(analysis)

        return self.mcp_dir

    def _generate_readme(self, analysis: Dict[str, Any]) -> None:
        """生成 README.md"""
        name = analysis.get('name', self.skill_path.name)
        description = analysis.get('description', '')
        parameters = analysis.get('parameters', [])

        readme_content = f"""# {name} MCP Server

{description}

## Overview

This MCP server wraps the {name} skill to make it available via the Model Context Protocol.

## Tools

The server provides a single tool:

### `{name}`
- **Description**: {description}
- **Parameters**:
"""

        for param in parameters:
            param_name = param.get('name', '')
            param_type = param.get('type', 'string')
            required = param.get('required', False)
            param_desc = param.get('description', '')
            default = param.get('default', '')

            readme_content += f"  - `{param_name}` ({param_type}"
            if not required:
                readme_content += ", optional"
            readme_content += f"): {param_desc}"
            if default:
                readme_content += f" (default: {default})"
            readme_content += "\n"

        readme_content += f"""
## Usage

```bash
# Start the MCP server
uv run {name.lower().replace('_', '-')} -m scripts.main
```

## Configuration

Copy the skill configuration from the original skill directory.

## Development

This is a Python MCP server using uv for package management.

```bash
# Install uv (if not already installed)
pip install uv

# Install dependencies
uv pip install -e .

# Run the server directly
python -m scripts.main
```

## Package Management

This project uses uv for fast, reliable Python package management.

## License

MIT
"""

        readme_path = self.mcp_dir / "README.md"
        readme_path.write_text(readme_content, encoding='utf-8')

    def _generate_pyproject_toml(self, analysis: Dict[str, Any]) -> None:
        """生成 pyproject.toml"""
        name = analysis.get('name', self.skill_path.name).lower().replace('_', '-')

        pyproject_toml = f"""[project]
name = "mcp-{name}"
version = "1.0.0"
description = "MCP server for {analysis.get('name', self.skill_path.name)} skill"
authors = [
    {{ name = "qd-agents", email = "auto-generated@qd-agents.local" }}
]
readme = "README.md"
requires-python = ">=3.8"
dependencies = [
    "mcp[cli] >=1.0.0",
    "pydantic >=2.0.0",
]

[project.scripts]
{name}-mcp = "scripts.main:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""

        pyproject_path = self.mcp_dir / "pyproject.toml"
        pyproject_path.write_text(pyproject_toml, encoding='utf-8')

    def _generate_main_py(self, analysis: Dict[str, Any], tool_definition: Tool) -> None:
        """生成 Python MCP 服务器实现"""
        name = analysis.get('name', self.skill_path.name)
        package_name = name.lower().replace('_', '-')
        description = analysis.get('description', '')
        parameters = analysis.get('parameters', [])
        invocation_command = analysis.get('invocation_command', 'python scripts/search.py')
        env_vars = analysis.get('env_vars', [])

        # 创建 scripts 目录
        scripts_dir = self.mcp_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)

        # 生成 main.py
        main_py = f'''"""
{name} MCP 服务器

将 {name} 技能包装为 MCP 服务器。
"""

import asyncio
import json
import sys
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from mcp.server import Server
from mcp import Tool as ToolModel
from mcp.server.stdio import stdio_server
from pydantic import BaseModel, Field


# 技能配置
import os
# 计算技能路径相对位置（MCP服务器在 tools/mcp/<技能名>/scripts/main.py）
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
SKILL_PATH = PROJECT_ROOT / "tools" / "skills" / "{name}"
CONFIG_PATH = PROJECT_ROOT / "config.json"
INVOCATION_COMMAND = {repr(invocation_command)}

def load_skill_config() -> dict:
    """从 config.json 加载技能配置"""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
        # 首先尝试从 skills.<name> 获取配置
        skill_config = config.get('skills', {{}}).get('{name}', {{}})
        if skill_config:
            return skill_config
        # 回退到百度搜索特定配置
        if '{name}' == 'baidu-search':
            baidu_config = config.get('search', {{}}).get('baidu', {{}})
            api_key = baidu_config.get('api_key_1') or baidu_config.get('api_key_2')
            if api_key:
                return {{'api_key': api_key}}
        # 如果找不到配置，返回空字典
        return {{}}
    except Exception as e:
        raise RuntimeError(f"加载技能配置失败: {{e}}")


# 参数模型定义
class {self._to_pascal_case(name)}Params(BaseModel):
    """
    {name} 工具参数
    """
'''

        # 添加参数字段
        for param in parameters:
            param_name = param.get('name', '')
            param_type = param.get('type', 'string')
            py_type = self._map_to_py_type(param_type)
            param_description = param.get('description', '')
            required = param.get('required', False)
            default = param.get('default')

            if required:
                field_def = f'    {param_name}: {py_type}'
            else:
                if default is not None:
                    # 处理布尔类型的默认值
                    if param_type.lower() in ['bool', 'boolean']:
                        default_value = 'True' if str(default).lower() == 'true' else 'False'
                    else:
                        default_value = repr(default)
                    # 转义描述中的引号
                    escaped_description = param_description.replace('"', '\\"')
                    field_def = f'    {param_name}: {py_type} = Field(default={default_value}, description="{escaped_description}")'
                else:
                    # 转义描述中的引号
                    escaped_description = param_description.replace('"', '\\"')
                    field_def = f'    {param_name}: {py_type} | None = Field(default=None, description="{escaped_description}")'

            main_py += f'{field_def}\n'

        if not parameters:
            main_py += '    # 无参数定义\n'

        main_py += f'''

# 工具定义
{name.upper().replace('-', '_')}_TOOL = ToolModel(
    name="{name}",
    description="{description}",
    inputSchema={repr(tool_definition.parameters)},
)


class {self._to_pascal_case(name)}MCPServer:
    """{name} MCP 服务器"""

    def __init__(self):
        self.server = Server("mcp-{package_name}", "1.0.0")
        self.setup_tools()

    def setup_tools(self):
        """设置工具处理器"""

        @self.server.list_tools()
        async def handle_list_tools():
            """列出可用工具"""
            return [{name.upper().replace('-', '_')}_TOOL]

        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: Dict[str, Any]):
            """处理工具调用"""
            if name != "{name}":
                raise ValueError(f"未知工具: {{name}}")

            # 验证参数
            try:
                params = {self._to_pascal_case(name)}Params(**arguments)
            except Exception as e:
                raise ValueError(f"参数验证失败: {{e}}")

            try:
                result = await self.execute_skill(params.model_dump())
                return {{
                    "content": [
                        {{
                            "type": "text",
                            "text": json.dumps(result, indent=2, ensure_ascii=False)
                        }}
                    ]
                }}
            except Exception as e:
                return {{
                    "content": [
                        {{
                            "type": "text",
                            "text": f"错误: {{str(e)}}"
                        }}
                    ],
                    "isError": True
                }}

    async def execute_skill(self, params: Dict[str, Any]) -> Any:
        """执行技能"""
        # 加载技能配置
        config = load_skill_config()

        # 解析调用命令
        cmd_parts = INVOCATION_COMMAND.split()

        # 添加参数（作为 JSON 字符串传递）
        args = [json.dumps(params)]
        full_command = cmd_parts + args

        # 设置环境变量（包含配置中的键值对）
        env = os.environ.copy()
        for key, value in config.items():
            env[key.upper()] = str(value)

        # 执行命令
        process = await asyncio.create_subprocess_exec(
            *full_command,
            cwd=str(SKILL_PATH),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode('utf-8', errors='ignore') if stderr else stdout.decode('utf-8', errors='ignore')
            raise RuntimeError(f"技能执行失败 (代码{{process.returncode}}): {{error_msg}}")

        # 尝试解析 JSON 输出
        output = stdout.decode('utf-8', errors='ignore').strip()
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return {{"output": output}}


async def main():
    """主函数"""
    server = {self._to_pascal_case(name)}MCPServer()

    async with stdio_server() as (read_stream, write_stream):
        await server.server.run(
            read_stream,
            write_stream,
            server.server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
'''

        main_path = scripts_dir / "main.py"
        main_path.write_text(main_py, encoding='utf-8')

    def _generate_skill_wrapper(self, analysis: Dict[str, Any]) -> None:
        """生成技能包装器脚本（Python）"""
        name = analysis.get('name', self.skill_path.name)

        wrapper_py = f'''"""
{name} Skill Wrapper

This script wraps the {name} skill for use with the MCP server.
"""

import json
import sys
import os
import subprocess
from pathlib import Path

def execute_skill(params: dict) -> dict:
    """
    执行技能

    Args:
        params: 技能参数

    Returns:
        执行结果
    """
    # 这里应该调用原始技能脚本
    # 由于技能实现可能不同，这里提供一个通用包装器

    # 获取技能目录
    skill_dir = Path(__file__).parent.parent / "{self._get_relative_skill_path()}"

    # 查找主要的技能脚本
    script_path = None
    for pattern in ['*.py', '*.sh', '*.js']:
        scripts = list(skill_dir.rglob(pattern))
        if scripts:
            script_path = scripts[0]
            break

    if not script_path:
        raise FileNotFoundError(f"No skill script found in {{skill_dir}}")

    # 执行脚本
    cmd = []
    if script_path.suffix == '.py':
        cmd = ['python', str(script_path)]
    elif script_path.suffix == '.sh':
        cmd = ['bash', str(script_path)]
    elif script_path.suffix == '.js':
        cmd = ['node', str(script_path)]

    # 添加参数（作为 JSON 字符串传递）
    input_json = json.dumps(params)

    try:
        result = subprocess.run(
            cmd + [input_json],
            capture_output=True,
            text=True,
            cwd=skill_dir,
            timeout=30
        )

        if result.returncode != 0:
            raise RuntimeError(f"Skill execution failed: {{result.stderr}}")

        # 尝试解析 JSON 输出
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {{"output": result.stdout.strip()}}

    except subprocess.TimeoutExpired:
        raise TimeoutError("Skill execution timed out after 30 seconds")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({{"error": "No parameters provided"}}))
        sys.exit(1)

    try:
        params = json.loads(sys.argv[1])
        result = execute_skill(params)
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({{"error": str(e)}}))
        sys.exit(1)
'''

        scripts_dir = self.mcp_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        wrapper_path = scripts_dir / "skill_wrapper.py"
        wrapper_path.write_text(wrapper_py, encoding='utf-8')

    def _generate_claude_md(self, analysis: Dict[str, Any]) -> None:
        """生成 CLAUDE.md"""
        name = analysis.get('name', self.skill_path.name)
        package_name = name.lower().replace('_', '-')

        claude_md = f"""# CLAUDE.md - {name} MCP Server

This MCP server provides access to the {name} skill via the Model Context Protocol.

## Architecture

- `scripts/main.py` - Main MCP server implementation (Python)
- `skill_wrapper.py` - Python wrapper for the original skill
- `pyproject.toml` - Python project configuration and dependencies
- `test/validate.py` - Python validation script to verify server functionality

## Development

This is a Python MCP server using uv for package management.

```bash
# Install uv (if not already installed)
pip install uv

# Install dependencies
uv pip install -e .

# Run the server
uv run {package_name} -m scripts.main

# Or run directly
python -m scripts.main
```

## Configuration

The server reads configuration from the original skill directory.

## Package Management

This project uses uv for fast, reliable Python package management.

## Notes

This is an auto-generated MCP server wrapper for the {name} skill.
"""

        claude_path = self.mcp_dir / "CLAUDE.md"
        claude_path.write_text(claude_md, encoding='utf-8')

    def generate_validation_script(self, analysis: Dict[str, Any], tool_definition: Tool) -> None:
        """生成验证脚本"""
        name = analysis.get('name', self.skill_path.name)
        package_name = name.lower().replace('_', '-')
        test_dir = self.mcp_dir / "test"
        test_dir.mkdir(exist_ok=True)

        # 生成简单的 Python 验证脚本
        validate_py = f'''"""
{name} MCP 服务器验证脚本
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path
import signal
import time


async def validate_server():
    """验证 MCP 服务器"""
    print(f"验证 {name} MCP 服务器...")

    # 启动服务器进程
    server_process = subprocess.Popen(
        [sys.executable, "-m", "scripts.main"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # 行缓冲
    )

    try:
        # 给服务器一点时间启动
        await asyncio.sleep(1)

        # 发送初始化请求（MCP 初始化协议）
        init_request = {{
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {{
                "protocolVersion": "2024-11-05",
                "capabilities": {{}},
                "clientInfo": {{
                    "name": "validation-client",
                    "version": "1.0.0"
                }}
            }}
        }}

        server_process.stdin.write(json.dumps(init_request) + "\\n")
        server_process.stdin.flush()

        # 发送列出工具请求
        list_tools_request = {{
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        }}

        server_process.stdin.write(json.dumps(list_tools_request) + "\\n")
        server_process.stdin.flush()

        # 等待响应
        await asyncio.sleep(2)

        # 读取输出
        stdout_lines = []
        while True:
            line = server_process.stdout.readline()
            if not line:
                break
            stdout_lines.append(line)

            # 检查是否有工具被列出
            if '"method":"tools/list"' in line or '"result"' in line:
                print("SUCCESS: 服务器响应工具列表请求")
                break

        stdout = "".join(stdout_lines)
        if '"method":"tools/list"' not in stdout and '"result"' not in stdout:
            print("WARNING: 服务器未响应工具列表请求")
            print("输出:", stdout[:500])  # 只显示前500字符

    except Exception as e:
        print(f"验证过程中出错: {{e}}")

    finally:
        # 清理
        try:
            server_process.send_signal(signal.SIGTERM)
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_process.kill()
        except Exception as e:
            print(f"清理过程中出错: {{e}}")

    print("验证完成。")


def main():
    """主函数"""
    try:
        asyncio.run(validate_server())
        sys.exit(0)
    except Exception as e:
        print(f"验证失败: {{e}}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
'''

        validate_path = test_dir / "validate.py"
        validate_path.write_text(validate_py, encoding='utf-8')

    def _generate_requirements_txt(self, analysis: Dict[str, Any]) -> None:
        """生成 requirements.txt 文件"""
        # 生成基本的依赖
        requirements_txt = '''mcp[cli]>=1.0.0
pydantic>=2.0.0
'''
        requirements_path = self.mcp_dir / "requirements.txt"
        requirements_path.write_text(requirements_txt, encoding='utf-8')

    def _generate_init_py(self, analysis: Dict[str, Any]) -> None:
        """生成 __init__.py 文件（可选，放在 scripts 目录下）"""
        # 创建 scripts 目录
        scripts_dir = self.mcp_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)

        # 生成 __init__.py（可选，scripts 目录不需要，但为了兼容性生成）
        init_py = '''"""
MCP Server Scripts Package
"""
__version__ = "1.0.0"
'''
        init_path = scripts_dir / "__init__.py"
        init_path.write_text(init_py, encoding='utf-8')

    def _to_pascal_case(self, name: str) -> str:
        """将烤肉串或蛇形命名转换为帕斯卡命名"""
        return ''.join(word.capitalize() for word in name.replace('-', '_').split('_'))

    def _get_relative_skill_path(self) -> str:
        """获取技能相对于基础目录的相对路径，如果不在子路径中则返回绝对路径"""
        try:
            # 尝试计算相对路径
            return self.skill_path.relative_to(self.base_dir).as_posix()
        except ValueError:
            # 如果不在子路径中，返回绝对路径
            return self.skill_path.absolute().as_posix()

    def _map_to_py_type(self, param_type: str) -> str:
        """将参数类型映射到 Python 类型"""
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
            'json': 'Any'
        }
        return type_mapping.get(param_type.lower(), 'str')

    def _map_to_ts_type(self, param_type: str) -> str:
        """将参数类型映射到 TypeScript 类型"""
        type_mapping = {
            'str': 'string',
            'string': 'string',
            'int': 'number',
            'integer': 'number',
            'float': 'number',
            'number': 'number',
            'bool': 'boolean',
            'boolean': 'boolean',
            'list': 'any[]',
            'array': 'any[]',
            'dict': 'Record<string, any>',
            'object': 'Record<string, any>',
            'json': 'any'
        }

        return type_mapping.get(param_type.lower(), 'string')