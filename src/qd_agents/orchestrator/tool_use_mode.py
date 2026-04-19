"""
工具调用模式调度器

支持单阶段工具调用，自动展开 MCP 工具集。
"""
from __future__ import annotations

import asyncio
import warnings
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import copy

from ..llm import LLMClient
from ..registry import ToolRegistry, Tool, ToolExecutionType, ToolMetadata
from ..prompts import PromptLoader
from ..context import ContextManager
from ..tools.executors.mcp import MCPToolExecutor


logger = logging.getLogger(__name__)








@dataclass
class OrchestrationResult:
    """调度结果"""
    trace_id: str
    user_input: str
    session_id: str | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    final_output: Any = None
    final_status: str = "pending"
    total_latency_ms: int = 0
    # OpenAI tool calling 标准流程字段
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] = field(default_factory=dict)
    needs_more_rounds: bool = False




class ToolUseModeOrchestrator:
    """
    工具调用模式调度器

    单阶段工具调用，自动展开 MCP 工具集。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        context_manager: ContextManager | None = None,
        prompt_loader: PromptLoader | None = None,
        tool_threshold: int = 50,
        execute_tool_callback: callable | None = None,
    ):
        """
        初始化工具调用模式调度器

        Args:
            llm_client: LLM 客户端
            tool_registry: 工具注册中心
            context_manager: 上下文管理器
            prompt_loader: 提示词加载器
            tool_threshold: 工具数量阈值
            execute_tool_callback: 工具执行回调函数，格式: async def callback(tool_name: str, tool_input: dict) -> Any
        """
        self.llm = llm_client
        self.registry = tool_registry
        self.prompts = prompt_loader
        self.context = context_manager or ContextManager(prompt_loader=prompt_loader)
        self.tool_threshold = tool_threshold
        self.execute_tool_callback = execute_tool_callback

        # MCP 工具缓存
        self._mcp_tools_cache: dict[str, list[Tool]] = {}  # server -> list of subtools
        self._mcp_executors_cache: dict[str, Any] = {}  # server -> MCPToolExecutor

        # 展开工具缓存
        self._expanded_tools_cache: list[Tool] | None = None
        self._openai_tools_cache: list[dict[str, Any]] | None = None

        # 内置元工具定义
        self._meta_tools = self._build_meta_tools()

    def _build_meta_tools(self) -> dict[str, dict[str, Any]]:
        """构建元工具定义（现在为空，使用标准工具调用）"""
        return {}

    def set_mcp_cache(self, mcp_tools_cache: dict[str, list[Tool]], mcp_executors_cache: dict[str, Any]) -> None:
        """设置MCP缓存（当上层已预加载MCP工具时使用）

        Args:
            mcp_tools_cache: MCP工具缓存 {server_key: [subtools]}
            mcp_executors_cache: MCP执行器缓存 {server_key: executor}
        """
        # 共享引用，不复制，由QDAgent统一管理生命周期
        self._mcp_tools_cache = mcp_tools_cache
        self._mcp_executors_cache = mcp_executors_cache
        logger.info(f"Set MCP cache from parent: {len(mcp_tools_cache)} servers, {sum(len(tools) for tools in mcp_tools_cache.values())} total tools")

    def set_expanded_tools_cache(self, expanded_tools: list[Tool], openai_tools: list[dict[str, Any]]) -> None:
        """设置展开工具缓存（当上层已计算展开工具时使用）

        Args:
            expanded_tools: 展开后的工具列表
            openai_tools: OpenAI格式的工具列表
        """
        self._expanded_tools_cache = expanded_tools.copy() if expanded_tools else None
        self._openai_tools_cache = openai_tools.copy() if openai_tools else None
        logger.info(f"Set expanded tools cache from parent: {len(expanded_tools) if expanded_tools else 0} expanded tools, {len(openai_tools) if openai_tools else 0} OpenAI tools")

    async def initialize(self, skip_mcp_preload: bool = False, skip_tool_caching: bool = False) -> None:
        """初始化调度器，预加载 MCP 工具

        Args:
            skip_mcp_preload: 如果为True，跳过MCP工具预加载（当上层已预加载时使用）
            skip_tool_caching: 如果为True，跳过工具缓存（当上层已缓存工具时使用）
        """
        logger.info("Initializing ToolUseModeOrchestrator...")
        try:
            if not skip_mcp_preload:
                # 预加载 MCP 工具（允许失败，只记录日志）
                await self._preload_mcp_tools()

            if not skip_tool_caching:
                # 缓存展开后的工具列表
                await self._cache_expanded_tools()
            elif self._expanded_tools_cache is None or self._openai_tools_cache is None:
                # 即使skip_tool_caching为True，但如果缓存为空，仍然需要缓存
                logger.warning("skip_tool_caching is True but expanded tools cache is empty, caching anyway")
                await self._cache_expanded_tools()
            else:
                logger.info("Skipping tool caching, using existing cache")

        except Exception as e:
            # MCP连接失败不应该阻止程序启动
            logger.warning(f"MCP tool preloading failed, but continuing initialization: {e}")
            # 尝试缓存基础工具列表（即使MCP连接失败）
            try:
                await self._cache_expanded_tools()
            except Exception as inner_e:
                logger.warning(f"Failed to cache tools: {inner_e}")

        logger.info("ToolUseModeOrchestrator initialized")

    async def close(self, skip_mcp_close: bool = False) -> None:
        """关闭调度器，终止所有 MCP 连接

        Args:
            skip_mcp_close: 如果为True，跳过MCP连接关闭（当上层已关闭时使用）
        """
        logger.info("Closing ToolUseModeOrchestrator...")

        if not skip_mcp_close:
            # 关闭所有 MCP 连接（如果缓存非空）
            if self._mcp_executors_cache:
                for server_key, executor in self._mcp_executors_cache.items():
                    try:
                        await executor.close()
                        logger.info(f"Closed MCP connection to server: {server_key}")
                    except Exception as e:
                        logger.error(f"Error closing MCP connection to server {server_key}: {e}")
            else:
                logger.info("MCP executors cache is empty, nothing to close")
        else:
            logger.info("Skipping MCP connection close (handled by parent)")

        # 清空缓存
        self._mcp_tools_cache.clear()
        self._mcp_executors_cache.clear()
        self._expanded_tools_cache = None
        self._openai_tools_cache = None
        logger.info("ToolUseModeOrchestrator closed")

    async def _preload_mcp_tools(self) -> None:
        """预加载 MCP 工具（会话开始时连接 MCP 服务器）"""
        logger.info("Preloading MCP tools...")

        # 获取所有 MCP 工具
        all_tools = self.registry.list_all()
        mcp_tools = [t for t in all_tools if t.execution.type == ToolExecutionType.MCP]

        if not mcp_tools:
            logger.info("No MCP tools found to preload")
            return

        # 并发连接所有 MCP 服务器
        tasks = []
        tool_configs = []

        for tool in mcp_tools:
            exec_config = tool.execution
            server_config = {
                'type': ToolExecutionType.MCP,
                'server': exec_config.server or "",
                'transport': exec_config.transport or "stdio",
                'command': exec_config.command,
                'args': exec_config.args,
                'endpoint': exec_config.endpoint,
                'headers': exec_config.headers,
                'timeout': exec_config.timeout,
                'tool': None,
            }
            # 创建异步任务
            tasks.append(self._get_mcp_server_tools(server_config, original_tool=tool))
            tool_configs.append((tool, server_config))

        # 并发执行所有连接任务
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 处理每个任务的结果
            for i, result in enumerate(results):
                tool, server_config = tool_configs[i]
                exec_config = tool.execution
                server_key = exec_config.server or ""

                if isinstance(result, Exception):
                    if isinstance(result, asyncio.CancelledError):
                        logger.warning(f"MCP server '{server_key}' connection cancelled, skipping")
                    else:
                        logger.error(f"Error preloading MCP server '{server_key}': {result}")
                    continue

                subtools, executor = result
                if subtools:
                    logger.info(f"Preloaded MCP server '{server_key}' with {len(subtools)} tools")
                else:
                    logger.warning(f"Failed to preload MCP server '{server_key}' (no tools returned)")

    async def _cache_expanded_tools(self) -> None:
        """缓存展开后的工具列表和OpenAI格式工具"""
        logger.info("Caching expanded tools...")

        # 获取所有工具
        all_tools = self.registry.list_all()

        # 展开 MCP 工具（使用缓存）
        expanded_tools = []
        for tool in all_tools:
            if tool.execution.type != ToolExecutionType.MCP:
                # 非 MCP 工具直接添加
                expanded_tools.append(tool)
                continue

            # 检查缓存中是否有该服务器的工具
            exec_config = tool.execution
            server_key = exec_config.server or ""
            if server_key in self._mcp_tools_cache:
                mcp_subtools = self._mcp_tools_cache[server_key]
                if mcp_subtools:
                    # 添加所有子工具
                    expanded_tools.extend(mcp_subtools)
                else:
                    # 缓存为空，保留原始工具
                    logger.warning(f"Cached MCP tools for server '{server_key}' is empty, keeping original tool")
                    expanded_tools.append(tool)
            else:
                # 缓存中没有该服务器的工具，保留原始工具
                logger.warning(f"MCP server '{server_key}' not in cache, keeping original tool")
                expanded_tools.append(tool)

        # 构建 OpenAPI 格式工具
        openai_tools = []
        # 优先添加 search.web 工具（如果可用）
        search_web = self.registry.get("search.web")
        if search_web:
            openai_tools.append(search_web.to_openai_function())
            # 从 expanded_tools 中移除 search.web 工具，避免重复
            expanded_tools = [t for t in expanded_tools if t.id != "search.web"]

        # 添加其他工具
        openai_tools.extend([t.to_openai_function() for t in expanded_tools])

        # 缓存结果
        self._expanded_tools_cache = expanded_tools
        self._openai_tools_cache = openai_tools

        logger.info(f"Cached {len(expanded_tools)} expanded tools and {len(openai_tools)} OpenAI tools")

    async def _get_mcp_server_tools(self, server_config: dict, original_tool: Tool | None = None) -> tuple[list[Tool], Any]:
        """
        获取 MCP 服务器的工具列表和执行器

        Args:
            server_config: MCP服务器配置字典，包含server, transport, command等字段
            original_tool: 原始工具对象（用于元数据继承）

        Returns:
            (工具列表, MCP执行器)
        """
        from ..tools.executors.mcp import MCPToolExecutor

        server_key = server_config.get('server', '')
        if not server_key:
            return [], None

        # 检查缓存
        if server_key in self._mcp_tools_cache:
            logger.debug(f"Using cached MCP tools for server: {server_key}")
            return self._mcp_tools_cache[server_key], self._mcp_executors_cache.get(server_key)

        try:
            # 创建 MCP 执行器
            executor = MCPToolExecutor(
                server=server_key,
                transport=server_config.get('transport', 'stdio'),
                command=server_config.get('command'),
                args=server_config.get('args', []),
                url=server_config.get('endpoint'),  # 复用 endpoint 作为 URL
                headers=server_config.get('headers', {}),
                timeout=server_config.get('timeout', 30),
            )

            # 连接到服务器（异步上下文管理器）
            # 注意：这里我们不使用 async with，因为我们要保持连接打开
            # 设置连接超时（使用配置的超时或默认5秒）
            connect_timeout = server_config.get('timeout', 5)
            try:
                await asyncio.wait_for(executor._ensure_connected(), timeout=connect_timeout)
            except asyncio.TimeoutError:
                logger.error(f"MCP server {server_key} connection timeout after {connect_timeout}s")
                try:
                    await executor.close()
                except Exception:
                    pass
                return [], None
            except asyncio.CancelledError:
                logger.warning(f"MCP server {server_key} connection cancelled")
                # 不重新抛出，直接返回空结果
                try:
                    await executor.close()
                except Exception:
                    pass
                return [], None
            except Exception as e:
                logger.error(f"MCP server {server_key} connection failed: {e}")
                try:
                    await executor.close()
                except Exception:
                    pass
                return [], None

            # 获取服务器提供的所有工具
            mcp_tools = executor.get_cached_tools()

            if not mcp_tools:
                logger.warning(f"MCP server {server_key} returned no tools")
                try:
                    await executor.close()
                except Exception:
                    pass
                return [], None

            # 为每个 MCP 工具创建独立的 Tool 对象
            subtools = []
            for mcp_tool_name, mcp_tool in mcp_tools.items():
                # 创建子工具 ID
                subtool_id = f"mcp.{server_key}.{mcp_tool_name}"

                # 提取参数 schema
                parameters = self._extract_mcp_tool_parameters(mcp_tool)

                # 创建 Tool 对象
                if original_tool and hasattr(original_tool, 'metadata'):
                    metadata = original_tool.metadata.model_copy(update={
                        'tags': original_tool.metadata.tags + [mcp_tool_name, 'mcp-subtool', server_key]
                    })
                else:
                    metadata = ToolMetadata(
                        category="mcp",
                        tags=[mcp_tool_name, 'mcp-subtool', server_key],
                    )

                # 创建执行配置，包含具体工具名
                exec_config = server_config.copy()
                exec_config['tool'] = mcp_tool_name

                subtool = Tool(
                    id=subtool_id,
                    name=mcp_tool_name,
                    description=mcp_tool.description if hasattr(mcp_tool, 'description') else f"MCP tool: {mcp_tool_name}",
                    parameters=parameters,
                    execution=exec_config,
                    metadata=metadata,
                )
                subtools.append(subtool)

            # 缓存结果
            self._mcp_tools_cache[server_key] = subtools
            self._mcp_executors_cache[server_key] = executor

            logger.info(f"Connected to MCP server {server_key} and cached {len(subtools)} subtools")
            return subtools, executor

        except asyncio.CancelledError:
            # 重新抛出取消异常，让上层处理
            logger.warning(f"MCP server {server_key} connection cancelled at outer level")
            raise
        except Exception as e:
            logger.error(f"Failed to connect to MCP server {server_key}: {e}")
            return [], None

    async def _expand_mcp_tools(self, tools: list[Tool]) -> list[Tool]:
        """
        展开 MCP 工具

        对于 MCP 类型的工具，使用缓存的 MCP 工具列表，避免重复连接。

        Args:
            tools: 原始工具列表

        Returns:
            展开后的工具列表（MCP 工具被替换为服务器提供的具体工具）
        """
        expanded_tools = []

        for tool in tools:
            if tool.execution.type != ToolExecutionType.MCP:
                # 非 MCP 工具直接添加
                expanded_tools.append(tool)
                continue

            # 获取 MCP 服务器配置
            exec_config = tool.execution
            server_key = exec_config.server or ""

            if not server_key:
                logger.warning(f"MCP tool {tool.id} has no server configuration, keeping original tool")
                expanded_tools.append(tool)
                continue

            # 检查缓存中是否有该服务器的工具
            if server_key in self._mcp_tools_cache:
                # 使用缓存的子工具
                mcp_subtools = self._mcp_tools_cache[server_key]
                if mcp_subtools:
                    # 添加所有子工具
                    expanded_tools.extend(mcp_subtools)
                    logger.debug(f"Expanded MCP tool {tool.id} into {len(mcp_subtools)} subtools (from cache)")
                else:
                    # 缓存为空，保留原始工具
                    logger.warning(f"Cached MCP tools for server '{server_key}' is empty, keeping original tool")
                    expanded_tools.append(tool)
            else:
                # 缓存中没有该服务器的工具，这可能意味着预加载失败
                # 为了兼容性，保留原始工具
                logger.warning(f"MCP server '{server_key}' not in cache, preload may have failed, keeping original tool")
                expanded_tools.append(tool)

        return expanded_tools

    def _extract_mcp_tool_parameters(self, mcp_tool: Any) -> dict[str, Any]:
        """
        从 mcp.Tool 对象提取参数 schema

        Args:
            mcp_tool: mcp.Tool 对象

        Returns:
            参数 schema 字典
        """
        # 尝试从 input_schema 提取
        if hasattr(mcp_tool, 'input_schema'):
            input_schema = mcp_tool.input_schema
            if isinstance(input_schema, dict):
                # 直接使用 input_schema
                return input_schema

        # 如果没有 input_schema，尝试从其他属性提取
        # 或者返回一个通用的 schema
        return {
            "type": "object",
            "properties": {
                "arguments": {
                    "type": "object",
                    "description": "工具参数",
                    "additionalProperties": True
                }
            },
            "required": ["arguments"]
        }




