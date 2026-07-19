"""Regression tests for #395: entity code (product_id) propagation.

- dispatch_child copies the parent node's product_id to the child.
- set_current_node_product_id stamps the running node and persists.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from unittest.mock import MagicMock, patch

from onemancompany.core.task_tree import TaskTree
from onemancompany.core.vessel import ScheduleEntry, _current_task_id, _current_vessel


def _set_context(vessel, task_id):
    return _current_vessel.set(vessel), _current_task_id.set(task_id)


def _reset_context(tok_v, tok_t):
    _current_vessel.reset(tok_v)
    _current_task_id.reset(tok_t)


def _make_mock_em(root_id, tree_path="/tmp/proj/task_tree.yaml"):
    mock_em = MagicMock()
    mock_em._schedule = defaultdict(list)
    mock_em._schedule["_any_"] = [ScheduleEntry(node_id=root_id, tree_path=tree_path)]
    mock_em._current_entries = {}
    return mock_em


class TestDispatchChildProductPropagation:
    def test_child_inherits_product_id(self):
        from onemancompany.agents.tree_tools import dispatch_child

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00003", description="root")
        root.product_id = "prod_099301bf"
        root_id = tree.root_id

        vessel = MagicMock()
        vessel.employee_id = "00003"
        tok_v, tok_t = _set_context(vessel, root_id)
        mock_em = _make_mock_em(root_id)

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.agents.tree_tools._add_to_project_team"),
                patch("onemancompany.core.store.load_employee", return_value={"id": "00010", "name": "Dev"}),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = dispatch_child.invoke({
                    "target_employee_id": "00010",
                    "description": "为 'Web Agent Safety Framework' 产品制定规划",
                    "acceptance_criteria": ["plan done"],
                })

            child = tree.get_node(result["node_id"])
            assert child.product_id == "prod_099301bf"   # inherited (#395)
            assert child.project_id == root.project_id     # existing behavior preserved
        finally:
            _reset_context(tok_v, tok_t)


class TestSetCurrentNodeProductId:
    def test_stamps_and_persists(self):
        from onemancompany.agents import tree_tools

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00004", description="root")
        root_id = tree.root_id
        assert root.product_id == ""

        vessel = MagicMock()
        vessel.employee_id = "00004"
        tok_v, tok_t = _set_context(vessel, root_id)
        mock_em = _make_mock_em(root_id)
        saved = []

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree", side_effect=lambda *a, **k: saved.append(True)),
                patch("onemancompany.core.task_tree.get_tree_lock", return_value=threading.Lock()),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                ok = tree_tools.set_current_node_product_id("prod_099301bf")

            assert ok is True
            assert tree.get_node(root_id).product_id == "prod_099301bf"
            assert saved == [True]  # persisted once
        finally:
            _reset_context(tok_v, tok_t)

    def test_no_context_returns_false(self):
        """No vessel/task context (system/adhoc) → graceful no-op."""
        from onemancompany.agents import tree_tools
        # No _set_context call — context vars are empty here.
        assert tree_tools.set_current_node_product_id("prod_x") is False
