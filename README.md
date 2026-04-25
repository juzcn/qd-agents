# qd-agents

从对话到自动化流程的智能体系统。

## 特性

- **Agent/元Agent 架构** - 双层架构，元Agent是原子LLM调用单元，Agent是任务处理单元
- **三阶段智能路由** - Code-Plan模式采用Judge→ToolCalling/Coding三阶段路由
- **多 LLM 提供商支持** - NVIDIA、讯飞星辰等
- **可配置模型列表** - 为每个提供商配置多个模型
- **自动模型发现** - NVIDIA 等提供商支持动态发现模型
- **模型评分选择** - 基于系列优先级和参数大小智能选择模型
- **Fallback 机制** - 模型失败时自动切换到下一个
- **上下文管理** - 统一管理会话历史和提示词构建
- **Tool Registry** - SQLite 存储的工具注册中心
- **多种工具执行** - 支持 HTTP/CLI/Function/MCP/Bash/Skill 6种工具类型
- **MCP 服务器管理** - 通过命令行注册 MCP 服务器，自动发现和展开子工具
- **异步代码执行** - 支持顶层 await 语法，自动包装为异步函数执行
- **重试与熔断** - 4 种退避策略 + 熔断器模式
- **CLI 界面** - 简洁的命令行交互
- **内置搜索工具** - 支持 Tavily、Serper 搜索引擎
- **Add-Skill 元Agent** - 用 LLM 分析 SKILL.md，自动识别参数和工具依赖
- **运行时配置分离** - 静态配置(config.json)与运行时配置(runtime.json)分离存储
- **详细日志记录** - LLM 请求/响应完整日志，支持 DEBUG 级别
- **实时日志刷新** - ImmediateFlushFileHandler 确保日志实时写入磁盘
- **Agent 切换** - 支持 tool-use 和 code-plan 两种 Agent，可通过命令行或聊天命令切换

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
uv run qd-agents -a code-plan      # 指定启动 Agent（tool-use / code-plan）
uv run qd-agents -c config.json    # 指定配置文件路径
uv run qd-agents -d /path/to/dir  # 指定基础目录
```

### 工具管理

```bash
# 列出已注册工具（表格形式，显示名称、类型、描述、分类、ID）
uv run qd-agents tools list

# 初始化内置工具（清空数据库并重新注册）
uv run qd-agents tools init

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
# 默认启动（使用 tool-use Agent）
uv run qd-agents

