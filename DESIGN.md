# qd-agents 设计文档

## 项目定位

基于 LLM 的智能体系统，通过 Tool Calling 和代码编排实现自动化流程。CLI 交互，Python 3.13，OpenAI 兼容 API。

---

## 1. 架构总览

```
用户输入
   ↓
QDAgent（容器 + 资源管理）
   ├─ ToolUseAgent      → ToolCallingMetaAgent（多轮 Tool Calling 循环）
   ├─ CodePlanAgent     → JudgeMetaAgent → ToolCallingMetaAgent / CodingMetaAgent
   └─ EvolveAgent       → EvolveMetaAgent（自主循环 + function calling + 工具进化）
```

**双层抽象**：

| 层级 | 角色 | 特征 |
|------|------|------|
| **MetaAgent** | 原子 LLM 调用单元 | 一个系统提示词 + 一种处理逻辑 + 固定终止条件 |
| **Agent** | 完整任务处理单元 | 编排一个或多个 MetaAgent，对外暴露 `execute()` |

---

## 2. Agent 体系

### 2.1 MetaAgent

| MetaAgent | 类型 | 温度 | 终止条件 | 用途 |
|-----------|------|------|----------|------|
| JudgeMetaAgent | 单轮 | 0.1 | 首次 LLM 回复 | 路由判断：direct / tool_use / coding |
| ToolCallingMetaAgent | 多轮 | 0.7 | LLM 不返回 tool_calls 或达到 max_iterations | OpenAI Tool Calling 循环 |
| CodingMetaAgent | 单轮 | 0.3 | 首次 LLM 回复 | 生成 Python 代码 + 沙盒执行 |
| AddSkillMetaAgent | 单轮 | 0.1 | 首次 LLM 回复 | 分析 SKILL.md，提取参数和工具依赖 |
| EvolveMetaAgent | 多轮 | 0.3 | LLM 不返回 tool_calls 或达到 max_iterations | 自主进化：function calling + SKILL渐进式披露 + 工具进化 |

### 2.2 Agent

| Agent | 编排方式 | 说明 |
|-------|----------|------|
| ToolUseAgent | 简单包装 | 委托给 ToolCallingMetaAgent，使用全部工具 |
| CodePlanAgent | 三阶段路由 | Judge → ToolCalling/Coding，按 tool_list 过滤工具 |

### 2.3 数据流

```
MetaAgentInput  →  MetaAgent.run()  →  MetaAgentOutput
  user_message                         output (Any)
  history                              output_type (text/judge_result/...)
  context (dict)                       success, messages, model, tokens, latency

Agent.execute(user_input, history, **kwargs) → AgentResult
                                            final_answer, success, meta_traces, trace_id
```

---

## 3. 两种工作模式

### 3.1 Tool Use 模式

标准 OpenAI Tool Calling 循环：

1. 系统启动时预加载所有工具（含 MCP 展开），缓存 OpenAI 格式工具列表
2. LLM 接收用户输入 + 全部工具定义
3. LLM 选择直接回复或调用工具（支持并行多工具调用）
4. 执行工具，结果作为 `tool` 消息反馈
5. 重复直到 LLM 不再调用工具

### 3.2 Code-Plan 模式

三阶段智能路由：

```
JudgeMetaAgent
  ├─ direct    → 返回 direct_answer
  ├─ tool_use  → ToolCallingMetaAgent（过滤后工具列表）
  └─ coding    → CodingMetaAgent（代码生成 + 沙盒执行）
```

**工具过滤**：Judge 输出 `tool_list`，后续阶段只接收相关工具，减少上下文压力。

**工具依赖解析**：SKILL 工具声明的 `dependencies.tool_deps` 会被递归加载。

### 3.3 Coding 沙盒

- 允许导入：math, datetime, json, re, collections, itertools, asyncio
- 禁止：eval, exec, `__import__`, open, subprocess, os.system
- 自动检测 `await`，包装为异步函数执行
- 工具函数通过 `extra_globals` 注入执行环境

### 3.4 Evolve 模式（自主进化）

EvolveAgent 是一个真正的自主 agent，不依赖路由判断，直接持有完整对话上下文并通过 function calling 调用工具：

```
用户输入 → EvolveMetaAgent（自主循环）
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

**SKILL 渐进式披露流程**：
1. 系统提示词列出所有工具（含 SKILL），只有名称和描述
2. LLM 调用 SKILL 工具名 → 检测到 SKILL 类型 → 加载 SKILL.md 注入系统提示词
3. LLM 阅读 SKILL.md 的 Usage 部分 → 生成正确的 `execute_bash` 命令执行

**上下文压缩**：

Evolve Agent 在长程迭代中，工具返回结果会不断累积到 messages 列表，导致 prompt 膨胀超出模型上限。上下文压缩机制解决此问题：

1. 工具结果首次出现时完整展示（LLM 本轮可直接使用）
2. 同时将完整结果写入临时文件（`data/tmp/result_xxx.txt`），并用裸 LLM 调用生成摘要
3. 后续轮次中，旧 tool_result 被替换为"摘要 + 文件指针"
4. LLM 需要细节时自主调用 `read_text_file` 读取临时文件

```
第N轮:   LLM → tool_call → 完整结果写入 /tmp + 生成摘要
         → messages 追加完整结果（LLM 本轮可见完整内容）

第N+1轮: compress_old_results() 替换旧结果为摘要+文件指针
         → "[摘要: 项目共47个.py文件...] 完整结果: data/tmp/result_xxx.txt"
         → LLM 需要细节时调用 read_text_file 读取
