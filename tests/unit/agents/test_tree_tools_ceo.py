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


class TestDispatchChildCeo:
    """dispatch_child targeting CEO creates ceo_request node without scheduling."""

    def test_ceo_request_node_type_recognized(self):
        """is_ceo_node returns True for ceo_request."""
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="emp001", description="root")
        child = tree.add_child(parent_id=root.id, employee_id=CEO_ID,
                               description="test", acceptance_criteria=["ok"])
        child.node_type = "ceo_request"
        assert child.is_ceo_node is True

    def test_dispatch_child_ceo_skips_scheduling(self):
        """dispatch_child to CEO should NOT call employee_manager.schedule_node."""
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
                    "employee_id": CEO_ID,
                    "description": "Need approval",
                    "acceptance_criteria": ["Approve"],
                })

            mock_em.schedule_node.assert_not_called()
            assert result["status"] == "dispatched"
            assert result.get("ceo_request") is True
            assert result.get("node_type") == "ceo_request"
        finally:
            _reset_context(tok_v, tok_t)

    @pytest.mark.asyncio
    async def test_dispatch_child_ceo_publishes_event(self):
        """dispatch_child to CEO should publish ceo_inbox_updated event."""
        import asyncio
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree()
        root_id = tree.root_id
        vessel = MagicMock()
        tok_v, tok_t = _set_context(vessel, root_id)
        mock_em = _make_mock_em(root_id)
        mock_event_bus = AsyncMock()

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.store.load_employee", return_value={"id": CEO_ID, "name": "CEO"}),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
                patch("onemancompany.core.events.event_bus", mock_event_bus),
            ):
                result = dispatch_child.invoke({
                    "employee_id": CEO_ID,
                    "description": "Need approval",
                    "acceptance_criteria": ["Approve"],
                })
                # Allow the created task to run
                await asyncio.sleep(0)

            assert mock_event_bus.publish.called, "event_bus.publish should have been called"
            call_args = mock_event_bus.publish.call_args
            event = call_args[0][0] if call_args[0] else call_args[1].get("event")
            assert event.type == "ceo_inbox_updated"
        finally:
            _reset_context(tok_v, tok_t)
