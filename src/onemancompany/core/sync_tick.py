"""3-second sync tick — broadcasts dirty categories to WebSocket clients.

The tick loop runs as a background asyncio.Task started in the FastAPI lifespan.
Each tick:
  1. Calls store.flush_dirty() to get changed categories
  2. Broadcasts {"type": "state_changed", "changed": [...]} to all WS clients
  3. Sleeps 3 seconds

Chat messages are still pushed in real-time (outside the tick).
"""
from __future__ import annotations

import asyncio

from loguru import logger

from onemancompany.core.store import flush_dirty

TICK_INTERVAL_SECONDS = 3.0


async def _run_tick() -> None:
    """Execute one tick — broadcast dirty categories if any."""
    from onemancompany.api.websocket import ws_manager

    changed = flush_dirty()
    if changed:
        await ws_manager.broadcast({
            "type": "state_changed",
            "changed": changed,
        })


async def start_sync_tick() -> None:
    """Run the sync tick loop forever (call as background task)."""
    logger.info("Sync tick started ({}s interval)", TICK_INTERVAL_SECONDS)
    try:
        while True:
            await asyncio.sleep(TICK_INTERVAL_SECONDS)
            try:
                await _run_tick()
            except Exception as e:
                logger.error("Sync tick error: {}", e)
    except asyncio.CancelledError:
        logger.info("Sync tick stopped")
        raise
