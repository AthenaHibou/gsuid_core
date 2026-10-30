"""
Web Search 公共 API 模块

提供统一的 web 搜索接口，根据用户配置自动选择搜索引擎
（Tavily / Exa / DeepSeek / 百度千帆 / DuckDuckGo / MCP）。
外部模块应通过本模块的函数调用搜索，无需关心底层搜索引擎的实现细节。
"""

from gsuid_core.i18n import t
from gsuid_core.logger import logger
from gsuid_core.ai_core.mcp.utils import (
    get_mcp_tool_id,
    is_mcp_provider,
    sanitize_mcp_text,
    build_mcp_arguments,
    call_mcp_tool_checked,
)
from gsuid_core.ai_core.configs.ai_config import (
    ai_config,
    exa_config,
    tavily_config,
    baidu_search_config,
    deepseek_search_config,
)

from .exa_search import exa_search
from .baidu_search import baidu_search
from .tavily_search import (
    tavily_search,
    tavily_search_with_context,
)
from .deepseek_search import deepseek_search
from .duckduckgo_search import duckduckgo_search

# 单条 MCP 原始返回兜底透传时的最大字符数，避免一次搜索把上下文吃满
_MAX_MCP_RAW_CHARS = 4000

# 链路条目里的源名称 -> 标准 provider 名（大小写/常见别名归一）
_PROVIDER_ALIASES = {
    "tavily": "Tavily",
    "exa": "Exa",
    "deepseek": "DeepSeek",
    "baidu": "百度",
    "百度": "百度",
    "duckduckgo": "DuckDuckGo",
    "ddg": "DuckDuckGo",
    "mcp": "MCP",
}

# 需要 API Key 的源 -> 对应配置对象，用于链路里判断该源当前是否可用
_KEY_CONFIGS = {
    "Tavily": tavily_config,
    "Exa": exa_config,
    "DeepSeek": deepseek_search_config,
    "百度": baidu_search_config,
}


def _get_provider() -> str:
    """
    获取当前配置的搜索引擎提供方

    Returns:
        搜索引擎名称，如 "Tavily"、"Exa" 或 "MCP"
    """
    return ai_config.get_config("websearch_provider").data


async def _mcp_search(query: str, max_results: int | None = None) -> list[dict]:
    """
    使用 MCP 进行 web 搜索

    Args:
        query: 搜索查询关键词
        max_results: 最大返回结果数量

    Returns:
        搜索结果列表
    """
    mcp_tool_id = get_mcp_tool_id("websearch_mcp_tool_id", "Web Search")

    arguments = build_mcp_arguments(
        "websearch_mcp_tool_id",
        {"query": query, "max_results": max_results},
    )

    result = await call_mcp_tool_checked(mcp_tool_id, arguments, "Web Search")

    return _parse_mcp_search_result(result.text, max_results)


def _parse_mcp_search_result(raw_text: str, max_results: int | None = None) -> list[dict]:
    """
    解析 MCP web search 返回的原始文本为结构化结果

    两段式策略（不同 MCP 返回格式差异极大，无法用一套 schema 归一）：
    1. 能 ``json.loads`` → 走结构化归一，按条干净截断（MiniMax 等）。
    2. 不能（知乎等返回 XML 化文本）→ **不再丢弃返回 []**，而是消毒（剥指令壳）
       + 限长后整段透传给模型当参考资料，由 LLM 自己阅读。

    Args:
        raw_text: MCP 返回的原始文本
        max_results: 最大结果数

    Returns:
        搜索结果列表
    """
    import json

    # 先消毒：剥掉 instruction 壳，避免间接 prompt injection
    cleaned = sanitize_mcp_text(raw_text)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # 非 JSON：兜底透传（限长），而不是返回空让模型误以为“搜不到”
        logger.debug(t("log.ai.websearch_mcp_non_json_return", p0=len(cleaned)))
        return [
            {
                "title": "",
                "url": "",
                "content": sanitize_mcp_text(raw_text, max_chars=_MAX_MCP_RAW_CHARS),
                "score": 0.0,
            }
        ]

    # 调试日志：打印原始返回数据
    logger.debug(t("log.ai.websearch_mcp_structured_return", p0=cleaned[:500]))

    # 尝试解析为结果列表
    # MiniMax MCP 返回格式可能是 {"organic": [...]} 或 {"results": [...]} 或直接是 [...]
    if isinstance(data, list):
        results = data
    elif isinstance(data, dict):
        if "organic" in data:
            results = data["organic"]
        elif "results" in data:
            results = data["results"]
        else:
            results = [data]
    else:
        results = [data]

    # 限制结果数量
    if max_results is not None and len(results) > max_results:
        results = results[:max_results]

    # 标准化结果格式
    normalized: list[dict] = []
    for item in results:
        if isinstance(item, dict):
            normalized.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": item.get("content", item.get("snippet", "")),
                    "score": item.get("score", 0.0),
                }
            )
        else:
            normalized.append({"title": str(item), "url": "", "content": "", "score": 0.0})

    return normalized


def _normalize_provider(raw: str) -> str | None:
    """把链路条目里的源名称归一为标准 provider 名"""
    name = raw.strip().strip("\"'")
    if not name:
        return None
    return _PROVIDER_ALIASES.get(name.lower())


def _parse_source_entries(entries: list | None) -> list[tuple[str, int]]:
    """
    解析主/备用源配置，如 ["Tavily:3", "百度=2"] -> [("Tavily", 3), ("百度", 2)]

    未写条数的条目默认 3 条；无法识别的条目跳过并记日志。
    """
    import re

    parsed: list[tuple[str, int]] = []
    for raw in entries or []:
        if not isinstance(raw, str):
            continue
        match = re.match(r"^\s*([^\s:：=＝]+)\s*(?:[:：=＝]\s*(\d+))?\s*$", raw)
        if not match:
            logger.warning(t("log.ai.websearch_chain_parse_skip", p0=raw))
            continue
        provider = _normalize_provider(match.group(1))
        if provider is None:
            logger.warning(t("log.ai.websearch_chain_parse_skip", p0=raw))
            continue
        quota = int(match.group(2)) if match.group(2) else 3
        parsed.append((provider, max(1, min(50, quota))))
    return parsed


