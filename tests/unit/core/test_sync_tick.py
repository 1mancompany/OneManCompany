"""Tests for core/sync_tick.py — 3-second sync tick loop."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_sync_tick_broadcasts_dirty_categories():
    """sync_tick sends state_changed with dirty categories via ws_manager."""
    from onemancompany.core import store
    from onemancompany.core.sync_tick import _run_tick

    store._dirty.clear()  # ensure no leftover state from other tests
    store.mark_dirty("employees", "rooms")

    mock_broadcast = AsyncMock()
    mock_ws_manager = MagicMock()
    mock_ws_manager.broadcast = mock_broadcast

    with patch("onemancompany.api.websocket.ws_manager", mock_ws_manager):
        await _run_tick()

    mock_broadcast.assert_called_once()
    call_arg = mock_broadcast.call_args[0][0]
    assert call_arg["type"] == "state_changed"
    assert set(call_arg["changed"]) == {"employees", "rooms"}


@pytest.mark.asyncio
async def test_sync_tick_does_nothing_when_clean():
    """sync_tick sends nothing if nothing is dirty."""
    from onemancompany.core.sync_tick import _run_tick
    from onemancompany.core import store

    store._dirty.clear()  # ensure clean

    mock_broadcast = AsyncMock()
    mock_ws_manager = MagicMock()
    mock_ws_manager.broadcast = mock_broadcast

    with patch("onemancompany.api.websocket.ws_manager", mock_ws_manager):
        await _run_tick()

    mock_broadcast.assert_not_called()
