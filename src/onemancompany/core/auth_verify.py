"""Shared chat-probe verifier for provider connectivity.

Used by:
- POST /api/auth/verify (onboarding)
- heartbeat.py (periodic health checks)
"""
from __future__ import annotations

import asyncio

from loguru import logger

from onemancompany.core.config import CHAT_CLASS_ANTHROPIC, CHAT_CLASS_OPENAI


def _make_openai_client(api_key: str, base_url: str):
    """Create an async OpenAI client."""
    from openai import AsyncOpenAI
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


def _make_anthropic_client(api_key: str):
    """Create an async Anthropic client."""
    from anthropic import AsyncAnthropic
    return AsyncAnthropic(api_key=api_key)


async def probe_health(
    provider: str,
    api_key: str,
    *,
    timeout: float = 15.0,
) -> tuple[bool, str]:
    """Verify API key via provider health endpoint (zero tokens consumed).

    Uses the provider's health_url with the API key as auth header.
    Returns (ok, error_message).
    """
    from onemancompany.core.config import get_provider

    provider_cfg = get_provider(provider)
    if not provider_cfg or not provider_cfg.health_url:
        return False, f"No health endpoint for provider '{provider}'"

    import httpx

    headers: dict[str, str] = {}
    if provider_cfg.health_auth == "anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(provider_cfg.health_url, headers=headers)
            if resp.status_code in (200, 201):
                logger.debug("probe_health OK for {}", provider)
                return True, ""
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error_msg = str(exc)
        if len(error_msg) > 200:
            error_msg = error_msg[:200] + "..."
        logger.debug("probe_health failed for {}: {}", provider, error_msg)
        return False, error_msg


async def probe_chat(
    provider: str,
    api_key: str,
    model: str,
    *,
    timeout: float = 30.0,
    base_url: str = "",
    chat_class: str = "",
) -> tuple[bool, str]:
    """Send a minimal chat request to verify provider connectivity.

    Returns (ok, error_message) — ok=True means connectivity verified.
    """
    from onemancompany.core.config import get_provider

    provider_cfg = get_provider(provider)
    resolved_base_url = base_url or (provider_cfg.base_url if provider_cfg else "")
    resolved_chat_class = chat_class or (provider_cfg.chat_class if provider_cfg else CHAT_CLASS_OPENAI)

    try:
        if resolved_chat_class == CHAT_CLASS_ANTHROPIC:
            client = _make_anthropic_client(api_key)
            await asyncio.wait_for(
                client.messages.create(
                    model=model,
                    max_tokens=1,
                    messages=[{"role": "user", "content": "hi"}],
                ),
                timeout=timeout,
            )
        else:
            client = _make_openai_client(api_key, resolved_base_url)
            await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    max_tokens=1,
                    messages=[{"role": "user", "content": "hi"}],
                ),
                timeout=timeout,
            )
        return True, ""
    except asyncio.TimeoutError:
        return False, f"Connection timeout ({timeout}s)"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error_msg = str(exc)
        if len(error_msg) > 200:
            error_msg = error_msg[:200] + "..."
        logger.debug("probe_chat failed for {}/{}: {}", provider, model, error_msg)
        return False, error_msg
