"""Web search tool via DuckDuckGo.

Provides one LangChain @tool:
- web_search(query, max_results=5)

No API key required. Uses duckduckgo-search package.
"""

from __future__ import annotations

from ddgs import DDGS
from langchain_core.tools import tool
from loguru import logger


@tool
def web_search(query: str, max_results: int = 5) -> dict:
    """Search the web for real-time information. USE THIS for any task needing current data.

    Suitable for: market research, competitor analysis, tech docs, news, pricing,
    regulations, trends, or any knowledge that may have changed after training.

    Args:
        query: Search query describing what information you need.
        max_results: Maximum number of search results to return (default 5, max 20).
    """
    query = (query or "").strip()
    if not query:
        return {"status": "error", "message": "query is empty"}

    max_results = max(1, min(int(max_results), 20))

    try:
        with DDGS() as ddgs:
            raw_results = list(ddgs.text(query, max_results=max_results))

        sources = [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "page_snippet": r.get("body", ""),
            }
            for r in raw_results
        ]

        # Build a text answer from snippets
        answer_parts = []
        for i, s in enumerate(sources, 1):
            answer_parts.append(f"{i}. **{s['title']}**\n   {s['page_snippet']}\n   Source: {s['url']}")
        answer = "\n\n".join(answer_parts) if answer_parts else "No results found."

        return {
            "status": "ok",
            "query": query,
            "answer": answer,
            "sources": sources,
            "source_count": len(sources),
        }
    except Exception as exc:
        logger.debug("[web_search] DuckDuckGo search failed: {}", exc)
        return {"status": "error", "message": f"Search failed: {exc}"}
