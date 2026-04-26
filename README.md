# qd-agents

从对话到自动化流程的智能体系统。

## 特性

- **Evolve 自主进化 Agent** - 自主思考、决策、行动，发现缺失工具时自动安装使用，成功后注册到工具箱
- **SKILL 渐进式披露** - Evolve Agent 按需加载 SKILL.md，不预加载全部技能指南
- **步骤回调** - Evolve Agent 执行过程实时输出到终端，用户可观察中间步骤
- **上下文压缩** - 长程迭代时自动压缩旧工具结果，保留摘要+文件指针
- **多 LLM 提供商支持** - NVIDIA、讯飞星辰等
- **自动模型发现** - NVIDIA 等提供商支持动态发现模型
- **模型评分选择** - 基于系列优先级和参数大小智能选择模型
- **Fallback 机制** - 模型失败时自动切换到下一个
- **上下文管理** - 统一管理会话历史和提示词构建
- **Tool Registry** - SQLite 存储的工具注册中心
- **多种工具执行** - 支持 HTTP/CLI/Function/MCP/Bash/Skill 6种工具类型
- **MCP 服务器管理** - 通过命令行注册 MCP 服务器，自动发现和展开子工具
- **重试与熔断** - 4 种退避策略 + 熔断器模式
- **CLI 界面** - 简洁的命令行交互
- **内置搜索工具** - 支持 Tavily、Serper 搜索引擎
- **AddSkill 分析器** - 用 LLM 分析 SKILL.md，自动识别参数和工具依赖
- **运行时配置分离** - 静态配置(config.json)与运行时配置(runtime.json)分离存储
- **详细日志记录** - LLM 请求/响应完整日志，支持 DEBUG 级别
- **实时日志刷新** - ImmediateFlushFileHandler 确保日志实时写入磁盘
- **工具箱管理 CLI** - `tools add/skill add/mcp add/list/remove` 命令管理工具注册

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
    }
  }
}
```

### 配置说明

**config.json** - 静态系统配置
- LLM 提供商、模型、搜索 API Keys 等不变配置
- 不提交到版本控制（已在 .gitignore 中）

**config.json.template** - 配置模板
- 提交到版本控制
- 包含所有配置项和默认值

**runtime.json** - 运行时配置
- 工具凭证（CLI/MCP/Skill 等工具的 API key）
- 由 `qd-agents tools skill add` 等命令自动写入
- 首次运行时自动从 config.json 迁移 tools_credentials

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
    "log_output": ["file"],
    "log_session_dir": "."
  }
}
```

- `log_level`: 日志级别 (DEBUG/INFO/WARNING/ERROR) - 开发环境建议使用 `DEBUG`
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

## 使用

### 全局选项

```bash
uv run qd-agents --help            # 查看帮助
uv run qd-agents --version         # 查看版本
uv run qd-agents --list-models     # 列出所有提供商的可用模型
uv run qd-agents -c config.json    # 指定配置文件路径
uv run qd-agents -d /path/to/dir  # 指定基础目录
```

### 工具管理

```bash
# 列出已注册工具（表格形式，显示名称、类型、描述、分类、ID）
uv run qd-agents tools list

# 初始化内置工具（清空数据库并重新注册）
uv run qd-agents tools init

# 注册 CLI/Bash 工具到工具箱
uv run qd-agents tools add <name> --command "<命令模板>"
# 例：注册 yt-dlp
uv run qd-agents tools add yt-dlp --command "uvx yt-dlp {args}" --description "Download videos from YouTube and other sites"

# 移除已注册工具
uv run qd-agents tools remove <tool_name_or_id>

# 移除工具但保留凭证
uv run qd-agents tools remove <tool_name_or_id> --keep-credentials
```

**内置工具**（`tools init` 注册）：
- 搜索工具：serper_search、tavily_search
- 实用工具：echo
- Bash 工具：execute_bash

### 管理 MCP 服务器

```bash
# 添加 MCP 服务器
uv run qd-agents tools mcp add <name> <server> [options]
```

**参数说明**：

