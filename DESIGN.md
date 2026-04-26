# qd-agents 设计文档

## 项目定位

基于 LLM 的智能体系统，通过 Tool Calling 和代码编排实现自动化流程。CLI 交互，Python 3.13，OpenAI 兼容 API。

---

## 1. 架构总览

```
用户输入
   ↓
QDAgent（资源管理器 + EvolveAgent 容器）
   ├─ MCPService（MCP 连接管理）
   ├─ ToolService（工具缓存构建）
   ├─ ContextCompressor（上下文压缩）
   └─ EvolveAgent（自主循环 + function calling + SKILL渐进式披露 + 工具进化）
```

**单层架构**：

| 层级 | 角色 | 特征 |
|------|------|------|
| **Agent** | 完整任务处理单元 | EvolveAgent 持有完整对话上下文，自主循环，function calling 直接调用工具 |

---

## 2. Agent 体系

### 2.1 EvolveAgent

EvolveAgent 是唯一的 Agent，直接持有完整对话上下文并通过 function calling 调用工具：

```
用户输入 → EvolveAgent（自主循环）
             ├─ LLM 返回 tool_calls → 执行工具 → 观察结果 → 继续循环
             ├─ LLM 返回 SKILL 工具 → 渐进式披露：注入 SKILL.md → LLM 按 Usage 执行
             ├─ LLM 不返回 tool_calls → 输出最终答案
             └─ 达到 max_iterations → 终止
```

**核心能力**：

1. **自主循环**：LLM 自己决定是否需要调用工具、调用哪些工具、何时停止
2. **SKILL 渐进式披露**：初始只展示工具名+描述，LLM 调用 SKILL 工具名时才注入 SKILL.md 到系统提示词
3. **工具进化**：发现缺失工具时自动安装使用（如 `uvx yt-dlp`），成功后通过 `qd-agents tools add` 注册到工具箱
4. **步骤回调**：通过 `on_step` 回调实时输出中间过程到终端
5. **ask_user/delegate**：需要用户输入或委托用户执行时输出 JSON 格式

**工具执行逻辑**提取到 `agent/tool_execution.py`，EvolveAgent 通过模块级函数调用：

| 函数 | 职责 |
|------|------|
| `execute_tool()` | 执行工具调用，分发到对应执行器 |
| `find_skill_tool()` | 检测工具是否为 SKILL 类型 |
| `ensure_bash_available()` | 确保 bash 工具可用（SKILL 依赖） |
| `inject_skill_into_system_prompt()` | 将 SKILL.md 注入系统提示词 |
| `format_tool_result()` | 格式化工具执行结果 |

### 2.2 AddSkillAnalyzer

AddSkillAnalyzer 是一个调用大模型的工具类（不是 Agent），用于分析 SKILL.md 内容，识别技能的参数定义和工具依赖。

### 2.3 数据流

```
Agent.execute(user_input, history, **kwargs) → AgentResult
                                            final_answer, success, total_tokens, trace_id
```

---

## 3. Evolve 工作模式

EvolveAgent 是一个真正的自主 agent，不依赖路由判断，直接持有完整对话上下文并通过 function calling 调用工具：

```
用户输入 → EvolveAgent（自主循环）
             ├─ LLM 返回 tool_calls → 执行工具 → 观察结果 → 继续循环
             ├─ LLM 返回 SKILL 工具 → 渐进式披露：注入 SKILL.md → LLM 按 Usage 执行
             ├─ LLM 不返回 tool_calls → 输出最终答案
             └─ 达到 max_iterations → 终止
```

**SKILL 渐进式披露流程**：
1. 系统提示词列出所有工具（含 SKILL），只有名称和描述
2. LLM 调用 SKILL 工具名 → 检测到 SKILL 类型 → 加载 SKILL.md 注入系统提示词
3. LLM 阅读 SKILL.md 的 Usage 部分 → 生成正确的 `execute_bash` 命令执行

**上下文压缩**：

EvolveAgent 在长程迭代中，工具返回结果会不断累积到 messages 列表，导致 prompt 膨胀超出模型上限。上下文压缩机制解决此问题：

1. 工具结果首次出现时完整展示（LLM 本轮可直接使用）
2. 同时将完整结果写入临时文件（`data/tmp/result_xxx.txt`），并用裸 LLM 调用生成摘要
3. 后续轮次中，旧 tool_result 被替换为"摘要 + 文件指针"
4. LLM 需要细节时自主调用 `read_text_file` 读取临时文件

