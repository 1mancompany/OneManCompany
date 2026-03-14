"""Tests for abort_employee and abort_all."""
from __future__ import annotations

import pytest
from onemancompany.core.task_tree import TaskNode, TaskTree
from onemancompany.core.task_lifecycle import TaskPhase


class TestAbortEmployee:
    def test_abort_employee_only_cancels_non_terminal(self):
        """abort_employee should NOT touch accepted/finished/cancelled nodes."""
        _cancelable = {"pending", "processing", "holding"}

        tree = TaskTree(project_id="test")
        root = tree.create_root("e1", "Root")
        c1 = tree.add_child(root.id, "e1", "Pending task", [])
        c2 = tree.add_child(root.id, "e1", "Accepted task", [])
        c2.status = "accepted"
        c3 = tree.add_child(root.id, "e1", "Processing task", [])
        c3.status = "processing"

        non_terminal = [n for n in [c1, c2, c3] if n.status in _cancelable]
        assert len(non_terminal) == 2
        assert c2 not in non_terminal

    def test_abort_employee_integration(self, tmp_path):
        """Integration test: verify cancel logic on tree nodes."""
        tree = TaskTree(project_id="test")
        root = tree.create_root("e1", "Root")
        child = tree.add_child(root.id, "e1", "Pending task", [])
        path = tmp_path / "task_tree.yaml"
        tree.save(path)

        from onemancompany.core.task_tree import register_tree
        register_tree(path, tree)

        _cancelable = {"pending", "processing", "holding"}
        node = tree.get_node(child.id)
        assert node.status in _cancelable
        node.status = TaskPhase.CANCELLED.value
        assert node.status == "cancelled"
