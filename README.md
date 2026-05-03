# QD-Agents

自主进化的 AI Agent 框架。Agent 在运行时发现缺少工具时，能主动安装、注册、使用新工具，实现能力持续扩展。

## 核心设计理念

- **路由决策权完全交给模型**：框架不硬编码路由逻辑，通过唯一的 `delegate` 工具让模型自主决定何时、如何调用子 Agent
- **One loop & Bash is all you need**：所有 Agent 共享 MetaAgent 标准工具调用循环，bash 是万能执行器
- **Adding a tool means adding new capacity**：工具即能力，发现缺少工具时主动搜索、安装、注册
- **An agent without a plan drifts**：系统提示词注入原则性指令，引导模型规划而非漂移
- **Context will fill up; you need a way to make room**：自动总结压缩上下文，永久记忆基于向量检索

## 特性

- **自主进化**：Agent 发现需要新工具时，自动搜索、安装并注册到工具箱
- **7 种工具类型**：Bash、CLI、Skill、HTTP/OpenAPI、MCP、Function、Delegate
- **工具自主管理**：LLM 直接调用 `tool_register_*` 函数注册新工具，无需 CLI 命令
- **Skill 渐进式披露**：首次调用返回用法指南，Agent 了解后再执行；支持系统提示词注入模式
- **长期记忆**：向量 + 关键词混合检索，跨会话保留经验
- **多模型 Fallback**：主模型失败自动切换备用模型
- **上下文自动管理**：超过阈值自动触发 context_summarizer 压缩历史

## 架构概览

```
                    ┌─────────────────────────────────┐
                    │          QDAgent（编排器）        │
                    │  初始化组件 / 管理会话 / 缓存工具  │
                    └────────────┬────────────────────┘
                                 │
                    ┌────────────▼────────────────────┐
                    │       EvolveAgent（主循环）       │
                    │  system prompt + delegate 工具    │
                    │  路由决策完全由模型自主完成         │
                    └────────────┬────────────────────┘
                                 │ delegate
              ┌──────────────────┼──────────────────┐
              │                  │                  │
   ┌──────────▼──────┐ ┌────────▼────────┐ ┌──────▼──────────┐
   │ Use-Tool Agent  │ │ Find-Tools Agent│ │ Coding Agent    │
   │ 工具执行子循环   │ │ 工具发现子循环   │ │ （尚未实现）    │
   └─────────────────┘ └─────────────────┘ └─────────────────┘
```

所有 Agent 都是 **MetaAgent** 的子类，共享相同的 OpenAI 标准工具调用循环：

1. 调用 LLM API，传入 messages + tools，`tool_choice="auto"`
2. 若响应包含 `tool_calls` → 执行工具，追加结果，继续循环
3. 若响应仅为文本 → 终止循环，返回最终答案
4. `ask_user` 工具暂停循环等待用户回复
5. `delegate` 工具由 EvolveAgent 拦截，路由到子 Agent

## 快速开始

### 安装

```bash
git clone <repo-url> && cd qd-agents
uv sync
```

### 配置

```bash
cp config.json.template config.json
# 编辑 config.json，填入 LLM API key 和提供商配置
```

### 运行

```bash
# 交互式对话（直接运行 qd-agents 即可启动）
uv run qd-agents

# 指定模型和提供商
uv run qd-agents -p openai -m gpt-4o

# 初始化工具箱（注册默认工具）
uv run qd-agents tools init

# 查看可用模型
uv run qd-agents --list-models
```

## CLI 命令

### 主命令

| 命令 | 说明 |
|------|------|
| `qd-agents` | 启动交互式对话 |
| `qd-agents -p <provider>` | 指定 LLM 提供商 |
| `qd-agents -m <model>` | 指定模型名称 |
| `qd-agents --list-models` | 列出可用模型 |
| `qd-agents -v` / `--version` | 显示版本信息 |

### 工具管理

