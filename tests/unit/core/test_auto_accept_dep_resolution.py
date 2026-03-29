"""Regression: auto-accept must trigger _trigger_dep_resolution for dependents."""

from __future__ import annotations

from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from onemancompany.core.task_lifecycle import TaskPhase, NodeType


@pytest.mark.asyncio
async def test_auto_accept_triggers_dep_resolution():
    """When a review finishes and siblings are auto-accepted,
    _trigger_dep_resolution must be called for each so downstream
    tasks with depends_on get unblocked."""
    from onemancompany.core.vessel import EmployeeManager, ScheduleEntry

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
    em._pending_ceo_reports = {}

    # Build a minimal tree: parent → [child_task (COMPLETED), review (FINISHED)]
    child_task = MagicMock()
    child_task.id = "child1"
    child_task.node_type = NodeType.TASK
    child_task.status = TaskPhase.COMPLETED.value
    child_task.is_ceo_node = False
    child_task.branch_active = True
    child_task.project_dir = "/fake/project"
    child_task.set_status = MagicMock()
    child_task.acceptance_result = None

    review_node = MagicMock()
    review_node.id = "review1"
    review_node.node_type = NodeType.REVIEW
    review_node.status = TaskPhase.FINISHED.value
    review_node.parent_id = "parent1"
    review_node.is_ceo_node = False
    review_node.branch_active = True
    review_node.project_dir = "/fake/project"
    review_node.project_id = "proj1"

    parent_node = MagicMock()
    parent_node.id = "parent1"
    parent_node.status = TaskPhase.HOLDING.value
    parent_node.is_ceo_node = False
    parent_node.node_type = NodeType.TASK
    parent_node.project_dir = "/fake/project"

    mock_tree = MagicMock()
    mock_tree.get_node = lambda nid: {
        "review1": review_node,
        "parent1": parent_node,
        "child1": child_task,
    }.get(nid)
    mock_tree.get_active_children = MagicMock(return_value=[child_task, review_node])
    mock_tree.get_children = MagicMock(return_value=[child_task, review_node])
    mock_tree.is_project_complete = MagicMock(return_value=False)
    mock_tree.mode = "standard"

    entry = ScheduleEntry(node_id="review1", tree_path="/fake/tree.yaml")
    em._schedule["emp1"] = [entry]

    with patch("onemancompany.core.task_tree.get_tree", return_value=mock_tree), \
         patch("onemancompany.core.task_tree.save_tree_async"), \
         patch("onemancompany.core.vessel._trigger_dep_resolution") as mock_dep_res, \
         patch("onemancompany.core.vessel._store") as mock_store, \
         patch("onemancompany.core.vessel.Path") as mock_path:
        mock_path.return_value.exists.return_value = True
        mock_path.return_value.parent = "/fake"
        mock_store.save_project_status = AsyncMock()
        mock_store.save_employee_runtime = AsyncMock()

        await em._on_child_complete_inner("emp1", entry, project_id="proj1")

        # _trigger_dep_resolution must have been called for auto-accepted child
        assert mock_dep_res.called, "_trigger_dep_resolution was not called after auto-accept"
        # Should be called with the child_task node (the one that was auto-accepted)
        call_args = mock_dep_res.call_args_list
        resolved_nodes = [c.args[2] for c in call_args]
        assert child_task in resolved_nodes, f"Expected child_task in resolved nodes, got {resolved_nodes}"