```

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

1. 启动时从 ToolRegistry 读取所有 MCP 类型工具
2. 连接 MCP 服务器（支持 stdio / sse / streamable-http）
3. 展开服务器提供的所有子工具为独立 Tool 对象
4. 缓存展开结果，同一服务器工具共享执行器连接
5. 会话结束时关闭所有 MCP 连接

### 4.4 Skill 工具

- **有脚本**：`execution.command` 指定脚本路径，通过 BashToolExecutor 的 exec 模式直接执行
- **无脚本**：SKILL.md 正文注入系统提示词，LLM 按 SKILL.md 指南 + execute_bash 工具执行
- **SKILL.md 注入**：ContextManager 读取 `tools/skills/{name}/SKILL.md`，在构建 tool_use 提示词时注入正文
- **依赖声明**：`dependencies.tool_deps` 指定 Skill 依赖的其他工具，CodePlanAgent 递归加载

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
- **模板**：tool_use.j2 / judge.j2 / coding.j2 / add_skill.j2
- **缓存**：ContextManager 按工具 ID 集合缓存系统提示词，避免重复渲染
- **回退**：PromptLoader 不可用时使用硬编码提示词

---

## 7. 上下文管理

ContextManager 统一构建各阶段的 LLM 消息：

```
[system_prompt] + [history] + [current_user_input]
```

- `build_tool_use_messages()`：SKILL 工具注入 SKILL.md 正文
- `build_judge_messages()`：工具 L0 信息（名称+描述）
- `build_coding_messages()`：工具函数列表
- `build_add_skill_messages()`：SKILL.md 全文 + 已注册工具

---

## 8. 配置管理

**双文件分离**：

| 文件 | 内容 | 性质 |
|------|------|------|
| `config.json` | 系统配置（LLM/工具/执行/提示词/存储/可观测性） | 静态，可入版本控制 |
| `runtime.json` | 运行时数据（工具凭证） | 动态，不入版本控制 |

**配置加载**：Pydantic BaseModel 验证，支持环境变量插值（`QD_` 前缀 + `__` 嵌套分隔符）。

---

## 9. 可靠性机制

### 重试

- 4 种退避策略：fixed / linear / exponential / exponential_with_jitter
- 默认：exponential_with_jitter，max_attempts=3，initial_delay=1s

### 熔断器

- 状态：CLOSED → OPEN（失败率 > 阈值）→ HALF_OPEN（冷却后）→ CLOSED
- 默认：error_rate_threshold=0.5，minimum_requests=10，half_open_timeout=30s

---

## 10. CLI

```
qd-agents                    # 默认启动交互式聊天
qd-agents --agent tool-use   # 指定 Agent
qd-agents --list-models      # 列出可用模型
qd-agents --version          # 版本信息

qd-agents tools init         # 初始化内置工具
qd-agents tools list         # 列出已注册工具
qd-agents tools remove ID    # 移除工具
qd-agents tools mcp add      # 添加 MCP 服务器
qd-agents tools skill add    # 添加 Skill 工具
```

**聊天命令**：`/quit` `/help` `/model` `/models` `/tools` `/agent`

---

## 11. 模块结构

```
src/qd_agents/
├── agent/           # Agent 体系
│   ├── base.py      # MetaAgent/Agent 基类 + 数据模型
│   ├── core.py      # QDAgent 容器
│   ├── judge_meta.py
│   ├── tool_calling_meta.py
│   ├── coding_meta.py
│   ├── add_skill_meta.py
│   ├── tool_use.py
│   ├── code_plan.py
│   ├── evolve.py      # EvolveAgent（外层包装）
│   ├── evolve_meta.py # EvolveMetaAgent（自主循环核心）
│   ├── mcp_service.py
│   └── tool_service.py
├── cli/             # CLI 入口
│   ├── app.py       # Typer 应用
│   ├── main.py      # 入口 + prompt_style
│   ├── commands/    # chat / tools / mcp / skills / models / version
│   ├── managers/    # configuration / llm_client / tool_registration
│   └── utils/       # formatting
├── config/          # 配置加载（Pydantic + JSON）
├── context/         # 上下文管理器
│   ├── manager.py   # ContextManager
│   └── compressor.py # ContextCompressor（工具结果压缩）
├── execution/       # 执行引擎（沙盒 + 工具执行）
├── llm/             # LLM 客户端 + 评分 + 格式化
├── models/          # Pydantic 数据模型（Tool / Judge / Execution / AddSkill）
├── prompts/         # Jinja2 模板加载
├── registry/        # 工具注册中心（SQLite）
├── tools/           # 工具执行器 + 内置工具
│   ├── executors/   # base / function / http / cli / mcp / factories
│   ├── builtins.py
│   ├── builtin_search.py
│   └── mcp_manager.py
└── utils/           # retry / circuit_breaker / logging / parsing
```

---

## 12. 未实现功能

| 功能 | 状态 |
|------|------|
| 向量检索（sqlite-vec） | 配置预留，未实现 |
| 工具版本管理 | 数据模型已定义，功能未实现 |
| 工具依赖关系 | tool_deps 字段存在，仅 Skill 递归加载已实现 |
| 流式输出 | 未实现 |
| 用户协作/动态调整 | 未实现 |
| 渐进式披露 L2/L3 | 未实现 |
| 历史分离 | 未实现（使用统一历史） |
| 审计日志 | 未完整实现 |
| 测试 | 无测试文件 |