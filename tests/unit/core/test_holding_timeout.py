"""Tests for HOLDING global timeout (MAX_HOLD_SECONDS).

Covers:
- TaskNode.hold_started_at field serialization
- _check_holding_timeout logic
- Integration with holding entry point (hold_started_at is set)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from onemancompany.core.task_lifecycle import TaskPhase
from onemancompany.core.task_tree import TaskNode, TaskTree


# ---------------------------------------------------------------------------
# TaskNode.hold_started_at field
# ---------------------------------------------------------------------------

class TestHoldStartedAtField:
    """hold_started_at round-trips through to_dict / from_dict."""

    def test_default_empty(self):
        node = TaskNode()
        assert node.hold_started_at == ""

    def test_to_dict_includes_hold_started_at(self):
        node = TaskNode(hold_started_at="2026-01-01T00:00:00")
        d = node.to_dict()
        assert d["hold_started_at"] == "2026-01-01T00:00:00"

    def test_from_dict_restores_hold_started_at(self):
        d = {
            "id": "abc123",
            "status": "holding",
            "hold_started_at": "2026-01-01T12:00:00",
        }
        node = TaskNode.from_dict(d)
        assert node.hold_started_at == "2026-01-01T12:00:00"

    def test_from_dict_missing_field_uses_default(self):
        d = {"id": "abc123", "status": "pending"}
        node = TaskNode.from_dict(d)
        assert node.hold_started_at == ""


# ---------------------------------------------------------------------------
# _check_holding_timeout
# ---------------------------------------------------------------------------

class TestCheckHoldingTimeout:
    """EmployeeManager._check_holding_timeout auto-fails expired HOLDING tasks."""

    def _make_tree_with_holding_node(self, hold_started_at: str, tmp_path: Path) -> tuple:
        """Helper: create a tree with one HOLDING node, saved to tmp_path."""
        tree = TaskTree("test_proj")
        root = tree.create_root("00010", "root task")
        child = tree.add_child(root.id, "00010", "child task", ["criteria"])
        child.status = TaskPhase.PROCESSING.value
        child.set_status(TaskPhase.HOLDING)
        child.hold_started_at = hold_started_at
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)
        return tree, child, tree_path

    @pytest.fixture
    def manager(self):
        """Create a minimal EmployeeManager mock for testing _check_holding_timeout."""
        # Import here to avoid heavy module-level imports
        from onemancompany.core.vessel import EmployeeManager
        with patch.object(EmployeeManager, "__init__", lambda self: None):
            mgr = EmployeeManager.__new__(EmployeeManager)
            mgr._schedule = {}
            mgr._running_tasks = {}
            mgr._system_tasks = {}
            mgr._hooks = {}
            mgr.task_histories = {}
            mgr._history_summaries = {}
            return mgr

    def test_not_holding_returns_false(self, manager, tmp_path):
        """If the node is not HOLDING, timeout check returns False."""
        tree = TaskTree("test_proj")
        root = tree.create_root("00010", "root task")
        # Node is still PENDING, not HOLDING
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        from onemancompany.core.task_tree import register_tree
        register_tree(tree_path, tree)

        result = manager._check_holding_timeout(str(tree_path), root.id)
        assert result is False

    def test_no_hold_started_at_returns_false(self, manager, tmp_path):
        """If hold_started_at is empty, timeout check returns False (legacy data)."""
        tree, child, tree_path = self._make_tree_with_holding_node("", tmp_path)

        from onemancompany.core.task_tree import register_tree
        register_tree(tree_path, tree)

        result = manager._check_holding_timeout(str(tree_path), child.id)
        assert result is False

    def test_not_expired_returns_false(self, manager, tmp_path):
        """If elapsed < MAX_HOLD_SECONDS, returns False."""
        recent = datetime.now().isoformat()
        tree, child, tree_path = self._make_tree_with_holding_node(recent, tmp_path)

        from onemancompany.core.task_tree import register_tree
        register_tree(tree_path, tree)

        result = manager._check_holding_timeout(str(tree_path), child.id)
        assert result is False

    @patch("onemancompany.core.vessel.stop_cron")
    def test_expired_auto_fails(self, mock_stop_cron, manager, tmp_path):
        """If elapsed > MAX_HOLD_SECONDS, node is set to FAILED and returns True."""
        expired_time = (datetime.now() - timedelta(seconds=2000)).isoformat()
        tree, child, tree_path = self._make_tree_with_holding_node(expired_time, tmp_path)

        from onemancompany.core.task_tree import register_tree
        register_tree(tree_path, tree)

        with patch("onemancompany.core.task_tree.save_tree_async") as mock_save, \
             patch("onemancompany.core.vessel.MAX_HOLD_SECONDS", 1800):
            result = manager._check_holding_timeout(str(tree_path), child.id)

        assert result is True
        assert child.status == TaskPhase.FAILED.value
        # Should stop both cron variants
        mock_stop_cron.assert_any_call("00010", f"reply_{child.id}")
        mock_stop_cron.assert_any_call("00010", f"holding_{child.id}")

    def test_node_not_found_returns_false(self, manager, tmp_path):
        """If node ID doesn't exist in tree, returns False."""
        tree = TaskTree("test_proj")
        tree.create_root("00010", "root task")
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        from onemancompany.core.task_tree import register_tree
        register_tree(tree_path, tree)

        result = manager._check_holding_timeout(str(tree_path), "nonexistent_id")
        assert result is False
