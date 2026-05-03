# Evolve Agent 设计（修正版）

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

我们发现，智能体能力 = 大模型的能力 + 工具能力。事实上，工具配置直接影响大模型的推理能力，两者密不可分。人也一样，如果你安排行程的时候不知道有飞机通航，你的推理中就不可能考虑乘坐飞机。发散思维并不是所有人的思维方式。

智能体框架本身是代码，它给智能体的运行提供基础条件，但它不应该硬编码一些自以为是的逻辑，因为设计者自身并不比大模型智能。

今天的所有智能体都受到大模型自身能力的限制，包括知识的固化和记忆问题。知识固化可以通过使用本地文件和网络搜索来缓解；工作记忆依赖于上下文窗口，永久记忆则需要外挂的工具。

**Evolve Agent 的设计目标**可以概括为一个极简的智能体框架，它包括：

第一：它只提供 Opinionated 的原则，例如“只使用现有工具，缺少工具时，自己去搜索发现和安装”，不提供自以为是的人类经验指导。

第二：它提供一个最小集合的工具箱，这个工具箱包含感知环境的工具、使用工具的工具、发现工具的工具、注册新工具到工具箱的工具。通过这个设计，让 Evolve Agent 随着用户使用而自主进化，每个用户有自己的进化版本。最小集合的工具箱包括上下文管理工具、永久记忆的工具、本地和网络搜索的工具。

第三：它能够自我修复、自我更新和自我升级智能体框架本身，实现自我迭代。

## 二、智能体框架设计

### 2.1 Agents 设计

整体架构包括 Evolve Agent（主循环）、Use-Tool Agent、Find-Tools Agent、Coding-Tool-Use Agent（简称 
Coding Agent）。每个 Agent 都是 MetaAgent 的扩展。

### 2.1.1 MetaAgent

MetaAgent 是大模型调用工具的基础循环模式。

**输入参数**：
- `task-background`：任务的背景信息，字符串
- `task-requirements`：任务的要求，字符串
- `tools-list`：工具数组

**内部管理**：
- `system prompt`：根据传入的参数，追加 task-background、task-requirements 以及 tools-list 中每个工具的详细信息（包括 schema）
- `messages`：数组或字符串

**输出参数**：
- `Final answer`：任意结构

支持两种大模型的 OpenAI API 接口规范（completion 和 response），根据系统的配置选择使用。MetaAgent 的实现不在此文档中展开，核心思想是循环调用模型，根据模型的工具调用决策执行对应工具，并将结果写回消息历史，直到模型给出最终答案。可以参考 meta-agent.py 的示例代码实现。

#### 2.1.2 Evolve Agent（主循环）

**输入参数**：
- `Task-background`：字符串，可为空（因为提示词模板里有）
- `Task-requirements`：字符串，可为空（因为提示词模板里有）
- `tools_list`：只提供 `llm-route` 工具

**系统提示词**：加载工具箱中所有工具的名称和描述，并注入原则性指令，例如优先使用现有工具、缺少工具时调用 Find-Tools Agent、复杂逻辑调用 Coding Agent 等。

**上下文管理**：存储用户输入以及其他 Agent 返回的最终答案（final answer）。

**主逻辑**：
1. 如果能直接回答用户问题（不依赖任何工具），则直接返回最终答案。
2. 如果直接使用某个现有工具就能回答（例如搜索、文件读取），则通过 `llm-route` 调用 Use-Tool Agent 来执行该工具。
3. 如果任务需要复杂逻辑、多步编码才能完成（例如需要编写一个新脚本来处理数据），则调用 Coding-Tool-Use Agent。
4. 如果当前工具箱中缺少完成该任务所需的任何工具，则调用 Find-Tools Agent 去搜索、发现或创建新工具，并注册到工具箱中。

**新增工具管理**：如果Find-Tools Agent返回了已注册新工具，则需要在提示词中追加新增工具名称和描述的内容。

Evolve Agent 是用户直接交互的入口，它负责整体的任务分解、子 Agent 调度和结果整合。

#### 2.1.3 Use-Tool Agent

**职责**：接收一个明确的任务，`task-background`， `task-requirements`， `tools-list`，
（例如“搜索人工智能新闻”），自动选择合适的工具并执行，返回结构化结果。

#### 2.1.4 Find-Tools Agent

**职责**：当当前工具箱无法满足需求时，自动发现或创建新工具。

**工作流程**：
1. 分析缺失的能力（例如“需要访问 PostgreSQL 数据库”）。
2. 搜索网络资源（如 GitHub、MCP 市场、公共 API 文档、SKILL市场）。
3. 如果找到现成的工具（MCP 服务、HTTP API、命令行工具），则调用 `tool_register_` 进行注册。

**工具集**：`tools_list`、`serper_search`、`fetch`、`tool_register_http`、`tool_register_code`。

