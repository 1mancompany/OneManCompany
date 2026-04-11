"""Tests for dispatch_child CEO interception."""
from __future__ import annotations

from collections import defaultdict
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from onemancompany.core.task_tree import TaskTree
from onemancompany.core.vessel import ScheduleEntry, _current_task_id, _current_vessel


CEO_ID = "00001"


def _set_context(vessel, task_id: str):
    tok_v = _current_vessel.set(vessel)
    tok_t = _current_task_id.set(task_id)
    return tok_v, tok_t


def _reset_context(tok_v, tok_t):
    _current_vessel.reset(tok_v)
    _current_task_id.reset(tok_t)


def _make_tree(project_id="proj1", employee_id="emp001"):
    tree = TaskTree(project_id=project_id)
    tree.create_root(employee_id=employee_id, description="root task")
    return tree


def _make_mock_em(root_id, tree_path="/tmp/proj/task_tree.yaml"):
    mock_em = MagicMock()
    entry = ScheduleEntry(node_id=root_id, tree_path=tree_path)
    mock_em._schedule = defaultdict(list)
    mock_em._schedule["_any_"] = [entry]
    return mock_em


class TestStandaloneCeoRequest:
    """dispatch_child to CEO works even without tree context (system/adhoc tasks)."""

    def test_standalone_ceo_request_no_schedule(self):
        """When task_id not in _schedule but target is CEO, creates standalone request."""
        from onemancompany.agents.tree_tools import dispatch_child

        vessel = MagicMock()
        vessel.employee_id = "00003"
        tok_v, tok_t = _set_context(vessel, "adhoc_task_123")
        # Empty schedule — no tree context
        mock_em = MagicMock()
        mock_em._schedule = defaultdict(list)

        try:
            with patch("onemancompany.core.vessel.employee_manager", mock_em):
                result = dispatch_child.invoke({
                    "target_employee_id": CEO_ID,
                    "description": "Need CEO decision on budget",
                    "acceptance_criteria": ["Approve"],
                })

            assert result["status"] == "dispatched"
            assert result.get("ceo_request") is True
            assert result.get("node_type") == "ceo_request"
            assert "node_id" in result
        finally:
            _reset_context(tok_v, tok_t)

    def test_standalone_non_ceo_still_errors(self):
        """When no tree context and target is NOT CEO, still returns error."""
        from onemancompany.agents.tree_tools import dispatch_child

        vessel = MagicMock()
        vessel.employee_id = "00003"
        tok_v, tok_t = _set_context(vessel, "adhoc_task_123")
        mock_em = MagicMock()
        mock_em._schedule = defaultdict(list)

        try:
            with patch("onemancompany.core.vessel.employee_manager", mock_em):
                result = dispatch_child.invoke({
                    "target_employee_id": "00010",
                    "description": "Some task",
                    "acceptance_criteria": ["Done"],
                })

            assert result["status"] == "error"
            assert "No project directory" in result["message"]
        finally:
            _reset_context(tok_v, tok_t)


class TestDispatchChildCeo:
    """dispatch_child targeting CEO creates ceo_request node and schedules via normal path."""

    def test_ceo_request_node_type_recognized(self):
        """is_ceo_node returns True for ceo_request."""
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="emp001", description="root")
        child = tree.add_child(parent_id=root.id, employee_id=CEO_ID,
                               description="test", acceptance_criteria=["ok"])
        child.node_type = "ceo_request"
        assert child.is_ceo_node is True

    def test_dispatch_child_ceo_calls_schedule_node(self):
        """dispatch_child to CEO SHOULD call employee_manager.schedule_node (normal path)."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree()
        root_id = tree.root_id
        vessel = MagicMock()
        tok_v, tok_t = _set_context(vessel, root_id)
        mock_em = _make_mock_em(root_id)

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.store.load_employee", return_value={"id": CEO_ID, "name": "CEO"}),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = dispatch_child.invoke({
                    "target_employee_id": CEO_ID,
                    "description": "Need approval",
                    "acceptance_criteria": ["Approve"],
                })

            mock_em.schedule_node.assert_called_once()
            assert result["status"] == "dispatched"
            assert result["node_id"] is not None
        finally:
            _reset_context(tok_v, tok_t)

    def test_dispatch_child_ceo_sets_node_type(self):
        """dispatch_child to CEO sets node_type to CEO_REQUEST."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree()
        root_id = tree.root_id
        vessel = MagicMock()
        tok_v, tok_t = _set_context(vessel, root_id)
        mock_em = _make_mock_em(root_id)

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.store.load_employee", return_value={"id": CEO_ID, "name": "CEO"}),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = dispatch_child.invoke({
                    "target_employee_id": CEO_ID,
                    "description": "Need approval",
                    "acceptance_criteria": ["Approve"],
                })

            child_node = tree.get_node(result["node_id"])
            assert child_node.node_type == "ceo_request"
        finally:
            _reset_context(tok_v, tok_t)


class TestCeoRequestIdempotency:
    """Duplicate dispatch_child to CEO should return existing node."""

    def test_duplicate_ceo_request_returns_existing(self):
        """Second dispatch_child to CEO returns already_dispatched."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree()
        root_id = tree.root_id
        vessel = MagicMock()
        tok_v, tok_t = _set_context(vessel, root_id)
        mock_em = _make_mock_em(root_id)

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.store.load_employee", return_value={"id": CEO_ID, "name": "CEO"}),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result1 = dispatch_child.invoke({
                    "target_employee_id": CEO_ID,
                    "description": "Need approval",
                    "acceptance_criteria": ["Approve"],
                })
                assert result1["status"] == "dispatched"
                first_id = result1["node_id"]

                result2 = dispatch_child.invoke({
                    "target_employee_id": CEO_ID,
                    "description": "Need approval again",
                    "acceptance_criteria": ["Approve"],
                })
                assert result2["status"] == "already_dispatched"
                assert result2["node_id"] == first_id
        finally:
            _reset_context(tok_v, tok_t)


class TestHoldReason:
    """dispatch_child to CEO sets hold_reason on parent node."""

    def test_dispatch_ceo_sets_hold_reason_on_parent(self):
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree()
        root_id = tree.root_id
        vessel = MagicMock()
        tok_v, tok_t = _set_context(vessel, root_id)
        mock_em = _make_mock_em(root_id)

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.store.load_employee", return_value={"id": CEO_ID, "name": "CEO"}),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = dispatch_child.invoke({
                    "target_employee_id": CEO_ID,
                    "description": "Need approval",
                    "acceptance_criteria": ["Approve"],
                })

            root = tree.get_node(root_id)
            assert root.hold_reason != ""
            assert f"ceo_request={result['node_id']}" in root.hold_reason
            assert "no_watchdog=1" in root.hold_reason
        finally:
            _reset_context(tok_v, tok_t)

    def test_hold_reason_serializes_to_dict(self):
        """hold_reason field persists through to_dict/from_dict round-trip."""
        from onemancompany.core.task_tree import TaskNode

        node = TaskNode(employee_id="emp001", description="test")
        node.hold_reason = "ceo_request=abc123,no_watchdog=1"
        d = node.to_dict()
        assert d["hold_reason"] == "ceo_request=abc123,no_watchdog=1"

        restored = TaskNode.from_dict(d)
        assert restored.hold_reason == "ceo_request=abc123,no_watchdog=1"