| 参数 | 说明 |
|------|------|
| `name` | 工具名称，在聊天中使用的名称 |
| `server` | MCP 服务器标识，描述性名称 |
| `-t/--transport` | 传输模式：stdio（默认）/ sse / streamable-http |
| `--command/--cmd` | stdio 模式下启动服务器的命令（如 "node"、"npx"） |
| `-a/--args` | stdio 模式下的命令参数（JSON 数组或逗号分隔） |
| `-u/--url` | SSE / streamable-http 模式的 URL |
| `-j/--json` | 从 JSON 文件读取配置（tools/mcp/\<name\>.json） |
| `-c/--config` | 指定配置文件路径 |
| `-d/--base-dir` | 指定基础目录 |

**示例：注册 open-meteo-mcp 服务器**

```bash
# 构建 MCP 服务器（如果尚未构建）
cd tools/mcp/open-meteo-mcp
npm install
npm run build
cd ../..

# 注册到 qd-agents
uv run qd-agents tools mcp add open-meteo "Open Meteo Weather" \
  --transport stdio \
  --command "node" \
  --args "tools/mcp/open-meteo-mcp/dist/index.js"
```

**注意**：MCP 工具执行器具有自动发现功能，在上下文管理中会自动连接到 MCP 服务器并展开所有可用工具，将 MCP 服务器提供的每个工具作为独立工具加载到可用工具列表中，无需手动注册每个工具。

### 管理 Skill 工具

```bash
# 添加 Skill 工具（用 LLM 分析 SKILL.md，自动识别参数和工具依赖）
uv run qd-agents tools skill add <skill_name>
```

**参数说明**：
- `skill_name`：Skill 目录名（tools/skills/ 下的文件夹名）

**Skill 目录结构**：
```
tools/skills/<skill_name>/
├── SKILL.md          # Skill 元数据（YAML frontmatter）
└── scripts/          # 脚本目录
    └── *.py          # Python 脚本
```

**SKILL.md 格式**：
```yaml
---
name: skill-name
description: Skill 描述
metadata:
  openclaw:
    requires:
      env: [ENV_VAR1, ENV_VAR2]  # 所需环境变量
      bins: [command1, command2]  # 所需命令
---
```

### 启动交互式聊天

```bash
# 默认启动（使用 Evolve Agent）
uv run qd-agents
```

### 聊天命令

在聊天会话中可以使用以下命令：
- `/help` - 显示帮助信息
- `/model` - 显示当前模型
- `/models` - 列出并切换可用模型
- `/tools` - 列出可用工具
- `/quit` - 退出程序

## 项目结构

```
qd-agents/
├── src/qd_agents/
│   ├── agent/           # Agent 体系
│   │   ├── base.py       # Agent 基类 + 数据模型
│   │   ├── core.py       # QDAgent 容器（资源管理）
│   │   ├── evolve.py     # EvolveAgent（自主循环核心）
│   │   ├── tool_execution.py  # 工具执行辅助函数
│   │   └── add_skill.py # AddSkillAnalyzer（SKILL.md 分析）
│   ├── services/         # 服务类
│   │   ├── mcp_service.py  # MCP 服务管理（连接/展开/关闭）
│   │   └── tool_service.py # 工具服务（注册/缓存构建）
│   ├── cli/              # CLI 界面
│   │   ├── app.py        # Typer 应用配置
│   │   ├── main.py       # 入口 + prompt 样式
│   │   ├── commands/     # chat / tools / mcp / skills / models / version
│   │   ├── managers/     # configuration / llm_client
│   │   └── utils/        # formatting / credentials
│   ├── config/           # 配置管理
│   │   ├── models.py     # Pydantic 配置模型
│   │   └── loader.py    # JSON 加载/保存逻辑
│   ├── context/          # 上下文管理器
│   │   ├── manager.py   # ContextManager
│   │   └── compressor.py # ContextCompressor（工具结果压缩）
│   ├── llm/              # LLM 客户端 + 评分 + 格式化
│   │   ├── client.py     # LLMClient（多模型 Fallback）
│   │   ├── scoring.py    # 模型评分与选择
│   │   └── formatters.py # 消息格式化与日志
│   ├── models/           # Pydantic 数据模型
│   │   ├── tool.py       # Tool / ToolExecutionConfig / ToolMetadata
│   │   ├── evolve.py     # EvolveResult / AskUserInfo / DelegateInfo
│   │   ├── execution.py  # ExecutionResult / ExecutionStep / ExecutionStatus
│   │   └── add_skill.py  # AddSkillResult
│   ├── prompts/          # Jinja2 模板加载
│   │   └── templates/    # evolve.j2 / add_skill.j2
│   ├── registry/         # 工具注册中心（SQLite）
│   │   └── registry.py   # ToolRegistry
│   ├── tools/            # 工具执行器 + 内置工具
│   │   ├── builtins.py   # echo 等基础工具
│   │   ├── search.py     # Serper、Tavily 搜索工具
│   │   └── executors/    # base / function / http / cli / bash / mcp / factories
│   └── utils/            # retry / circuit_breaker / logging / parsing
├── tools/                # 工具资源目录
│   ├── mcp/              # MCP 服务器配置和子项目
│   └── skills/           # SKILL 工具目录（由 qd-agents 管理）
│       └── <skill>/
│           ├── SKILL.md
│           └── scripts/
├── data/                  # 数据目录（自动创建）
│   └── tools.db          # Tool Registry 数据库
├── config.json            # 静态系统配置
├── config.json.template   # 配置模板
├── runtime.json           # 运行时配置（工具凭证）
├── pyproject.toml         # 项目配置
└── README.md
```

