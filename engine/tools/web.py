"""Web tool handlers: web_search, web_fetch."""

import re
import logging
import httpx

logger = logging.getLogger("alfe.tools.web")

# Try ddgs (new name) then duckduckgo_search (old name), then fall back to Instant Answers API
try:
    from ddgs import DDGS
    _HAS_DDGS = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        _HAS_DDGS = True
    except ImportError:
        _HAS_DDGS = False
        logger.info("ddgs not installed — falling back to DDG Instant Answers API")


def handle_web_search(inp: dict) -> str:
    query = inp["query"]
    max_results = int(inp.get("max_results", 5))

    if _HAS_DDGS:
        return _search_ddgs(query, max_results)
    return _search_instant_answers(query, max_results)


def _search_ddgs(query: str, max_results: int) -> str:
    """Real web search using duckduckgo_search package."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))

        if not results:
            return f"No results found for '{query}'."

        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}\n   {r['href']}\n   {r['body'][:200]}")
        return f"Search results for '{query}':\n\n" + "\n\n".join(lines)

    except Exception as e:
        logger.error(f"DDG search error: {e}")
        return f"Web search failed: {e}"


def _search_instant_answers(query: str, max_results: int) -> str:
    """Fallback: DuckDuckGo Instant Answers API (limited to encyclopedia-style results)."""
    try:
        resp = httpx.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_redirect": "1", "no_html": "1"},
            timeout=10,
            headers={"User-Agent": "Alf-E/2.0"},
        )
        data = resp.json()
        results = []

        if data.get("Abstract"):
            results.append({
                "title": data.get("Heading", "Featured"),
                "url": data.get("AbstractURL", ""),
                "snippet": data["Abstract"],
            })

        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "title": topic.get("Text", "")[:80],
                    "url": topic.get("FirstURL", ""),
                    "snippet": topic.get("Text", ""),
                })
            if len(results) >= max_results:
                break

        if not results:
            return f"No results found for '{query}'. Try web_fetch with a specific URL instead."

        lines = []
        for i, r in enumerate(results[:max_results], 1):
            lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet'][:200]}")
        return f"Search results for '{query}':\n\n" + "\n\n".join(lines)

    except Exception as e:
        logger.error(f"Web search error: {e}")
        return f"Web search failed: {e}"


def handle_web_fetch(inp: dict) -> str:
    url = inp["url"]
    max_chars = int(inp.get("max_chars", 4000))
    try:
        resp = httpx.get(
            url,
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "Alf-E/2.0"},
        )
        resp.raise_for_status()

        content = resp.text
        content = re.sub(r"<script[^>]*>.*?</script>", " ", content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r"<style[^>]*>.*?</style>", " ", content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r"<[^>]+>", " ", content)
        content = re.sub(r"\s+", " ", content).strip()

        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n[truncated — {len(content) - max_chars} more chars]"

        return f"Content from {url}:\n\n{content}"

    except Exception as e:
        logger.error(f"Web fetch error: {e}")
        return f"Failed to fetch {url}: {e}"