| 命令 | 说明 |
|------|------|
| `qd-agents tools init` | 初始化/重注册工具箱（`--keep` 保留用户工具） |
| `qd-agents tools list` | 列出已注册工具（`--mcp`/`--skill`/`--cli`/`--function`/`--bash` 筛选） |
| `qd-agents tools remove <name>` | 移除工具（`--keep-credentials` 保留凭证） |
| `qd-agents tools update` | 更新默认 MCP 工具到最新版本 |
| `qd-agents tools update-check` | 检查默认 MCP 工具版本更新 |
| `qd-agents tools skill add <dir>` | 注册 Skill 工具 |
| `qd-agents tools mcp add <server>` | 注册 MCP 服务器工具 |
| `qd-agents tools cli add <name> --command <cmd>` | 注册 CLI 工具 |
| `qd-agents tools http add <name> --openapi-url <url>` | 注册 HTTP/OpenAPI 工具 |

### 记忆管理

| 命令 | 说明 |
|------|------|
| `qd-agents memory list` | 显示所有永久记忆 |
| `qd-agents memory recall <query>` | 语义召回永久记忆 |

## 工具类型

| 类型 | 说明 | 示例 |
|------|------|------|
| **Bash** | Shell 命令执行 | `execute_bash` |
| **CLI** | 命令行工具封装（自动拼装 command + prefix_args + 用户参数） | `memory-list` |
| **Skill** | 技能包（SKILL.md + 脚本），支持两种注入模式 | `baidu-search` |
| **HTTP** | REST API / OpenAPI 自动发现 | 天气 API |
| **MCP** | Model Context Protocol 服务器（stdio/sse/streamable-http） | `filesystem`, `open-meteo` |
| **Function** | Python 函数直接调用 | `tool_register_*`, `fetch` |
| **Delegate** | 调用子 Agent 的特殊工具（仅 EvolveAgent 拥有） | `delegate` |

### Skill 注入模式

- **tool_manual**（默认）：SKILL.md 内容注入到工具 description，模型调用前阅读文档理解用法
- **prompt**：SKILL.md 内容追加到系统提示词，持续生效直至会话结束，用于改变 Agent 行为模式

## 工具 Scope

| Scope | 说明 | 可删除 | 可更新 |
|-------|------|--------|--------|
| **builtin** | 核心工具（execute_bash, tool_register_*） | 否 | 否 |
| **default** | 预装工具（local-search, memory-list 等） | 否 | 是 |
| **user** | 用户/Agent 添加的工具 | 是 | 是 |

## 内置工具箱

| 工具名 | Scope | 类型 | 说明 |
|--------|-------|------|------|
| `delegate` | builtin | delegate | 调用子 Agent（Use-Tool / Find-Tools / Coding） |
| `execute_bash` | builtin | bash | 执行 bash/shell 命令，带超时和输出截断 |
| `fetch` | builtin | function | HTTP 请求，获取网页内容或调用 API |
| `ask_user` | builtin | function | 向用户提问并等待回复，支持选项列表 |
| `context_summarizer` | builtin | function | 主动总结对话历史，压缩上下文 |
| `tools_list` | builtin | function | 列出当前所有可用工具 |
| `tool_register_http` | builtin | function | 注册 HTTP/OpenAPI 工具 |
| `tool_register_mcp` | builtin | function | 注册 MCP 服务器工具 |
| `tool_register_skill` | builtin | function | 注册 Skill 工具 |
| `tool_register_cli` | builtin | function | 注册 CLI 工具 |
| `tool_register_code` | builtin | function | 注册代码工具（预留接口） |
| `local-search` | default | bash | 使用 ripgrep/grep 搜索本地文件 |
| `memory-list` | default | cli | 显示所有永久记忆 |

## 工具注册架构

三种注册入口共用同一套纯逻辑层（`tools/registrars/`），避免重复代码：

```
CLI 命令 (cli add / mcp add / ...)  ─┐
LLM function call (tool_register_*) ──┤──→ registrars/*.py → ToolRegistry
tools init (重注册)                 ──┘
```

## 项目结构

