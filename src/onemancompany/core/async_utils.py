"""Async utilities — shared helpers for asyncio task management."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from loguru import logger

# Strong references to prevent Python 3.12+ GC of fire-and-forget tasks.
_background_tasks: set[asyncio.Task] = set()


def spawn_background(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """Launch a fire-and-forget background task with GC protection.

    Python 3.12+ only keeps weak references to asyncio tasks.
    Without a strong reference, fire-and-forget tasks may be silently
    garbage-collected before completion.
    """
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    logger.debug("spawn_background: created task {} (total active: {})", task.get_name(), len(_background_tasks))

    def _on_done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if t.cancelled():
            logger.debug("Background task {} cancelled", t.get_name())
        elif t.exception():
            logger.error("Background task {} failed: {}", t.get_name(), t.exception())
            logger.opt(exception=t.exception()).debug("Background task {} traceback", t.get_name())
        else:
            logger.debug("Background task {} completed successfully", t.get_name())

    task.add_done_callback(_on_done)
    return task
