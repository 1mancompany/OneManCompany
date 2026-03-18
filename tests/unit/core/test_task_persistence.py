"""Unit tests for core/task_persistence.py — tree-based schedule recovery."""

from __future__ import annotations

import yaml
import pytest
from pathlib import Path

from onemancompany.core.task_lifecycle import TaskPhase
from onemancompany.core import task_persistence as tp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_employees_dir(tmp_path, monkeypatch):
    """Redirect EMPLOYEES_DIR to a temp directory for every test."""
    monkeypatch.setattr(tp, "EMPLOYEES_DIR", tmp_path)
    yield


# ---------------------------------------------------------------------------
# _tasks_dir
# ---------------------------------------------------------------------------

class TestTasksDir:
    def test_returns_correct_path(self, tmp_path):
        expected = tmp_path / "00010" / "tasks"
        assert tp._tasks_dir("00010") == expected


# ---------------------------------------------------------------------------
# recover_schedule_from_trees
# ---------------------------------------------------------------------------

class _MockEM:
    """Minimal mock of EmployeeManager for schedule_node() tracking."""

    def __init__(self):
        self.scheduled: list[tuple[str, str, str]] = []

    def schedule_node(self, emp_id: str, node_id: str, tree_path: str) -> None:
        self.scheduled.append((emp_id, node_id, tree_path))


class TestRecoverScheduleFromTrees:
    def test_resets_processing_to_pending(self, tmp_path):
        """PROCESSING nodes should be reset to PENDING on recovery."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree("proj1")
        root = tree.create_root("emp1", "root task")
        root.status = "processing"
        proj_dir = tmp_path / "projects" / "proj1"
        tree_path = proj_dir / "task_tree.yaml"
        tree.save(tree_path)

        em = _MockEM()
        tp.recover_schedule_from_trees(em, tmp_path / "projects", tmp_path / "employees")

        # Verify node reset to pending
        loaded = TaskTree.load(tree_path)
        assert loaded.get_node(root.id).status == "pending"
        # And scheduled
        assert len(em.scheduled) == 1

    def test_schedules_pending_with_deps_met(self, tmp_path):
        """PENDING nodes with deps resolved should be scheduled."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree("proj1")
        root = tree.create_root("emp1", "root")
        child = tree.add_child(root.id, "emp2", "child", [])
        root.status = "accepted"  # parent is done
        # child is pending with no deps
        proj_dir = tmp_path / "projects" / "proj1"
        tree_path = proj_dir / "task_tree.yaml"
        tree.save(tree_path)

        em = _MockEM()
        tp.recover_schedule_from_trees(em, tmp_path / "projects", tmp_path / "employees")

        # child should be scheduled (it's pending with no deps)
        assert any(s[1] == child.id for s in em.scheduled)

    def test_skips_resolved_nodes(self, tmp_path):
        """Resolved nodes (accepted, finished, etc.) should not be scheduled."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree("proj1")
        root = tree.create_root("emp1", "root")
        root.status = "accepted"
        proj_dir = tmp_path / "projects" / "proj1"
        tree_path = proj_dir / "task_tree.yaml"
        tree.save(tree_path)

        em = _MockEM()
        tp.recover_schedule_from_trees(em, tmp_path / "projects", tmp_path / "employees")

        assert len(em.scheduled) == 0

    def test_holding_nodes_left_as_is(self, tmp_path):
        """HOLDING nodes should not be reset or scheduled."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree("proj1")
        root = tree.create_root("emp1", "root")
        root.status = "holding"
        proj_dir = tmp_path / "projects" / "proj1"
        tree_path = proj_dir / "task_tree.yaml"
        tree.save(tree_path)

        em = _MockEM()
        tp.recover_schedule_from_trees(em, tmp_path / "projects", tmp_path / "employees")

        loaded = TaskTree.load(tree_path)
        assert loaded.get_node(root.id).status == "holding"
        assert len(em.scheduled) == 0

    def test_pending_with_unresolved_deps_not_scheduled(self, tmp_path):
        """PENDING nodes whose deps are not yet resolved should NOT be scheduled."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree("proj1")
        root = tree.create_root("emp1", "root")
        child_a = tree.add_child(root.id, "emp2", "child A", [])
        child_b = tree.add_child(root.id, "emp3", "child B", [], depends_on=[child_a.id])
        # child_a is pending, so child_b's dep is unresolved
        proj_dir = tmp_path / "projects" / "proj1"
        tree_path = proj_dir / "task_tree.yaml"
        tree.save(tree_path)

        em = _MockEM()
        tp.recover_schedule_from_trees(em, tmp_path / "projects", tmp_path / "employees")

        scheduled_ids = {s[1] for s in em.scheduled}
        assert child_a.id in scheduled_ids  # pending, no deps → scheduled
        assert child_b.id not in scheduled_ids  # pending, dep unresolved → NOT scheduled

    def test_recovers_system_task_trees(self, tmp_path):
        """System task trees should also be recovered."""
        from onemancompany.core.system_tasks import SystemTaskTree

        sys_tree = SystemTaskTree("emp1")
        node = sys_tree.create_system_node("emp1", "check gmail")
        node.status = "processing"
        emp_dir = tmp_path / "employees" / "emp1"
        sys_path = emp_dir / "system_tasks.yaml"
        sys_tree.save(sys_path)

        em = _MockEM()
        tp.recover_schedule_from_trees(em, tmp_path / "projects", tmp_path / "employees")

        # Node should be reset and scheduled
        loaded = SystemTaskTree.load(sys_path, "emp1")
        recovered = loaded.get_all_nodes()
        assert len(recovered) == 1
        assert recovered[0].status == "pending"
        assert any(s[1] == node.id for s in em.scheduled)

    def test_skips_corrupt_tree_files(self, tmp_path):
        """Corrupt tree YAML files should be skipped without crashing."""
        proj_dir = tmp_path / "projects" / "badproj"
        tree_path = proj_dir / "task_tree.yaml"
        tree_path.parent.mkdir(parents=True, exist_ok=True)
        tree_path.write_text(": : : invalid yaml {{{\n", encoding="utf-8")

        em = _MockEM()
        # Should not raise
        tp.recover_schedule_from_trees(em, tmp_path / "projects", tmp_path / "employees")
        assert len(em.scheduled) == 0

    def test_empty_dirs(self, tmp_path):
        """No crash when projects/employees dirs don't exist."""
        em = _MockEM()
        tp.recover_schedule_from_trees(em, tmp_path / "projects", tmp_path / "employees")
        assert len(em.scheduled) == 0

    def test_system_task_tree_preserves_old_nodes(self, tmp_path):
        """Finished system task nodes must NOT be deleted on save — trees only grow."""
        from datetime import datetime, timedelta
        from onemancompany.core.system_tasks import SystemTaskTree

        sys_tree = SystemTaskTree("emp1")
        node = sys_tree.create_system_node("emp1", "old task")
        node.status = "finished"
        node.completed_at = (datetime.now() - timedelta(hours=48)).isoformat()

        sys_path = tmp_path / "system_tasks.yaml"
        sys_tree.save(sys_path)

        loaded = SystemTaskTree.load(sys_path, "emp1")
        assert len(loaded.get_all_nodes()) == 1, "Finished nodes must be preserved on save"