```
qd_agents/
├── agent/              # Agent 核心
│   ├── base.py         # MetaAgent 基类 + AgentResult 数据模型
│   ├── chat.py         # EvolveAgent（主循环，delegate 路由）
│   ├── core.py         # QDAgent（编排器，组件初始化和生命周期）
│   ├── use_tool.py     # Use-Tool Agent（工具执行子循环）
│   ├── find_tools.py   # Find-Tools Agent（工具发现子循环）
│   ├── add_skill.py    # analyze_skill() 函数（LLM 分析 SKILL.md）
│   └── tool_execution.py # 工具执行辅助（ensure_bash_available 等）
├── cli/                # CLI 命令
│   ├── app.py          # Typer 主应用
│   ├── commands/       # 各子命令实现
│   │   ├── chat.py     # 交互式对话
│   │   ├── tools/      # 工具管理命令（init/list/remove/update）
│   │   ├── mcp.py      # MCP 注册命令
│   │   ├── cli.py      # CLI 注册命令
│   │   ├── http.py     # HTTP 注册命令
│   │   ├── skills.py   # Skill 注册命令
│   │   ├── memory.py   # 记忆管理命令
│   │   └── models.py   # 模型列表命令
│   └── utils/          # CLI 工具函数（registry/credentials/formatting）
├── config/             # 配置管理
│   ├── models.py       # Pydantic 配置模型（Config, RuntimeConfig）
│   ├── loader.py       # JSON 配置加载/保存
│   └── paths.py        # 路径解析
├── context/            # 上下文与提示词构建
│   └── manager.py      # ContextManager（历史管理、消息构建）
├── llm/                # LLM 客户端
│   ├── client.py       # LLMClient（多模型 Fallback + API 调用）
│   ├── logging.py      # LLMLogger（日志记录，独立模块）
│   ├── formatters.py   # 消息格式化
│   └── scoring.py      # 模型评分
├── memory/             # 长期记忆
│   ├── service.py      # MemoryService（QA 保存/召回）
│   ├── store.py        # MemoryStore（SQLite + sqlite-vec 向量检索）
│   ├── embedder.py     # BaseEmbedder + LlamaCppEmbedder + SentenceTransformersEmbedder
│   └── recall.py       # RecallService（混合检索）
├── models/             # 数据模型
│   └── tool.py         # Tool, ToolExecutionConfig, ToolExecutionType, ToolMetadata
├── prompts/            # Jinja2 提示词模板
│   └── loader.py       # PromptLoader
├── registry/           # 工具注册表
│   └── registry.py     # ToolRegistry（SQLite 持久化）
├── services/           # 业务服务
│   ├── mcp_service.py  # MCPService（MCP 连接生命周期管理）
│   └── tool_service.py # ToolService（工具缓存构建）
├── tools/              # 工具模块
│   ├── builtin_register.py # 内置 function 工具注册
│   ├── builtins.py     # 内置简单函数（echo）
│   ├── search.py       # 搜索工具（serper_search, tavily_search, fetch）
│   ├── llm_helpers.py  # LLM 辅助分析（parse_help_with_llm, run_add_skill_analyzer）
│   ├── openapi.py      # OpenAPI spec 解析
│   ├── registrars/     # 纯逻辑注册器（三入口共用）
│   │   ├── base.py     # 注册器基类
│   │   ├── cli_registrar.py
│   │   ├── mcp_registrar.py
│   │   ├── http_registrar.py
│   │   └── skill_registrar.py
│   └── executors/      # 工具执行器
│       ├── base.py     # ToolExecutor ABC
│       ├── bash.py     # BashToolExecutor（shell 命令执行）
│       ├── cli.py      # CliToolExecutor（命令拼装 + bash 执行）
│       ├── http.py     # HTTPToolExecutor（HTTP 请求）
│       ├── mcp.py      # MCPToolExecutor（MCP 协议通信）
│       ├── function.py # FunctionToolExecutor（Python 函数调用）
│       └── factories.py # create_executor() + ToolExecutorRegistry
└── utils/              # 通用工具
    ├── logging.py      # 会话日志配置
    ├── parsing.py      # JSON 解析辅助
    └── retry.py        # 重试逻辑
```

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.13 |
| 包管理 | uv |
| 数据模型 | Pydantic v2 |
| CLI | Typer + Rich |
| LLM | OpenAI API（兼容多提供商） |
| 存储 | SQLite + sqlite-vec |
| 嵌入 | sentence-transformers (BGE-M3) 或 llama-cpp-python (GGUF) |
| 提示词 | Jinja2 |
| MCP | mcp SDK（stdio/sse/streamable-http） |
| 类型检查 | mypy |

## 配置文件

- **config.json** — 主配置（LLM 提供商、记忆、工具注册表、存储路径等）
- **runtime.json** — 运行时配置（API key 等敏感凭证，独立存储）
- **config.json.template** — 配置模板

详细配置说明参见 DESIGN.md。