配置项（`config.json` → `context_compression`）：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| enabled | true | 是否启用压缩 |
| result_threshold | 2000 | 工具结果超过此字符数才触发压缩 |
| summary_max_length | 500 | 摘要最大字符数 |
| temp_dir | data/tmp | 临时文件存储目录 |
| keep_recent_results | 1 | 保留最近N轮完整结果不压缩 |

---

## 4. 工具系统

### 4.1 六种执行类型

| 类型 | 执行器 | 说明 |
|------|--------|------|
| function | FunctionToolExecutor | Python 函数调用（echo, serper_search, tavily_search） |
| http | HTTPToolExecutor | HTTP API 调用（搜索工具等） |
| cli | CLIToolExecutor | 外部命令行程序 |
| bash | BashToolExecutor | Shell 命令执行 |
| mcp | MCPToolExecutor | Model Context Protocol 工具 |
| skill | BashToolExecutor / `_ScriptlessSkillExecutor` | 有脚本用 exec 模式，无脚本引导 LLM 用 bash 执行 |

### 4.2 工具注册中心（ToolRegistry）

- **存储**：SQLite（`data/tools.db`），WAL 模式
- **操作**：register / get / get_by_name / list_all / search / delete
- **搜索**：关键词匹配（向量检索预留但未实现）
- **版本状态**：draft / active / deprecated / retired（数据模型已定义，功能未实现）

### 4.3 MCP 工具展开

由 `MCPService`（`services/mcp_service.py`）管理：

1. 启动时从 ToolRegistry 读取所有 MCP 类型工具
2. 连接 MCP 服务器（支持 stdio / sse / streamable-http）
3. 展开服务器提供的所有子工具为独立 Tool 对象
4. 缓存展开结果，同一服务器工具共享执行器连接
5. 会话结束时关闭所有 MCP 连接

### 4.4 Skill 工具

- **有脚本**：`execution.command` 指定脚本路径，通过 BashToolExecutor 的 exec 模式直接执行
- **无脚本**：SKILL.md 正文注入系统提示词，LLM 按 SKILL.md 指南 + execute_bash 工具执行
- **SKILL.md 注入**：ContextManager 读取 `tools/skills/{name}/SKILL.md`，在构建 evolve 提示词时注入正文
- **依赖声明**：`dependencies.tool_deps` 指定 Skill 依赖的其他工具

### 4.5 内置工具

| ID | 名称 | 类型 | 说明 |
|----|------|------|------|
| echo | echo | function | 回显输入，测试用 |
| search.serper | serper_search | http | Serper API 网络搜索 |
| search.tavily | tavily_search | http | Tavily AI 增强搜索 |
| search.baidu | baidu_search | http | 百度搜索 |
| search.web | web_search | skill | 统一搜索接口，自动选择引擎 |
| execute_bash | execute_bash | bash | 执行 Bash 命令 |

---

## 5. LLM 客户端

- **协议**：OpenAI 兼容 API（AsyncOpenAI）
- **多提供商**：配置文件中声明，支持 NVIDIA NIM / 讯飞等
- **模型发现**：NVIDIA 提供商调用 `/v1/models` 自动发现，评分取 Top K
- **Fallback**：按模型列表顺序自动降级
- **评分函数**：基础分(1000, 仅 chat 模型) + 参数大小(0-100) + 模型系列(0-50) + 后缀加分(20)

### 模型评分优先级

deepseek/glm > qwen > minimax > llama/mistral/gemma > other

---

## 6. 提示词系统

- **引擎**：Jinja2（`.j2` 模板）
- **模板**：evolve.j2 / add_skill.j2
- **缓存**：ContextManager 按工具 ID 集合缓存系统提示词，避免重复渲染
- **回退**：PromptLoader 不可用时使用硬编码提示词

---

## 7. 上下文管理

ContextManager 统一构建 LLM 调用的消息：

```
[system_prompt] + [history] + [current_user_input]
```

- `build_evolve_messages()`：构建 EvolveAgent 的系统提示词（渐进式披露）
- `build_add_skill_messages()`：SKILL.md 全文 + 已注册工具

---

## 8. 配置管理

**双文件分离**：

| 文件 | 内容 | 性质 |
|------|------|------|
| `config.json` | 系统配置（LLM/工具/执行/提示词/存储/可观测性） | 静态，可入版本控制 |
| `runtime.json` | 运行时数据（工具凭证） | 动态，不入版本控制 |

**配置加载**：Pydantic BaseModel 验证，支持环境变量插值（`QD_` 前缀 + `__` 嵌套分隔符）。

