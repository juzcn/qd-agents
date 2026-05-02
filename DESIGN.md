# QD-Agents 设计文档

## 项目概述

QD-Agents 是一个自主进化的 AI Agent 框架，核心能力是「工具自主获取 + 工具箱自动管理」。Agent 在运行时发现缺少工具时，能主动安装、注册、使用新工具，实现能力持续扩展。

## 架构

```
qd_agents/
├── agent/                    # Agent 核心
│   ├── base.py              # Agent 基类 + AgentResult 数据模型
│   ├── chat.py              # ChatAgent：主 Agent（三循环架构 + 工具编排）
│   ├── tool_execution.py    # 工具执行调度（按类型路由到对应 executor）
│   ├── add_skill.py         # AddSkillAnalyzer（SKILL.md 分析）
│   └── core.py              # QDAgent：资源管理容器
├── cli/                      # CLI 命令
│   ├── app.py               # Typer 应用入口 + 命令注册
│   ├── main.py              # 入口 + prompt 样式
│   ├── managers/            # 配置管理 + LLM 客户端管理
│   └── commands/            # 命令实现
│       ├── chat.py           # qd-agents chat
│       ├── _registration_base.py  # 注册命令共享模式
│       ├── cli.py            # tools cli add
│       ├── mcp.py            # tools mcp add
│       ├── skills.py         # tools skill add
│       ├── http.py           # tools http add
│       ├── memory.py         # memory 子命令
│       ├── models.py         # models 子命令
│       ├── version.py        # version 命令
│       └── tools/            # tools 子命令
│           ├── init_cmd.py   # tools init [--keep]
│           ├── list_cmd.py   # tools list
│           ├── remove_cmd.py # tools remove
│           └── update_cmd.py # tools update / update-check
├── config/                   # 配置管理
│   ├── loader.py            # 配置加载（JSON + 环境变量）
│   ├── models.py            # Pydantic 配置模型
│   └── paths.py             # 路径常量
├── context/                  # 上下文管理
│   └── manager.py           # 系统提示词构建 + 工具分组渲染 + format_tools_markdown
├── llm/                      # LLM 客户端
│   ├── client.py            # LLMClient（多模型 Fallback）
│   ├── scoring.py           # 模型评分与选择
│   └── formatters.py        # 消息格式化与日志
├── memory/                   # 长期记忆
│   ├── service.py           # 记忆服务（存储 + 语义召回）
│   ├── embedder.py          # 嵌入后端（sentence-transformers BGE-M3）
│   ├── store.py             # SQLite + sqlite-vec 存储
│   └── recall.py            # 记忆召回逻辑
├── models/                   # 数据模型
│   ├── tool.py              # Tool + ToolExecutionConfig + ToolExecutionType + ToolMetadata
│   ├── execution.py         # ExecutionResult / ExecutionStep / ExecutionStatus
│   └── add_skill.py         # AddSkillResult
├── prompts/                  # 提示词模板
│   ├── loader.py            # Jinja2 模板加载与渲染
│   └── templates/
│       ├── add_skill.j2     # 技能分析提示词（使用 {{ tools_section }}）
│       └── add_cli.j2       # CLI help 解析提示词
├── registry/                 # 工具注册表
│   └── registry.py          # 持久化工具注册（SQLite + WAL 模式）
├── services/                 # 业务服务
│   ├── mcp_service.py       # MCP 服务器生命周期管理
│   ├── tool_service.py      # 工具箱 CRUD + OpenAI schema 生成
└── tools/                    # 工具模块
    ├── builtins.py           # echo 等基础工具
    ├── search.py             # Serper、Tavily 搜索工具
    ├── builtin_register.py   # 4 个工具注册 function（LLM 调用入口）
    ├── register.py           # 兼容性重导出层
    ├── env.py                # 环境变量解析
    ├── errors.py             # 工具错误类型
    ├── llm_helpers.py        # LLM 辅助（help 解析、add_skill 分析）
    ├── openapi.py            # OpenAPI spec 解析
    ├── skill_parsing.py      # SKILL.md 解析
    ├── version.py            # 版本检测
    ├── registrars/           # 纯逻辑注册器（CLI/用户/Agent 三环境共用）
    │   ├── base.py           # save_tool / get_registry
    │   ├── cli_registrar.py  # register_cli_tool + extract_registration_args
    │   ├── mcp_registrar.py  # register_mcp_tool + extract_registration_args
    │   ├── skill_registrar.py# register_skill_tool + extract_registration_args
    │   └── http_registrar.py # register_http_tool + extract_registration_args
    └── executors/            # 工具执行器
        ├── base.py           # BaseExecutor 抽象基类
        ├── bash.py           # BashExecutor：shell 命令执行
        ├── cli.py            # CliToolExecutor：CLI 命令执行
        ├── http.py           # HttpExecutor：HTTP API 调用
        ├── mcp.py            # McpExecutor：MCP 协议调用
        ├── function.py       # FunctionExecutor：Python 函数调用
        └── factories.py      # create_executor 工厂 + ToolExecutorRegistry
```