def _is_provider_ready(provider: str) -> bool:
    """链路里判断某个源当前是否可用（无 Key 的源直接跳过，不浪费一次请求）"""
    if provider in ("DuckDuckGo", "MCP"):
        return True
    key_config = _KEY_CONFIGS.get(provider)
    if key_config is None:
        return False
    keys = key_config.get_config("api_key").data
    if isinstance(keys, str):
        keys = [keys]
    return bool([k for k in (keys or []) if str(k).strip()])


async def _dispatch_search(provider: str, query: str, max_results: int) -> list[dict]:
    """按 provider 名把请求分发到具体搜索实现（失败即换下一个引擎，不轮询同引擎的多个 Key）"""
    if provider == "Exa":
        return await exa_search(query=query, max_results=max_results, fail_fast=True)

    if provider == "DeepSeek":
        return await deepseek_search(query=query, max_results=max_results, fail_fast=True)

    if provider == "百度":
        return await baidu_search(query=query, max_results=max_results, fail_fast=True)

    if provider == "DuckDuckGo":
        return await duckduckgo_search(query=query, max_results=max_results)

    if is_mcp_provider(provider):
        return await _mcp_search(query=query, max_results=max_results)

    return await tavily_search(query=query, max_results=max_results, fail_fast=True)


async def _run_source_group(
    group: list[tuple[str, int]],
    total: int,
    query: str,
) -> list[dict]:
    """
    按顺序执行一组源，凑够 total 条即停。

    某个源无 Key / 请求失败 / 返回不足时，它没凑够的条数会顺延给下一个源承担。
    """
    collected: list[dict] = []
    carry = 0

    for provider, quota in group:
        if len(collected) >= total:
            break

        if not _is_provider_ready(provider):
            logger.info(t("log.ai.websearch_chain_skip_provider", p0=provider))
            carry += quota
            continue

        want = min(quota + carry, total - len(collected))
        if want <= 0:
            break

        got = await _dispatch_search(provider, query, want)
        collected.extend(got)
        # 没凑够的部分继续顺延给下一个源
        carry = max(0, want - len(got))

    return collected[:total]


async def _search_chain(query: str) -> list[dict] | None:
    """
    主备链路搜索。

    返回 None 表示没有配置主用源列表（调用方应退回单一 provider 模式）。
    """
    primary = _parse_source_entries(ai_config.get_config("websearch_primary_sources").data)
    if not primary:
        return None

    total = int(ai_config.get_config("websearch_total_results").data or "6")
    results = await _run_source_group(primary, total, query)

    if not results:
        backup = _parse_source_entries(ai_config.get_config("websearch_backup_sources").data)
        if backup:
            logger.info(t("log.ai.websearch_chain_fallback"))
            results = await _run_source_group(backup, total, query)

    return results


async def web_search(
    query: str,
    max_results: int | None = None,
) -> list[dict]:
    """
    统一的 web 搜索接口

    根据用户配置的 websearch_provider 自动选择搜索引擎。

    Args:
        query: 搜索查询关键词
        max_results: 最大返回结果数量，默认由各搜索引擎配置决定

    Returns:
        搜索结果列表，每条包含 title、url、content、score 等字段

    Example:
        >>> results = await web_search("Python 教程")
        >>> for r in results:
        ...     print(r["title"], r["url"])
    """
    chained = await _search_chain(query)
    if chained is not None:
        return chained

    provider = _get_provider()

    if provider == "Exa":
        return await exa_search(query=query, max_results=max_results)

    if provider == "DeepSeek":
        return await deepseek_search(query=query, max_results=max_results)

    if provider == "百度":
        return await baidu_search(query=query, max_results=max_results)

    if provider == "DuckDuckGo":
        return await duckduckgo_search(query=query, max_results=max_results)

    if is_mcp_provider(provider):
        return await _mcp_search(query=query, max_results=max_results)

    # 默认使用 Tavily
    return await tavily_search(query=query, max_results=max_results)


async def web_search_with_context(
    query: str,
    max_results: int = 5,
) -> dict:
    """
    统一的带上下文 web 搜索接口

    根据用户配置的 websearch_provider 自动选择搜索引擎。
    该方法会同时返回搜索结果和 AI 生成的摘要答案（如果搜索引擎支持）。

    Args:
        query: 搜索查询关键词
        max_results: 最大返回结果数量，默认5条

    Returns:
        包含 results(结果列表) 和 answer(AI摘要) 的字典
    """
    chained = await _search_chain(query)
    if chained is not None:
        return {"results": chained, "answer": None}

    provider = _get_provider()

    if provider == "Exa":
        results = await exa_search(query=query, max_results=max_results)
        return {"results": results, "answer": None}

    if provider == "DeepSeek":
        results = await deepseek_search(query=query, max_results=max_results)
        return {"results": results, "answer": None}

    if provider == "百度":
        results = await baidu_search(query=query, max_results=max_results)
        return {"results": results, "answer": None}

    if provider == "DuckDuckGo":
        results = await duckduckgo_search(query=query, max_results=max_results)
        return {"results": results, "answer": None}

    if is_mcp_provider(provider):
        results = await _mcp_search(query=query, max_results=max_results)
        return {"results": results, "answer": None}

    # 默认使用 Tavily
    return await tavily_search_with_context(query=query, max_results=max_results)
