"""Shared LLM invocation utilities.

Extracted from api/routes.py so core modules can use LLM retry logic
without depending on the API layer.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from onemancompany.agents.base import tracked_ainvoke
from onemancompany.core.errors import ErrorCode, classify_exception


async def llm_invoke_with_retry(
    llm,
    messages: list,
    *,
    category: str = "",
    employee_id: str = "",
    max_retries: int = 3,
    quota_max_retries: int = 6,
    base_delay: float = 2.0,
    quota_base_delay: float = 30.0,
) -> object:
    """Invoke LLM with retry. Quota/billing errors get more retries and longer delays."""
    last_exc = None
    for attempt in range(max(max_retries, quota_max_retries)):
        try:
            return await tracked_ainvoke(
                llm, messages, category=category, employee_id=employee_id,
            )
        except Exception as e:
            last_exc = e
            err = classify_exception(e)
            is_quota = err.code == ErrorCode.LLM_QUOTA_EXCEEDED
            is_rate_limit = err.code == ErrorCode.LLM_RATE_LIMIT
            limit = quota_max_retries if is_quota else max_retries
            if attempt + 1 >= limit:
                break
            if not err.recoverable:
                break
            if is_quota:
                delay = quota_base_delay * (attempt + 1)  # 30s, 60s, 90s, ...
            elif is_rate_limit:
                delay = base_delay * 2 ** attempt  # 2s, 4s, 8s, ...
            else:
                delay = base_delay * (attempt + 1)  # 2s, 4s, 6s
            logger.warning(
                "LLM invoke retry {}/{} for {} ({}), waiting {:.0f}s",
                attempt + 1, limit, category, err.code, delay,
            )
            await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]
