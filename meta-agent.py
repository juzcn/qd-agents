import json
from openai import OpenAI
from typing import List, Dict, Any, Union, Optional, Callable

class MetaAgent:
    """
    自主工具调用智能体。
    自动处理模型发起的工具调用请求，直到模型生成最终答案。
    """

    def __init__(
        self,
        system_prompt: str,
        tools: List[Dict[str, Any]],
        tool_implementations: Dict[str, Callable],
        model: str = "gpt-4o",
        max_iterations: int = 10,
        temperature: float = 1.0,
    ):
        """
        初始化 MetaAgent。

        Args:
            system_prompt: 系统提示词，定义智能体行为。
            tools: OpenAI 格式的工具定义列表。
            tool_implementations: 工具名称到实际执行函数的映射。
            model: 使用的模型名称，默认 "gpt-4o"。
            max_iterations: 最大循环次数，防止无限循环。
            temperature: 模型温度参数。
        """
        self.system_prompt = system_prompt
        self.tools = tools
        self.tool_implementations = tool_implementations
        self.model = model
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.client = OpenAI()  # 假设环境变量已设置 OPENAI_API_KEY

    def run(
        self,
        input_messages: Union[str, List[Dict[str, str]]]
    ) -> Any:
        """
        执行智能体循环，返回最终答案。

        Args:
            input_messages: 字符串（转为单条user消息）或消息列表。

        Returns:
            最终答案。可以是文本字符串，也可以是解析后的JSON对象。
        """
        # 构建消息历史
        messages = [{"role": "system", "content": self.system_prompt}]

        if isinstance(input_messages, str):
            messages.append({"role": "user", "content": input_messages})
        else:
            messages.extend(input_messages)  # 假设已是正确格式

        for iteration in range(self.max_iterations):
            # 调用模型
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self.tools,
                tool_choice="auto",
                temperature=self.temperature,
            )

            assistant_msg = response.choices[0].message
            messages.append(assistant_msg.model_dump())  # 保存助手消息

            # 处理工具调用
            if assistant_msg.tool_calls:
                for tool_call in assistant_msg.tool_calls:
                    func_name = tool_call.function.name
                    func_args = json.loads(tool_call.function.arguments)

                    # 执行对应工具
                    if func_name in self.tool_implementations:
                        result = self.tool_implementations[func_name](**func_args)
                    else:
                        result = f"错误：未找到工具 '{func_name}'"

                    # 添加工具结果消息
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": str(result),  # 工具结果必须是字符串
                    })
                # 工具调用完成后，继续下一轮循环让模型处理结果
                continue

            # 没有工具调用 -> 最终答案
            final_answer = assistant_msg.content

            # 如果最终答案是 JSON 字符串，可尝试解析（按需）
            # 若希望返回结构化的任意对象，可在此转换
            if final_answer is None:
                # 某些模型可能返回 None（比如拒绝回答），可根据需求处理
                return None
            return final_answer

        raise RuntimeError(f"达到最大循环次数 {self.max_iterations}，未得到最终答案。")

# ================= 示例使用 =================
if __name__ == "__main__":
    # 1. 定义工具函数（实际业务逻辑）
    def get_weather(city: str) -> str:
        return f"{city} 当前天气：晴，24°C"

    def calculate(expression: str) -> str:
        """简单的计算器"""
        try:
            result = eval(expression)
            return str(result)
        except Exception as e:
            return f"计算错误: {e}"

    # 2. 定义工具元数据（OpenAI 格式）
    tools_def = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "查询指定城市的天气",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "城市名称"}
                    },
                    "required": ["city"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "calculate",
                "description": "计算数学表达式",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {"type": "string", "description": "数学表达式，如 '2+3*4'"}
                    },
                    "required": ["expression"],
                },
            },
        },
    ]

    # 3. 创建 Agent
    agent = MetaAgent(
        system_prompt="你是一个智能助手，可以调用工具获取天气或进行计算。",
        tools=tools_def,
        tool_implementations={
            "get_weather": get_weather,
            "calculate": calculate,
        },
        model="gpt-4o",
        max_iterations=5,
    )

    # 4. 执行（字符串输入）
    answer = agent.run("北京天气如何？另外，帮我算一下 123 * 456 等于多少？")
    print("最终答案:", answer)

    # 也可以传入消息列表（多轮）
    multi_turn = [
        {"role": "user", "content": "上海天气怎么样？"},
        {"role": "assistant", "content": "稍等，我查询一下。"},
        {"role": "user", "content": "顺便帮我计算 2+2。"}
    ]
    answer2 = agent.run(multi_turn)
    print("多轮对话答案:", answer2)