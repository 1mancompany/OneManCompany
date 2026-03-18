"""Tests for the project_progress_watchdog system cron."""
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from onemancompany.core.system_cron import (
    _watchdog_nudged,
    clear_watchdog_nudge,
    project_progress_watchdog,
    _build_tree_status_summary,
)
from onemancompany.core.task_tree import TaskTree, TaskNode


def _make_tree(project_id: str, nodes_spec: list[dict]) -> TaskTree:
    """Helper: build a TaskTree with the given nodes.

    nodes_spec: list of dicts with keys:
        id, status, employee_id, node_type (default "task"),
        branch_active (default True), parent_id (default "")
    The first node is used as root.
    """
    tree = TaskTree(project_id=project_id)
    for i, spec in enumerate(nodes_spec):
        node = TaskNode(
            id=spec["id"],
            status=spec.get("status", "pending"),
            employee_id=spec.get("employee_id", "00004"),
            node_type=spec.get("node_type", "task"),
            project_id=project_id,
            project_dir="/tmp/fake_project",
        )
        node.branch_active = spec.get("branch_active", True)
        node.parent_id = spec.get("parent_id", "")
        if i == 0:
            node.node_type = spec.get("node_type", "ceo_prompt")
            tree.root_id = node.id
        tree._nodes[node.id] = node
        # Wire children_ids
        if node.parent_id and node.parent_id in tree._nodes:
            parent = tree._nodes[node.parent_id]
            if node.id not in parent.children_ids:
                parent.children_ids.append(node.id)
    return tree


@pytest.fixture(autouse=True)
def _clear_nudge_state():
    """Reset watchdog nudge set between tests."""
    _watchdog_nudged.clear()
    yield
    _watchdog_nudged.clear()


# ---------------------------------------------------------------------------
# _build_tree_status_summary
# ---------------------------------------------------------------------------

def test_build_tree_status_summary_groups_by_status():
    tree = _make_tree("proj_1", [
        {"id": "root", "node_type": "ceo_prompt"},
        {"id": "ea", "status": "completed", "parent_id": "root"},
        {"id": "t1", "status": "completed", "parent_id": "ea"},
        {"id": "t2", "status": "pending", "parent_id": "ea"},
        {"id": "t3", "status": "finished", "parent_id": "ea"},
    ])
    summary = _build_tree_status_summary(tree)
    assert "pending" in summary
    assert "completed" in summary
    assert "finished" in summary


def test_build_tree_status_summary_excludes_inactive_branch():
    tree = _make_tree("proj_2", [
        {"id": "root", "node_type": "ceo_prompt"},
        {"id": "old", "status": "pending", "parent_id": "root", "branch_active": False},
        {"id": "new", "status": "completed", "parent_id": "root"},
    ])
    summary = _build_tree_status_summary(tree)
    # Only active-branch nodes should appear (and root is excluded)
    assert "completed" in summary
    # old node is inactive, so pending shouldn't show
    assert "pending" not in summary


# ---------------------------------------------------------------------------
# project_progress_watchdog — main handler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_watchdog_skips_when_processing_exists(tmp_path):
    """If any node is PROCESSING, the project should be skipped."""
    tree = _make_tree("proj_active", [
        {"id": "root", "node_type": "ceo_prompt"},
        {"id": "ea", "status": "holding", "parent_id": "root"},
        {"id": "t1", "status": "processing", "parent_id": "ea"},
    ])

    projects_dir = tmp_path / "projects" / "proj_active"
    projects_dir.mkdir(parents=True)
    tree.save(projects_dir / "task_tree.yaml")

    mock_em = MagicMock()
    mock_em._schedule = {}
    mock_em._running_tasks = {}

    with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path / "projects"), \
         patch("onemancompany.core.vessel.employee_manager", mock_em):
        events = await project_progress_watchdog()

    assert events is None
    mock_em.push_task.assert_not_called()


@pytest.mark.asyncio
async def test_watchdog_skips_fully_terminal(tmp_path):
    """If all active nodes are terminal, project is done — skip."""
    tree = _make_tree("proj_done", [
        {"id": "root", "node_type": "ceo_prompt"},
        {"id": "ea", "status": "finished", "parent_id": "root"},
        {"id": "t1", "status": "finished", "parent_id": "ea"},
        {"id": "t2", "status": "cancelled", "parent_id": "ea"},
    ])

    projects_dir = tmp_path / "projects" / "proj_done"
    projects_dir.mkdir(parents=True)
    tree.save(projects_dir / "task_tree.yaml")

    mock_em = MagicMock()
    mock_em._schedule = {}
    mock_em._running_tasks = {}

    with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path / "projects"), \
         patch("onemancompany.core.vessel.employee_manager", mock_em):
        events = await project_progress_watchdog()

    assert events is None
    mock_em.push_task.assert_not_called()


@pytest.mark.asyncio
async def test_watchdog_nudges_when_all_pending(tmp_path):
    """All nodes pending (nobody working) should trigger EA nudge."""
    tree = _make_tree("proj_pending", [
        {"id": "root", "node_type": "ceo_prompt"},
        {"id": "ea", "status": "pending", "parent_id": "root", "employee_id": "00004"},
        {"id": "t1", "status": "pending", "employee_id": "00010", "parent_id": "ea"},
        {"id": "t2", "status": "pending", "employee_id": "00011", "parent_id": "ea"},
    ])

    projects_dir = tmp_path / "projects" / "proj_pending"
    projects_dir.mkdir(parents=True)
    tree.save(projects_dir / "task_tree.yaml")

    mock_em = MagicMock()
    mock_em._schedule = {}
    mock_em._running_tasks = {}

    with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path / "projects"), \
         patch("onemancompany.core.vessel.employee_manager", mock_em), \
         patch("onemancompany.core.task_tree.save_tree_async"):
        events = await project_progress_watchdog()

    assert events is not None
    assert events[0].payload["watchdog_nudged"] == ["proj_pending"]
    mock_em.push_task.assert_called_once()


