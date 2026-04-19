"""
baidu-search Skill Wrapper

This script wraps the baidu-search skill for use with the MCP server.
"""

import json
import sys
import os
import subprocess
from pathlib import Path

def execute_skill(params: dict) -> dict:
    """
    执行技能

    Args:
        params: 技能参数

    Returns:
        执行结果
    """
    # 这里应该调用原始技能脚本
    # 由于技能实现可能不同，这里提供一个通用包装器

    # 获取技能目录
    skill_dir = Path(__file__).parent.parent / "tools/skills/baidu-search"

    # 查找主要的技能脚本
    script_path = None
    for pattern in ['*.py', '*.sh', '*.js']:
        scripts = list(skill_dir.rglob(pattern))
        if scripts:
            script_path = scripts[0]
            break

    if not script_path:
        raise FileNotFoundError(f"No skill script found in {skill_dir}")

    # 执行脚本
    cmd = []
    if script_path.suffix == '.py':
        cmd = ['python', str(script_path)]
    elif script_path.suffix == '.sh':
        cmd = ['bash', str(script_path)]
    elif script_path.suffix == '.js':
        cmd = ['node', str(script_path)]

    # 添加参数（作为 JSON 字符串传递）
    input_json = json.dumps(params)

    try:
        result = subprocess.run(
            cmd + [input_json],
            capture_output=True,
            text=True,
            cwd=skill_dir,
            timeout=30
        )

        if result.returncode != 0:
            raise RuntimeError(f"Skill execution failed: {result.stderr}")

        # 尝试解析 JSON 输出
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"output": result.stdout.strip()}

    except subprocess.TimeoutExpired:
        raise TimeoutError("Skill execution timed out after 30 seconds")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No parameters provided"}))
        sys.exit(1)

    try:
        params = json.loads(sys.argv[1])
        result = execute_skill(params)
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