# 指定 Agent 启动
uv run qd-agents -a tool-use
uv run qd-agents -a code-plan
```

### 聊天命令

在聊天会话中可以使用以下命令：
- `/help` - 显示帮助信息
- `/model` - 显示当前模型
- `/models` - 列出并切换可用模型
- `/tools` - 列出可用工具
- `/agent` - 显示/切换 Agent
- `/quit` 或 `/q` - 退出程序

## 项目结构

```
qd-agents/
├── src/qd_agents/
│   ├── config/          # 配置管理（JSON + 运行时配置分离）
│   ├── llm/             # LLM 客户端 + 消息格式化 + 模型评分
│   ├── models/          # 共享数据模型
│   │   ├── tool.py      # Tool/ToolExecutionConfig/ToolMetadata
│   │   ├── judge.py     # JudgeResult
│   │   ├── execution.py # ExecutionResult/ExecutionStep
│   │   └── add_skill.py # AddSkillResult
│   ├── registry/        # Tool Registry（注册/查询/搜索）
│   ├── prompts/         # 提示词模板
│   │   └── templates/
│   │       ├── tool_use.j2   # 工具调用系统提示词
│   │       ├── judge.j2      # 路由判断系统提示词
│   │       ├── coding.j2     # 代码生成系统提示词
│   │       └── add_skill.j2  # 技能分析系统提示词
│   ├── context/         # 上下文管理器
│   ├── tools/           # 工具执行器 + 内置工具 + MCP 管理器
│   ├── execution/       # 执行引擎
│   ├── agent/           # Agent 核心
│   │   ├── base.py      # MetaAgent/Agent 基类和数据模型
│   │   ├── core.py      # QDAgent 容器（Agent注册/切换/委托）
│   │   ├── mcp_service.py  # MCP 服务管理（连接/展开/关闭）
│   │   ├── tool_service.py # 工具服务（注册/缓存/执行器）
│   │   ├── judge_meta.py   # JudgeMetaAgent
│   │   ├── tool_calling_meta.py  # ToolCallingMetaAgent
│   │   ├── coding_meta.py  # CodingMetaAgent
│   │   ├── add_skill_meta.py # AddSkillMetaAgent
│   │   ├── tool_use.py   # ToolUseAgent
│   │   └── code_plan.py  # CodePlanAgent
│   ├── utils/           # 工具函数
│   │   ├── retry.py     # 重试与熔断
│   │   ├── logging.py   # 日志配置
│   │   └── parsing.py   # LLM 输出 JSON 解析
│   └── cli/             # CLI 界面
├── tools/               # 工具资源目录
│   ├── mcp/             # MCP 服务器配置和子项目
│   └── skills/          # SKILL 工具目录（由 qd-agents 管理）
│       └── <skill>/
│           ├── SKILL.md
│           └── scripts/
├── skills/              # Claude Code skills（非 qd-agents SKILL 工具）
├── data/                # 数据目录（自动创建）
│   └── tools.db         # Tool Registry 数据库
├── config.json          # 静态系统配置
├── config.json.template # 配置模板
├── runtime.json         # 运行时配置（工具凭证）
├── pyproject.toml       # 项目配置
└── README.md
```

## 核心模块

### Agent 体系架构

系统采用双层 Agent 架构：

**元Agent（MetaAgent）**：原子 LLM 调用单元
- 一个系统提示词 + 一种上下文构建 + 一种处理逻辑
- 类型：单轮（Judge、Coding、AddSkill）或多轮 Tool Calling（ToolCalling）

**Agent**：任务处理单元
- 简单 Agent：包装单个元Agent（ToolUseAgent）
- 编排型 Agent：协调多个元Agent（CodePlanAgent）

**当前实现的元Agent**：
| 元Agent | 类型 | 功能 | 提示词模板 |
|---------|------|------|------------|
| JudgeMetaAgent | 单轮 | 路由判断（direct/tool_use/coding） | judge.j2 |
| ToolCallingMetaAgent | 多轮 | 工具调用循环 | tool_use.j2 |
| CodingMetaAgent | 单轮 | 复杂工具编排（代码生成+执行） | coding.j2 |
| AddSkillMetaAgent | 单轮 | 分析 SKILL.md，识别参数和依赖 | add_skill.j2 |

### Code-Plan 模式

三阶段智能路由：
```
用户输入 → JudgeMetaAgent（路由判断）
           ├─ direct → 直接回答
           ├─ tool_use → ToolCallingMetaAgent
           └─ coding → CodingMetaAgent（代码生成+沙盒执行）
```

### 配置管理 (config/)

- JSON 配置文件（config.json + runtime.json 分离）
- 环境变量插值
- Pydantic 类型验证
- 运行时配置自动迁移

### LLM 客户端 (llm/)

- NVIDIA NIM API 集成（OpenAI 兼容）
- 自动模型发现（`/v1/models`）
- Top 5 模型评分选择
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
- 支持三阶段路由模式的上下文构建

### 数据模型 (models/)

共享 Pydantic 数据模型，供多个模块引用：
- `Tool` / `ToolExecutionConfig` / `ToolMetadata` — 工具定义和执行配置
- `JudgeResult` — 路由判断结果（route, reasoning, tool_list）
- `ExecutionStatus` / `ExecutionStep` / `ExecutionResult` — 执行轨迹模型
- `AddSkillResult` — 技能分析结果

### 执行引擎 (execution/)

- Python 代码沙盒执行
- 异步代码支持（顶层 await）
- 工具函数注入（extra_globals）
- 安全限制：禁止危险模块和函数

### Tool Registry (registry/)

- SQLite 存储
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
- `tools/builtin_search.py` — Serper、Tavily 搜索工具

**MCP 管理**：
- `tools/mcp_manager.py` — MCP 服务器连接、工具发现和注册
- MCP 工具自动展开：连接 MCP 服务器后，将每个子工具作为独立工具加载到可用工具列表

**Skill 管理**：
- `tools/skills/` — Skill 工具目录
- 通过 `qd-agents tools skill add` 命令注册 Skill
- Skill 支持环境变量和命令依赖声明
- AddSkillMetaAgent 自动分析 SKILL.md 识别参数和工具依赖

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

## 需求文档

详细设计文档请参考 [REQUIREMENTS.md](REQUIREMENTS.md)。

## 许可证

本项目采用 MIT 许可证。
