从对话到自动化流程：一个准确、稳定、可靠的智能体系统设计文档
版本：1.1（修订版）
日期：2026-04-14
作者：AI 系统设计团队

1. 背景与目标
1.1 业务需求
设计并开发一个智能体系统，能够根据会话历史和当前用户提示词，自动生成可执行的自动化处理流程。该流程必须由程序准确、稳定、可靠地运行，满足生产环境要求。

1.2 核心挑战
任务复杂多变，可能包含多步骤、条件分支、循环、错误处理等逻辑。

需要整合大量预定义工具（可能成百上千个），每个工具都有明确的输入输出和安全约束。

必须保证运行时确定性和可审计性，避免大模型的随机性导致流程不可预测。

1.3 关键原则
工具是基础设施，由开发团队预先构建、测试和审计。运行时智能体只选择与编排已有工具，绝不动态创建或修改工具。

- 大模型用于意图理解、工具检索和流程规划，不用于实际执行底层逻辑。
- 大模型用于工具调用，统一使用tool calling这个事实的工业标准

执行引擎完全确定性，生成后的流程固化运行，不再依赖大模型。

2. 现有方案评估与不足
方案	优点	不足
单步 Tool Calling（如 OpenAI function calling）	简单、标准	无法处理多步骤、条件循环等复杂逻辑
ReAct 动态编排	灵活，适应未知任务	多次 LLM 调用，成本高，结果不确定
Plan-and-Execute (DAG)	执行图确定，可观测	表达能力受限（条件/循环需特殊节点），且仍可能依赖运行时 LLM
Coding Agent 生成代码	理论图灵完备，表达力强	代码不可靠、难复用、浪费 token，已被实践证实不适合生产
Skill 自然语言流程	语义清晰	执行仍依赖大模型，不确定性未解决
DSL/宏语言	可控	需设计新语法，LLM 生成和解析成本高
结论：需要一种结合 LLM 规划能力与确定性执行引擎的混合架构，且必须解决大规模工具集的管理问题。

3. 核心设计理念
3.1 确定性执行优先
任何由 LLM 生成的流程，最终必须转化为确定性执行单元（DAG、状态机、组件配置）。

运行时不再调用 LLM，仅由执行引擎驱动。

3.2 工具注册中心 (Tool Registry) 管理大规模工具集
所有原子工具（weather, email, db query, http request 等）统一注册到本地 Tool Registry。

注册信息包含：名称、描述、输入输出 JSON Schema、调用端点、安全标签等。

3.3 两阶段 LLM 调用（针对超大工具集）
第一阶段：不加载任何原子工具，只加载 direct（直接回答）和 find_tools（工具检索）两个元工具。

第二阶段：加载第一阶段筛选出的原子工具，以及 coding_tool_use（复杂逻辑后备）和 step_down（降级处理）。

* 若工具集规模可控（<50），可合并为单阶段调用。

4. 系统架构
4.1 总体模块

[用户输入] → [上下文管理器] → [两阶段调度器]
                                   ├─ 第一阶段 (轻量路由)
                                   │     ├─ direct
                                   │     └─ find_tools → [Tool Registry]
                                   │
                                   └─ 第二阶段 (规划器)
                                         ├─ coding_tool_use
                                         └─ step-down (后备)

[确定性执行（Python）] → [结果] → [回复生成]

4.2 模块说明
4.2.1 上下文管理器
维护会话历史，裁剪过长的历史。

注入系统提示词（角色、安全边界等）。

4.2.2 两阶段调度器

第一阶段：

调用 LLM，仅提供 direct 和 find_tools 两个工具。

若 LLM 选择 direct，直接生成最终回复。

若选择 find_tools，解析参数（如查询关键词），调用 Tool Registry 检索工具列表。

**元工具定义：**

```json
{
  "name": "find_tools",
  "description": "根据用户需求检索相关工具",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "描述用户需要什么功能的自然语言，例如：获取天气、发送邮件"
      }
    },
    "required": ["query"]
  },
  "returns": {
    "type": "array",
    "items": {
      "type": "object",
      "properties": {
        "tool_id": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "similarity_score": {"type": "number"}
      }
    }
  }
}
```

