"""Tests for dependency resolution logic."""
import pytest
from onemancompany.core.task_tree import TaskTree, TaskNode


class TestBuildDependencyContext:
    def test_single_dep_accepted(self):
        """Accepted dependency result injected into context."""
        from onemancompany.core.vessel import _build_dependency_context
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "analyze requirements", [])
        a.status = "accepted"
        a.result = "Requirements: build X with Y"
        b = tree.add_child(root.id, "e2", "implement", [], depends_on=[a.id])

        context = _build_dependency_context(tree, b)
        assert "=== Dependency Results ===" in context
        assert "analyze requirements" in context
        assert "Requirements: build X with Y" in context

    def test_no_deps_returns_empty(self):
        from onemancompany.core.vessel import _build_dependency_context
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        context = _build_dependency_context(tree, a)
        assert context == ""

    def test_result_truncated(self):
        from onemancompany.core.vessel import _build_dependency_context
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        a.status = "accepted"
        a.result = "x" * 5000
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id])

        context = _build_dependency_context(tree, b)
        # Should be truncated to last 2000 chars
        assert len(context) < 3000

    def test_failed_dep_included(self):
        from onemancompany.core.vessel import _build_dependency_context
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        a.status = "failed"
        a.result = "Error: something broke"
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id], fail_strategy="continue")

        context = _build_dependency_context(tree, b)
        assert "failed" in context
        assert "Error: something broke" in context


class TestTreeLock:
    def test_get_tree_lock_returns_lock(self):
        from onemancompany.core.task_tree import get_tree_lock
        import threading
        lock = get_tree_lock("/tmp/test_project/tree.yaml")
        assert isinstance(lock, threading.RLock().__class__)

    def test_same_path_same_lock(self):
        from onemancompany.core.task_tree import get_tree_lock
        lock1 = get_tree_lock("/tmp/same_proj/tree.yaml")
        lock2 = get_tree_lock("/tmp/same_proj/tree.yaml")
        assert lock1 is lock2

    def test_different_path_different_lock(self):
        from onemancompany.core.task_tree import get_tree_lock
        lock1 = get_tree_lock("/tmp/proj_a/tree.yaml")
        lock2 = get_tree_lock("/tmp/proj_b/tree.yaml")
        assert lock1 is not lock2


class TestResolveDependenciesLogic:
    """Test dependency resolution via TaskTree helpers (unit tests)."""

    def test_dep_accepted_unlocks_dependent(self):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        a.status = "accepted"
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id])
        assert tree.all_deps_resolved(b.id)
        assert not tree.has_failed_deps(b.id)

    def test_dep_failed_blocks_dependent(self):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        a.status = "failed"
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id], fail_strategy="block")
        assert tree.has_failed_deps(b.id)

    def test_dep_failed_continue_unlocks(self):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        a.status = "failed"
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id], fail_strategy="continue")
        assert tree.all_deps_resolved(b.id)

    def test_partial_deps_still_waiting(self):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        a.status = "accepted"
        c = tree.add_child(root.id, "e3", "task C", [])
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id, c.id])
        assert not tree.all_deps_resolved(b.id)
