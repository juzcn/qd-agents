"""
URL 分析结果数据模型
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SkillInfo(BaseModel):
    """skillset 中的单个 skill 信息"""
    name: str = Field(description="Skill 名称")
    description: str = Field(default="", description="Skill 描述")
    skill_md_url: str = Field(default="", description="SKILL.md 的下载 URL（raw 内容链接）")


class UrlAnalyzeResult(BaseModel):
    """URL 分析结果"""
    type: Literal["pypi", "mcp", "skill", "skillset"] = Field(
        description="工具类型: pypi(Python包), mcp(MCP服务器), skill(单个技能), skillset(技能集合)"
    )
    name: str = Field(description="工具/项目名称")
    description: str = Field(default="", description="工具/项目描述")
    # pypi
    package_name: str = Field(default="", description="PyPI 包名（pypi 类型时）")
    install_command: str = Field(default="", description="安装命令（如 uv add xxx, pip install xxx）")
    # mcp
    mcp_command: str = Field(default="", description="MCP 启动命令（如 npx, uvx）")
    mcp_args: list[str] = Field(default_factory=list, description="MCP 启动参数")
    mcp_transport: str = Field(default="stdio", description="MCP 传输模式")
    mcp_env: dict[str, str] = Field(default_factory=dict, description="MCP 环境变量")
    # skill/skillset
    skill_md_content: str = Field(default="", description="SKILL.md 内容（skill 类型时直接内嵌）")
    skills: list[SkillInfo] = Field(default_factory=list, description="子 skill 列表（skillset 类型时）")
    # API key / 环境变量
    env_vars: dict[str, str] = Field(
        default_factory=dict,
        description="工具所需的环境变量，key=变量名，value=说明（如 {'TAVILY_API_KEY': 'Tavily API 密钥，从 https://tavily.com 获取'}）",
    )
    # 版本信息
    version: str = Field(default="", description="工具版本号（如 1.2.3）")
    install_source: str = Field(default="", description="安装源标识（如 npm 包名 @anthropic/mcp-server、pip 包名 tavily-python）")
    # 通用
    prereqs: list[str] = Field(default_factory=list, description="前置安装步骤（如安装 CLI）")
    success: bool = Field(default=True, description="是否分析成功")
    failure_reason: str | None = Field(default=None, description="失败原因")
