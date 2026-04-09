"""Web search tool via DuckDuckGo.

Provides one LangChain @tool:
- web_search(query, max_results=8)

No API key required. Uses duckduckgo-search package.
Inspired by cc-agent's WebSearchTool implementation.
"""

from __future__ import annotations

import time

from ddgs import DDGS
from langchain_core.tools import tool
from loguru import logger

# Consistent with cc-agent's max_uses: 8
MAX_SEARCH_RESULTS = 8
MIN_QUERY_LENGTH = 2


@tool
def web_search(query: str, max_results: int = 8) -> dict:
    """Search the web for real-time information. USE THIS for any task needing current data.

    Suitable for: market research, competitor analysis, tech docs, news, pricing,
    regulations, trends, or any knowledge that may have changed after training.

    CRITICAL: After using search results in your response, you MUST include a
    "Sources:" section at the end listing all relevant URLs as markdown hyperlinks:
      Sources:
      - [Title](URL)
      - [Title](URL)
    This is MANDATORY — never skip including sources.

    Args:
        query: Search query (minimum 2 characters).
        max_results: Maximum number of search results (default 8, max 8).
    """
    # Input validation (consistent with cc-agent's zod schema)
    query = (query or "").strip()
    if len(query) < MIN_QUERY_LENGTH:
        return {"status": "error", "message": f"Query too short (minimum {MIN_QUERY_LENGTH} characters)"}

    max_results = max(1, min(int(max_results), MAX_SEARCH_RESULTS))

    start_time = time.time()

    try:
        with DDGS() as ddgs:
            raw_results = list(ddgs.text(query, max_results=max_results))

        duration_seconds = round(time.time() - start_time, 2)

        sources = [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "page_snippet": r.get("body", ""),
            }
            for r in raw_results
        ]

        # Build structured answer with source links
        answer_parts = []
        for i, s in enumerate(sources, 1):
            answer_parts.append(f"{i}. **{s['title']}**\n   {s['page_snippet']}\n   Source: {s['url']}")
        answer = "\n\n".join(answer_parts) if answer_parts else "No results found."

        # Append source citation reminder (consistent with cc-agent)
        source_links = "\n".join(f"- [{s['title']}]({s['url']})" for s in sources)
        if source_links:
            answer += f"\n\nSources:\n{source_links}"
            answer += "\n\nREMINDER: You MUST include the sources above in your response using markdown hyperlinks."

        logger.debug("[web_search] query='{}' results={} duration={:.2f}s", query[:50], len(sources), duration_seconds)

        return {
            "status": "ok",
            "query": query,
            "answer": answer,
            "sources": sources,
            "source_count": len(sources),
            "duration_seconds": duration_seconds,
        }
    except Exception as exc:
        duration_seconds = round(time.time() - start_time, 2)
        logger.debug("[web_search] DuckDuckGo search failed after {:.2f}s: {}", duration_seconds, exc)
        return {"status": "error", "message": f"Search failed: {exc}", "duration_seconds": duration_seconds}
