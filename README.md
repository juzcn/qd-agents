# qd-agents

意图驱动的上下文隔离多Agent系统

## 功能特性

- **三模块架构**：会话Agent → 工具Agent → 执行层，关注点完全分离
- **上下文隔离**：工具链路完全不接触自然语言历史，避免上下文污染
- **NVIDIA 模型池**：动态获取 Top 5 免费模型，自动 fallback 机制
- **调试模式**：`--debug` 选项显示完整中间步骤输出
- **模型切换**：交互式命令 `/models` 和 `/model <序号>` 手动选择模型

## 架构概述

本项目实现了一个三模块架构的多Agent系统：

```
用户输入
    ↓
┌─────────────────────────────────────────┐
│          会话Agent (有状态)             │
│  输入：会话历史 + 长期记忆 + 人格/规则  │
│  输出：自然语言回复 + 意图对象(结构化)  │
└─────────────────────────────────────────┘
    │                                ↑
    │ 意图对象                   │ 执行结果(作为新记忆)
    ↓                                │
┌─────────────────────────────────────────┐
│          工具Agent (无状态)             │
│  输入：意图对象 + 工具列表(Schema)      │
│  输出：工具调用(tool_calls)             │
└─────────────────────────────────────────┘
    │
    │ 工具名+参数
    ↓
┌─────────────────────────────────────────┐
│        执行层 (非Agent)                  │
│  输入：工具调用指令                      │
│  输出：执行结果 / 需要用户交互的信号     │
└─────────────────────────────────────────┘
```

## 核心原则

| 原则 | 说明 |
|------|------|
| **上下文最小化** | 只有会话Agent可访问会话历史和长期记忆 |
| **意图作为唯一桥梁** | 会话Agent对外输出"完整意图" |
| **无状态工具链** | 工具Agent和执行层不保留任何跨请求状态 |
| **自然语言输出** | 会话Agent输出自然语言回复 + 结构化意图 |

## 配置

### NVIDIA API

在 `.env` 文件中配置：

```env
NVAPI_KEY=your-nvidia-api-key
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
```

获取 API Key: https://build.nvidia.com/

## 项目结构

```
qd-agents/
├── src/qd_agents/
│   ├── __init__.py
│   ├── agents/              # Agent模块
│   │   ├── __init__.py
│   │   ├── session_agent.py # 会话Agent (有状态)
│   │   └── tool_agent.py    # 工具Agent (无状态)
│   ├── execution/           # 执行层
│   │   ├── __init__.py
│   │   └── executor.py      # 工具执行器
│   ├── intent/              # 意图对象
│   │   ├── __init__.py
│   │   ├── schema.py        # Intent Schema定义 (Pydantic)
│   │   └── builder.py       # 流式API意图构建器
│   ├── memory/              # 记忆模块
│   │   ├── __init__.py
│   │   ├── short_term.py    # 短期记忆 (会话历史)
│   │   └── long_term.py     # 长期记忆 (用户档案)
│   ├── models/              # 模型池
│   │   ├── __init__.py
│   │   └── nvidia_pool.py   # NVIDIA模型池 + Fallback机制
│   ├── utils/               # 工具函数
│   │   ├── __init__.py
│   │   └── debug.py         # 调试输出工具
│   ├── orchestrator.py      # 编排器 (协调整个流程)
│   └── cli.py               # 命令行入口 (Typer)
├── pyproject.toml
├── .env                     # 环境变量 (不提交到git)
└── README.md
```

## 安装

使用 `uv` 安装：

```bash
uv sync
```

## 使用

### 命令行

```bash
# 查看帮助
uv run qd-agents --help

# 列出可用模型
uv run qd-agents models

# 交互式聊天
uv run qd-agents chat

# 聊天 + 调试模式（显示中间步骤）
uv run qd-agents chat --debug

# 单次消息模式
uv run qd-agents chat "你好"
```

### 交互式命令

在聊天模式下可用：

| 命令 | 说明 |
|------|------|
| `/models` 或 `/model list` | 显示所有可用模型 |
| `/model <序号>` | 切换到指定模型 (如 `/model 1`) |
| `/debug` | 切换调试模式 |
| `/quit` 或 `/exit` | 退出 |

## 开发

```bash
# 安装依赖
uv sync --dev

# 运行类型检查
uv run mypy src/qd_agents

# 运行测试
uv run pytest
```

## 模型池优先级

程序启动时自动从 NVIDIA API 获取模型，并按以下优先级排序（Top 5）：

1. Llama 3.1 (70B → 8B)
2. Llama 3 (70B → 8B)
3. Gemma 2
4. Mixtral / Mistral
5. Qwen

## 许可证

MIT
