"""
提示词加载器 - 使用 Jinja2
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, Template, select_autoescape


class PromptLoader:
    """
    提示词加载器
    """

    def __init__(self, template_dir: Path | str):
        """
        初始化提示词加载器

        Args:
            template_dir: 模板目录
        """
        self.template_dir = Path(template_dir)
        self.env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.env.policies["json.dumps_kwargs"] = {"ensure_ascii": False}

    def get_template(self, template_name: str) -> Template:
        """
        获取模板对象

        Args:
            template_name: 模板名称（不含.j2扩展名也可）

        Returns:
            Jinja2 模板对象
        """
        if not template_name.endswith(".j2"):
            template_name = template_name + ".j2"
        return self.env.get_template(template_name)

    def render(self, template_name: str, **kwargs: Any) -> str:
        """
        渲染指定模板文件

        Args:
            template_name: 模板名称
            **kwargs: 模板变量

        Returns:
            渲染后的字符串
        """
        template = self.get_template(template_name)
        return template.render(**kwargs)

    def render_string(self, template_string: str, **kwargs: Any) -> str:
        """
        渲染模板字符串

        Args:
            template_string: 模板字符串
            **kwargs: 模板变量

        Returns:
            渲染后的字符串
        """
        template = self.env.from_string(template_string)
        return template.render(**kwargs)

    def list_templates(self) -> list[str]:
        """
        列出所有可用模板

        Returns:
            模板名称列表
        """
        return self.env.list_templates()


def render_template(template_string: str, **kwargs: Any) -> str:
    """
    快速渲染字符串模板

    Args:
        template_string: 模板字符串
        **kwargs: 模板变量

    Returns:
        渲染后的字符串
    """
    env = Environment()
    template = env.from_string(template_string)
    return template.render(**kwargs)
