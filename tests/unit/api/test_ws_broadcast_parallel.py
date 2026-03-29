"""Test that WebSocket broadcast sends to all clients in parallel."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from onemancompany.api.websocket import WebSocketManager


@pytest.mark.asyncio
async def test_broadcast_sends_in_parallel():
    """Slow client should not block fast clients. All sends run concurrently."""
    call_times = []

    async def slow_send(msg):
        call_times.append(time.monotonic())
        await asyncio.sleep(0.1)

    mgr = WebSocketManager()
    for _ in range(3):
        ws = MagicMock()
        ws.send_json = AsyncMock(side_effect=slow_send)
        mgr.connections.add(ws)

    await mgr.broadcast({"type": "test"})

    assert len(call_times) == 3
    spread = max(call_times) - min(call_times)
    assert spread < 0.05, f"Sends started {spread:.3f}s apart — not concurrent"


@pytest.mark.asyncio
async def test_broadcast_removes_dead_connections():
    """Failed sends should remove the dead connection, not crash."""
    mgr = WebSocketManager()

    good_ws = MagicMock()
    good_ws.send_json = AsyncMock()

    bad_ws = MagicMock()
    bad_ws.send_json = AsyncMock(side_effect=ConnectionError("gone"))

    mgr.connections.add(good_ws)
    mgr.connections.add(bad_ws)

    await mgr.broadcast({"type": "test"})

    assert good_ws in mgr.connections
    assert bad_ws not in mgr.connections
    good_ws.send_json.assert_called_once()


@pytest.mark.asyncio
async def test_broadcast_timeout_removes_stalled_connection():
    """A client that stalls beyond timeout should be dropped."""
    mgr = WebSocketManager()
    # Patch timeout to 0.2s for fast test
    mgr._SEND_TIMEOUT = 0.2

    stalled_ws = MagicMock()

    async def stall_forever(msg):
        await asyncio.sleep(100)

    stalled_ws.send_json = AsyncMock(side_effect=stall_forever)
    mgr.connections.add(stalled_ws)

    start = time.monotonic()
    await mgr.broadcast({"type": "test"})
    elapsed = time.monotonic() - start

    assert stalled_ws not in mgr.connections
    assert elapsed < 1.0, f"Broadcast took {elapsed:.1f}s — should timeout at 0.2s"


@pytest.mark.asyncio
async def test_broadcast_empty_is_noop():
    """Broadcasting with no connections should not raise."""
    mgr = WebSocketManager()
    await mgr.broadcast({"type": "test"})  # should not raise
