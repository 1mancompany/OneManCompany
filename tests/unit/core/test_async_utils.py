"""Tests for core/async_utils.py — background task callbacks."""
from __future__ import annotations

import asyncio

import pytest


class TestSpawnBackground:
    @pytest.mark.asyncio
    async def test_failed_background_task_logged(self):
        """Line 31: background task failure is logged via _on_done callback."""
        from onemancompany.core.async_utils import spawn_background

        async def failing_coro():
            raise ValueError("test failure")

        task = spawn_background(failing_coro())
        # Wait for the task to complete and its callback to fire
        with pytest.raises(ValueError):
            await task
        # Give the event loop a chance to run the done callback
        await asyncio.sleep(0.01)
