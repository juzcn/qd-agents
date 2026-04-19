"""
诊断 MCP 服务器问题
"""

import asyncio
import json
import subprocess
import sys
import os
from pathlib import Path


def check_paths():
    """检查路径是否正确"""
    print("检查路径...")

    # 从 scripts/main.py 计算项目根目录
    script_dir = Path(__file__).parent
    project_root = script_dir

    # 检查 tools/mcp/baidu-search/scripts/main.py
    mcp_main = project_root / "tools" / "mcp" / "baidu-search" / "scripts" / "main.py"
    print(f"MCP主文件: {mcp_main}")
    print(f"MCP主文件是否存在: {mcp_main.exists()}")

    # 检查 tools/skills/baidu-search/scripts/search.py
    skill_script = project_root / "tools" / "skills" / "baidu-search" / "scripts" / "search.py"
    print(f"\n技能脚本: {skill_script}")
    print(f"技能脚本是否存在: {skill_script.exists()}")

    if skill_script.exists():
        print("\n技能脚本内容预览:")
        try:
            with open(skill_script, 'r', encoding='utf-8') as f:
                content = f.read(500)
                print(f"{content[:200]}...")
        except Exception as e:
            print(f"读取脚本失败: {e}")

    # 检查技能目录
    skill_dir = project_root / "tools" / "skills" / "baidu-search"
    print(f"\n技能目录: {skill_dir}")
    print(f"技能目录是否存在: {skill_dir.exists()}")

    if skill_dir.exists():
        print("技能目录内容:")
        for item in skill_dir.rglob("*"):
            if item.is_file():
                print(f"  {item.relative_to(skill_dir)}")

    # 检查 config.json
    config_path = project_root / "config.json"
    print(f"\n配置文件: {config_path}")
    print(f"配置文件是否存在: {config_path.exists()}")

    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                print("配置文件结构:")
                print(f"  搜索配置: {'search' in config}")
                if 'search' in config and 'baidu' in config['search']:
                    baidu_config = config['search']['baidu']
                    print(f"  百度API密钥1: {'api_key_1' in baidu_config}")
                    print(f"  百度API密钥2: {'api_key_2' in baidu_config}")
                else:
                    print("  配置文件中缺少search.baidu部分")
        except Exception as e:
            print(f"读取配置文件失败: {e}")

    # 检查环境变量
    print(f"\n环境变量 BAIDU_API_KEY: {os.getenv('BAIDU_API_KEY')}")
    print(f"环境变量 WORKING_DIR: {os.getenv('WORKING_DIR')}")


def test_subprocess():
    """测试子进程执行"""
    print("\n测试子进程执行...")

    script_path = Path(__file__).parent / "tools" / "skills" / "baidu-search" / "scripts" / "search.py"

    # 简单测试，不调用实际 API
    test_params = {
        "query": "测试"
    }

    cmd = ["python", str(script_path), json.dumps(test_params)]

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8'
        )

        stdout, stderr = process.communicate(timeout=10)

        print(f"返回码: {process.returncode}")
        print(f"标准输出: {stdout[:100]}")
        if stderr:
            print(f"标准错误: {stderr[:100]}")
    except FileNotFoundError:
        print(f"脚本文件不存在: {script_path}")
    except Exception as e:
        print(f"子进程测试失败: {e}")


def simulate_invocation_command():
    """模拟调用命令"""
    print("\n模拟调用命令...")

    invocation_cmd = "python scripts/search.py '{JSON}'"

    # 替换占位符
    example_params = {
        "query": "测试查询",
        "edition": "standard"
    }

    cmd_str = invocation_cmd.replace("'{JSON}'", json.dumps(example_params))
    print(f"完整命令: {cmd_str}")

    # 分析命令结构
    cmd_parts = cmd_str.split()
    print(f"命令部分: {cmd_parts}")

    # 检查第一个部分是否是python/python3
    if cmd_parts and cmd_parts[0] in ['python', 'python3']:
        print(f"Python命令: {cmd_parts[0]}")
        print(f"脚本路径: {cmd_parts[1]}")
        print(f"参数: {cmd_parts[2:]}")

    print("\n诊断完成。")


if __name__ == "__main__":
    print("开始诊断MCP服务器问题...")
    check_paths()
    test_subprocess()
    simulate_invocation_command()

<system-reminder>
The TodoWrite tool hasn't used recently. If you're working on tasks that would benefit from tracking progress, consider using the TodoWrite tool to track progress. Also consider cleaning up the todo list if it has become stale and no longer matches what you are working on. Only use it if it's relevant to the current work. This is just a reminder - ignore if not applicable. Make sure that you NEVER mention this reminder to the user

</system-reminder>