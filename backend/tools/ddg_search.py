import time
import logging
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)


class DDGSearchError(Exception):
    pass


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def ddg_search(query: str, max_results: int = 8) -> list[dict]:
    """Search DuckDuckGo and return result snippets."""
    try:
        from duckduckgo_search import DDGS
        time.sleep(1.0)  # polite delay
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        logger.info(f"DDG '{query[:60]}' → {len(results)} results")
        return results
    except Exception as e:
        logger.warning(f"DDG search failed for '{query[:60]}': {e}")
        raise


def brave_search(query: str, api_key: str, max_results: int = 8) -> list[dict]:
    """Brave Search API fallback (free tier: 2000/month)."""
    import httpx
    try:
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            params={"q": query, "count": max_results},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("web", {}).get("results", []):
            results.append({
                "title": item.get("title", ""),
                "body": item.get("description", ""),
                "href": item.get("url", ""),
            })
        return results
    except Exception as e:
        logger.warning(f"Brave search failed: {e}")
        return []


def search_with_fallback(query: str, brave_api_key: str = "") -> list[dict]:
    """Try DDG first, fall back to Brave if available."""
    try:
        return ddg_search(query)
    except Exception as e:
        logger.warning(f"DDG failed, trying Brave: {e}")
        if brave_api_key:
            return brave_search(query, brave_api_key)
        return []
