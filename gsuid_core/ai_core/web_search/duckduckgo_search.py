"""
DuckDuckGo Web Search 模块

基于 ddgs 元搜索库聚合 DuckDuckGo/Bing/Brave 等免费后端，无需 API Key。
支持配置搜索地区与后端，请求在线程池中执行以免阻塞事件循环。
"""

import asyncio
from typing import Optional

from gsuid_core.i18n import t
from gsuid_core.logger import logger
from gsuid_core.ai_core.configs.ai_config import duckduckgo_search_config

_PROVIDER = "DuckDuckGo"


def _do_ddg_search(
    query: str,
    max_results: int,
    region: str,
    backend: str,
) -> list[dict]:
    """同步执行 ddgs 搜索（在线程池中调用）"""
    from ddgs import DDGS

    raw_results = DDGS().text(
        query,
        region=region,
        safesearch="moderate",
        max_results=max_results,
        backend=backend,
    )

    results = []
    for item in raw_results or []:
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("href") or item.get("url", ""),
                "content": item.get("body") or item.get("snippet", ""),
                "score": 0.0,
            }
        )

    return results


async def duckduckgo_search(
    query: str,
    max_results: Optional[int] = None,
) -> list[dict]:
    """
    使用 ddgs 元搜索进行免费 web 搜索（无需 API Key）

    Args:
        query: 搜索查询关键词
        max_results: 最大返回结果数量，默认取配置项

    Returns:
        搜索结果列表，每条包含 title、url、content、score 等字段
    """
    if max_results is None:
        max_results = int(duckduckgo_search_config.get_config("max_results").data or "10")

    region = duckduckgo_search_config.get_config("region").data or "wt-wt"
    backend = duckduckgo_search_config.get_config("backend").data or "duckduckgo"

    try:
        results = await asyncio.to_thread(
            _do_ddg_search,
            query,
            max_results,
            region,
            backend,
        )
    except ImportError:
        logger.warning(t("log.ai.websearch_provider_dependency_missing", p0=_PROVIDER))
        return []
    except Exception as e:
        logger.warning(t("log.ai.websearch_provider_search_fail", p0=_PROVIDER, e=e))
        return []

    logger.info(
        t(
            "log.ai.websearch_provider_search_query",
            p0=_PROVIDER,
            query=query,
            p1=len(results),
        )
    )
    return results