## 核心模块

### Agent 体系

系统采用单层 Agent 架构：

**EvolveAgent**：唯一的 Agent，直接持有完整对话上下文，通过 function calling 调用工具：
```
用户输入 → EvolveAgent（自主循环）
             ├─ LLM 返回 tool_calls → 执行工具 → 观察结果 → 继续循环
             ├─ LLM 返回 SKILL 工具 → 渐进式披露：注入 SKILL.md → LLM 按 Usage 执行
             ├─ LLM 不返回 tool_calls → 输出最终答案
             └─ 达到 max_iterations → 终止
```

**AddSkillAnalyzer**：调用大模型的工具类（不是 Agent），用于分析 SKILL.md 内容，识别技能的参数定义和工具依赖。

**QDAgent**：资源管理容器，管理工具注册、MCP 连接、上下文压缩等资源，将用户输入委托给 EvolveAgent 执行。

### 配置管理 (config/)

- `config/models.py` — Pydantic 配置模型定义
- `config/loader.py` — JSON 文件加载/保存逻辑
- 环境变量插值（`QD_` 前缀 + `__` 嵌套分隔符）
- 运行时配置自动迁移

### LLM 客户端 (llm/)

- OpenAI 兼容 API（AsyncOpenAI）
- 自动模型发现（`/v1/models`）
- Top K 模型评分选择
- 自动 Fallback 机制
- 消息格式化与日志（`llm/formatters.py`）

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
- SKILL.md 自动注入（SKILL 类型工具在提示词中注入 SKILL.md 正文）
- 提示词缓存（按工具集合缓存系统提示词）
- 上下文压缩（长程迭代时压缩旧工具结果）

### 数据模型 (models/)

共享 Pydantic 数据模型，供多个模块引用：
- `Tool` / `ToolExecutionConfig` / `ToolMetadata` — 工具定义和执行配置
- `EvolveResult` / `AskUserInfo` / `DelegateInfo` — Evolve 特殊输出结果
- `ExecutionStatus` / `ExecutionStep` / `ExecutionResult` — 执行轨迹模型
- `AddSkillResult` — 技能分析结果

### 服务层 (services/)

- `MCPService` — MCP 服务器连接管理、工具展开、资源清理
- `ToolService` — 工具缓存构建、内置工具注册

### Tool Registry (registry/)

- SQLite 存储（WAL 模式）
- 工具注册/检索/更新/删除
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

**内置工具**：
- `tools/builtins.py` — echo 等基础工具
- `tools/search.py` — Serper、Tavily 搜索工具

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

### LLM 输出解析 (utils/parsing.py)

- `extract_json_from_llm_output()` — 从 LLM 输出提取 JSON（支持 markdown 代码块和裸 JSON）
- `parse_json_from_llm_output()` — 提取并解析 JSON 字典

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

## 许可证

本项目采用 MIT 许可证。