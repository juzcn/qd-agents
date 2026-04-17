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


async def baidu_search(query: str, count: int = 10) -> dict[str, Any]:
    """使用百度搜索 API 进行中文网络搜索"""
    config = get_config()
    if not config:
        raise ValueError("Config not found")

    api_key_1 = config.search.baidu.api_key_1
    api_key_2 = config.search.baidu.api_key_2

    if not api_key_1 and not api_key_2:
        raise ValueError("BAIDU_API_KEY_1 or BAIDU_API_KEY_2 not found in config")

    api_key = api_key_1 or api_key_2

    # 确保 count 在有效范围内 (1-50)
    count = max(1, min(50, count))

    request_body = {
        "messages": [
            {
                "content": query,
                "role": "user"
            }
        ],
        "search_source": "baidu_search_v2",
        "resource_type_filter": [{"type": "web", "top_k": count}],
        "search_filter": {}
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://qianfan.baidubce.com/v2/ai_search/web_search",
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Appbuilder-From": "qd-agents",
                "Content-Type": "application/json",
            },
            json=request_body,
        )
        response.raise_for_status()
        result = response.json()

        if "code" in result:
            raise Exception(f"Baidu API error: {result.get('message', 'Unknown error')}")

        # 格式化返回结果，统一格式
        references = result.get("references", [])
        return {
            "engine": "baidu",
            "query": query,
            "results": [
                {
                    "title": item.get("title", ""),
                    "link": item.get("url", item.get("link", "")),
                    "snippet": item.get("summary", item.get("snippet", "")),
                }
                for item in references
            ],
        }




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
