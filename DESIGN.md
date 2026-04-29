# QD-Agents 设计文档

## 项目概述

QD-Agents 是一个自主进化的 AI Agent 框架，核心能力是「工具自主获取 + 工具箱自动管理」。Agent 在运行时发现缺少工具时，能主动安装、注册、使用新工具，实现能力持续扩展。

## 架构

```
qd_agents/
├── agent/                    # Agent 核心
│   ├── base.py              # Agent 基类 + AgentResult 数据模型
│   ├── evolve.py            # EvolveAgent：主 Agent（自主循环 + 工具编排）
│   ├── tool_execution.py    # 工具执行调度（按类型路由到对应 executor）
│   └── core.py              # QDAgent：底层 LLM 调用封装
├── cli/                      # CLI 命令
│   ├── app.py               # Typer 应用入口 + 命令注册
│   └── commands/            # 命令实现
│       ├── __init__.py      # 公共导出
│       ├── chat_cmd.py      # qd-agents chat
│       ├── tools/           # tools 子命令
│       │   ├── init_cmd.py  # tools init [--keep]
│       │   ├── list_cmd.py  # tools list
│       │   ├── remove_cmd.py# tools remove
│       │   ├── update_cmd.py# tools update
│       │   ├── add_skill_cmd.py  # tools skill add
│       │   └── add_mcp_cmd.py    # tools mcp add
│       └── ...
├── config/                   # 配置管理
│   ├── loader.py            # 配置加载（YAML + 环境变量）
│   └── models.py            # Pydantic 配置模型
├── context/                  # 上下文管理
│   └── manager.py           # 系统提示词构建 + 工具分组渲染
├── executors/                # 工具执行器
│   ├── base.py              # BaseExecutor 抽象基类
│   ├── bash.py              # BashExecutor：shell 命令执行
│   ├── http.py              # HttpExecutor：HTTP API 调用
│   ├── mcp.py               # McpExecutor：MCP 协议调用
│   └── function.py          # FunctionExecutor：Python 函数调用
├── memory/                   # 长期记忆
│   ├── service.py           # 记忆服务（存储 + 语义召回）
│   └── embedder.py          # 嵌入后端（sentence-transformers BGE-M3）
├── models/                   # 数据模型
│   ├── tool.py              # Tool + ToolExecutionConfig + ToolExecutionType
│   └── ...
├── prompts/                  # 提示词模板
│   ├── manager.py           # Jinja2 模板渲染
│   └── templates/
│       ├── evolve.j2        # EvolveAgent 系统提示词
│       └── add_skill.j2     # 技能分析提示词
├── registry/                 # 工具注册表
│   └── tool_registry.py     # 持久化工具注册（JSON 文件）
├── services/                 # 业务服务
│   ├── mcp_service.py       # MCP 服务器生命周期管理
│   ├── tool_service.py      # 工具箱 CRUD + OpenAI schema 生成
│   └── memory_service.py    # 记忆服务门面
└── tools/                    # 内置工具
    └── builtins.py          # execute_bash, memory_list, memory_recall
```

## 核心流程

### 1. 自主循环（Evolve Loop）

```
用户输入 → EvolveAgent.run()
  → 构建 messages（系统提示词 + 历史 + 工具列表）
  → LLM 调用（QDAgent）
  → 解析响应
    → 文本回答 → 返回用户
    → 工具调用 → 执行工具 → 观察结果 → 继续循环
```

### 2. 工具执行调度

```
工具调用请求 → ToolExecutionHandler
  → 按 execution.type 路由：
    BASH    → BashExecutor（shell 命令）
    SKILL   → 渐进式披露（首次返回 SKILL.md，后续 execute_bash 执行）
    HTTP    → HttpExecutor（REST API 调用）
    MCP     → McpExecutor（MCP 协议，通过 mcp_service 管理 server 生命周期）
    FUNCTION→ FunctionExecutor（Python 函数调用）
```

### 3. 工具注册与发现

```
qd-agents tools init [--keep]  → 初始化工具箱
  --keep: 保留用户工具，只重新注册 builtin + default
  默认:   清除所有工具后重新注册

qd-agents tools skill add <name>  → 注册 Skill 工具
qd-agents tools mcp add <name> <server>  → 注册 MCP 工具
qd-agents tools list    → 查看工具箱
qd-agents tools remove  → 移除工具
```

工具分类：
- **builtin**：核心工具（execute_bash, memory_list, memory_recall），不可删除不可更新
- **default**：预装 MCP 工具（filesystem, fetch, serper-search），不可删除但可更新
- **user**：用户添加的工具，可删除可更新

### 4. 系统提示词构建

系统提示词由 `context/manager.py` 构建，包含：
- 核心身份与自主行动原则
- 运行环境信息
- Bash 执行注意事项（Windows 适配）
- 工具获取能力说明
- 工具箱管理命令说明
- **工具列表**（按类型分组渲染）

工具列表渲染格式（结构化 Markdown）：
```
## 可用工具

### bash
- [bash] **execute_bash**: 执行bash/shell命令

### skill
- [skill] **web_search**: 统一搜索接口

### mcp
#### filesystem (MCP server: filesystem)
- [mcp] **read_file**: 读取文件内容
- [mcp] **write_file**: 写入文件

#### fetch (MCP server: fetch)
- [mcp] **fetch**: 发起 HTTP 请求
```

工具分组由 `_group_tools_by_type()` 实现，按 `ToolExecutionType` 分组，MCP 工具按 `execution.server` 嵌套。

### 5. 长期记忆

- **存储**：SQLite + 向量索引（sentence-transformers BGE-M3）
- **召回**：`memory_recall` 工具（向量 + 关键词混合检索）
- **列表**：`memory_list` 工具（按时间/会话筛选）
- **CLI**：`qd-agents memory list` / `qd-agents memory recall <查询>`

### 6. Skill 渐进式披露

Skill 工具首次调用时不执行，而是将 SKILL.md 注入系统提示词，让 Agent 了解用法后再通过 `execute_bash` 执行。这避免了 Agent 在不了解工具用法时盲目调用。

## 数据模型

### Tool

```python
class ToolExecutionType(str, Enum):
    BASH = "bash"
    SKILL = "skill"
    HTTP = "http"
    MCP = "mcp"
    FUNCTION = "function"

class ToolExecutionConfig(BaseModel):
    type: ToolExecutionType
    shell_command: str | None = None    # BASH
    skill_dir: str | None = None        # SKILL
    endpoint: str | None = None         # HTTP
    server: str | None = None           # MCP
    function_name: str | None = None    # FUNCTION

class Tool(BaseModel):
    id: str
    name: str
    description: str          # 完整描述，不截断
    parameters: dict           # JSON Schema
    execution: ToolExecutionConfig
    metadata: ToolMetadata
```

### AgentResult

```python
@dataclass
class AgentResult:
    final_answer: str
    success: bool
    working_memory: dict
    interaction_log: list[dict]
    total_tokens: int
    last_prompt_tokens: int
    total_duration_ms: int
    trace_id: str
```

## 配置

配置通过 `config.json` 加载，支持环境变量覆盖。主要配置项：

- `llm`：模型选择（provider, model, api_key, base_url）
- `tools`：工具箱配置
- `storage`：数据存储路径
- `memory`：记忆服务配置（embedding backend, vector dimension）
