# qd-agents

从对话到自动化流程的智能体系统。

## 特性

- **多 LLM 提供商支持** - NVIDIA、讯飞星辰等
- **可配置模型列表** - 为每个提供商配置多个模型
- **自动模型发现** - NVIDIA 等提供商支持动态发现模型
- **模型评分选择** - 基于系列优先级和参数大小智能选择模型
- **Fallback 机制** - 模型失败时自动切换到下一个
- **单阶段调度** - 简化架构下的智能工具路由与执行
- **上下文管理** - 统一管理会话历史和提示词构建
- **Tool Registry** - SQLite 存储的工具注册中心
- **多种工具执行** - 支持 HTTP/CLI/Function/MCP 工具
- **MCP 服务器管理** - 通过命令行注册、列出和移除 MCP 服务器
- **重试与熔断** - 4 种退避策略 + 熔断器模式
- **CLI 界面** - 简洁的命令行交互
- **内置搜索工具** - 支持 Baidu、Tavily、Serper 搜索引擎
- **详细日志记录** - LLM 请求/响应完整日志，支持 DEBUG 级别
- **实时日志刷新** - ImmediateFlushFileHandler 确保日志实时写入磁盘
- **模式切换** - 支持 tool-use 和 code-plan 两种工作模式，可通过命令行或聊天命令切换

## 安装

### 前置要求