@pytest.mark.asyncio
async def test_watchdog_nudges_stuck_project(tmp_path):
    """A stuck project (completed children, no processing) triggers EA nudge."""
    tree = _make_tree("proj_stuck", [
        {"id": "root", "node_type": "ceo_prompt"},
        {"id": "ea", "status": "holding", "parent_id": "root", "employee_id": "00004"},
        {"id": "t1", "status": "completed", "employee_id": "00010", "parent_id": "ea"},
        {"id": "t2", "status": "accepted", "employee_id": "00011", "parent_id": "ea"},
    ])

    projects_dir = tmp_path / "projects" / "proj_stuck"
    projects_dir.mkdir(parents=True)
    tree.save(projects_dir / "task_tree.yaml")

    mock_em = MagicMock()
    mock_em._schedule = {}
    mock_em._running_tasks = {}

    with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path / "projects"), \
         patch("onemancompany.core.vessel.employee_manager", mock_em), \
         patch("onemancompany.core.task_tree.save_tree_async"):
        events = await project_progress_watchdog()

    assert events is not None
    assert events[0].payload["watchdog_nudged"] == ["proj_stuck"]
    mock_em.push_task.assert_called_once()
    # The nudge should target EA
    call_args = mock_em.push_task.call_args
    assert call_args[0][0] == "00004"  # EA_ID
    assert "proj_stuck" in _watchdog_nudged


@pytest.mark.asyncio
async def test_watchdog_does_not_double_nudge(tmp_path):
    """Once nudged, the same project should not be nudged again until cleared."""
    tree = _make_tree("proj_nudged", [
        {"id": "root", "node_type": "ceo_prompt"},
        {"id": "ea", "status": "holding", "parent_id": "root", "employee_id": "00004"},
        {"id": "t1", "status": "completed", "employee_id": "00010", "parent_id": "ea"},
    ])

    projects_dir = tmp_path / "projects" / "proj_nudged"
    projects_dir.mkdir(parents=True)
    tree.save(projects_dir / "task_tree.yaml")

    _watchdog_nudged.add("proj_nudged")

    mock_em = MagicMock()
    mock_em._schedule = {}
    mock_em._running_tasks = {}

    with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path / "projects"), \
         patch("onemancompany.core.vessel.employee_manager", mock_em):
        events = await project_progress_watchdog()

    assert events is None
    mock_em.push_task.assert_not_called()


@pytest.mark.asyncio
async def test_watchdog_re_nudges_after_clear(tmp_path):
    """After clear_watchdog_nudge, the project can be nudged again."""
    tree = _make_tree("proj_re", [
        {"id": "root", "node_type": "ceo_prompt"},
        {"id": "ea", "status": "holding", "parent_id": "root", "employee_id": "00004"},
        {"id": "t1", "status": "failed", "employee_id": "00010", "parent_id": "ea"},
    ])

    projects_dir = tmp_path / "projects" / "proj_re"
    projects_dir.mkdir(parents=True)
    tree.save(projects_dir / "task_tree.yaml")

    _watchdog_nudged.add("proj_re")
    clear_watchdog_nudge("proj_re")
    assert "proj_re" not in _watchdog_nudged

    mock_em = MagicMock()
    mock_em._schedule = {}
    mock_em._running_tasks = {}

    with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path / "projects"), \
         patch("onemancompany.core.vessel.employee_manager", mock_em), \
         patch("onemancompany.core.task_tree.save_tree_async"):
        events = await project_progress_watchdog()

    assert events is not None
    mock_em.push_task.assert_called_once()


@pytest.mark.asyncio
async def test_watchdog_ignores_old_branch_nodes(tmp_path):
    """Old branch nodes (branch_active=False) should not affect stuck detection."""
    tree = _make_tree("proj_branch", [
        {"id": "root", "node_type": "ceo_prompt"},
        # Old branch: incomplete but inactive
        {"id": "old_ea", "status": "holding", "parent_id": "root", "branch_active": False},
        {"id": "old_t1", "status": "pending", "parent_id": "old_ea", "branch_active": False},
        # Current branch: all done
        {"id": "new_ea", "status": "finished", "parent_id": "root"},
        {"id": "new_t1", "status": "finished", "parent_id": "new_ea"},
    ])

    projects_dir = tmp_path / "projects" / "proj_branch"
    projects_dir.mkdir(parents=True)
    tree.save(projects_dir / "task_tree.yaml")

    mock_em = MagicMock()
    mock_em._schedule = {}
    mock_em._running_tasks = {}

    with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path / "projects"), \
         patch("onemancompany.core.vessel.employee_manager", mock_em):
        events = await project_progress_watchdog()

    # Should not nudge — current branch is all finished
    assert events is None
    mock_em.push_task.assert_not_called()


@pytest.mark.asyncio
async def test_watchdog_skips_tree_without_ea_node(tmp_path):
    """A tree with no EA node (only root) should be skipped gracefully."""
    tree = _make_tree("proj_no_ea", [
        {"id": "root", "node_type": "ceo_prompt", "status": "pending"},
    ])

    projects_dir = tmp_path / "projects" / "proj_no_ea"
    projects_dir.mkdir(parents=True)
    tree.save(projects_dir / "task_tree.yaml")

    mock_em = MagicMock()
    mock_em._schedule = {}
    mock_em._running_tasks = {}

    with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path / "projects"), \
         patch("onemancompany.core.vessel.employee_manager", mock_em):
        events = await project_progress_watchdog()

    assert events is None
    mock_em.push_task.assert_not_called()
