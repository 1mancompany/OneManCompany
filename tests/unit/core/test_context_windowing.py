"""Tests for distance-based tree context windowing."""
from __future__ import annotations

import pytest
from onemancompany.core.task_tree import TaskNode, TaskTree


class TestBuildTreeContext:
    def _make_tree(self, tmp_path):
        """Create a 4-level tree for testing context windowing."""
        tree = TaskTree(project_id="test")
        root = tree.create_root("e1", "Root task: build the app")
        root.result = "Root result text"
        root.project_dir = str(tmp_path)

        child = tree.add_child(root.id, "e2", "Child task: implement API", [])
        child.result = "Child result: API done"
        child.project_dir = str(tmp_path)

        grandchild = tree.add_child(child.id, "e3", "Grandchild: write endpoints", [])
        grandchild.result = "Grandchild result: endpoints written"
        grandchild.project_dir = str(tmp_path)

        great_gc = tree.add_child(grandchild.id, "e4", "Great-grandchild: tests", [])
        great_gc.result = "Great-gc result: tests pass"
        great_gc.project_dir = str(tmp_path)

        # Save content files so load_content works
        path = tmp_path / "task_tree.yaml"
        tree.save(path)
        return tree, root, child, grandchild, great_gc

    def test_current_node_has_full_content(self, tmp_path):
        from onemancompany.core.vessel import _build_tree_context
        tree, _, _, _, great_gc = self._make_tree(tmp_path)
        ctx = _build_tree_context(tree, great_gc, str(tmp_path))
        assert "Great-grandchild: tests" in ctx
        assert "Great-gc result: tests pass" in ctx

    def test_parent_has_full_content(self, tmp_path):
        from onemancompany.core.vessel import _build_tree_context
        tree, _, _, grandchild, great_gc = self._make_tree(tmp_path)
        ctx = _build_tree_context(tree, great_gc, str(tmp_path))
        assert "Grandchild: write endpoints" in ctx
        assert "Grandchild result: endpoints written" in ctx

    def test_grandparent_has_preview_only(self, tmp_path):
        from onemancompany.core.vessel import _build_tree_context
        tree, _, child, grandchild, great_gc = self._make_tree(tmp_path)
        ctx = _build_tree_context(tree, great_gc, str(tmp_path))
        # Grandparent (child, distance=2) should have preview only, NOT full result
        assert child.id in ctx
        assert "Child result: API done" not in ctx
        # Parent (grandchild, distance=1) SHOULD have full result
        assert "Grandchild result: endpoints written" in ctx

    def test_accepted_children_show_preview_only(self, tmp_path):
        from onemancompany.core.vessel import _build_tree_context
        tree, root, child, _, _ = self._make_tree(tmp_path)
        child.status = "accepted"
        ctx = _build_tree_context(tree, root, str(tmp_path))
        assert child.id in ctx
        # Full result should NOT be in context for accepted children
        assert "Child result: API done" not in ctx

    def test_no_ancestors_for_root(self, tmp_path):
        from onemancompany.core.vessel import _build_tree_context
        tree, root, _, _, _ = self._make_tree(tmp_path)
        ctx = _build_tree_context(tree, root, str(tmp_path))
        assert "Task Chain" not in ctx
        assert "Current Task" in ctx

    def test_completed_children_show_full_result(self, tmp_path):
        from onemancompany.core.vessel import _build_tree_context
        tree, root, child, _, _ = self._make_tree(tmp_path)
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.COMPLETED)
        ctx = _build_tree_context(tree, root, str(tmp_path))
        assert "Child result: API done" in ctx


# Need TaskPhase for status transitions
from onemancompany.core.task_lifecycle import TaskPhase
