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




_ENV_KEY_NAME = "ANTHROPIC_API_KEY"
_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-sonnet-4-20250514"
_API_VERSION = "2025-01-01"
_USER_AGENT = "OneManCompany-WebSearch/1.0"
_TOOL_TYPE = "web_search_20250305"

# Claude API response block types
_BLOCK_TYPE_SEARCH_RESULT = "web_search_tool_result"
_BLOCK_TYPE_RESULT_ITEM = "web_search_result"
_BLOCK_TYPE_TEXT = "text"

# Response field keys
_FIELD_CONTENT = "content"
_FIELD_TYPE = "type"
_FIELD_TEXT = "text"
_FIELD_TITLE = "title"
_FIELD_URL = "url"
_FIELD_PAGE_SNIPPET = "page_snippet"


def _load_env_var(name: str) -> str:
    """Load an env var, falling back to company .env file."""
    val = os.environ.get(name, "").strip()
    if val:
        return val
    try:
        from onemancompany.core.config import DATA_ROOT, DOT_ENV_FILENAME
        env_path = DATA_ROOT / DOT_ENV_FILENAME
        if not env_path.exists():
            return ""
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, v = line.partition("=")
            if key.strip() == name:
                return v.strip().strip("\"'")
    except Exception:
        pass
    return ""


def _resolve_anthropic_auth() -> tuple[str, dict]:
    """Resolve Anthropic API authentication — supports both API key and OAuth.

    Returns (key_or_token, auth_headers_dict). Empty dict if no auth available.
    """
    auth_method = _load_env_var("ANTHROPIC_AUTH_METHOD")

    if auth_method == "oauth":
        # OAuth: refresh token if needed, use Bearer header
        access_token = _load_env_var(_ENV_KEY_NAME)
        refresh_token = _load_env_var("ANTHROPIC_REFRESH_TOKEN")
        if not access_token and not refresh_token:
            return "", {}

        # Try refreshing via the OAuth module
        try:
            from onemancompany.core.oauth import get_oauth_token, OAuthServiceConfig
            config = OAuthServiceConfig(
                service_name="anthropic",
                authorize_url="https://console.anthropic.com/oauth/authorize",
                token_url="https://console.anthropic.com/oauth/token",
                scopes="",
                client_id_env="ANTHROPIC_CLIENT_ID",
                client_secret_env="ANTHROPIC_CLIENT_SECRET",
            )
            fresh_token = get_oauth_token(config)
            if fresh_token:
                return fresh_token, {"Authorization": f"Bearer {fresh_token}"}
        except Exception as exc:
            logger.debug("[web_search] OAuth refresh failed: {}", exc)

        # Fallback: use the stored access token directly (may be expired)
        if access_token:
            return access_token, {"Authorization": f"Bearer {access_token}"}
        return "", {}

    # Default: API key auth
    api_key = _load_env_var(_ENV_KEY_NAME)
    if not api_key:
        return "", {}
    return api_key, {"x-api-key": api_key}


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
    for block in response.get(_FIELD_CONTENT, []):
        if block.get(_FIELD_TYPE) == _BLOCK_TYPE_SEARCH_RESULT:
            for item in block.get(_FIELD_CONTENT, []):
                if item.get(_FIELD_TYPE) == _BLOCK_TYPE_RESULT_ITEM:
                    results.append({
                        _FIELD_TITLE: item.get(_FIELD_TITLE, ""),
                        _FIELD_URL: item.get(_FIELD_URL, ""),
                        _FIELD_PAGE_SNIPPET: item.get(_FIELD_PAGE_SNIPPET, ""),
                    })
    return results


def _extract_text_answer(response: dict) -> str:
    """Extract the text summary from Claude's response."""
    parts = []
    for block in response.get(_FIELD_CONTENT, []):
        if block.get(_FIELD_TYPE) == _BLOCK_TYPE_TEXT:
            parts.append(block.get(_FIELD_TEXT, ""))
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

    api_key, auth_header = _resolve_anthropic_auth()
    if not auth_header:
        return {"status": "error", "message": f"{_ENV_KEY_NAME} not configured in .env"}

    max_results = max(1, min(int(max_results), 20))

    headers = {
        **auth_header,
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
