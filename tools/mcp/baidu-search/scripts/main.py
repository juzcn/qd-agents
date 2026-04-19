"""
test-search MCP 服务器

将 test-search 技能包装为 MCP 服务器。
"""

import asyncio
import json
import sys
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from mcp import Server
from mcp.server.models import Tool as ToolModel
from mcp.server.stdio import stdio_server
from pydantic import BaseModel, Field


# 技能配置
import os
# 从项目根目录计算技能路径（MCP服务器在 tools/mcp/<技能名>/scripts/main.py）
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
SKILL_PATH = PROJECT_ROOT / "tools" / "skills" / "test-search"
CONFIG_PATH = PROJECT_ROOT / "config.json"
INVOCATION_COMMAND = "python scripts/search.py"

def load_baidu_api_key() -> str:
    """从 config.json 加载百度 API 密钥"""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
        # 从 search.baidu 获取 API 密钥
        baidu_config = config.get('search', {}).get('baidu', {})
        # 尝试获取 api_key_1 或 api_key_2
        api_key = baidu_config.get('api_key_1') or baidu_config.get('api_key_2')
        if not api_key:
            raise ValueError("在 config.json 中未找到百度 API 密钥 (api_key_1 或 api_key_2)")
        return api_key
    except Exception as e:
        raise RuntimeError(f"加载百度 API 密钥失败: {e}")


# 参数模型定义
class TestSearchParams(BaseModel):
    """
    test-search 工具参数
    """
    query: str


# 工具定义
TEST_SEARCH_TOOL = ToolModel(
    name="test-search",
    description="Test search description",
    inputSchema={
  "type": "object",
  "properties": {
    "query": {
      "type": "string"
    }
  },
  "required": [
    "query"
  ]
},
)


class TestSearchMCPServer:
    """test-search MCP 服务器"""

    def __init__(self):
        self.server = Server("mcp-test-search", "1.0.0")
        self.setup_tools()

    def setup_tools(self):
        """设置工具处理器"""

        @self.server.list_tools()
        async def handle_list_tools():
            """列出可用工具"""
            return [TEST_SEARCH_TOOL]

        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: Dict[str, Any]):
            """处理工具调用"""
            if name != "test-search":
                raise ValueError(f"未知工具: {name}")

            # 验证参数
            try:
                params = TestSearchParams(**arguments)
            except Exception as e:
                raise ValueError(f"参数验证失败: {e}")

            try:
                result = await self.execute_skill(params.model_dump())
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, indent=2, ensure_ascii=False)
                        }
                    ]
                }
            except Exception as e:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"错误: {str(e)}"
                        }
                    ],
                    "isError": True
                }

    async def execute_skill(self, params: Dict[str, Any]) -> Any:
        """执行技能"""
        # 加载百度 API 密钥
        api_key = load_baidu_api_key()

        # 解析调用命令
        cmd_parts = INVOCATION_COMMAND.split()

        # 添加参数（作为 JSON 字符串传递）
        args = [json.dumps(params)]
        full_command = cmd_parts + args

        # 设置环境变量（包含 API 密钥）
        env = os.environ.copy()
        env['BAIDU_API_KEY'] = api_key

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
    server = TestSearchMCPServer()

    async with stdio_server() as (read_stream, write_stream):
        await server.server.run(
            read_stream,
            write_stream,
            server.server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