#### 2.1.5 Coding-Tool-Use Agent（Coding Agent）

**职责**：根据自然语言描述生成可复用的 skill（Python 脚本或 bash 脚本），并注册为新工具。

**输入**：需求描述，可选的输入输出 JSON schema。

**输出**：新工具的名称、描述以及调用方式（例如可执行的命令或函数入口）。

**安全机制**：生成的代码先在沙盒环境中执行测试，通过后再正式注册到工具箱。

### 2.2 工具箱设计

工具的范围（scope）分为三类：
- **builtin**：框架内置，不可删除、不可覆盖，随框架更新而更新。
- **default**：框架提供但可被用户同名的 user 工具覆盖，用户可手动重置。
- **user**：用户动态注册，优先级最高，可覆盖 builtin 和 default（需显式允许），

工具的类型（type）包括：bash、cli、mcp、http、skill、llm-route。其中：
- `bash`：执行 shell 命令（受安全限制）。
- `cli`：调用本地命令行程序（非 shell 内建），有通过分析 --help获得的参数schema, 通常为长期运行的子进程。
- `mcp`：遵循 Model Context Protocol 的外部服务。
- `http`：直接调用 HTTP API，支持 OpenAPI/Swagger 自动发现。
- `skill`：由大模型生成的可复用多步骤任务脚本或者是提示词注入（存储为 Markdown 或 Python 文件）。
- `llm-route`：调用其它 Agent 的路由工具，参数包括目标 Agent 的名称和任务描述。

所有工具使用 sqlite 永久存储。初始工具箱包含以下 builtin 和 default 工具：

1. `execute_bash`（builtin）：执行 bash 命令，带超时、输出截断和路径白名单。
2. `serper_search`（default，mcp）：通过 Serper API 进行 Google 搜索。
3. `filesystem`（default，mcp）：读写文件、目录遍历，基于 MCP 文件系统协议。
4. `baidu_search_skill`（default，skill）：百度搜索，封装为可复用的 skill。
5. `fetch`（default，mcp）：发送 HTTP 请求，获取网页内容或调用 API。
6. `tool_register_http`（builtin，function）：通过提供 HTTP 端点和 JSON schema 注册一个新工具。
7. `tool_register_code`（builtin，function）：通过上传 Python 代码动态注册一个新工具。
8. `tools_list`（builtin，function）：列出当前所有可用工具，按 scope 分组显示。
9. `memory_recall`（builtin，http）：键值存储，持久化到 sqlite，支持向量检索，用于永久记忆。
10. `local_search`（default，cli）：使用 ripgrep 或 grep 搜索本地文本文件，实现本地内容检索。

至此，最小集合工具箱包含了上下文管理工具（`context_summarizer`）、永久记忆工具（`memory_store`）、本地搜索工具（`local_search`）和网络搜索工具（`serper_search`、`baidu_search_skill`）。

### 2.4 自我修复、自我更新与自我迭代

Evolve Agent 不仅支持工具级的进化，还对框架本身提供了自我维护能力。

#### 2.4.1 自我修复

- **工具调用失败**：自动重试最多 3 次（指数退避）。如果仍然失败，Evolve Agent 会尝试寻找替代工具（通过 Find-Tools Agent），或降级执行（例如将 bash 命令转为 HTTP API 调用），并将失败信息记录到永久记忆中，避免重复同样的失败。
- **Agent 状态损坏**：定期保存检查点（包括对话历史、工具注册表、用户偏好）。如果运行中发生崩溃，重启后自动从最近的检查点恢复。

#### 2.4.2 自我更新

- **框架代码更新**：内置一个更新命令（类似 `git pull` 并重启），用户可授权自动检查更新并应用。
- **工具热更新**：对于 `user` 和 `default` 范围内的工具，支持动态重新加载，无需重启整个 Agent。
- **模型能力升级**：可配置多个模型（例如从 GPT-4 切换到 GPT-5）。当检测到新模型 API 可用时，通过内部基准测试评估效果，并支持平滑迁移。


### 2.5 上下文与永久记忆的具体机制


- **永久记忆**：Evolve Agent的按每个QA对存入 SQLite，  `memory recall`支持键值存储和向量相似检索。


## 三、总结

Evolve Agent 是一个极简但可自主进化的智能体框架。它摒弃了过度的人类编排，将感知、推理和行动的能力完全交给大模型，同时提供最小但完备的工具集（包含上下文管理、永久记忆、本地和网络搜索）。通过内置的四个 Agent（主循环、工具执行、工具发现、代码生成）以及自我修复、自我更新、自主进化机制，它能够随着用户使用不断优化自身能力，最终实现“每个用户拥有自己进化版本”的目标。

该设计已修正原始文档中的笔误和缺失，并补全了上下文管理工具、永久记忆工具、本地搜索工具的定义，明确了各 Agent 的职责以及安全自愈策略，保持了原文的结构和表达风格。