**代码分离**：

| 文件 | 职责 |
|------|------|
| `config/models.py` | Pydantic 配置模型定义（Config, RuntimeConfig, LLMProviderConfig 等） |
| `config/loader.py` | JSON 文件加载/保存逻辑（load_config, save_config, load_runtime_config, save_runtime_config） |

---

## 9. 服务层

服务类从 `agent/` 提取到独立的 `services/` 包，职责清晰：

| 服务 | 文件 | 职责 |
|------|------|------|
| MCPService | `services/mcp_service.py` | MCP 服务器连接管理、工具展开、资源清理 |
| ToolService | `services/tool_service.py` | 工具缓存构建、内置工具注册 |

---

## 10. 可靠性机制

### 重试

- 4 种退避策略：fixed / linear / exponential / exponential_with_jitter
- 默认：exponential_with_jitter，max_attempts=3，initial_delay=1s

### 熔断器

- 状态：CLOSED → OPEN（失败率 > 阈值）→ HALF_OPEN（冷却后）→ CLOSED
- 默认：error_rate_threshold=0.5，minimum_requests=10，half_open_timeout=30s

---

## 11. CLI

```
qd-agents                    # 默认启动交互式聊天
qd-agents --list-models      # 列出可用模型
qd-agents --version          # 版本信息

qd-agents tools init         # 初始化内置工具
qd-agents tools list         # 列出已注册工具
qd-agents tools remove ID    # 移除工具
qd-agents tools mcp add      # 添加 MCP 服务器
qd-agents tools skill add    # 添加 Skill 工具
```

**聊天命令**：`/quit` `/help` `/model` `/models` `/tools`

---

## 12. 模块结构

```
src/qd_agents/
├── agent/           # Agent 体系
│   ├── base.py      # Agent 基类 + 数据模型（AgentResult, StepCallback 等）
│   ├── core.py      # QDAgent 容器（资源管理）
│   ├── evolve.py    # EvolveAgent（自主循环核心）
│   ├── tool_execution.py  # 工具执行辅助函数（从 evolve.py 提取）
│   └── add_skill.py # AddSkillAnalyzer（SKILL.md 分析）
├── services/        # 服务类（从 agent/ 提取）
│   ├── mcp_service.py   # MCPService（MCP 连接管理）
│   └── tool_service.py  # ToolService（工具缓存构建）
├── cli/             # CLI 入口
│   ├── app.py       # Typer 应用
│   ├── main.py      # 入口 + prompt_style
│   ├── commands/    # chat / tools / mcp / skills / models / version
│   ├── managers/    # configuration / llm_client
│   └── utils/       # formatting / credentials
├── config/          # 配置管理
│   ├── models.py    # Pydantic 配置模型定义
│   └── loader.py    # JSON 加载/保存逻辑
├── context/         # 上下文管理器
│   ├── manager.py   # ContextManager
│   └── compressor.py # ContextCompressor（工具结果压缩）
├── llm/             # LLM 客户端 + 评分 + 格式化
├── models/          # Pydantic 数据模型
│   ├── tool.py      # Tool / ToolExecutionConfig / ToolMetadata
│   ├── evolve.py    # EvolveResult / AskUserInfo / DelegateInfo
│   ├── add_skill.py # AddSkillResult
│   └── execution.py # ExecutionResult / ExecutionStep / ExecutionStatus
├── prompts/         # Jinja2 模板加载
├── registry/        # 工具注册中心（SQLite）
├── tools/           # 工具执行器 + 内置工具
│   ├── executors/   # base / function / http / cli / bash / mcp / factories
│   ├── builtins.py  # echo 等基础工具
│   └── search.py    # Serper、Tavily 搜索工具
└── utils/           # retry / circuit_breaker / logging / parsing
```

**导入规范**：
- 工具模型从 `qd_agents.models.tool` 导入（不从 `registry` 导入）
- 服务类从 `qd_agents.services` 导入（不从 `agent` 导入）
- 配置模型从 `qd_agents.config.models` 导入，加载逻辑从 `qd_agents.config.loader` 导入

---

## 13. 未实现功能

| 功能 | 状态 |
|------|------|
| 向量检索（sqlite-vec） | 配置预留，未实现 |
| 工具版本管理 | 数据模型已定义，功能未实现 |
| 流式输出 | 未实现 |
| 渐进式披露 L2/L3 | 未实现 |
| 历史分离 | 未实现（使用统一历史） |
| 审计日志 | 未完整实现 |
| 测试 | 无测试文件 |