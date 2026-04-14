"""
内置工具实现
"""
import os
from typing import Any
import httpx

from ..config import get_config


# ==================== 搜索工具实现 ====================

async def serper_search(query: str, num: int = 10) -> dict[str, Any]:
    """使用 Serper API 进行网络搜索"""
    config = get_config()
    if not config or not config.search.serper.api_key:
        raise ValueError("SERPER_API_KEY not found in config")

    api_key = config.search.serper.api_key

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://google.serper.dev/search",
            headers={
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
            },
            json={"q": query, "num": num},
        )
        response.raise_for_status()
        return response.json()


async def tavily_search(
    query: str,
    search_depth: str = "basic",
    include_answer: bool = True,
    max_results: int = 5,
) -> dict[str, Any]:
    """使用 Tavily API 进行 AI 增强的网络搜索"""
    config = get_config()
    if not config or not config.search.tavily.api_key:
        raise ValueError("TAVILY_API_KEY not found in config")

    api_key = config.search.tavily.api_key

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.tavily.com/search",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "search_depth": search_depth,
                "include_answer": include_answer,
                "max_results": max_results,
            },
        )
        response.raise_for_status()
        return response.json()


async def baidu_search(query: str, pn: int = 0) -> dict[str, Any]:
    """使用百度搜索 API 进行中文网络搜索"""
    config = get_config()
    if not config:
        raise ValueError("Config not found")

    api_key_1 = config.search.baidu.api_key_1
    api_key_2 = config.search.baidu.api_key_2

    if not api_key_1 and not api_key_2:
        raise ValueError("BAIDU_API_KEY_1 or BAIDU_API_KEY_2 not found in config")

    api_key = api_key_1 or api_key_2

    # 注意：这是一个简化实现，实际百度搜索 API 可能需要不同的调用方式
    # 这里假设使用类似百度智能云的搜索 API
    async with httpx.AsyncClient(timeout=30) as client:
        # 占位实现，实际需要根据百度 API 文档调整
        response = await client.post(
            "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/plugin/search",
            headers={
                "Content-Type": "application/json",
            },
            params={"access_token": api_key},
            json={"query": query, "pn": pn},
        )
        response.raise_for_status()
        return response.json()


async def web_search(
    query: str,
    num_results: int = 5,
    engine: str = "auto",
    language: str = "zh-CN",
) -> dict[str, Any]:
    """
    通用网络搜索工具，自动选择合适的搜索引擎

    Args:
        query: 搜索关键词或问题
        num_results: 返回结果数量
        engine: 指定搜索引擎 (auto, serper, tavily, baidu)
        language: 搜索结果语言偏好

    Returns:
        搜索结果
    """
    config = get_config()
    if not config:
        raise ValueError("Config not found")

    # 自动选择搜索引擎
    if engine == "auto":
        # 优先级: Tavily > Serper > Baidu
        if config.search.tavily.api_key:
            engine = "tavily"
        elif config.search.serper.api_key:
            engine = "serper"
        elif config.search.baidu.api_key_1 or config.search.baidu.api_key_2:
            engine = "baidu"
        else:
            raise ValueError("No search engine API key found in config")

    if engine == "serper":
        result = await serper_search(query=query, num=num_results)
        # 格式化结果
        organic = result.get("organic", [])
        return {
            "engine": "serper",
            "query": query,
            "results": [
                {
                    "title": item.get("title"),
                    "link": item.get("link"),
                    "snippet": item.get("snippet"),
                }
                for item in organic[:num_results]
            ],
        }

    elif engine == "tavily":
        result = await tavily_search(
            query=query,
            max_results=num_results,
            include_answer=True,
        )
        return {
            "engine": "tavily",
            "query": query,
            "answer": result.get("answer"),
            "results": result.get("results", []),
        }

    elif engine == "baidu":
        result = await baidu_search(query=query)
        return {
            "engine": "baidu",
            "query": query,
            "results": result,
        }

    else:
        raise ValueError(f"Unknown search engine: {engine}")


# ==================== 元工具占位实现 ====================
# 元工具由系统内部处理，不需要实际执行函数

async def meta_direct() -> dict[str, Any]:
    """direct 元工具 - 由系统内部处理"""
    raise NotImplementedError("meta.direct is handled internally by the orchestrator")


async def meta_find_tools() -> dict[str, Any]:
    """find_tools 元工具 - 由系统内部处理"""
    raise NotImplementedError("meta.find_tools is handled internally by the orchestrator")


async def meta_coding_tool_use() -> dict[str, Any]:
    """coding_tool_use 元工具 - 由系统内部处理"""
    raise NotImplementedError("meta.coding_tool_use is handled internally by the orchestrator")


async def meta_step_down() -> dict[str, Any]:
    """step_down 元工具 - 由系统内部处理"""
    raise NotImplementedError("meta.step_down is handled internally by the orchestrator")
