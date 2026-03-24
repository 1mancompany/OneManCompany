"""Tests for CEO_PROMPT root node advancing to FINISHED after CEO confirmation.

Covers the bug where _confirm_ceo_report completes cleanup but leaves
the CEO_PROMPT root node stuck at 'completed' instead of 'finished'.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.task_lifecycle import NodeType, TaskPhase


def _make_tree_yaml(root_status: str = "completed") -> str:
    """Create a minimal task tree YAML with CEO_PROMPT root + EA child."""
    return f"""\
project_id: test-project/iter_001
root_id: root_001
current_branch: 0
mode: standard
nodes:
- id: root_001
  parent_id: ''
  children_ids:
  - ea_001
  employee_id: '00001'
  description_preview: Test project
  acceptance_criteria: []
  node_type: {NodeType.CEO_PROMPT.value}
  model_used: ''
  project_dir: ''
  status: {root_status}
  acceptance_result: null
  project_id: test-project/iter_001
  created_at: '2026-03-24T12:00:00'
  completed_at: ''
  cost_usd: 0.0
  input_tokens: 0
  output_tokens: 0
  timeout_seconds: 3600
  branch: 0
  branch_active: true
  depends_on: []
  hold_reason: ''
  directives_count: 0
- id: ea_001
  parent_id: root_001
  children_ids: []
  employee_id: '00004'
  description_preview: EA dispatched task
  acceptance_criteria: []
  node_type: task
  model_used: ''
  project_dir: ''
  status: finished
  acceptance_result:
    passed: true
    notes: All done.
  project_id: test-project/iter_001
  created_at: '2026-03-24T12:00:01'
  completed_at: '2026-03-24T12:30:00'
  cost_usd: 0.0
  input_tokens: 0
  output_tokens: 0
  timeout_seconds: 3600
  branch: 0
  branch_active: true
  depends_on: []
  hold_reason: ''
  directives_count: 0
"""


@pytest.mark.asyncio
async def test_confirm_ceo_report_advances_root_to_finished():
    """After CEO confirms, the CEO_PROMPT root should be ACCEPTED → FINISHED."""
    from onemancompany.core.task_tree import TaskTree

    with tempfile.TemporaryDirectory() as tmpdir:
        tree_path = Path(tmpdir) / "task_tree.yaml"
        tree_path.write_text(_make_tree_yaml("completed"))

        tree = TaskTree.load(tree_path)
        ea_node = tree.get_node("ea_001")
        # Set project_dir so _confirm_ceo_report can find the tree
        ea_node.project_dir = tmpdir

        root = tree.get_node("root_001")
        assert root.status == TaskPhase.COMPLETED.value

        # Build the pending report structure that _confirm_ceo_report expects
        from onemancompany.core.vessel import EmployeeManager

        em = EmployeeManager.__new__(EmployeeManager)
        em._pending_ceo_reports = {}
        em._running_tasks = {}
        em._schedule = {}
        em._restart_pending = False

        em._pending_ceo_reports["test-project/iter_001"] = {
            "timer_task": None,
            "cleanup_ctx": {
                "employee_id": "00004",
                "node": ea_node,
                "project_id": "test-project/iter_001",
                "run_retrospective": False,
            },
        }

        # Mock _full_cleanup to avoid side effects
        em._full_cleanup = AsyncMock()

        result = await em._confirm_ceo_report("test-project/iter_001")
        assert result is True

        # Reload tree from disk (save_tree_async writes synchronously in test)
        tree2 = TaskTree.load(tree_path)
        root2 = tree2.get_node("root_001")
        assert root2.status == TaskPhase.FINISHED.value
        assert root2.acceptance_result["passed"] is True


@pytest.mark.asyncio
async def test_confirm_ceo_report_no_double_finish():
    """If root is already FINISHED, _confirm_ceo_report should not error."""
    from onemancompany.core.task_tree import TaskTree

    with tempfile.TemporaryDirectory() as tmpdir:
        tree_path = Path(tmpdir) / "task_tree.yaml"
        tree_path.write_text(_make_tree_yaml("finished"))

        tree = TaskTree.load(tree_path)
        ea_node = tree.get_node("ea_001")
        ea_node.project_dir = tmpdir

        from onemancompany.core.vessel import EmployeeManager

        em = EmployeeManager.__new__(EmployeeManager)
        em._pending_ceo_reports = {}
        em._running_tasks = {}
        em._schedule = {}
        em._restart_pending = False

        em._pending_ceo_reports["test-project/iter_001"] = {
            "timer_task": None,
            "cleanup_ctx": {
                "employee_id": "00004",
                "node": ea_node,
                "project_id": "test-project/iter_001",
                "run_retrospective": False,
            },
        }

        em._full_cleanup = AsyncMock()

        result = await em._confirm_ceo_report("test-project/iter_001")
        assert result is True

        # Root should stay finished, no error
        tree2 = TaskTree.load(tree_path)
        root2 = tree2.get_node("root_001")
        assert root2.status == TaskPhase.FINISHED.value
