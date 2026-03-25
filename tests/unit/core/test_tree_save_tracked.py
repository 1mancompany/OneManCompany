"""Test that save_tree_async uses spawn_background for GC-safe task tracking."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from onemancompany.core.task_tree import save_tree_async, _cache, _key, TaskTree


@pytest.fixture()
def fake_tree(tmp_path: Path):
    """Register a fake tree in the cache and clean up after."""
    tree = MagicMock(spec=TaskTree)
    tree_path = tmp_path / "tree.yaml"
    key = _key(tree_path)
    _cache[key] = tree
    yield tree, tree_path
    _cache.pop(key, None)


@pytest.mark.asyncio
async def test_save_tree_async_uses_spawn_background(fake_tree):
    """save_tree_async should delegate to spawn_background in async context."""
    tree, tree_path = fake_tree

    with patch("onemancompany.core.async_utils.spawn_background") as mock_spawn:
        save_tree_async(tree_path)
        mock_spawn.assert_called_once()
        # The argument should be a coroutine (_do_save)
        coro = mock_spawn.call_args[0][0]
        assert asyncio.iscoroutine(coro)
        # Clean up the unawaited coroutine
        coro.close()


@pytest.mark.asyncio
async def test_save_tree_async_no_tree_is_noop():
    """save_tree_async should be a no-op when tree is not in cache."""
    with patch("onemancompany.core.async_utils.spawn_background") as mock_spawn:
        save_tree_async("/nonexistent/path/tree.yaml")
        mock_spawn.assert_not_called()


def test_save_tree_async_sync_fallback(fake_tree):
    """When no event loop is running, save_tree_async saves synchronously."""
    tree, tree_path = fake_tree

    with patch("onemancompany.core.async_utils.spawn_background") as mock_spawn:
        save_tree_async(tree_path)
        # spawn_background should NOT be called in sync context
        mock_spawn.assert_not_called()
        # Tree.save should have been called synchronously
        tree.save.assert_called_once_with(tree_path)
