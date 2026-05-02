我相信以下的哲学格言：

- "Agency — the ability to perceive, reason, and act — comes from model training, not from external code orchestration."

"What they build is a Rube Goldberg machine — an over-engineered, brittle pipeline of procedural rules, with an LLM wedged in as a glorified text-completion node. That is not an agent. That is a shell script with delusions of grandeur."

- One loop & Bash is all you need

- Adding a tool means adding new capacity

- An agent without a plan drifts

- Context will fill up; you need a way to make room

- Run slow operations in the background; the agent keeps thinking"

- "When the task is too big for one, delegate to teammates"

evolve agent 设计

能够感知环境，特别是自己的能力和局限，能力主要包括能使用的工具，局限性是指上下文窗口的约束，需要自主管理。

这个Agent初期只有最基本的能力，通过与用户的交互逐渐成长，进化。

它需要通过改进自己的代码，或者通过更新或增加工具，实现进化。

参考下面的代码设计基础Agent.

def agent_loop(messages):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM,
            messages=messages, tools=TOOLS,
        )
        messages.append({"role": "assistant",
                         "content": response.content})

        if response.stop_reason != "tool_use":
            return  # 模型决定停止，返回

        results = []
        for block in response.content:
            if block.type == "tool_use":
                output = TOOL_HANDLERS[block.name](**block.input)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
        messages.append({"role": "user", "content": results})

当前任务是设计一个minimal agent，然后靠它自己演化