第二阶段：

将检索到的工具定义（完整 Schema）与用户问题、会话历史一同送入 LLM。

提供 coding_tool_use 和 step_down 作为元工具。

可以直接使用基础工具的，则tool calling

复杂逻辑：coding_tool_use，生成python的流程代码执行

无合适工具或其他情景：step_down (降级处理)

**coding_tool_use 定义与约束：**

```json
{
  "name": "coding_tool_use",
  "description": "生成Python代码来编排多个工具的执行（支持条件、循环等复杂逻辑）",
  "parameters": {
    "type": "object",
    "properties": {
      "code": {
        "type": "string",
        "description": "Python代码字符串，仅允许调用已注册工具"
      }
    },
    "required": ["code"]
  }
}
```

**代码执行安全约束：**
- 禁止使用：`eval()`、`exec()`、`compile()`
- 禁止使用：`os`、`subprocess`、`sys` 等系统模块
- 禁止文件 I/O 操作（除工具内部封装的）
- 禁止网络请求（除工具内部封装的）
- 仅允许调用通过 Tool Registry 注册的工具
- 代码必须是纯函数式，无副作用（除工具调用本身）

**step_down 定义与触发条件：**

```json
{
  "name": "step_down",
  "description": "当无法通过工具完成任务时，降级为人工友好的回复",
  "parameters": {
    "type": "object",
    "properties": {
      "reason": {
        "type": "string",
        "enum": ["no_matching_tools", "too_complex", "safety_concern", "user_confirmation_required"]
      },
      "message": {
        "type": "string",
        "description": "给用户的解释信息"
      }
    },
    "required": ["reason", "message"]
  }
}
```

**降级策略：**
- `no_matching_tools` → 告知用户暂无此功能，建议替代方案
- `too_complex` → 建议用户拆分为多个简单步骤
- `safety_concern` → 需要用户明确确认后再执行
- `user_confirmation_required` → 列出将要执行的操作，等待用户确认

4.2.3 Tool Registry

嵌入式数据库SQLite + 向量索引。

**向量检索技术栈：**
- Embedding 模型：hf_KimChen_bge-m3-q4_k_m.gguf（GGUF 量化格式）
- 向量数据库：[待定：ChromaDB / FAISS / Qdrant / sqlite-vss]
- 相似度算法：余弦相似度

提供注册、检索、更新工具。

支持按类别、关键词、向量相似度检索。

每个工具包含：

```json
{
  "id": "weather.get",
  "name": "get_weather",
  "description": "获取指定城市的当前天气",
  "parameters": {
    "type": "object",
    "properties": {
      "city": {"type": "string"}
    },
    "required": ["city"]
  },
  "execution": {
    "type": "http",
    "endpoint": "http://weather-service/current",
    "method": "GET"
  },
  "security": ["readonly", "no_side_effects"],
  "metadata": {
    "category": "utilities",
    "tags": ["weather", "forecast"],
    "version": "1.0.0"
  }
}
```

5. 工作流示例
用户输入：
"查询今天北京的天气，如果温度超过30度，给我发一封邮件提醒。"

5.1 第一阶段
系统调用 LLM（仅 direct / find_tools）。

LLM 输出：find_tools，参数 "获取天气和发送邮件"。

Tool Registry 检索出 weather.get 和 email.send 两个工具。

```json
{
  "tool_calls": [
    {
      "id": "call_123",
      "type": "function",
      "function": {
        "name": "find_tools",
        "arguments": "{\"query\": \"获取天气和发送邮件\"}"
      }
    }
  ]
}
```

Tool Registry 返回：
```json
[
  {
    "tool_id": "weather.get",
    "name": "get_weather",
    "description": "获取指定城市的当前天气",
    "similarity_score": 0.95
  },
  {
    "tool_id": "email.send",
    "name": "send_email",
    "description": "发送邮件到指定地址",
    "similarity_score": 0.88
  }
]
```

5.2 第二阶段
加载检索到的工具
将两个工具的定义 + 用户问题送入 LLM。

LLM 判断需要条件判断，选择 coding_tool_use：