## 核心流程

### 1. 三循环架构

Chat 模式采用三循环架构：1 个系统提示词主循环（chat）+ 2 个消息循环子循环（use-tool、find-tools）。

#### 1.1 主循环 Chat

系统提示词里加载所有工具的名称和描述，上下文中只保留所有 QA。

- **直接回答**：不用工具就能回答的（如问候语），直接输出
- **已有工具可完成**：输出 Job 信息（需要使用的工具列表 + 工具编排逻辑），进入 use-tool 子循环
- **工具缺失**：进入 find-tools 子循环

#### 1.2 子循环 Use-Tool

以主循环输出的 Job 为参数执行任务。在 user 消息和 tool 消息中加载任务背景、任务描述、工具编排逻辑和需要使用的工具列表。

- 加载 skill 工具时：如果是提示词注入类型，加载到主循环的系统提示词中；否则加入到自己的 tool 消息中
- 工具编排逻辑复杂时，用 Python 编排执行
- 循环结束后生成 final answer

#### 1.3 子循环 Find-Tools

根据任务背景、任务描述自主上网查找可用工具，安装，最后生成完整的工具列表交给 use-tool 执行。

- 加载工具箱里所有 builtin 工具的工具详情，和所有搜索工具的工具详情
- 安装成功的工具按类型注册到工具箱

#### 1.4 上下文管理

只使用一套提示词模板。为避免上下文膨胀，主循环只保留子循环的 final answer，视子循环为任务循环——一旦任务完成，任务过程中的其它消息都不保留。子循环回到主循环后，上下文中只有 QA。

**为什么使用双层循环**：实现所有工具的渐进式披露，避免主循环的工具 token 爆炸。

### 2. 工具执行调度

```
工具调用请求 → ToolExecutionHandler
  → 按 execution.type 路由：
    BASH    → BashExecutor（shell 命令）
    CLI     → CliToolExecutor（CLI 命令执行）
    SKILL   → 渐进式披露（首次返回 SKILL.md，后续 execute_bash 执行）
    HTTP    → HttpExecutor（REST API 调用）
    MCP     → McpExecutor（MCP 协议，通过 mcp_service 管理 server 生命周期）
    FUNCTION→ FunctionExecutor（Python 函数调用，需先注册到 ToolExecutorRegistry）
```

### 3. 工具注册与发现

**三种注册入口，共用同一套纯逻辑层（registrars）**：

| 入口 | 调用方式 | 场景 |
|------|---------|------|
| CLI 命令 | `qd-agents tools skill/mcp/cli/http add` | 用户手动注册 |
| LLM function call | `tool_register_skill/mcp/cli/http` | Agent 自主注册 |
| tools init | `register_*_tool()` + `extract_registration_args()` | 重注册已有工具 |

工具 scope 分类：
- **builtin**：核心工具（execute_bash, tool_register_*），不可删除不可更新
- **default**：预装工具（filesystem, fetch, serper-search 等），不可删除但可更新
- **user**：用户/Agent 添加的工具，可删除可更新

### 4. 系统提示词构建

