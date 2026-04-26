"""
CLI 主入口 - 重构后版本

注意：此文件已大幅简化，大部分逻辑已移至 modules/ 和 commands/ 目录。
"""

import logging
from typing import Optional

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.styles import Style

from .app import app, main, console


logger = logging.getLogger("qd-agents")


class ChatCommandCompleter(Completer):
    """聊天命令补全器"""

    COMMANDS = {
        "/quit": "退出程序",
        "/help": "显示帮助信息",
        "/model": "显示当前模型",
        "/models": "列出并切换可用模式",
        "/tools": "列出可用工具",
    }

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        # 如果以 / 开头，补全命令
        if text.startswith("/"):
            for cmd, desc in self.COMMANDS.items():
                if cmd.startswith(text):
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display_meta=desc,
                    )


# 定义 prompt 样式
prompt_style = Style.from_dict(
    {
        "prompt": "bold cyan",
    }
)


def run():
    """脚本入口点"""
    app()


# 导出公共接口
__all__ = ["app", "main", "run", "console", "ChatCommandCompleter", "prompt_style"]


if __name__ == "__main__":
    run()