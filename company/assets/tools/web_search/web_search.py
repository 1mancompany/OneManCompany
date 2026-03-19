"""Web search tool via Anthropic Claude API with built-in web search.

Provides one LangChain @tool:
- web_search(query, max_results=5)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from langchain_core.tools import tool
from loguru import logger


def _load_key_from_dotenv() -> str:
    """Try to load ANTHROPIC_API_KEY from the company .env file."""
    try:
        from onemancompany.core.config import DATA_ROOT, DOT_ENV_FILENAME
        env_path = DATA_ROOT / DOT_ENV_FILENAME
        if not env_path.exists():
            return ""
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() == _ENV_KEY_NAME:
                return val.strip().strip("\"'")
    except Exception as exc:
        logger.debug("[web_search] failed to load API key from .env: {}", exc)
    return ""


_ENV_KEY_NAME = "ANTHROPIC_API_KEY"
_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-sonnet-4-20250514"
_API_VERSION = "2025-01-01"
_USER_AGENT = "OneManCompany-WebSearch/1.0"
_BLOCK_TYPE_SEARCH_RESULT = "web_search_tool_result"
_BLOCK_TYPE_RESULT_ITEM = "web_search_result"
_BLOCK_TYPE_TEXT = "text"
_TOOL_TYPE = "web_search_20250305"


def _post_json(url: str, headers: dict, payload: dict, timeout: int = 30) -> tuple[dict | None, str | None]:
    """POST JSON and return (json_body, error)."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw), None
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return None, f"HTTP {e.code}: {body_text[:800]}"
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON response: {e}"
    except Exception as e:
        return None, str(e)


def _extract_search_results(response: dict) -> list[dict]:
    """Extract web search results from Claude API response content blocks."""
    results = []
    for block in response.get("content", []):
        if block.get("type") == _BLOCK_TYPE_SEARCH_RESULT:
            for item in block.get("content", []):
                if item.get("type") == _BLOCK_TYPE_RESULT_ITEM:
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("page_snippet", ""),
                    })
    return results


def _extract_text_answer(response: dict) -> str:
    """Extract the text summary from Claude's response."""
    parts = []
    for block in response.get("content", []):
        if block.get("type") == _BLOCK_TYPE_TEXT:
            parts.append(block.get("text", ""))
    return "\n".join(parts).strip()


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

    api_key = os.environ.get(_ENV_KEY_NAME, "").strip()
    if not api_key:
        # Fallback: read from company .env file
        api_key = _load_key_from_dotenv()
    if not api_key:
        return {"status": "error", "message": f"{_ENV_KEY_NAME} not configured in .env"}

    max_results = max(1, min(int(max_results), 20))

    headers = {
        "x-api-key": api_key,
        "anthropic-version": _API_VERSION,
        "content-type": "application/json",
        "User-Agent": _USER_AGENT,
    }

    payload = {
        "model": _MODEL,
        "max_tokens": 4096,
        "tools": [
            {
                "type": _TOOL_TYPE,
                "name": "web_search",
                "max_uses": max_results,
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": f"Search the web and provide a comprehensive answer: {query}",
            }
        ],
    }

    resp_json, err = _post_json(_API_URL, headers, payload, timeout=60)
    if err or resp_json is None:
        return {"status": "error", "message": err or "empty response from API"}

    answer = _extract_text_answer(resp_json)
    sources = _extract_search_results(resp_json)

    return {
        "status": "ok",
        "query": query,
        "answer": answer,
        "sources": sources[:max_results],
        "source_count": len(sources),
    }
