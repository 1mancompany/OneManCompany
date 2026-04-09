"""Check for new versions on npm registry and notify via WebSocket."""

from __future__ import annotations

import asyncio

import httpx
from loguru import logger

NPM_PACKAGE = "@1mancompany/onemancompany"
NPM_REGISTRY_URL = f"https://registry.npmjs.org/{NPM_PACKAGE}/latest"
CHECK_INTERVAL_HOURS = 24


def _get_current_version() -> str:
    """Read current version from importlib metadata."""
    try:
        from importlib.metadata import version
        return version("onemancompany")
    except Exception:
        return ""


async def _fetch_latest_version() -> str:
    """Fetch the latest version from npm registry."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(NPM_REGISTRY_URL)
            if resp.status_code == 200:
                return resp.json().get("version", "")
    except Exception as exc:
        logger.debug("[update-checker] Failed to check npm: {}", exc)
    return ""


def _is_newer(latest: str, current: str) -> bool:
    """Compare semver-ish version strings. Returns True if latest > current."""
    try:
        def _parse(v: str) -> tuple[int, ...]:
            return tuple(int(x) for x in v.split("."))
        return _parse(latest) > _parse(current)
    except (ValueError, TypeError):
        return False


async def check_and_notify() -> None:
    """Check for updates and publish a notification if a newer version exists."""
    current = _get_current_version()
    if not current or current == "dev":
        return

    latest = await _fetch_latest_version()
    if not latest:
        return

    if _is_newer(latest, current):
        logger.info("[update-checker] New version available: {} → {}", current, latest)
        from onemancompany.core.events import CompanyEvent, event_bus
        from onemancompany.core.models import EventType
        await event_bus.publish(CompanyEvent(
            type=EventType.ACTIVITY,
            payload={
                "message": f"🆕 New version available: v{current} → v{latest}. "
                           f"Run `npx @1mancompany/onemancompany@latest` to update.",
                "type": "update",
            },
            agent="SYSTEM",
        ))
    else:
        logger.debug("[update-checker] Up to date (v{})", current)


async def start_update_checker() -> None:
    """Start periodic update checks (runs in background)."""
    # Initial check after 10s delay (don't slow down startup)
    await asyncio.sleep(10)
    while True:
        try:
            await check_and_notify()
        except Exception as exc:
            logger.debug("[update-checker] Error: {}", exc)
        await asyncio.sleep(CHECK_INTERVAL_HOURS * 3600)
