# QD-Agents

自主进化的 AI Agent 框架。Agent 在运行时发现缺少工具时，能主动安装、注册、使用新工具，实现能力持续扩展。

## 特性

- **自主进化**：Agent 发现需要新工具时，自动安装并注册到工具箱
- **6 种工具类型**：Bash、CLI、Skill、HTTP/OpenAPI、MCP、Function
- **工具自主管理**：LLM 直接调用 `tool_register_*` 函数注册新工具，无需 CLI 命令
- **Skill 渐进式披露**：首次调用返回用法指南，Agent 了解后再执行
- **长期记忆**：向量 + 关键词混合检索，跨会话保留经验
- **多模型 Fallback**：主模型失败自动切换备用模型

## 快速开始

### 安装

```bash
git clone <repo-url> && cd qd-agents
uv sync
```

### 配置

```bash
cp config.json.template config.json
# 编辑 config.json，填入 API key
```

### 运行

```bash
# 交互式对话
uv run qd-agents chat

# 初始化工具箱（注册默认工具）
uv run qd-agents tools init
```

## CLI 命令

| 命令 | 说明 |
|------|------|
| `qd-agents chat` | 交互式对话 |
| `qd-agents tools init` | 初始化/重注册工具箱 |
| `qd-agents tools list` | 查看已注册工具 |
| `qd-agents tools remove <name>` | 移除工具 |
| `qd-agents tools update` | 更新工具 |
| `qd-agents tools skill add <name>` | 注册 Skill 工具 |
| `qd-agents tools mcp add <server>` | 注册 MCP 工具 |
| `qd-agents tools cli add <name> --command <cmd>` | 注册 CLI 工具 |
| `qd-agents tools http add <name> --openapi-url <url>` | 注册 HTTP 工具 |
| `qd-agents memory list` | 查看记忆列表 |
| `qd-agents memory recall <query>` | 语义召回记忆 |
| `qd-agents models list` | 查看可用模型 |
| `qd-agents version` | 查看版本 |

## 工具类型

| 类型 | 说明 | 示例 |
|------|------|------|
| **Bash** | Shell 命令执行 | `execute_bash` |
| **CLI** | 命令行工具封装 | `memory-recall` |
| **Skill** | 技能包（SKILL.md + 脚本） | `baidu-search`, `tavily-search` |
| **HTTP** | REST API / OpenAPI | 天气 API |
| **MCP** | Model Context Protocol | `filesystem`, `fetch`, `serper-search` |
| **Function** | Python 函数直接调用 | `tool_register_cli/mcp/skill/http` |

## 工具 Scope

| Scope | 说明 | 可删除 | 可更新 |
|-------|------|--------|--------|
| **builtin** | 核心工具（execute_bash, tool_register_*） | 否 | 否 |
| **default** | 预装工具（filesystem, fetch 等） | 否 | 是 |
| **user** | 用户/Agent 添加的工具 | 是 | 是 |

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
├── agent/          # Agent 核心（EvolveAgent + 工具执行调度）
├── cli/            # CLI 命令
├── config/         # 配置管理
├── context/        # 上下文与提示词构建
├── llm/            # LLM 客户端（多模型 Fallback）
├── memory/         # 长期记忆（SQLite + 向量检索）
├── models/         # 数据模型
├── prompts/        # Jinja2 提示词模板
├── registry/       # 工具注册表（SQLite 持久化）
├── services/       # 业务服务（MCP 生命周期、工具箱 CRUD）
└── tools/          # 工具模块（注册器 + 执行器 + builtin function）
    ├── registrars/ # 纯逻辑注册器（三环境共用）
    └── executors/  # 工具执行器（bash/cli/skill/http/mcp/function）
```

## 技术栈

- Python 3.13 + uv
- Pydantic v2（数据模型）
- Typer（CLI）
- Rich（终端渲染）
- SQLite + sqlite-vec（工具注册表 + 记忆存储）
- sentence-transformers BGE-M3（记忆嵌入）
- Jinja2（提示词模板）
