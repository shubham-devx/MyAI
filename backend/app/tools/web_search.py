"""Configurable web search adapters.

Supported providers:
- Brave Search API (``BRAVE_SEARCH_API_KEY``)
- Tavily Search API (``TAVILY_API_KEY``)
- SearXNG JSON endpoint (``SEARXNG_BASE_URL``), suitable for a local/self-hosted instance.
"""

import html
import re
from typing import Any

import httpx

from app.config import settings
from app.tools.base import ToolError, ToolSource

_USER_AGENT = "V-Sutra/0.4 (local workspace; configurable search tool)"
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def _clean(value: Any, limit: int = 600) -> str:
    if not isinstance(value, str):
        return ""
    text = _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value))).strip()
    return text[:limit]


def _normalize_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    url = value.strip()
    if url.startswith("http://") or url.startswith("https://"):
        return url[:2_000]
    return ""


def configured_provider() -> str:
    provider = (settings.search_provider or "none").strip().lower()
    return provider if provider in {"brave", "tavily", "searxng"} else "none"


def provider_status() -> dict[str, Any]:
    provider = configured_provider()
    if provider == "brave":
        configured = bool(settings.brave_search_api_key and settings.brave_search_api_key.strip())
        hint = None if configured else "Set BRAVE_SEARCH_API_KEY on the backend to enable Brave web search."
        label = "Brave Search"
    elif provider == "tavily":
        configured = bool(settings.tavily_api_key and settings.tavily_api_key.strip())
        hint = None if configured else "Set TAVILY_API_KEY on the backend to enable Tavily web search."
        label = "Tavily"
    elif provider == "searxng":
        configured = bool(settings.searxng_base_url and settings.searxng_base_url.strip())
        hint = None if configured else "Set SEARXNG_BASE_URL to a SearXNG instance with JSON output enabled."
        label = "SearXNG"
    else:
        configured = False
        label = "Not configured"
        hint = "Set SEARCH_PROVIDER to brave, tavily, or searxng on the backend to enable web search."
    return {
        "provider": provider,
        "label": label,
        "configured": configured,
        "external": True,
        "hint": hint,
    }


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(settings.search_timeout_seconds, connect=min(10.0, settings.search_timeout_seconds))


def _error(provider: str, error: Exception) -> ToolError:
    if isinstance(error, httpx.TimeoutException):
        return ToolError(f"{provider} web search timed out. Try a narrower query or raise SEARCH_TIMEOUT_SECONDS.")
    if isinstance(error, (ValueError, TypeError, KeyError, AttributeError)):
        return ToolError(f"{provider} returned an invalid search response.")
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status in (401, 403):
            return ToolError(f"{provider} rejected the configured credentials or search quota.")
        if status == 429:
            return ToolError(f"{provider} rate limit was reached. Try again later.")
        return ToolError(f"{provider} search returned HTTP {status}.")
    return ToolError(f"{provider} search service is unreachable.")


async def _brave(query: str, limit: int) -> list[ToolSource]:
    headers = {"Accept": "application/json", "User-Agent": _USER_AGENT, "X-Subscription-Token": (settings.brave_search_api_key or "").strip()}
    params = {"q": query, "count": limit, "safesearch": "moderate", "text_decorations": "false"}
    async with httpx.AsyncClient(timeout=_timeout(), follow_redirects=True) as client:
        response = await client.get("https://api.search.brave.com/res/v1/web/search", headers=headers, params=params)
        response.raise_for_status()
        body = response.json()
    results = (body.get("web") or {}).get("results") or []
    return [
        ToolSource(title=_clean(item.get("title")), url=_normalize_url(item.get("url")), snippet=_clean(item.get("description")), provider="brave")
        for item in results
        if isinstance(item, dict) and _normalize_url(item.get("url"))
    ][:limit]


async def _tavily(query: str, limit: int) -> list[ToolSource]:
    headers = {"Content-Type": "application/json", "User-Agent": _USER_AGENT}
    api_key = (settings.tavily_api_key or "").strip()
    # Tavily's /search API authenticates with `api_key` in the JSON body.
    # Sending only a Bearer header (as the previous implementation did) is
    # rejected by the documented endpoint even though status reports it ready.
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": limit,
        "search_depth": "basic",
        "include_answer": False,
        "include_raw_content": False,
    }
    async with httpx.AsyncClient(timeout=_timeout(), follow_redirects=True) as client:
        response = await client.post("https://api.tavily.com/search", headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
    results = body.get("results") or []
    return [
        ToolSource(title=_clean(item.get("title")), url=_normalize_url(item.get("url")), snippet=_clean(item.get("content")), provider="tavily")
        for item in results
        if isinstance(item, dict) and _normalize_url(item.get("url"))
    ][:limit]


async def _searxng(query: str, limit: int) -> list[ToolSource]:
    base = (settings.searxng_base_url or "").strip().rstrip("/")
    headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
    params = {"q": query, "format": "json", "categories": "general", "language": "auto"}
    async with httpx.AsyncClient(timeout=_timeout(), follow_redirects=True) as client:
        response = await client.get(f"{base}/search", headers=headers, params=params)
        response.raise_for_status()
        body = response.json()
    results = body.get("results") or []
    return [
        ToolSource(title=_clean(item.get("title")), url=_normalize_url(item.get("url")), snippet=_clean(item.get("content")), provider="searxng")
        for item in results
        if isinstance(item, dict) and _normalize_url(item.get("url"))
    ][:limit]


async def search_web(query: str, max_results: int = 5) -> list[ToolSource]:
    status = provider_status()
    if not status["configured"]:
        raise ToolError(status["hint"] or "Web search is not configured.", 503)
    cleaned_query = query.strip()
    if not cleaned_query:
        raise ToolError("Enter a search query.")
    limit = max(1, min(int(max_results), 10))
    provider = status["provider"]
    try:
        if provider == "brave":
            return await _brave(cleaned_query, limit)
        if provider == "tavily":
            return await _tavily(cleaned_query, limit)
        return await _searxng(cleaned_query, limit)
    except ToolError:
        raise
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise _error(status["label"], error) from error


def format_results(query: str, results: list[ToolSource]) -> str:
    if not results:
        return f"No web results were returned for: {query}"
    lines = [f"Web search results for: {query}", ""]
    for index, result in enumerate(results, start=1):
        lines.append(f"{index}. {result.title or result.url}")
        lines.append(f"   URL: {result.url}")
        if result.snippet:
            lines.append(f"   Snippet: {result.snippet}")
    lines.append("")
    lines.append("Use these sources only when relevant and cite their URLs in the answer.")
    return "\n".join(lines)