```json
{
  "tool_calls": [
    {
      "id": "call_456",
      "type": "function",
      "function": {
        "name": "coding_tool_use",
        "arguments": {
          "code": "weather = get_weather(city=\"北京\")\nif weather.temperature > 30:\n    send_email(to=\"user@example.com\", subject=\"高温提醒\", body=f\"北京今天温度{weather.temperature}度，超过30度了！\")\nreturn weather"
        }
      }
    }
  ]
}
```

代码经安全检查后执行，记录执行轨迹。

6. 两阶段调用的深度讨论
6.1 何时需要两阶段？
工具总数 > 100 且每个工具定义长度 > 500 字符，导致单次 prompt 无法容纳所有工具。

希望完全避免手动分类或关键词匹配，完全由 LLM 驱动工具发现。

6.2 两阶段的优缺点
优点	缺点
避免上下文过载	两次 LLM 调用增加延迟和成本
工具检索由模型自适应完成	第一阶段 find_tools 的检索质量依赖模型
架构统一（无需外部检索系统）	状态管理复杂（需传递筛选结果）

7. 工具注册中心 (Tool Registry) 实现要点
7.1 存储模型
关系表：tools(id, name, description, schema_json, execution_config, security_tags, metadata_json, created_at, updated_at)

向量索引：对 name + description 做 embedding，用于语义检索。

7.2 集成方式

或嵌入式库（ SQLite + sqlite-vss）。

提供 Python SDK，供智能体系统调用。

注册时记录提交人、审核状态。

8. 确定性执行设计补充

8.1 可靠性保障
每个tool执行超时（例如 30s）。

异常捕获。

8.2 可观测性

**执行轨迹 Schema：**

```json
{
  "trace_id": "uuid",
  "session_id": "uuid",
  "timestamp": "2026-04-14T09:00:00Z",
  "user_input": "查询今天北京的天气...",
  "phase_one": {
    "tool_choice": "find_tools",
    "found_tools": ["weather.get", "email.send"],
    "latency_ms": 1200
  },
  "phase_two": {
    "tool_choice": "coding_tool_use",
    "generated_code": "...",
    "latency_ms": 2500
  },
  "execution": {
    "steps": [
      {
        "step": 1,
        "tool": "weather.get",
        "input": {"city": "北京"},
        "output": {"temperature": 32, "...": "..."},
        "start_time": "2026-04-14T09:00:03Z",
        "end_time": "2026-04-14T09:00:04Z",
        "duration_ms": 800,
        "status": "success"
      },
      {
        "step": 2,
        "tool": "email.send",
        "input": {"to": "user@example.com", "...": "..."},
        "output": {"status": "sent"},
        "start_time": "2026-04-14T09:00:04Z",
        "end_time": "2026-04-14T09:00:05Z",
        "duration_ms": 600,
        "status": "success"
      }
    ],
    "total_duration_ms": 2100,
    "final_status": "completed"
  }
}
```

记录每个步骤的开始、结束、耗时、输入输出。

生成执行轨迹 JSON，用于调试和审计。

9. 安全与稳定性设计

9.1 工具调用安全

- 所有原子工具通过 Registry 注册，生成的Python代码仅允许调用已注册工具。
- 工具按安全标签分级：`readonly`、`readwrite`、`destructive`
- `destructive` 级别的工具必须经过用户确认才能执行

9.2 代码执行沙箱

- 使用 RestrictedPython 或类似库限制 Python 语法
- 禁用危险模块和函数
- 执行超时控制（默认 30 秒）
- 内存使用限制

9.3 审计与日志

- 所有工具调用记录完整输入输出
- 执行轨迹持久化存储
- 支持按用户、时间、工具类型查询历史

10. 非功能需求（暂定）

10.1 性能目标

- 端到端响应时间（P95）：< 5 秒
- 第一阶段 LLM 调用：< 2 秒
- 第二阶段 LLM 调用：< 3 秒
- 工具执行超时：30 秒

10.2 规模目标

- 支持注册工具数：≥ 1000 个
- 并发会话数：≥ 100
- 向量检索延迟：< 100ms

10.3 可靠性目标

- 系统可用性：≥ 99.5%
- 工具调用成功率：≥ 99%
- 数据持久化：所有执行轨迹保留 30 天
