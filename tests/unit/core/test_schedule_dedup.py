"""Tests for schedule_node dedup and resume_held_task double-call guard."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from onemancompany.core.vessel import EmployeeManager, ScheduleEntry


class TestScheduleNodeDedup:
    """schedule_node must not create duplicate entries for the same node_id."""

    def test_no_duplicate_entries(self):
        em = EmployeeManager.__new__(EmployeeManager)
        em._schedule = {}
        em.executors = {"emp1": MagicMock()}

        with patch("onemancompany.core.store.append_task_index_entry"):
            em.schedule_node("emp1", "node1", "/path/tree.yaml")
            em.schedule_node("emp1", "node1", "/path/tree.yaml")

        entries = em._schedule["emp1"]
        node_ids = [e.node_id for e in entries]
        assert node_ids == ["node1"], f"Expected 1 entry, got {len(node_ids)}: {node_ids}"

    def test_different_nodes_both_added(self):
        em = EmployeeManager.__new__(EmployeeManager)
        em._schedule = {}
        em.executors = {"emp1": MagicMock()}

        with patch("onemancompany.core.store.append_task_index_entry"):
            em.schedule_node("emp1", "node1", "/path/tree.yaml")
            em.schedule_node("emp1", "node2", "/path/tree.yaml")

        entries = em._schedule["emp1"]
        node_ids = [e.node_id for e in entries]
        assert node_ids == ["node1", "node2"]

    def test_same_node_different_tree_path_still_deduped(self):
        """node_id is globally unique — different tree_path should not create duplicate."""
        em = EmployeeManager.__new__(EmployeeManager)
        em._schedule = {}
        em.executors = {"emp1": MagicMock()}

        with patch("onemancompany.core.store.append_task_index_entry"):
            em.schedule_node("emp1", "node1", "/path/tree_v1.yaml")
            em.schedule_node("emp1", "node1", "/path/tree_v2.yaml")

        entries = em._schedule["emp1"]
        assert len(entries) == 1


class TestResumeHeldTaskGuard:
    """resume_held_task must guard against double-resume (idempotent)."""

    @pytest.mark.asyncio
    async def test_second_resume_returns_false(self):
        """If a task was already resumed (no longer HOLDING), return False."""
        em = EmployeeManager.__new__(EmployeeManager)
        em._schedule = {}
        em.executors = {"emp1": MagicMock()}
        em._running_tasks = {}
        em._hooks = {}
        em._deferred_schedule = set()
        em._event_loop = None
        em._employees = {}
        em._completion_queue = None
        em._completion_consumer = None

        mock_node = MagicMock()
        mock_node.status = "completed"  # already resumed
        mock_node.node_type = "task"
        mock_tree = MagicMock()
        mock_tree.get_node.return_value = mock_node

        entry = ScheduleEntry(node_id="task1", tree_path="/fake/tree.yaml")
        em._schedule["emp1"] = [entry]

        with patch("onemancompany.core.task_tree.get_tree", return_value=mock_tree):
            result = await em.resume_held_task("emp1", "task1", "some result")

        assert result is False, "Second resume should return False (not HOLDING)"