系统提示词由 `context/manager.py` 构建，包含：
- 核心身份与自主行动原则
- 运行环境信息
- Bash 执行注意事项（Windows 适配）
- 工具箱管理说明（直接调用 tool_register_* 函数）
- **工具列表**（由 `format_tools_markdown()` 统一渲染）

工具列表渲染格式（按 scope→type 排序）：
```
## 可用工具

### 内置
- [bash] **execute_bash**: 执行bash/shell命令
- [function] **tool_register_cli**: 注册 CLI 工具
- [function] **tool_register_mcp**: 注册 MCP 服务器工具

### 默认
- [skill] **baidu-search**: 搜索引擎
- [cli] **memory-recall**: 记忆召回
#### filesystem (MCP server: filesystem)
- [mcp] **read_file**: 读取文件内容

### 用户安装
- [skill] **tavily-search**: 搜索引擎
```

`format_tools_markdown()` 是唯一的渲染函数，`add_skill.j2` 通过 `{{ tools_section }}` 变量使用它。

### 5. Builtin Function 工具

4 个工具注册 function 注册为 `scope=builtin` 的 `FUNCTION` 类型工具，LLM 可直接调用管理工具箱：

| 函数 | 参数 | 说明 |
|------|------|------|
| `tool_register_cli` | name, command, extra_env, timeout, default | 注册 CLI 工具 |
| `tool_register_mcp` | server, default | 注册 MCP 服务器工具 |
| `tool_register_skill` | skill_name, extra_env, default | 注册 Skill 工具 |
| `tool_register_http` | name, openapi_url, filter_str, extra_env, timeout, default | 注册 HTTP/OpenAPI 工具 |

这些函数的 OpenAI function calling schema 由 `_generate_openai_schema()` 从函数签名自动生成（使用 `typing.get_type_hints` 解析真实类型注解），无需手写。

### 6. 长期记忆

- **存储**：SQLite + sqlite-vec 向量索引（sentence-transformers BGE-M3）
- **召回**：`memory_recall` 工具（向量 + 关键词混合检索）
- **CLI**：`qd-agents memory list` / `qd-agents memory recall <查询>`

### 7. Skill 渐进式披露

Skill 工具首次调用时不执行，而是将 SKILL.md 注入系统提示词，让 Agent 了解用法后再通过 `execute_bash` 执行。这避免了 Agent 在不了解工具用法时盲目调用。

## 数据模型

### Tool

```python
class ToolExecutionType(str, Enum):
    BASH = "bash"
    CLI = "cli"
    SKILL = "skill"
    HTTP = "http"
    MCP = "mcp"
    FUNCTION = "function"

class ToolExecutionConfig(BaseModel):
    type: ToolExecutionType
    command: str | None = None        # CLI
    args: list[str] = []              # CLI / MCP
    server: str | None = None         # MCP
    transport: str = "stdio"          # MCP
    module: str | None = None         # FUNCTION
    function: str | None = None       # FUNCTION
    shell_command: str | None = None  # BASH
    base_url: str | None = None       # HTTP
    openapi_url: str | None = None    # HTTP
    env: dict[str, str] = {}          # 环境变量
    timeout: int = 120

class Tool(BaseModel):
    id: str
    name: str
    description: str
    parameters: dict                   # JSON Schema
    execution: ToolExecutionConfig
    scope: str = "user"               # builtin / default / user
    metadata: ToolMetadata
    dependencies: dict = {}            # skill_type, tool_deps 等
    source_path: str | None = None    # MCP json 文件路径 / skill 目录名
```

### AgentResult

```python
@dataclass
class AgentResult:
    final_answer: str
    success: bool
    total_tokens: int
    total_duration_ms: int
    trace_id: str
```

## 配置

配置通过 `config.json` 加载，支持环境变量覆盖。主要配置项：

- `llm`：模型选择（provider, model, api_key, base_url）
- `tool_registry`：工具注册表配置（db_path）
- `observability`：日志配置
- `memory`：记忆服务配置（embedding backend, vector dimension）
- `search`：搜索 API 配置

**配置分离**：
- `config.json` — 静态系统配置（不提交到版本控制）
- `config.json.template` — 配置模板（提交到版本控制）
- `runtime.json` — 运行时配置（工具凭证，自动管理）