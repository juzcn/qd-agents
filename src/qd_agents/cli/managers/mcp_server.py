"""
MCP 服务器管理器

负责 MCP 天气服务器的自动启动、状态检查和清理。
"""

import asyncio
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional, Callable, Any

from rich.console import Console


class MCPWeatherServerManager:
    """MCP 天气服务器管理器"""

    def __init__(self, console: Console, port: int = 8000, host: str = '127.0.0.1'):
        """
        初始化 MCP 服务器管理器

        Args:
            console: Rich 控制台对象，用于输出信息
            port: 服务器端口，默认 8000
            host: 服务器主机，默认 '127.0.0.1'
        """
        self.console = console
        self.port = port
        self.host = host
        self.server_process: Optional[subprocess.Popen] = None

    def _check_port_available(self) -> bool:
        """检查端口是否可用"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                result = s.connect_ex((self.host, self.port))
                return result != 0  # 0表示端口已占用
        except Exception:
            return False

    def _get_executable_path(self) -> Path:
        """
        获取跨平台的可执行文件路径

        Returns:
            Path: 可执行文件的路径
        """
        exe_dir = Path(sys.executable).parent

        # 根据平台选择可执行文件名
        if sys.platform == "win32":
            exe_name = "mcp_weather_server.exe"
        else:
            # Linux/macOS 和其他 Unix 系统
            exe_name = "mcp_weather_server"

        return exe_dir / exe_name

    async def start_server(self) -> Optional[Callable[[], Any]]:
        """
        启动 MCP 天气服务器

        Returns:
            如果成功启动服务器，返回清理函数；否则返回 None
        """
        # 获取可执行文件路径（在 try 块外部，以便错误处理中使用）
        exe_path = self._get_executable_path()

        try:
            self.console.print("[dim]正在检查 MCP 天气服务器状态...[/]", style="dim")

            # 检查端口是否可用
            if not self._check_port_available():
                self.console.print(f"[dim]✅ MCP 天气服务器已在运行 (端口 {self.port})[/]", style="dim")
                return None

            self.console.print("[dim]正在启动 MCP 天气服务器...[/]", style="dim")

            # 启动 mcp-weather-server 进程
            # 使用 subprocess.Popen 以便在会话结束时清理
            # 使用 exe 文件路径，为未来集成一般 CLI 工具做准备
            self.server_process = subprocess.Popen(
                [str(exe_path), "--mode", "sse", "--port", str(self.port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                # 分离进程组，避免信号传播
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
            )

            # 等待服务器启动（最多5秒）
            for i in range(10):
                await asyncio.sleep(0.5)
                if not self._check_port_available():
                    self.console.print("[dim]✅ MCP 天气服务器启动成功[/]", style="dim")
                    return self.cleanup

            # 如果启动失败，清理进程
            if self.server_process and self.server_process.poll() is None:
                self.server_process.terminate()
                self.server_process.wait(timeout=2)
            self.console.print("[yellow]⚠️  MCP 天气服务器启动超时，将继续使用演示模式[/]", style="dim")
            return None

        except FileNotFoundError:
            self.console.print(f"[yellow]⚠️  未找到 {exe_path.name} 可执行文件[/]", style="dim")
            self.console.print("[dim]  请安装: uv add mcp-weather-server[/]", style="dim")
            return None
        except Exception as e:
            self.console.print(f"[yellow]⚠️  启动 MCP 服务器失败: {e}[/]", style="dim")
            self.console.print("[dim]  将继续使用演示模式[/]", style="dim")
            return None

    def cleanup(self):
        """清理函数：在会话结束时停止 MCP 服务器"""
        if self.server_process and self.server_process.poll() is None:
            try:
                self.console.print("[dim]正在停止 MCP 天气服务器...[/]", style="dim")
                self.server_process.terminate()
                self.server_process.wait(timeout=2)
                self.console.print("[dim]✅ MCP 天气服务器已停止[/]", style="dim")
            except Exception as e:
                self.console.print(f"[dim]停止 MCP 服务器时出错: {e}[/]", style="dim")
                try:
                    self.server_process.kill()
                except:
                    pass

    async def auto_start_mcp_weather_server(self) -> Optional[Callable[[], Any]]:
        """
        自动启动 MCP 天气服务器（兼容旧接口）

        Returns:
            如果成功启动服务器，返回清理函数；否则返回 None
        """
        return await self.start_server()