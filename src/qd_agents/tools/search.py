"""
内置搜索工具实现

包含：
- serper_search: Serper API (Google Search)
- tavily_search: Tavily API (AI-augmented search)
- fetch: HTTP 请求工具，获取网页内容或调用 API
"""
import logging
from typing import Any

import httpx

from ..config import get_config

logger = logging.getLogger(__name__)


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


# ==================== fetch ====================

async def fetch(url: str, method: str = "GET", headers: dict[str, str] | None = None, body: str | None = None, timeout: int = 30) -> dict[str, Any]:
    """发送 HTTP 请求，获取网页内容或调用 API。url 为请求地址，method 为 HTTP 方法，headers 为请求头，body 为请求体，timeout 为超时秒数。"""
    req_headers = headers or {}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            if method.upper() == "GET":
                response = await client.get(url, headers=req_headers)
            elif method.upper() == "POST":
                response = await client.post(url, headers=req_headers, content=body)
            elif method.upper() == "PUT":
                response = await client.put(url, headers=req_headers, content=body)
            elif method.upper() == "DELETE":
                response = await client.delete(url, headers=req_headers)
            else:
                response = await client.request(method, url, headers=req_headers, content=body)

            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": response.text[:10000],
            }
        except Exception as e:
            return {"error": str(e), "status_code": 0}