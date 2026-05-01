"""
LLM 客户端管理器

负责 LLM 客户端和代理的初始化、管理和切换。
"""

import logging
from contextlib import nullcontext
from typing import Optional, Any
from pathlib import Path

from rich.console import Console

from qd_agents.config import Config, load_config
from qd_agents.llm import LLMClient
from qd_agents.registry import ToolRegistry
from qd_agents.prompts import PromptLoader
from qd_agents.agent import QDAgent
from qd_agents.context import ContextManager

logger = logging.getLogger(__name__)


class LLMClientManager:
    """LLM 客户端和代理管理器"""

    def __init__(
        self,
        console: Console | None,
        config: Config,
        tool_registry: ToolRegistry,
        prompt_loader: Optional[PromptLoader],
        context_manager: ContextManager,
    ):
        self.console = console
        self.config = config
        self.tool_registry = tool_registry
        self.prompt_loader = prompt_loader
        self.context_manager = context_manager

        self.llm_client: Optional[LLMClient] = None
        self.agent: Optional[QDAgent] = None
        self.provider_name: Optional[str] = None
        self.provider_config: Optional[Any] = None

    def _print(self, message: str) -> None:
        """输出信息：日志始终记录，Console 可选"""
        logger.info(message)
        if self.console:
            self.console.print(message)

    def _status(self, message: str):
        """返回 status context：Console 可用时用 console.status，否则 nullcontext"""
        logger.info(message)
        if self.console:
            return self.console.status(message)
        return nullcontext()

    async def initialize(self, provider_name: str, model: Optional[str] = None) -> bool:
        self.provider_name = provider_name
        self.provider_config = self.config.llm.providers.get(provider_name)

        if not self.provider_config or not self.provider_config.api_key:
            self._print(f"错误: 未找到 {provider_name.upper()}_API_KEY")
            return False

        # 关闭旧的客户端
        if self.llm_client is not None:
            await self.llm_client.close()

        self._print(f"正在连接 {self.provider_config.base_url}...")

        model_names = self.provider_config.get_model_names()
        if model:
            model_names = [model]

        self.llm_client = LLMClient(
            api_key=self.provider_config.api_key,
            base_url=self.provider_config.base_url,
            model_names=model_names if model_names else None,
        )

        # 如果启用了自动发现且没有预定义模型，则发现模型
        if self.provider_config.auto_discover and not model_names:
            with self._status("正在发现可用模型..."):
                await self.llm_client.discover_models()
        elif not model_names:
            # 使用默认模型
            with self._status("加载默认模型列表..."):
                await self.llm_client.discover_models(top_k=0)

        # 创建代理
        self.agent = QDAgent(
            config=self.config,
            llm_client=self.llm_client,
            tool_registry=self.tool_registry,
            prompt_loader=self.prompt_loader,
            context_manager=self.context_manager,
        )

        with self._status("正在初始化 Agent..."):
            await self.agent.initialize()

        self._print(f"当前模型: {self.provider_name}/{self.llm_client.current_model}")
        return True

    def switch_model(self, model_name: str) -> bool:
        if self.llm_client is None:
            self._print("错误: LLM 客户端未初始化")
            return False

        return self.llm_client.switch_model(model_name)

    async def close(self):
        """关闭 LLM 客户端和代理"""
        # 先关闭代理（它会关闭MCP连接等资源）
        if self.agent is not None:
            try:
                await self.agent.close()
            except Exception as e:
                logger.warning(f"Error closing agent: {e}")
            finally:
                self.agent = None

        # 然后关闭LLM客户端
        if self.llm_client is not None:
            await self.llm_client.close()
            self.llm_client = None

        self.provider_name = None
        self.provider_config = None
