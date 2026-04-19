"""
baidu-search MCP 服务器

将 baidu-search 技能包装为 MCP 服务器。
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
SKILL_PATH = Path(__file__).parent.parent / "tools/skills/baidu-search"
INVOCATION_COMMAND = "python3 skills/baidu-search/scripts/search.py '{JSON}'"


# 参数模型定义
class BaiduSearchParams(BaseModel):
    """
    baidu-search 工具参数
    """
    query: str
    edition: str = Field(default='standard', description="`standard` (full) or `lite` (light)")
    resource_type_filter: str = Field(default='web:20, others:0', description="Resource types: web (max 50), video (max 10), image (max 30), aladdin (max 5)")
    search_filter: str | None = Field(default=None, description="Advanced filters (see below)")
    block_websites: str | None = Field(default=None, description="Sites to block, e.g. ["tieba.baidu.com"]")
    search_recency_filter: str | None = Field(default=None, description="Time filter: `week`, `month`, `semiyear`, `year`")
    safe_search: bool = Field(default='false', description="Enable strict content filtering")


# 工具定义
BAIDU_SEARCH_TOOL = ToolModel(
    name="baidu-search",
    description="Enable strict content filtering",
    inputSchema={
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "Search query"
    },
    "edition": {
      "type": "string",
      "description": "`standard` (full) or `lite` (light)",
      "default": "standard"
    },
    "resource_type_filter": {
      "type": "string",
      "description": "Resource types: web (max 50), video (max 10), image (max 30), aladdin (max 5)",
      "default": "web:20, others:0"
    },
    "search_filter": {
      "type": "string",
      "description": "Advanced filters (see below)"
    },
    "block_websites": {
      "type": "string",
      "description": "Sites to block, e.g. [\"tieba.baidu.com\"]"
    },
    "search_recency_filter": {
      "type": "string",
      "description": "Time filter: `week`, `month`, `semiyear`, `year`"
    },
    "safe_search": {
      "type": "boolean",
      "description": "Enable strict content filtering",
      "default": "false"
    }
  },
  "required": [
    "query"
  ],
  "additionalProperties": false
},
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
        # 解析调用命令
        cmd_parts = INVOCATION_COMMAND.split()

        # 添加参数（作为 JSON 字符串传递）
        args = [json.dumps(params)]
        full_command = cmd_parts + args

        # 执行命令
        process = await asyncio.create_subprocess_exec(
            *full_command,
            cwd=str(SKILL_PATH),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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
