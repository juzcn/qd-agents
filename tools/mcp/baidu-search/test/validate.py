"""
baidu-search MCP 服务器验证脚本（简化同步版）
"""

import subprocess
import sys
import time
import os


def validate_server():
    """验证 MCP 服务器"""
    print(f"验证 baidu-search MCP 服务器...")

    # 启动服务器进程
    # 使用相对路径，确保工作目录正确
    cwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    server_process = subprocess.Popen(
        [sys.executable, "-m", "scripts.main"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=cwd
    )

    try:
        # 给服务器一点时间启动
        time.sleep(2)

        # 检查进程是否还在运行
        if server_process.poll() is not None:
            # 进程已退出，读取错误输出
            stderr_output = server_process.stderr.read() if server_process.stderr else ""
            print(f"服务器进程意外退出，返回码: {server_process.returncode}")
            if stderr_output:
                print(f"错误输出: {stderr_output[:500]}")
            return False

        print("SUCCESS: 服务器启动成功")
        return True

    except Exception as e:
        print(f"验证过程中出错: {e}")
        return False

    finally:
        # 清理
        try:
            # 先尝试优雅终止
            server_process.terminate()
            # 等待最多3秒
            for _ in range(30):  # 每0.1秒检查一次，共3秒
                if server_process.poll() is not None:
                    break
                time.sleep(0.1)
            else:
                # 如果进程还在运行，强制终止
                server_process.kill()
                server_process.wait(timeout=1)
        except Exception as e:
            print(f"清理过程中出错: {e}")
        finally:
            # 确保进程终止
            if server_process.poll() is None:
                try:
                    server_process.kill()
                except:
                    pass

    print("验证完成。")


def main():
    """主函数"""
    try:
        success = validate_server()
        if success:
            print("验证通过！")
            sys.exit(0)
        else:
            print("验证失败！")
            sys.exit(1)
    except Exception as e:
        print(f"验证失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
