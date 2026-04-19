"""
baidu-search MCP 服务器验证脚本
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path
import signal
import time


async def validate_server():
    """验证 MCP 服务器"""
    print(f"验证 baidu-search MCP 服务器...")

    # 启动服务器进程
    server_process = subprocess.Popen(
        [sys.executable, "-m", f"baidu-search.main"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # 行缓冲
    )

    try:
        # 给服务器一点时间启动
        await asyncio.sleep(1)

        # 发送初始化请求（MCP 初始化协议）
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "validation-client",
                    "version": "1.0.0"
                }
            }
        }

        server_process.stdin.write(json.dumps(init_request) + "\n")
        server_process.stdin.flush()

        # 发送列出工具请求
        list_tools_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        }

        server_process.stdin.write(json.dumps(list_tools_request) + "\n")
        server_process.stdin.flush()

        # 等待响应
        await asyncio.sleep(2)

        # 读取输出
        stdout_lines = []
        while True:
            line = server_process.stdout.readline()
            if not line:
                break
            stdout_lines.append(line)

            # 检查是否有工具被列出
            if '"method":"tools/list"' in line or '"result"' in line:
                print("SUCCESS: 服务器响应工具列表请求")
                break

        stdout = "".join(stdout_lines)
        if '"method":"tools/list"' not in stdout and '"result"' not in stdout:
            print("WARNING: 服务器未响应工具列表请求")
            print("输出:", stdout[:500])  # 只显示前500字符

    except Exception as e:
        print(f"验证过程中出错: {e}")

    finally:
        # 清理
        try:
            server_process.send_signal(signal.SIGTERM)
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_process.kill()
        except Exception as e:
            print(f"清理过程中出错: {e}")

    print("验证完成。")


def main():
    """主函数"""
    try:
        asyncio.run(validate_server())
        sys.exit(0)
    except Exception as e:
        print(f"验证失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
