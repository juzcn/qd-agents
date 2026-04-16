"""
工具注册管理

负责自动注册 MCP 工具和其他内置工具。
"""

import sys
from pathlib import Path
from typing import Optional

from rich.console import Console

from qd_agents.registry import ToolRegistry
from qd_agents.tools.executor import create_mcp_tool


async def auto_register_mcp_weather_tools(
    console: Console,
    tool_registry: ToolRegistry,
    endpoint: str = "http://localhost:8000",
) -> None:
    """
    自动注册 MCP 天气工具（如果尚未注册）

    Args:
        console: Rich 控制台对象，用于输出信息
        tool_registry: 工具注册表
        endpoint: MCP 服务器端点
    """
    # 检查当前天气工具是否已存在
    if not tool_registry.get("weather.get_current_weather"):
        current_weather_tool = create_mcp_tool(
            name="get_current_weather",
            description="获取指定城市的当前天气信息，包括温度、湿度、风速、天气描述等",
            server="weather",
            tool_name="get_current_weather",
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名称，如 'Beijing' 或 '上海'"},
                    "country": {"type": "string", "description": "国家代码，如 'CN'，可选"},
                    "latitude": {"type": "number", "description": "纬度，可选"},
                    "longitude": {"type": "number", "description": "经度，可选"},
                },
                "required": ["city"],
            },
            transport="sse",
            endpoint=endpoint,
            category="weather",
            tags=["weather", "mcp", "current"],
        )
        tool_registry.register(current_weather_tool)
        console.print("[dim]✅ 已自动注册工具: weather.get_current_weather[/]", style="dim")

    # 检查空气质量工具是否已存在
    if not tool_registry.get("weather.get_air_quality"):
        air_quality_tool = create_mcp_tool(
            name="get_air_quality",
            description="获取指定城市的空气质量信息，包括 PM2.5、PM10、臭氧、NO₂、CO 等级和健康建议",
            server="weather",
            tool_name="get_air_quality",
            parameters={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名称"},
                    "country": {"type": "string", "description": "国家代码，可选"},
                },
                "required": ["city"],
            },
            transport="sse",
            endpoint=endpoint,
            category="air-quality",
            tags=["air-quality", "mcp", "pollution"],
        )
        tool_registry.register(air_quality_tool)
        console.print("[dim]✅ 已自动注册工具: weather.get_air_quality[/]", style="dim")


async def auto_register_pdf_skill(
    console: Console,
    tool_registry: ToolRegistry,
    skill_script_path: str | Path | None = None,
) -> None:
    """
    自动注册 PDF 解析 skill 工具

    Args:
        console: Rich 控制台对象，用于输出信息
        tool_registry: 工具注册表
        skill_script_path: skill 脚本路径，如果为 None 则尝试自动查找
    """
    from qd_agents.tools.executor import create_skill_tool

    # 检查PDF解析工具是否已存在
    if tool_registry.get("pdf.parser"):
        console.print("[dim]✅ PDF 解析工具已注册[/]", style="dim")
        return

    # 确定skill脚本路径
    if skill_script_path is None:
        # 尝试多个可能的路径
        possible_paths = [
            Path("/tmp/pdf-parser-skill/scripts/pymupdf_parse.py"),
            Path("C:/Users/juz_c/AppData/Local/Temp/pdf-parser-skill/scripts/pymupdf_parse.py"),
            Path("./pdf-parser-skill/scripts/pymupdf_parse.py"),
            Path("~/.clawdbot/skills/PyMuPDF-PDF-Parser-openclaw-skill/scripts/pymupdf_parse.py").expanduser(),
        ]

        skill_path = None
        for p in possible_paths:
            if p.exists():
                skill_path = p
                break

        if skill_path is None:
            console.print("[yellow]⚠️  未找到 PDF 解析 skill 脚本[/]", style="yellow")
            console.print("[dim]请手动指定 skill_script_path 参数[/]", style="dim")
            return
    else:
        skill_path = Path(skill_script_path)

    console.print(f"[dim]找到 skill 脚本: {skill_path}[/]", style="dim")

    # 创建PDF解析skill工具
    pdf_skill_tool = create_skill_tool(
        name="parse_pdf",
        description="解析PDF文件，提取文本和元数据为JSON/Markdown格式",
        skill_id="pdf.parser",
        command=sys.executable,  # 使用当前Python解释器
        args=[str(skill_path), "{pdf_path}", "--format", "{format}", "--outroot", "{output_dir}"],
        parameters={
            "type": "object",
            "properties": {
                "pdf_path": {"type": "string", "description": "PDF文件路径"},
                "format": {"type": "string", "description": "输出格式", "enum": ["md", "json", "both"], "default": "json"},
                "output_dir": {"type": "string", "description": "输出目录", "default": "./skill-output"},
            },
            "required": ["pdf_path"]
        },
        category="document-processing",
        tags=["pdf", "parser", "document", "text-extraction"],
    )

    tool_registry.register(pdf_skill_tool)
    console.print("[dim]✅ 已自动注册工具: pdf.parser[/]", style="dim")


async def auto_register_bash_tools(
    console: Console,
    tool_registry: ToolRegistry,
) -> None:
    """
    自动注册 Bash 工具（如果尚未注册）

    Args:
        console: Rich 控制台对象，用于输出信息
        tool_registry: 工具注册表
    """
    from qd_agents.tools.executor import create_bash_tool

    # 检查通用bash工具是否已存在
    if not tool_registry.get("bash.execute"):
        bash_tool = create_bash_tool(
            name="execute_bash",
            description="执行bash/shell命令，支持管道、重定向等shell特性",
            shell_command="{command}",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的bash/shell命令"},
                },
                "required": ["command"],
            },
            category="shell",
            tags=["bash", "shell", "command"],
        )
        tool_registry.register(bash_tool)
        console.print("[dim]✅ 已自动注册工具: bash.execute[/]", style="dim")