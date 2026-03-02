"""Model cost registry — fetches pricing from OpenRouter API with local cache.

On first call (or when cache expires), fetches all model pricing from
GET https://openrouter.ai/api/v1/models and caches it in memory.
Falls back to a static default if the API is unreachable.
"""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

DEFAULT_COST = {"input": 1.00, "output": 3.00}  # fallback per 1M tokens

# In-memory cache: model_name -> {"input": float, "output": float} (per 1M tokens)
_cost_cache: dict[str, dict] = {}
_cache_ts: float = 0.0
_CACHE_TTL = 3600  # re-fetch every hour


def _fetch_openrouter_pricing() -> dict[str, dict]:
    """Fetch model pricing from OpenRouter API.

    Returns dict mapping model_id -> {"input": cost_per_1M, "output": cost_per_1M}.
    """
    try:
        resp = httpx.get("https://openrouter.ai/api/v1/models", timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except Exception as e:
        logger.warning("Failed to fetch OpenRouter pricing: %s", e)
        return {}

    result: dict[str, dict] = {}
    for model in data:
        model_id = model.get("id", "")
        pricing = model.get("pricing") or {}
        prompt_cost = pricing.get("prompt", "0")
        completion_cost = pricing.get("completion", "0")
        try:
            # OpenRouter returns cost per token as string; convert to per 1M tokens
            input_per_1m = float(prompt_cost) * 1_000_000
            output_per_1m = float(completion_cost) * 1_000_000
        except (ValueError, TypeError):
            continue
        if model_id:
            result[model_id] = {"input": round(input_per_1m, 4), "output": round(output_per_1m, 4)}
    return result


def refresh_cache() -> None:
    """Force refresh the pricing cache from OpenRouter."""
    global _cost_cache, _cache_ts
    fetched = _fetch_openrouter_pricing()
    if fetched:
        _cost_cache = fetched
        _cache_ts = time.time()
        logger.info("Refreshed OpenRouter pricing cache: %d models", len(_cost_cache))


def _ensure_cache() -> None:
    """Populate cache if empty or stale."""
    global _cache_ts
    if not _cost_cache or (time.time() - _cache_ts > _CACHE_TTL):
        refresh_cache()


def get_model_cost(model_name: str) -> dict:
    """Return {"input": ..., "output": ...} cost per 1M tokens for a model."""
    _ensure_cache()
    return _cost_cache.get(model_name, DEFAULT_COST)


def compute_salary(model_name: str) -> float:
    """Compute salary (avg of input+output cost per 1M tokens) for a model."""
    costs = get_model_cost(model_name)
    return round((costs["input"] + costs["output"]) / 2, 4)


def estimate_task_cost(model_name: str, estimated_tokens: int) -> float:
    """Rough estimate: assume 40% input, 60% output split."""
    costs = get_model_cost(model_name)
    input_tokens = estimated_tokens * 0.4
    output_tokens = estimated_tokens * 0.6
    return (input_tokens * costs["input"] + output_tokens * costs["output"]) / 1_000_000
