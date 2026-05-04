```markdown
# Evolve Agent 设计（正式版）

## 一、概述

工具的使用扩展了大模型的能力，使它成为一个今天所谓的智能体。经历了 prompt engineering、context engineering 到现在的 agent harness，设计者和开发人员都试图使得大模型+工具模式更加智能，更加好用。

但今天大多数智能体是静态的软件模式，有固定的人工设计的框架，通过外挂插件来扩展能力。使用工具的流程通过固定的人工编排逻辑，可以说是人在教大模型怎么做。也就是说设计者能力是智能体产品的上限，而没有充分使用到大模型自身的感知、推理和采取行动的能力。

最近上了 GitHub 热榜的 https://github.com/shareAI-lab/learn-claude-code 提炼了 Claude Code 的一些重要设计理念，非常精准到位：

1. Agency — the ability to perceive, reason, and act — comes from model training, not from external code orchestration.
2. One loop & Bash is all you need.
3. Adding a tool means adding new capacity.
4. An agent without a plan drifts.
5. Context will fill up; you need a way to make room.
6. Run slow operations in the background; the agent keeps thinking.
7. Break big goals into small tasks, order them, persist to disk.
8. When the task is too big for one, delegate to teammates.

我们发现，智能体能力 = 大模型的能力 + 工具能力。事实上，工具配置直接影响大模型的推理能力，两者密不可分。人也一样，如果你安排行程的时候不知道有飞机通航，你的推理中就不可能考虑乘坐飞机。

智能体框架本身是代码，它给智能体的运行提供基础条件，但它不应该硬编码一些自以为是的逻辑，因为设计者自身并不比大模型智能。

今天的所有智能体都受到大模型自身能力的限制，包括知识的固化和记忆问题。知识固化可以通过使用本地文件和网络搜索来缓解；工作记忆依赖于上下文窗口，永久记忆则需要外挂的工具。

**Evolve Agent 的设计目标**可以概括为一个极简的智能体框架——**依靠大模型的原生推理能力，而非硬编码逻辑**。它包括：

第一：它只提供 **Opinionated 的原则**，例如“只使用现有工具，缺少工具时，自己去搜索发现和安装”，不提供自以为是的人类经验指导。**路由决策权完全交给模型**：框架只提供统一的委托工具 `delegate`，由模型自主决定何时、如何使用专用子 Agent。

第二：它提供一个**最小集合的工具箱**，这个工具箱包含感知环境的工具、使用工具的工具、发现工具的工具、注册新工具的工具。通过这个设计，让 Evolve Agent 随着用户使用而自主进化，每个用户有自己的进化版本。最小集合的工具箱包括上下文管理工具、永久记忆的工具、本地和网络搜索的工具。

第三：它能够**自我修复、自我更新和自我升级**智能体框架本身，实现自我迭代，但所有更新需经过用户授权和签名验证。

## 二、智能体框架设计

### 2.1 Agents 设计

整体架构包括 **Evolve Agent（主循环）** 以及三个专用子 Agent：**Use-Tool Agent**、**Find-Tools Agent**、**Coding Agent**。每个 Agent 都是 **MetaAgent** 的扩展，即它们都遵循相同的“感知-推理-行动”循环模式，只是在具体的 system prompt 和工具集上有所不同。为了清晰地理解各个 Agent 的设计，下面首先定义 **MetaAgent** 基类，然后再分别介绍各个 Agent。

#### 2.1.1 MetaAgent

MetaAgent 是大模型调用工具的基础循环模式。它遵循 **OpenAI 标准工具调用循环**，直到模型返回最终答案。所有 Agent（Evolve、Use-Tool、Find-Tools、Coding）都是 MetaAgent 的扩展，共享相同的"感知-推理-行动"循环逻辑，只在 system prompt 和工具集上有所不同。

**输入参数**：
- `task-background`：任务的背景信息，字符串
- `task-requirements`：任务的要求，字符串
- `tools-list`：工具数组，每个工具包含名称、描述、参数 JSON schema

**内部状态**：
- `system_prompt`：根据传入的参数，追加 task-background、task-requirements 以及 tools-list 中每个工具的详细信息
- `messages`：消息列表，初始为 system 消息，随后添加用户消息和所有助手/工具响应

**输出参数**：`Final answer`

**核心循环逻辑**（标准 OpenAI 风格）：

1. **初始化**：将系统提示词和用户任务加入 `messages` 列表。

2. **循环**（最大迭代次数可配置，默认 20）：
   - 调用大模型 API，传入 `messages` 和 `tools` 列表，`tool_choice` 设为 `"auto"`。
   - 模型返回响应：
     - **若响应包含 `tool_calls`**：
       - 将助手的 `tool_calls` 消息追加到 `messages`。
       - 遍历每个 `tool_call`：
         - 如果工具名称为 `ask_user`：
           - 解析参数（`question`、可选的 `options`、`timeout_seconds`）。
           - 将问题输出给用户（支持选项菜单），**暂停循环**等待用户回复。
           - 用户回复后，构造工具响应消息（`role: "tool"`，`content` 为 JSON 格式 `{"answer": "...", "selected_option": idx/null}`），追加到 `messages`。
         - 否则（其他工具）：
           - 执行工具实现，获得返回值或错误。
           - 将工具响应消息（`role: "tool"`，`content` 为 JSON 字符串）追加到 `messages`。
       - 继续下一轮循环，将工具结果返回模型。
     - **若响应仅为文本内容（无 `tool_calls`）**：
       - 将该文本作为助手消息追加到 `messages`。
       - 终止循环，返回该文本作为最终答案。
   - 若达到最大迭代次数，强制终止并返回最后一条助手消息。

3. **错误处理**：API 调用或工具执行异常时，根据配置进行重试（最多 3 次，指数退避），重试失败则返回错误信息。

**上下文隔离**：每个 Agent 实例拥有完全独立的 `messages` 上下文，不与主循环或其他子 Agent 共享。子 Agent 只依赖通过 `delegate` 传递的 `task-background`、`task-requirements` 和 `tools-list` 运行，完成后只返回 `Final answer`。

**关键要点**：
- `ask_user` 是唯一需要暂停循环等待外部输入的工具，其余工具均为同步执行。
- 其他 `builtin` 工具（如 `execute_bash`、`fetch`）按标准方式执行并返回结果。
- 该循环完全兼容 OpenAI API 的 `chat/completions` 和 `responses` 端点（通过 `api_mode` 字段配置，`LLMClient.chat()` 自动分派，`format_tool_result()` 兼容两种格式）。

#### 2.1.2 Evolve Agent（主循环）

**输入参数**：
- `Task-background`：字符串，默认值为主会话Agent角色定义及通用行为规范
- `Task-requirements`：字符串，默认值为路由决策规则、delegate 调用规范和输出格式要求
- `tools_list`：默认为 `["delegate", "ask_user", "context_summarizer"]`

**系统提示词架构**：Evolve Agent 的系统提示词由四个职责明确的部分组成，各部分各司其职、不重复：

| 部分 | 职责 | 内容 |
|------|------|------|
| 原则 | 高层行为准则 | ask_user 交互、delegate 带 ask_user、分解大任务 |
| 任务背景 | 角色定位 | 你是主会话 Agent，协调子 Agent |
| 任务要求 | 具体怎么做 | 路由判断规则、delegate 参数要求、输出格式 |
| 工具 schema | 填参数时的指南 | agent 参数描述包含路由判断：有匹配→Use-Tool，无匹配→Find-Tools |

**核心规则**：路由决策只在任务要求和 delegate schema 的 agent 参数描述中出现，原则不再重复。原则只管”应该做什么”（高层行为），任务要求管”怎么做”（具体规则），工具 schema 管”参数怎么填”（填写指南）。三者各负其责，避免信息重复导致模型混淆或忽略。

**工具箱概览**：系统提示词末尾加载工具箱中所有工具的名称和描述（包括通过 `delegate` 可调用的子 Agent），供模型在 delegate 选择工具时参考，但不可直接调用。

**关于 skill 工具的特殊处理**：
- 技能（skill）工具在注册时可能不具有传统的参数 schema，而是附带一份 `SKILL.md` 文档，描述该技能的使用方法、步骤或提示词。
- 当 skill 工具被加载到工具箱时，框架会将其 `SKILL.md` 内容注入到该工具的 `description` 字段（或单独的 `configuration` 字段）中，供模型在调用前了解其能力。
- 若该 skill 属于“指导性提示词注入”类型（例如用于改变 Agent 行为模式的元技能），则框架还需将其内容**同时追加到当前会话的系统提示词中**，并持续生效直至会话结束或被显式卸载。这类 skill 通常通过 `tool_register_skill` 注册时标记 `inject_to_system_prompt: true`。

**关键设计**：路由选择完全由模型基于提示词自主决定，框架层面没有硬编码分支。模型只需决定是直接回答、提问还是调用 `delegate` 指向 `Use-Tool` / `Find-Tools` / `Coding`。提示词架构通过职责分离确保路由决策信息只在任务要求和工具 schema 中出现，避免原则层重复导致模型混淆。

**上下文管理**：存储用户输入以及各子 Agent 返回的最终答案。当上下文长度超过阈值（例如模型最大窗口的 80%）时，自动触发 `context_summarizer`（见 2.3 节）。

**工具调用流程**（具体路由规则见任务要求，此处仅列举可能的调用方向）：
- 如果模型认为可以直接回答，返回最终答案。
- 如果需要向用户澄清，调用 `ask_user` 工具。
- 如果需要使用任何工具（包括执行 bash、搜索、文件操作、HTTP 请求等），调用 `delegate` 工具，参数 `agent` 为 `"Use-Tool"`，`task` 为自然语言描述。
- 如果需要发现或安装新工具，调用 `delegate` 指向 `"Find-Tools"`，**并在任务描述中明确所需工具的能力**。
- 如果需要生成代码或复杂脚本，调用 `delegate` 指向 `"Coding"`。

**新增工具管理**：如果 Find-Tools Agent 返回了已注册的新工具，主循环会自动更新其 system prompt 中的工具列表（热加载，无需重启）。具体机制：对比 Find-Tools 执行前后的 registry 工具名差异，检测新注册的工具；如果列表非空，清除系统提示词缓存，用新工具列表重新渲染 `tools_section`，替换 `messages[0]` 的 content。Evolve Agent 看到更新后的工具箱概览后，自然会将任务路由到 Use-Tool Agent 执行。对于需要注入系统提示词的 skill 工具，同时更新 system prompt 内容。

#### 2.1.3 Use-Tool Agent

**职责**：接收一个明确的任务（例如”搜索人工智能新闻”），自动选择合适的工具并执行，返回结构化结果。继承 MetaAgent，在独立上下文中运行。

**输入参数**：
- `Task-background`：字符串，由 Evolve Agent 的 delegate 调用传入
- `Task-requirements`：字符串，任务具体描述
- `tools-list`：字符串数组，指定可用的工具名列表

**上下文隔离**：Use-Tool Agent 在独立上下文中运行，不共享 Evolve Agent 的 `messages`。只依赖传入的 `task_background`、`task_description` 和 `tool_list`，完成后返回 `final answer`。

**工作流程**：
1. 该 Agent 首先尝试**直接使用现有工具**完成任务，即通过自身的大模型推理和工具调用循环，顺序或并行调用一个或多个工具（如 `execute_bash`、`fetch`、`filesystem` 等），像普通 MetaAgent 一样完成单步或多步操作。
2. 如果任务只需要简单、线性的工具调用，Use-Tool Agent 直接执行并返回结果。
3. **如果任务需要复杂的工具编排**（例如多个工具之间有条件分支、循环、数据依赖），或者需要编写自定义代码才能完成，则 Use-Tool Agent 会进一步 `delegate` 给 Coding Agent，让 Coding Agent 生成可复用的脚本或编排逻辑，然后执行该脚本完成最终任务。
4. 最终返回结构化结果（例如 `{ "success": true, "data": ... }`）。

**输入**：`task-background`、`task-requirements`、`tools-list`（通常只给与任务最相关的工具，由主循环筛选）。

**输出**：结构化结果。

**与 Coding Agent 的关系**：Use-Tool Agent 可调用 Coding Agent 作为“复杂任务”的解决者，但 Coding Agent 专注于生成和注册新工具，而 Use-Tool Agent 负责整体任务的成功交付。

#### 2.1.4 Find-Tools Agent

**职责**：当当前工具箱无法满足需求时，自动发现现成的工具并注册。**Find-Tools 只负责发现和注册工具，不执行业务任务**（如文件转换、数据处理等），完成后返回 final answer 给 Evolve Agent。

**工作流程**：
1. 分析任务描述中明确指出的缺失能力（例如”需要访问 PostgreSQL 数据库”），以及通过自身推理判断的隐含缺失。
2. 使用网络搜索工具（`google_search`、`fetch`）搜索网络资源（如 GitHub、MCP 市场、公共 API 文档、SKILL 市场）。
3. 评估工具质量（下载量、更新时间、文档完整性），必要时用 `ask_user` 与用户确认选择。
4. 如果找到现成的工具（MCP 服务、HTTP API、命令行工具、Skill 模块），则调用对应的注册工具（`tool_register_http`、`tool_register_mcp`、`tool_register_cli` 或 `tool_register_skill`）进行注册。
5. 返回 final answer，列出成功注册的工具名及用途，或失败原因。**不执行业务任务**。

**工具集**：从主循环 `delegate` 的 `tools` 参数中获取，与 Use-Tool Agent 一致。Evolve Agent 在 delegate to Find-Tools 时，应在 `tools` 参数中包含网络搜索工具（`google_search`、`fetch`）、注册工具（`tool_register_cli`、`tool_register_mcp` 等）以及 `execute_bash` 和 `ask_user`。

**与 Evolve Agent 的协作**：Find-Tools 返回后，Evolve Agent 结合新注册的工具再次 delegate to Use-Tool 执行完整工具链。

#### 2.1.5 Coding Agent

**职责**：根据自然语言描述生成可复用的 skill（Python 脚本或 bash 脚本），并注册为新工具。

**当前状态**：尚未实现。Use-Tool Agent 遇到复杂编排需求时，可使用 `execute_bash` 执行 Python 脚本作为替代方案。

**安全机制**：待设计

**输出**：新工具的名称、描述以及调用方式（可执行命令或函数入口）。

### 2.2 工具箱设计

**工具选择原则**：优先使用外部成熟工具（如搜索使用 `serper-search` MCP、HTTP 请求使用 `fetch` function、中文搜索使用 `baidu-search` skill），避免重复造轮子。只有确认无合适第三方工具时才自行实现。

工具的范围（scope）分为三类：
- **builtin**：框架内置，不可删除、不可覆盖，随框架更新而更新。
- **default**：框架提供但可被用户同名的 user 工具覆盖，用户可手动重置。
- **user**：用户动态注册，优先级最高，可覆盖 builtin 和 default（需显式允许）。

工具的类型（type）包括：bash、cli、mcp、http、skill、delegate、function。其中：
- `bash`：执行 shell 命令（受安全限制）。
- `cli`：调用本地命令行程序（非 shell 内建），通过分析 `--help` 获得参数 schema，通常为长期运行的子进程。CLI 工具注册时需要提供参数 schema，`cli_executor` 负责执行并传递参数。
- `mcp`：遵循 Model Context Protocol 的外部服务。
- `http`：直接调用 HTTP API，支持 OpenAPI/Swagger 自动发现。
- `skill`：技能工具。与其他工具不同，skill 不一定有严格的参数 schema，而是携带一份 **SKILL.md 文档**（Markdown 格式），描述该技能的使用方法、步骤流程、提示词模板或指导性原则。skill 的加载方式有两种：
  - **普通技能**：SKILL.md 内容被注入到工具的 `description` 字段（或单独的 `documentation` 字段），模型在调用前可阅读该文档理解如何使用。此类技能通常用于封装多步骤操作指南或外部知识。
  - **指导性提示词注入**：注册时标记 `inject_to_system_prompt: true`，则 SKILL.md 内容会被**追加到当前会话的系统提示词中**，持续生效，用于改变 Agent 的整体行为模式（例如“始终使用中文回答”、“优先搜索本地文件”等元指令）。这类 skill 也会同时保留为可调用工具，以便在需要时重新加载或更新。
- `delegate`：调用子 Agent 的特殊工具（主循环拥有此工具，其他 Agent 视情况可拥有）。

**初始工具箱**（builtin 和 default）：

| 工具名 | 范围 | 类型 | 说明 |
|--------|------|------|------|
| `delegate` | builtin | delegate | 调用子 Agent（Use-Tool/Find-Tools/Coding） |
| `execute_bash` | builtin | bash | 执行 bash 命令，带超时、输出截断和路径白名单 |
| `fetch` | default | mcp | 发送 HTTP 请求，获取网页内容或调用 API（MCP 服务） |
| `tool_register_http` | builtin | function | 通过提供 HTTP 端点和 JSON schema 注册一个新工具 |
| `tool_register_code` | builtin | function | 通过上传 Python 代码动态注册一个新工具（当前版本暂不实现，预留接口） |
| `tool_register_skill` | builtin | function | 注册一个 skill 工具。参数包括：`name`、`description`、`skill_md_content`（SKILL.md 的完整文本），以及可选的 `inject_to_system_prompt`（默认为 false）。 |
| `tool_register_cli` | builtin | function | 通过提供命令行路径和参数 schema 注册一个 cli 工具 |
| `tool_register_mcp` | builtin | function | 通过提供 MCP 服务配置（URL、协议版本）注册一个 mcp 工具 |
| `tools_list` | builtin | function | 列出当前所有可用工具，按 scope 分组显示，支持过滤生命周期 |
| `context_summarizer` | builtin | function | 主动总结对话历史，压缩上下文（见 2.3 节） |
| `ask_user` | builtin | function | 向用户提问并等待回复。参数：`question`（字符串，必填）、`options`（字符串数组，可选）、`timeout_seconds`（整数，可选）。返回用户输入的内容。 |
| `memory-recall` | default | cli | 键值存储，持久化到 sqlite，支持向量检索 |
| `filesystem` | default | mcp | 读写文件、目录遍历，基于 MCP 文件系统协议 |
| `local-search` | default | bash | 使用 ripgrep 或 grep 搜索本地文本文件（自动检测可用命令） |
| `serper-search` | default | mcp | 通过 Serper API 进行 Google 搜索（需要 API key） |
| `baidu-search` | default | skill | 百度搜索的封装 skill，

至此，最小集合工具箱包含了上下文管理工具（`context_summarizer`）、永久记忆工具（`memory-recall`）、本地搜索工具（`local-search`）、文件操作工具（`filesystem`）、HTTP 请求工具（`fetch`）、网络搜索工具（`serper-search` + `baidu-search`）、用户交互工具（`ask_user`）以及完整的工具注册体系（支持 http/code/skill/cli/mcp 五种类型）。其中 skill 工具支持两种注入模式，灵活满足不同场景。

### 2.3 上下文管理（详细机制）

原则 #5：“Context will fill up; you need a way to make room.” 具体实现：

- **触发条件**：当消息历史中的 token 数量超过模型最大上下文窗口的 75% 时，自动触发 `context_summarizer`。也可由模型主动调用 `context_summarizer` 工具。
- **总结策略**：
  1. 保留最近 K 条消息（K 可配置，默认 20 条）不动。
  2. 将更早的消息按“重要性评分”筛选。重要性评分基于：消息中是否包含工具调用结果、是否被用户引用/加星、是否包含错误信息、以及模型对消息的注意力权重（模拟）。
  3. 将重要性较低的消息丢弃；对剩余早期消息，调用大模型生成一段摘要（200-300 字），插入到上下文开头。
  4. 摘要中需保留：用户原始目标、已经完成的步骤、尚未解决的问题、重要的中间结果。

### 2.4 自我修复、自我更新与自我迭代

**待设计**

### 2.5 永久记忆的具体机制

- 每个 QA 对（用户输入 + 最终回答）存入 SQLite，支持键值存储和向量相似检索（用于长期记忆的自动召回）。
- **向量嵌入**：支持 sentence-transformer 和 llama-cpp 两种嵌入模型，通过配置切换。
- 检索方式：精确 key 匹配或向量相似度（余弦相似度）阈值 0.7 以上自动召回，并注入到 system prompt。

### 2.6 日志管理

整个运行日志遵守 qd-agents 的日志方案。

## 三、总结

Evolve Agent 是一个极简但可自主进化的智能体框架。它摒弃了过度的人类编排，将感知、推理和行动的能力完全交给大模型，同时提供最小但完备的工具集。主要设计亮点：

- **无硬编码路由**：通过唯一的 `delegate` 工具将任务分解和子 Agent 调用的决策权完全交给模型。
- **自主进化**：内置 Find-Tools Agent（仅发现和注册现成工具）和 Coding Agent（待设计安全机制），支持动态扩展工具能力。
- **安全可控**：工具生命周期管理防止膨胀；代码生成的安全机制待设计。
- **上下文与记忆**：自动总结压缩上下文，永久记忆基于向量检索。
- **显式用户交互**：`ask_user` 工具提供结构化的澄清机制。
- **完整工具注册体系**：支持 HTTP、代码、Skill、CLI、MCP 五种类型的工具注册；其中 Skill 工具支持两种注入模式（普通文档注入和系统提示词注入），且 Find-Tools Agent 在调用时要求明确描述工具依赖，确保精准发现。

每个用户可以根据使用习惯进化出自己独特的工具集和配置，同时支持快照、重置和冻结，保证系统的可控性和可复现性。

## 四、配置管理
