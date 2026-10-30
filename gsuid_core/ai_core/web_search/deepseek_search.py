"""
DeepSeek Web Search 模块

提供基于 DeepSeek 官方 Search 接口的 web 搜索功能。
接口文档: https://api-docs.deepseek.com/zh-cn/guides/search/
支持 api_key 池配置，实现自动轮询和失败重试。
"""

import random
from typing import Optional

import httpx

from gsuid_core.i18n import t
from gsuid_core.logger import logger
from gsuid_core.ai_core.configs.ai_config import deepseek_search_config

_SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
_TIMEOUT = 30.0
_PROVIDER = "DeepSeek"


def _get_api_key_pool() -> list[str]:
    """获取 api_key 池"""
    api_key_data = deepseek_search_config.get_config("api_key").data

    if isinstance(api_key_data, list):
        return [k for k in api_key_data if k]
    elif isinstance(api_key_data, str) and api_key_data:
        return [api_key_data]
    else:
        return []


def _select_api_key(api_key_pool: list[str]) -> Optional[str]:
    """从 api_key 池中随机选择一个 api_key"""
    if not api_key_pool:
        return None
    return random.choice(api_key_pool)


async def _do_deepseek_search(
    query: str,
    max_results: int,
    api_key: str,
) -> list[dict]:
    """执行 DeepSeek 搜索的内部方法"""
    payload = {
        "query": query,
        "count": max(1, min(50, max_results)),
        "need_content": False,
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            _SEARCH_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    results = []
    for item in data.get("results", []):
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                # summary 为 600~800 字长摘要，最适合给模型阅读
                "content": item.get("summary") or item.get("snippet") or item.get("content", ""),
                "score": float(item.get("rerank_score") or 0.0),
            }
        )

    return results


async def deepseek_search(
    query: str,
    max_results: Optional[int] = None,
    fail_fast: bool = False,
) -> list[dict]:
    """
    使用 DeepSeek 官方 Search 接口进行 web 搜索

    Args:
        query: 搜索查询关键词
        max_results: 最大返回结果数量，默认取配置项
        fail_fast: 只尝试一个 api_key 即返回(引擎级主备链路里不做 Key 备份)

    Returns:
        搜索结果列表，每条包含 title、url、content、score 等字段
    """
    api_key_pool = _get_api_key_pool()

    if not api_key_pool:
        logger.warning(t("log.ai.websearch_provider_api_key_skip", p0=_PROVIDER))
        return []

    if max_results is None:
        max_results = int(deepseek_search_config.get_config("max_results").data or "10")

    max_attempts = 1 if fail_fast else len(api_key_pool)
    tried_keys = set()

    while len(tried_keys) < max_attempts:
        api_key = _select_api_key([k for k in api_key_pool if k not in tried_keys])
        if not api_key:
            break

        tried_keys.add(api_key)

        try:
            results = await _do_deepseek_search(
                query=query,
                max_results=max_results,
                api_key=api_key,
            )

            logger.info(
                t(
                    "log.ai.websearch_provider_search_query",
                    p0=_PROVIDER,
                    query=query,
                    p1=len(results),
                )
            )
            return results

        except Exception as e:
            logger.warning(
                t(
                    "log.ai.websearch_provider_key_trying",
                    p0=_PROVIDER,
                    p1=api_key[-4:],
                    e=e,
                )
            )
            continue

    logger.error(t("log.ai.websearch_provider_all_keys_failed", p0=_PROVIDER))
    return []
