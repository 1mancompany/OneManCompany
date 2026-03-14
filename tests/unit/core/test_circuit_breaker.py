"""Tests for tree growth circuit breaker."""
from __future__ import annotations

import pytest
from onemancompany.core.task_tree import TaskNode, TaskTree


class TestReviewCircuitBreaker:
    def test_count_review_rounds(self):
        """Count review-type children under a parent."""
        tree = TaskTree(project_id="test")
        root = tree.create_root("e1", "Root")
        for i in range(3):
            r = tree.add_child(root.id, "e1", f"Review {i}", [])
            r.node_type = "review"
            r.status = "finished"
        children = tree.get_active_children(root.id)
        review_count = sum(1 for c in children if c.node_type == "review")
        assert review_count == 3

    def test_circuit_breaker_threshold(self):
        """When review count >= MAX_REVIEW_ROUNDS, condition is met."""
        from onemancompany.core.config import MAX_REVIEW_ROUNDS
        tree = TaskTree(project_id="test")
        root = tree.create_root("e1", "Root")
        for i in range(MAX_REVIEW_ROUNDS):
            r = tree.add_child(root.id, "e1", f"Review {i}", [])
            r.node_type = "review"
            r.status = "finished"
        children = tree.get_active_children(root.id)
        review_count = sum(
            1 for c in children
            if c.node_type == "review" and c.employee_id == root.employee_id
        )
        assert review_count >= MAX_REVIEW_ROUNDS

    def test_non_review_children_filter(self):
        """Filtering out review nodes leaves only task nodes."""
        tree = TaskTree(project_id="test")
        root = tree.create_root("e1", "Root")
        # Add 2 task children and 1 review child
        tree.add_child(root.id, "e2", "Task A", ["criterion"])
        tree.add_child(root.id, "e3", "Task B", ["criterion"])
        r = tree.add_child(root.id, "e1", "Review", [])
        r.node_type = "review"

        children = tree.get_active_children(root.id)
        non_review = [c for c in children if c.node_type != "review"]
        assert len(non_review) == 2
        assert len(children) == 3
