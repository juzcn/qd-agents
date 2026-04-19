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






