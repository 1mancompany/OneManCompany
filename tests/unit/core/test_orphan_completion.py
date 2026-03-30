"""Tests for orphaned node auto-accept and project completion propagation.

Bug: When a regular task node completes but its parent is already RESOLVED,
nobody accepts it → it stays COMPLETED forever → is_project_complete() returns
False → CEO_PROMPT never advances → no retrospective.

Three fixes covered:
1. _on_child_complete_inner: auto-accept ANY node whose parent is RESOLVED
2. recover_schedule_from_trees: re-check is_project_complete after orphan cleanup
3. Recovery should skip CEO_PROMPT nodes from scheduling
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.task_lifecycle import NodeType, TaskPhase
from onemancompany.core.task_tree import TaskTree, TaskNode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_tree_with_orphan() -> tuple[TaskTree, Path]:
    """Build tree matching the bug scenario:

    CEO_PROMPT (pending)
      └── EA task (finished)
            ├── task_a (finished)
            │   ├── leaf_1 (finished)
            │   ├── review_1 (finished)
            │   │   └── orphan_task (COMPLETED ← the bug)
            │   └── review_2 (finished)
            └── review_ea (finished)
    """
    tree = TaskTree(project_id="test/iter_001", mode="standard")

    ceo = tree.create_root(employee_id="00001", description="CEO prompt")
    ceo.node_type = NodeType.CEO_PROMPT.value
    # CEO stays PENDING — this is the symptom

    ea = tree.add_child(ceo.id, "00004", "EA dispatch", [])
    ea.node_type = NodeType.TASK.value
    ea.set_status(TaskPhase.PROCESSING)
    ea.set_status(TaskPhase.COMPLETED)
    ea.set_status(TaskPhase.ACCEPTED)
    ea.set_status(TaskPhase.FINISHED)

    task_a = tree.add_child(ea.id, "00003", "Task A", [])
    task_a.node_type = NodeType.TASK.value
    task_a.set_status(TaskPhase.PROCESSING)
    task_a.set_status(TaskPhase.COMPLETED)
    task_a.set_status(TaskPhase.ACCEPTED)
    task_a.set_status(TaskPhase.FINISHED)

    leaf_1 = tree.add_child(task_a.id, "00006", "Leaf task", [])
    leaf_1.node_type = NodeType.TASK.value
    leaf_1.set_status(TaskPhase.PROCESSING)
    leaf_1.set_status(TaskPhase.COMPLETED)
    leaf_1.set_status(TaskPhase.ACCEPTED)
    leaf_1.set_status(TaskPhase.FINISHED)

    review_1 = tree.add_child(task_a.id, "00003", "Review 1", [])
    review_1.node_type = NodeType.REVIEW.value
    review_1.set_status(TaskPhase.PROCESSING)
    review_1.set_status(TaskPhase.COMPLETED)
    review_1.set_status(TaskPhase.ACCEPTED)
    review_1.set_status(TaskPhase.FINISHED)

    # THE ORPHAN: review_1 dispatched this child, then review_1 was auto-finished.
    # This task completed AFTER review_1 was already FINISHED.
    orphan = tree.add_child(review_1.id, "00002", "Orphan task", [])
    orphan.node_type = NodeType.TASK.value
    orphan.set_status(TaskPhase.PROCESSING)
    orphan.set_status(TaskPhase.COMPLETED)
    # Stays COMPLETED — nobody accepts it because parent is FINISHED

    review_2 = tree.add_child(task_a.id, "00003", "Review 2", [])
    review_2.node_type = NodeType.REVIEW.value
    review_2.set_status(TaskPhase.PROCESSING)
    review_2.set_status(TaskPhase.COMPLETED)
    review_2.set_status(TaskPhase.ACCEPTED)
    review_2.set_status(TaskPhase.FINISHED)

    review_ea = tree.add_child(ea.id, "00004", "Review EA", [])
    review_ea.node_type = NodeType.REVIEW.value
    review_ea.set_status(TaskPhase.PROCESSING)
    review_ea.set_status(TaskPhase.COMPLETED)
    review_ea.set_status(TaskPhase.ACCEPTED)
    review_ea.set_status(TaskPhase.FINISHED)

    return tree, orphan.id


# ---------------------------------------------------------------------------
# Fix 1: _on_child_complete_inner auto-accepts orphaned non-system nodes
# ---------------------------------------------------------------------------

class TestOrphanAutoAccept:
    """When any node completes and its parent is already RESOLVED,
    auto-accept it to FINISHED."""

    def test_orphan_blocks_project_completion(self):
        """Verify the bug: orphaned COMPLETED node blocks is_project_complete."""
        tree, orphan_id = _build_tree_with_orphan()
        orphan = tree.get_node(orphan_id)
        assert orphan.status == TaskPhase.COMPLETED.value
        # The orphan's parent is FINISHED
        parent = tree.get_node(orphan.parent_id)
        assert parent.status == TaskPhase.FINISHED.value
        # Project should NOT be complete because orphan is not RESOLVED
        assert tree.is_project_complete() is False

    def test_orphan_finished_allows_project_completion(self):
        """If orphan is promoted to FINISHED, project should be complete."""
        tree, orphan_id = _build_tree_with_orphan()
        orphan = tree.get_node(orphan_id)
        orphan.set_status(TaskPhase.ACCEPTED)
        orphan.set_status(TaskPhase.FINISHED)
        assert tree.is_project_complete() is True

    @pytest.mark.asyncio
    async def test_on_child_complete_auto_accepts_orphan(self):
        """_on_child_complete_inner should auto-accept a non-system node
        whose parent is already RESOLVED."""
        tree, orphan_id = _build_tree_with_orphan()

        with tempfile.TemporaryDirectory() as tmpdir:
            tree_path = Path(tmpdir) / "task_tree.yaml"
            tree.save(tree_path)

            # Register tree in cache
            from onemancompany.core.task_tree import register_tree, _cache
            register_tree(tree_path, tree)

            orphan = tree.get_node(orphan_id)

            # Build minimal EmployeeManager
            from onemancompany.core.vessel import EmployeeManager, ScheduleEntry
            em = EmployeeManager.__new__(EmployeeManager)
            em._running_tasks = {}
            em._schedule = {}
            em._restart_pending = False
            em.executors = {}
            em._current_entries = {}
            em._completion_queue = None
            em._completion_consumer = None

            # Mock methods that have side effects
            em._publish_node_update = MagicMock()
            em._schedule_next = MagicMock()

            entry = ScheduleEntry(node_id=orphan_id, tree_path=str(tree_path))

            await em._on_child_complete_inner("00002", entry, "test/iter_001")

            # Orphan should now be FINISHED
            assert orphan.status == TaskPhase.FINISHED.value

            # Clean up cache
            _cache.pop(str(tree_path.resolve()), None)


# ---------------------------------------------------------------------------
# Fix 2: Recovery triggers project completion check after orphan cleanup
# ---------------------------------------------------------------------------

class TestRecoveryProjectCompletion:
    """recover_schedule_from_trees should re-check is_project_complete
    after auto-finishing orphaned nodes."""

    def test_recovery_auto_finishes_orphan(self):
        """Existing behavior: recovery auto-finishes COMPLETED nodes with RESOLVED parent."""
        tree, orphan_id = _build_tree_with_orphan()

        with tempfile.TemporaryDirectory() as tmpdir:
            tree_path = Path(tmpdir) / "iter_001" / "task_tree.yaml"
            tree_path.parent.mkdir(parents=True)
            tree.save(tree_path)

            from onemancompany.core.task_tree import _cache
            _cache.clear()

            em = MagicMock()
            em.schedule_node = MagicMock()

            from onemancompany.core.task_persistence import recover_schedule_from_trees
            recover_schedule_from_trees(em, Path(tmpdir), Path("/nonexistent"))

            # Reload and check orphan is now finished
            from onemancompany.core.task_tree import get_tree
            reloaded = get_tree(tree_path)
            orphan = reloaded.get_node(orphan_id)
            assert orphan.status == TaskPhase.FINISHED.value

            _cache.clear()

    def test_recovery_triggers_completion_for_now_complete_project(self):
        """After orphan cleanup, if project is now complete,
        recovery should advance CEO_PROMPT and trigger completion flow."""
        tree, orphan_id = _build_tree_with_orphan()

        with tempfile.TemporaryDirectory() as tmpdir:
            tree_path = Path(tmpdir) / "iter_001" / "task_tree.yaml"
            tree_path.parent.mkdir(parents=True)
            tree.save(tree_path)

            from onemancompany.core.task_tree import _cache
            _cache.clear()

            em = MagicMock()
            em.schedule_node = MagicMock()

            from onemancompany.core.task_persistence import recover_schedule_from_trees
            recover_schedule_from_trees(em, Path(tmpdir), Path("/nonexistent"))

            # After recovery, project should be complete
            from onemancompany.core.task_tree import get_tree
            reloaded = get_tree(tree_path)
            assert reloaded.is_project_complete() is True

            # CEO_PROMPT should have been advanced past PENDING
            ceo = reloaded.get_node(reloaded.root_id)
            assert ceo.status != TaskPhase.PENDING.value, \
                "CEO_PROMPT should not remain PENDING after project is complete"

            _cache.clear()


# ---------------------------------------------------------------------------
# Fix 3: Skip CEO_PROMPT nodes from scheduling
# ---------------------------------------------------------------------------

class TestRecoverySkipsCeoPrompt:
    """CEO_PROMPT nodes should not be scheduled — they are containers."""

    def test_recovery_does_not_schedule_ceo_prompt(self):
        """CEO_PROMPT PENDING node should NOT be passed to schedule_node."""
        tree, _ = _build_tree_with_orphan()

        with tempfile.TemporaryDirectory() as tmpdir:
            tree_path = Path(tmpdir) / "iter_001" / "task_tree.yaml"
            tree_path.parent.mkdir(parents=True)
            tree.save(tree_path)

            from onemancompany.core.task_tree import _cache
            _cache.clear()

            em = MagicMock()
            em.schedule_node = MagicMock()

            from onemancompany.core.task_persistence import recover_schedule_from_trees
            recover_schedule_from_trees(em, Path(tmpdir), Path("/nonexistent"))

            # Check that schedule_node was never called with CEO employee ID
            for call in em.schedule_node.call_args_list:
                emp_id = call[0][0]  # First positional arg
                assert emp_id != "00001", \
                    f"schedule_node should not be called for CEO (00001), got: {call}"

            _cache.clear()