- Python 3.13+
- [uv](https://github.com/astral-sh/uv) 包管理器

### 安装步骤

```bash
# 克隆项目
git clone <repository-url>
cd qd-agents

# 安装依赖
uv sync
```

## 配置

### 快速开始

1. 复制配置模板：
```bash
cp config.json.template config.json
```

2. 编辑 `config.json`，填入你的 API Keys：
```json
{
  "llm": {
    "providers": {
      "nvidia": {
        "api_key": "your_nvidia_api_key_here"
      },
      "xunfei": {
        "api_key": "your_xunfei_api_key_here"
      }
    }
  },
  "search": {
    "serper": {
      "api_key": "your_serper_api_key_here"
    },
    "tavily": {
      "api_key": "your_tavily_api_key_here"
    },
    "baidu": {
      "api_key_1": "your_baidu_api_key_here",
      "api_key_2": "your_baidu_api_key_2_here"
    }
  }
}
```

### 配置说明

**config.json** - 唯一配置文件
- 所有配置都在此文件中，包括 API Keys
- 不提交到版本控制（已在 .gitignore 中）

**config.json.template** - 配置模板
- 提交到版本控制
- 包含所有配置项和默认值

### LLM 提供商配置

每个提供商可以配置多个模型：

```json
{
  "nvidia": {
    "api_key": "nvapi-xxx",
    "base_url": "https://integrate.api.nvidia.com/v1",
    "models": [],
    "auto_discover": true
  },
  "xunfei": {
    "api_key": "xxx",
    "base_url": "https://maas-coding-api.cn-huabei-1.xf-yun.com/v2",
    "models": ["astron-code-latest"],
    "auto_discover": false
  }
}
```

**NVIDIA**: `auto_discover: true` - 从 API 动态获取模型列表
**其他提供商**: `auto_discover: false` - 使用配置的模型列表

### 日志配置

```json
{
  "observability": {
    "log_level": "DEBUG",
    "log_format": "json",
    "log_output": ["file"],
    "log_session_dir": "."
  }
}
```

- `log_level`: 日志级别 (DEBUG/INFO/WARNING/ERROR) - 开发环境建议使用 `DEBUG`
- `log_format`: 日志格式 (console/json)
- `log_output`: 输出位置 (file/console) - 默认仅文件输出
- `log_session_dir`: 会话日志存放目录 - 默认当前目录 (`.`)

每次运行会生成独立的日志文件，命名格式：`YYYYMMDD_HHMMSS_shortid.log`

**DEBUG 级别日志包含：**
- LLM 请求的完整 messages
- 可用的 tools 列表
- 完整的 LLM 响应内容
- 工具调用详情

**VS Code 配置建议：**
在 `.vscode/settings.json` 中添加以下配置，让日志文件自动换行：
```json
{
  "[log]": {
    "editor.wordWrap": "on"
  }
}
```

### 获取 API Keys

- NVIDIA: https://build.nvidia.com/
- Baidu Search: https://console.bce.baidu.com/ai-search/qianfan/ais/console/apiKey

## 使用

### 查看帮助

```bash
uv run qd-agents --help
```

### 列出可用模型

```bash
# 列出所有提供商的模型
uv run qd-agents --list-models
```

### 列出已注册工具

```bash
uv run qd-agents tools list
```

输出格式为 `工具名(类型)`，其中类型包括：`function`、`cli`、`bash`、`mcp`、`skill`。

### 初始化内置工具

```bash
uv run qd-agents tools init
```

此命令会清空现有工具数据库并重新注册所有内置工具，包括：
- 搜索工具 (serper_search, tavily_search, baidu_search, web_search)
- 实用工具 (echo)
- Bash 工具 (execute_bash)

### 管理 MCP 服务器

MCP (Model Context Protocol) 服务器可以通过以下命令管理：

```bash
# 添加 MCP 服务器
uv run qd-agents mcp add <name> <server> [options]

# 列出已注册的 MCP 服务器
uv run qd-agents mcp list

# 移除 MCP 服务器
uv run qd-agents mcp remove <name>
```

**示例：注册 open-meteo-mcp 服务器**

```bash
# 构建 MCP 服务器（如果尚未构建）
cd tools/mcp/open-meteo-mcp
npm install
npm run build
cd ../..

# 注册到 qd-agents
uv run qd-agents mcp add open-meteo "Open Meteo Weather" \
  --transport stdio \
  --command "node" \
  --args "tools/mcp/open-meteo-mcp/dist/index.js"
```

### 启动交互式聊天

```bash
# 默认直接启动聊天
uv run qd-agents

# 指定工作模式启动
uv run qd-agents --mode enen
uv run qd-agents --mode code-plan
```

### 聊天命令

在聊天会话中可以使用以下命令：
- `/mode` - 显示/切换工作模式
- `/models` - 列出并切换可用模型
- `/tools` - 列出可用工具
- `/model` - 显示当前模型
- `/help` - 显示帮助信息
- `/quit` 或 `/q` - 退出程序
- `/clear` - 清空屏幕
- `/history` - 显示历史记录

### 查看版本

```bash
uv run qd-agents --version
```

## 项目结构

```
qd-agents/
├── src/qd_agents/
│   ├── config/          # 配置管理
│   ├── llm/             # LLM 客户端
│   ├── registry/        # Tool Registry
│   ├── prompts/         # 提示词模板
│   │   └── templates/
│   ├── context/         # 上下文管理器
│   ├── orchestrator/    # 调度器（单阶段架构）
│   ├── tools/           # 工具执行器
│   ├── execution/       # 执行引擎
│   ├── agent/           # Agent 核心
│   ├── utils/           # 工具函数
│   ├── models/          # 数据模型
│   └── cli/             # CLI 界面
├── data/                # 数据目录（自动创建）
│   ├── tools.db         # Tool Registry 数据库
│   ├── traces/          # 执行轨迹
│   └── audit/           # 审计日志
├── .env                 # 环境变量
├── pyproject.toml       # 项目配置
└── README.md
```

## 核心模块

### 配置管理 (config/)

- 多层配置支持（默认 → 全局 → 环境 → 实例）
- 环境变量插值
- Pydantic 类型验证

### LLM 客户端 (llm/)

- NVIDIA NIM API 集成（OpenAI 兼容）
- 自动模型发现（`/v1/models`）
- Top 5 模型评分选择
- 自动 Fallback 机制

**模型评分规则**：

| 评分项 | 说明 |
|--------|------|
| 基础分 | chat 模型 +1000 |
| 参数大小 | 70B+ → 100, 8x22B → 90, 32B → 80, 8x7B/13B → 70, 8B → 60, 7B → 50 |
| 系列优先级 | deepseek/glm → 42, qwen → 40, minimax → 38, llama/mistral/gemma → 35 |
| 后缀加分 | instruct/chat 后缀 +20 |

### 上下文管理器 (context/)

- 统一管理会话历史
- 分阶段消息构建（system_prompt + 历史 + 当前用户输入）
- 支持单阶段工具调用模式，简化架构提高性能

### Tool Registry (registry/)

- SQLite 存储
- 工具注册/检索/更新/删除
- 版本管理框架
- 关键词搜索

### 工具执行器 (tools/)

支持 6 种工具执行类型：

| 类型 | 说明 |
|------|------|
| `function` | Python 函数调用 |
| `cli` | 命令行程序调用 |
| `http` | HTTP 服务调用 |
| `skill` | 预置技能/工作流 |
| `mcp` | Model Context Protocol |
| `bash` | Bash 命令执行 |

### 重试与熔断 (utils/retry.py)

**退避策略**：

- `fixed` - 固定延迟
- `linear` - 线性递增
- `exponential` - 指数退避
- `exponential_with_jitter` - 指数退避 + 随机抖动

**熔断器状态**：

```
CLOSED → OPEN (失败率超过阈值)
OPEN → HALF_OPEN (冷却时间后)
HALF_OPEN → CLOSED (成功)
HALF_OPEN → OPEN (失败)
```

## 开发

### 安装开发依赖

```bash
uv sync --dev
```

### 类型检查

```bash
uv run mypy src/qd_agents
```

### 测试

```bash
uv run pytest
```

## 需求文档

详细设计文档请参考 [REQUIREMENTS.md](REQUIREMENTS.md)。

## 许可证

本项目采用 MIT 许可证。
