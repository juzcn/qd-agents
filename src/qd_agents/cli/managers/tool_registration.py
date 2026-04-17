"""
工具注册管理

负责自动注册内置工具。
"""

import sys
from pathlib import Path
from typing import Optional

from rich.console import Console

from qd_agents.registry import ToolRegistry




def auto_register_pdf_skill(
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
        console.print("[dim][OK] PDF 解析工具已注册[/]", style="dim")
        return

    # 确定skill脚本路径
    if skill_script_path is None:
        # 尝试多个可能的路径
        possible_paths = [
            # 项目内的 skills 目录（保持原始文件夹名）
            Path("./skills/PyMuPDF-PDF-Parser-openclaw-skill/scripts/pymupdf_parse.py"),
            # 用户主目录下的 skills 目录
            Path.home() / "skills" / "PyMuPDF-PDF-Parser-openclaw-skill" / "scripts" / "pymupdf_parse.py",
            # 原 clawdbot 目录（保留兼容性）
            Path("~/.clawdbot/skills/PyMuPDF-PDF-Parser-openclaw-skill/scripts/pymupdf_parse.py").expanduser(),
        ]

        skill_path = None
        for p in possible_paths:
            if p.exists():
                skill_path = p
                console.print(f"[dim]找到 skill 脚本: {skill_path}[/]", style="dim")
                break

        if skill_path is None:
            console.print("[yellow][WARN] 未找到 PDF 解析 skill 脚本[/]", style="yellow")
            console.print("[dim]请将 PDF 解析 skill 放置到以下任一位置:[/]", style="dim")
            console.print("[dim]  - ./skills/PyMuPDF-PDF-Parser-openclaw-skill/scripts/pymupdf_parse.py[/]", style="dim")
            console.print("[dim]  - ~/skills/PyMuPDF-PDF-Parser-openclaw-skill/scripts/pymupdf_parse.py[/]", style="dim")
            console.print("[dim]或手动指定 skill_script_path 参数[/]", style="dim")
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
    console.print("[dim][OK] 已自动注册工具: pdf.parser[/]", style="dim")


def auto_register_bash_tools(
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
        console.print("[dim][OK] 已自动注册工具: bash.execute[/]", style="dim")