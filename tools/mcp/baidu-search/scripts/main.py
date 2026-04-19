"""
baidu-search MCP 服务器

将 baidu-search 技能包装为 MCP 服务器。
"""

import asyncio
import json
import sys
import subprocess
import os
from pathlib import Path
from typing import Any, Dict, List

from mcp.server import Server
from mcp import Tool as ToolModel
from mcp.server.stdio import stdio_server
from pydantic import BaseModel, Field


# 技能配置
# 计算项目根目录：从 scripts/main.py 向上 5 级到项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
SKILL_PATH = PROJECT_ROOT / "tools" / "skills" / "baidu-search"
CONFIG_PATH = PROJECT_ROOT / "config.json"
INVOCATION_COMMAND = "python scripts/search.py '{JSON}'"

def load_skill_config() -> dict:
    """从 config.json 加载技能配置"""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
        # 首先尝试从 skills.<skill_name> 获取配置
        skill_config = config.get('skills', {}).get('baidu-search', {})
        if skill_config:
            return skill_config
        # 对于特定技能，尝试其他配置路径
        baidu_config = config.get('search', {}).get('baidu', {})
        if baidu_config:
            # 映射 API 密钥到环境变量
            api_key = baidu_config.get('api_key_1') or baidu_config.get('api_key_2')
            if api_key:
                return {'BAIDU_API_KEY': api_key}
        # 如果找不到配置，返回空字典
        return {}
    except Exception as e:
        raise RuntimeError(f"加载技能配置失败: {e}")


# 参数模型定义
class BaiduSearchParams(BaseModel):
    """
    baidu-search 工具参数
    """
    query: str
    edition: str = Field(default='standard', description="Search edition: `standard` (full) or `lite` (light)")
    resource_type_filter: List[Any] = Field(default=[{'type': 'web', 'top_k': 20}], description="Resource types: web (max 50), video (max 10), image (max 30), aladdin (max 5)")
    search_filter: Dict[str, Any] = Field(default={}, description="Advanced filters including site matching and date range")
    block_websites: List[Any] | None = Field(default=None, description="Sites to block, e.g. [\"tieba.baidu.com\"]")
    search_recency_filter: str = Field(default='year', description="Time filter: `week`, `month`, `semiyear`, `year`")
    safe_search: bool = Field(default=False, description="Enable strict content filtering")

# 工具定义
BAIDU_SEARCH_TOOL = ToolModel(
    name="baidu-search",
    description="Search the web using Baidu AI Search Engine (BDSE). Use for live information, documentation, or research topics.",
    inputSchema={'type': 'object', 'properties': {'query': {'type': 'string', 'description': 'Search query'}, 'edition': {'type': 'string', 'description': 'Search edition: `standard` (full) or `lite` (light)', 'default': 'standard'}, 'resource_type_filter': {'type': 'array', 'description': 'Resource types: web (max 50), video (max 10), image (max 30), aladdin (max 5)', 'default': [{'type': 'web', 'top_k': 20}]}, 'search_filter': {'type': 'object', 'description': 'Advanced filters including site matching and date range', 'default': {}}, 'block_websites': {'type': 'array', 'description': 'Sites to block, e.g. ["tieba.baidu.com"]'}, 'search_recency_filter': {'type': 'string', 'description': 'Time filter: `week`, `month`, `semiyear`, `year`', 'default': 'year'}, 'safe_search': {'type': 'boolean', 'description': 'Enable strict content filtering', 'default': False}}, 'required': ['query'], 'additionalProperties': False},
)


class BaiduSearchMCPServer:
    """baidu-search MCP 服务器"""

    def __init__(self):
        self.server = Server("mcp-baidu-search", "1.0.0")
        self.setup_tools()

    def setup_tools(self):
        """设置工具处理器"""

        @self.server.list_tools()
        async def handle_list_tools():
            """列出可用工具"""
            return [BAIDU_SEARCH_TOOL]

        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: Dict[str, Any]):
            """处理工具调用"""
            if name != "baidu-search":
                raise ValueError(f"未知工具: {name}")

            # 验证参数
            try:
                params = BaiduSearchParams(**arguments)
            except Exception as e:
                raise ValueError(f"参数验证失败: {e}")

            try:
                result = await self.execute_skill(params.model_dump())
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps(result, indent=2, ensure_ascii=False)
                    }]
                }
            except Exception as e:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"错误: {str(e)}"
                    }],
                    "isError": True
                }

    async def execute_skill(self, params: Dict[str, Any]) -> Any:
        """执行技能"""
        # 加载技能配置
        config = load_skill_config()

        # 解析调用命令，替换占位符
        invocation_str = INVOCATION_COMMAND
        full_command = []

        if invocation_str is None:
            raise ValueError("invocation_command 不能为 None")

        if not isinstance(invocation_str, str):
            invocation_str = str(invocation_str)

        if "'{JSON}'" in invocation_str:
            # 替换占位符为实际的 JSON 字符串
            command_str = invocation_str.replace("'{JSON}'", json.dumps(params))
            full_command = command_str.split()
        else:
            # 如果没有占位符，将 JSON 字符串作为最后一个参数添加
            cmd_parts = invocation_str.split()
            args = [json.dumps(params)]
            full_command = cmd_parts + args

        # 将 python/python3 替换为当前 Python 解释器
        if full_command and full_command[0] in ['python', 'python3']:
            full_command[0] = sys.executable

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
            raise RuntimeError(f"技能执行失败 (代码{process.returncode}): {error_msg}")

        # 尝试解析 JSON 输出
        output = stdout.decode('utf-8', errors='ignore').strip()
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return {"output": output}


async def main():
    """主函数"""
    server = BaiduSearchMCPServer()

    async with stdio_server() as (read_stream, write_stream):
        await server.server.run(
            read_stream,
            write_stream,
            server.server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())