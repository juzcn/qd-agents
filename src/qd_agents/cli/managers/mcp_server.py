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
        self.server_process: Optional[Any] = None

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

            # 使用异步shell执行命令，与bash tool执行方式一致
            command = f"{exe_path} --mode sse --port {self.port}"

            # 创建异步子进程（类似于BashToolExecutor的实现）
            self.server_process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
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
            if self.server_process and self.server_process.returncode is None:
                self.server_process.terminate()
                try:
                    await asyncio.wait_for(self.server_process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    self.server_process.kill()
                    await self.server_process.wait()

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

    async def cleanup_async(self):
        """异步清理函数：在会话结束时停止 MCP 服务器"""
        if self.server_process is None:
            return

        try:
            # 检查进程是否仍在运行
            is_running = False
            if hasattr(self.server_process, 'poll'):  # subprocess.Popen
                is_running = self.server_process.poll() is None
            elif hasattr(self.server_process, 'returncode'):  # asyncio.subprocess.Process
                is_running = self.server_process.returncode is None

            if not is_running:
                # 即使进程已经结束，也要确保管道关闭
                await self._close_pipes_async()
                return

            self.console.print("[dim]正在停止 MCP 天气服务器...[/]", style="dim")

            # 根据进程类型执行清理
            if hasattr(self.server_process, 'terminate'):
                # 先关闭管道，避免进程阻塞在写输出上
                await self._close_pipes_async()
                self.server_process.terminate()

                # 等待进程结束
                if hasattr(self.server_process, 'wait') and callable(self.server_process.wait):
                    if asyncio.iscoroutinefunction(self.server_process.wait):
                        # 异步wait
                        try:
                            await asyncio.wait_for(self.server_process.wait(), timeout=2)
                        except (asyncio.TimeoutError, TimeoutError):
                            if hasattr(self.server_process, 'kill'):
                                self.server_process.kill()
                                await self.server_process.wait()
                    else:
                        # 同步wait
                        try:
                            self.server_process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            if hasattr(self.server_process, 'kill'):
                                self.server_process.kill()
                                self.server_process.wait()

            self.console.print("[dim]✅ MCP 天气服务器已停止[/]", style="dim")

        except Exception as e:
            self.console.print(f"[dim]停止 MCP 服务器时出错: {e}[/]", style="dim")
            try:
                if hasattr(self.server_process, 'kill'):
                    self.server_process.kill()
            except:
                pass
        finally:
            # 确保管道关闭
            await self._close_pipes_async()
            # 帮助垃圾回收
            self.server_process = None

    def _close_pipes(self):
        """关闭子进程的管道（stdout/stderr）以避免Windows上的资源警告"""
        if self.server_process is None:
            return

        try:
            # 关闭stdout管道
            if hasattr(self.server_process, 'stdout') and self.server_process.stdout:
                if hasattr(self.server_process.stdout, 'close'):
                    try:
                        self.server_process.stdout.close()
                    except:
                        pass

            # 关闭stderr管道
            if hasattr(self.server_process, 'stderr') and self.server_process.stderr:
                if hasattr(self.server_process.stderr, 'close'):
                    try:
                        self.server_process.stderr.close()
                    except:
                        pass

        except Exception:
            # 忽略所有关闭管道时的错误
            pass

    async def _close_pipes_async(self):
        """异步关闭子进程的管道（stdout/stderr）并等待关闭完成"""
        if self.server_process is None:
            return

        try:
            # 关闭stdout管道并等待
            if hasattr(self.server_process, 'stdout') and self.server_process.stdout:
                if hasattr(self.server_process.stdout, 'close'):
                    try:
                        self.server_process.stdout.close()
                    except:
                        pass
                # 对于asyncio.StreamReader，等待关闭完成
                if hasattr(self.server_process.stdout, 'wait_closed'):
                    try:
                        await self.server_process.stdout.wait_closed()
                    except:
                        pass

            # 关闭stderr管道并等待
            if hasattr(self.server_process, 'stderr') and self.server_process.stderr:
                if hasattr(self.server_process.stderr, 'close'):
                    try:
                        self.server_process.stderr.close()
                    except:
                        pass
                # 对于asyncio.StreamReader，等待关闭完成
                if hasattr(self.server_process.stderr, 'wait_closed'):
                    try:
                        await self.server_process.stderr.wait_closed()
                    except:
                        pass

        except Exception:
            # 忽略所有关闭管道时的错误
            pass

    def cleanup(self):
        """同步清理函数（保持向后兼容）"""
        if self.server_process is None:
            return

        try:
            # 检查进程是否仍在运行
            is_running = False
            if hasattr(self.server_process, 'poll'):  # subprocess.Popen
                is_running = self.server_process.poll() is None
            elif hasattr(self.server_process, 'returncode'):  # asyncio.subprocess.Process
                is_running = self.server_process.returncode is None

            if not is_running:
                # 即使进程已经结束，也要确保管道关闭
                self._close_pipes()
                return

            self.console.print("[dim]正在停止 MCP 天气服务器...[/]", style="dim")

            # 对于 asyncio 子进程，直接发送终止信号，不等待
            if hasattr(self.server_process, 'terminate'):
                # 先关闭管道，避免进程阻塞在写输出上
                self._close_pipes()
                self.server_process.terminate()

            self.console.print("[dim]✅ MCP 天气服务器已停止[/]", style="dim")

        except Exception as e:
            self.console.print(f"[dim]停止 MCP 服务器时出错: {e}[/]", style="dim")
            try:
                if hasattr(self.server_process, 'kill'):
                    self.server_process.kill()
            except:
                pass
        finally:
            # 确保管道关闭
            self._close_pipes()
            # 帮助垃圾回收
            self.server_process = None

    async def auto_start_mcp_weather_server(self) -> Optional[Callable[[], Any]]:
        """
        自动启动 MCP 天气服务器（兼容旧接口）

        Returns:
            如果成功启动服务器，返回清理函数；否则返回 None
        """
        return await self.start_server()