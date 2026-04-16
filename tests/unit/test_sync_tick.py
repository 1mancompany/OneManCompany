"""Unit tests for onemancompany.core.sync_tick — 100% coverage."""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest


@pytest.mark.asyncio
async def test_run_tick_with_changes():
    """When flush_dirty returns categories, broadcast them."""
    mock_ws = MagicMock()
    mock_ws.broadcast = AsyncMock()

    with patch("onemancompany.core.sync_tick.flush_dirty", return_value=["employees", "tasks"]), \
         patch("onemancompany.api.websocket.ws_manager", mock_ws):
        from onemancompany.core.sync_tick import _run_tick
        await _run_tick()

    mock_ws.broadcast.assert_awaited_once_with({
        "type": "state_changed",
        "changed": ["employees", "tasks"],
    })


@pytest.mark.asyncio
async def test_run_tick_no_changes():
    """When flush_dirty returns empty list, no broadcast."""
    mock_ws = MagicMock()
    mock_ws.broadcast = AsyncMock()

    with patch("onemancompany.core.sync_tick.flush_dirty", return_value=[]), \
         patch("onemancompany.api.websocket.ws_manager", mock_ws):
        from onemancompany.core.sync_tick import _run_tick
        await _run_tick()

    mock_ws.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_sync_tick_runs_tick_and_cancels():
    """start_sync_tick loops, runs _run_tick, and re-raises CancelledError."""
    from onemancompany.core.sync_tick import start_sync_tick

    call_count = 0

    async def fake_run_tick():
        nonlocal call_count
        call_count += 1

    original_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        # After first tick, cancel the task
        if call_count >= 1:
            raise asyncio.CancelledError()
        await original_sleep(0)

    with patch("onemancompany.core.sync_tick._run_tick", side_effect=fake_run_tick), \
         patch("onemancompany.core.sync_tick.asyncio.sleep", side_effect=fast_sleep):
        with pytest.raises(asyncio.CancelledError):
            await start_sync_tick()

    assert call_count >= 1


@pytest.mark.asyncio
async def test_start_sync_tick_handles_tick_error():
    """start_sync_tick logs errors from _run_tick but keeps running."""
    from onemancompany.core.sync_tick import start_sync_tick

    call_count = 0

    async def failing_tick():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("tick boom")

    async def fast_sleep(seconds):
        if call_count >= 2:
            raise asyncio.CancelledError()

    with patch("onemancompany.core.sync_tick._run_tick", side_effect=failing_tick), \
         patch("onemancompany.core.sync_tick.asyncio.sleep", side_effect=fast_sleep):
        with pytest.raises(asyncio.CancelledError):
            await start_sync_tick()

    assert call_count >= 2  # Continued past the error
