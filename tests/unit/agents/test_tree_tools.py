"""Tests for tree tools — dispatch_child, accept_child, reject_child."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from onemancompany.core.task_tree import TaskNode, TaskTree
from onemancompany.core.vessel import _current_task_id, _current_vessel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vessel_and_task(project_dir: str = "/tmp/proj", project_id: str = "proj1"):
    """Create a mock vessel + AgentTask for context vars."""
    task = MagicMock()
    task.project_dir = project_dir
    task.project_id = project_id

    board = MagicMock()
    board.get_task.return_value = task

    vessel = MagicMock()
    vessel.board = board

    return vessel, task


def _set_context(vessel, task_id: str):
    """Set context vars and return reset tokens."""
    tok_v = _current_vessel.set(vessel)
    tok_t = _current_task_id.set(task_id)
    return tok_v, tok_t


def _reset_context(tok_v, tok_t):
    _current_vessel.reset(tok_v)
    _current_task_id.reset(tok_t)


def _make_tree_with_root(project_id: str = "proj1", employee_id: str = "00002") -> TaskTree:
    tree = TaskTree(project_id=project_id)
    tree.create_root(employee_id=employee_id, description="root task")
    return tree


# ---------------------------------------------------------------------------
# dispatch_child
# ---------------------------------------------------------------------------

class TestDispatchChild:
    def test_happy_path(self):
        """Creates child node, pushes task to employee, saves tree."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree_with_root()
        root_id = tree.root_id
        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-123")

        # Set up node mapping via tree's task_id_map
        tree.task_id_map["task-123"] = root_id

        mock_handle = MagicMock()
        mock_agent_task = MagicMock()
        mock_agent_task.id = "child-task-id"
        mock_handle.push_task.return_value = mock_agent_task

        mock_em = MagicMock()
        mock_em.get_handle.return_value = mock_handle

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree") as mock_save,
                patch("onemancompany.core.store.load_employee", return_value={"id": "00100", "name": "Test"}),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = dispatch_child.invoke({
                    "employee_id": "00100",
                    "description": "build feature X",
                    "acceptance_criteria": ["tests pass", "docs updated"],
                })

            assert result["status"] == "dispatched"
            assert result["employee_id"] == "00100"
            assert result["description"] == "build feature X"
            assert "node_id" in result

            # Verify tree was saved
            mock_save.assert_called_once()

            # Verify task was pushed
            mock_handle.push_task.assert_called_once()
            call_args = mock_handle.push_task.call_args
            assert "build feature X" in call_args[0][0]

            # Verify task_id_map mapping was set for child
            assert tree.task_id_map["child-task-id"] == result["node_id"]

            # Verify child node was added to tree
            child_node = tree.get_node(result["node_id"])
            assert child_node is not None
            assert child_node.employee_id == "00100"
            assert child_node.acceptance_criteria == ["tests pass", "docs updated"]
        finally:
            _reset_context(tok_v, tok_t)

    def test_unknown_employee_returns_error(self):
        """Returns error when employee_id not in company_state."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree_with_root()
        tree.task_id_map["task-456"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-456")


        try:
            with patch("onemancompany.core.store.load_employee", return_value={}):
                result = dispatch_child.invoke({
                    "employee_id": "99999",
                    "description": "do stuff",
                    "acceptance_criteria": ["done"],
                })

            assert result["status"] == "error"
            assert "99999" in result["message"]
        finally:
            _reset_context(tok_v, tok_t)

    def test_dispatch_child_with_timeout(self):
        """dispatch_child passes timeout_seconds to child node."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree_with_root()
        tree.task_id_map["task-t1"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-t1")

        mock_handle = MagicMock()
        mock_agent_task = MagicMock()
        mock_agent_task.id = "child-t1"
        mock_handle.push_task.return_value = mock_agent_task
        mock_em = MagicMock()
        mock_em.get_handle.return_value = mock_handle

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.store.load_employee", return_value={"id": "00100", "name": "Test"}),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = dispatch_child.invoke({
                    "employee_id": "00100",
                    "description": "build X",
                    "acceptance_criteria": ["works"],
                    "timeout_seconds": 1800,
                })

            assert result["status"] == "dispatched"
            child_node = tree.get_node(result["node_id"])
            assert child_node.timeout_seconds == 1800
        finally:
            _reset_context(tok_v, tok_t)

    def test_no_context_returns_error(self):
        """Returns error when no vessel/task context is set."""
        from onemancompany.agents.tree_tools import dispatch_child

        # Don't set any context vars
        tok_v = _current_vessel.set(None)
        tok_t = _current_task_id.set("")

        try:
            result = dispatch_child.invoke({
                "employee_id": "00100",
                "description": "do stuff",
                "acceptance_criteria": ["done"],
            })

            assert result["status"] == "error"
            assert "context" in result["message"].lower() or "No agent context" in result["message"]
        finally:
            _current_vessel.reset(tok_v)
            _current_task_id.reset(tok_t)


# ---------------------------------------------------------------------------
# accept_child
# ---------------------------------------------------------------------------

class TestAcceptChild:
    def test_marks_node_accepted(self):
        """Accept sets status=accepted and stores acceptance_result."""
        from onemancompany.agents.tree_tools import accept_child

        tree = _make_tree_with_root()
        child = tree.add_child(
            parent_id=tree.root_id,
            employee_id="00100",
            description="subtask",
            acceptance_criteria=["criterion"],
        )
        tree.task_id_map["task-789"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-789")

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree") as mock_save,
            ):
                result = accept_child.invoke({
                    "node_id": child.id,
                    "notes": "looks good",
                })

            assert result["status"] == "accepted"
            assert result["node_id"] == child.id
            assert result["notes"] == "looks good"

            # Check node was updated
            assert child.status == "accepted"
            assert child.acceptance_result == {"passed": True, "notes": "looks good"}

            mock_save.assert_called_once()
        finally:
            _reset_context(tok_v, tok_t)


# ---------------------------------------------------------------------------
# reject_child
# ---------------------------------------------------------------------------

class TestRejectChild:
    def test_reject_with_retry(self):
        """Reject with retry=True resets to pending and pushes correction task."""
        from onemancompany.agents.tree_tools import reject_child

        tree = _make_tree_with_root()
        child = tree.add_child(
            parent_id=tree.root_id,
            employee_id="00100",
            description="subtask",
            acceptance_criteria=["criterion A", "criterion B"],
        )
        child.status = "completed"
        child.result = "some result"
        tree.task_id_map["task-abc"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-abc")

        mock_handle = MagicMock()
        mock_correction_task = MagicMock()
        mock_correction_task.id = "correction-task-id"
        mock_handle.push_task.return_value = mock_correction_task

        mock_em = MagicMock()
        mock_em.get_handle.return_value = mock_handle

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree") as mock_save,
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = reject_child.invoke({
                    "node_id": child.id,
                    "reason": "tests failing",
                    "retry": True,
                })

            assert result["status"] == "rejected_retry"
            assert result["node_id"] == child.id

            # Node should be reset to pending
            assert child.status == "pending"
            assert child.result == ""
            assert child.acceptance_result == {"passed": False, "notes": "tests failing"}

            # Correction task should have been pushed
            mock_handle.push_task.assert_called_once()
            correction_desc = mock_handle.push_task.call_args[0][0]
            assert "tests failing" in correction_desc

            # Mapping should be set in tree's task_id_map
            assert tree.task_id_map["correction-task-id"] == child.id

            # Save called twice: once for status reset, once for task_id_map update
            assert mock_save.call_count == 2
        finally:
            _reset_context(tok_v, tok_t)

    def test_reject_retry_no_handle_returns_error(self):
        """Reject with retry=True returns error when employee handle is missing."""
        from onemancompany.agents.tree_tools import reject_child

        tree = _make_tree_with_root()
        child = tree.add_child(
            parent_id=tree.root_id,
            employee_id="00100",
            description="subtask",
            acceptance_criteria=["criterion A"],
        )
        child.status = "completed"
        child.result = "some result"
        tree.task_id_map["task-nohandle"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-nohandle")

        mock_em = MagicMock()
        mock_em.get_handle.return_value = None  # No handle available

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree") as mock_save,
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = reject_child.invoke({
                    "node_id": child.id,
                    "reason": "bad work",
                    "retry": True,
                })

            # Should return error, NOT "rejected_retry"
            assert result["status"] == "error"
            assert "00100" in result["message"]

            # Node status should NOT have been changed to pending
            assert child.status == "completed"
            assert child.result == "some result"

            # Tree should NOT have been saved (no state mutation)
            mock_save.assert_not_called()
        finally:
            _reset_context(tok_v, tok_t)



# ---------------------------------------------------------------------------
# EA dispatch constraint
# ---------------------------------------------------------------------------

class TestEADispatchConstraint:
    def test_ea_cannot_dispatch_to_regular_employee(self):
        """EA (00004) should NOT be able to dispatch to non-O-level employees."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree_with_root(employee_id="00004")
        tree.task_id_map["ea-task-1"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "ea-task-1")


        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.store.load_employee", return_value={"id": "00006", "name": "Test"}),
            ):
                result = dispatch_child.invoke({
                    "employee_id": "00006",
                    "description": "do coding",
                    "acceptance_criteria": ["done"],
                })

            assert result["status"] == "error"
            assert "00002" in result["message"] or "COO" in result["message"] or "HR" in result["message"]
        finally:
            _reset_context(tok_v, tok_t)

    def test_ea_can_dispatch_to_coo(self):
        """EA (00004) should be able to dispatch to COO (00003)."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree_with_root(employee_id="00004")
        tree.task_id_map["ea-task-2"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "ea-task-2")

        mock_handle = MagicMock()
        mock_agent_task = MagicMock()
        mock_agent_task.id = "coo-task-1"
        mock_handle.push_task.return_value = mock_agent_task
        mock_em = MagicMock()
        mock_em.get_handle.return_value = mock_handle

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.store.load_employee", return_value={"id": "00003", "name": "Test"}),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = dispatch_child.invoke({
                    "employee_id": "00003",
                    "description": "manage project",
                    "acceptance_criteria": ["delivered"],
                })

            assert result["status"] == "dispatched"
        finally:
            _reset_context(tok_v, tok_t)

    def test_non_ea_can_dispatch_to_anyone(self):
        """Non-EA employees (e.g. COO 00003) can dispatch to any employee."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree_with_root(employee_id="00003")
        tree.task_id_map["coo-task-3"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "coo-task-3")

        mock_handle = MagicMock()
        mock_agent_task = MagicMock()
        mock_agent_task.id = "eng-task-1"
        mock_handle.push_task.return_value = mock_agent_task
        mock_em = MagicMock()
        mock_em.get_handle.return_value = mock_handle

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.store.load_employee", return_value={"id": "00006", "name": "Test"}),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = dispatch_child.invoke({
                    "employee_id": "00006",
                    "description": "write code",
                    "acceptance_criteria": ["works"],
                })

            assert result["status"] == "dispatched"
        finally:
            _reset_context(tok_v, tok_t)


# ---------------------------------------------------------------------------
# reject_child (continued)
# ---------------------------------------------------------------------------

class TestDispatchChildDependency:
    def test_dispatch_with_unmet_dep_skips_push(self):
        """Task with unmet dependency should not be pushed to board."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree_with_root()
        # Create a dependency node that's still pending
        dep_node = tree.add_child(tree.root_id, "e1", "dep task", [])
        tree.task_id_map["task-dep1"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-dep1")

        mock_handle = MagicMock()
        mock_em = MagicMock()
        mock_em.get_handle.return_value = mock_handle

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.store.load_employee", return_value={"id": "00100", "name": "Test"}),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = dispatch_child.invoke({
                    "employee_id": "00100",
                    "description": "task B",
                    "acceptance_criteria": ["done"],
                    "depends_on": [dep_node.id],
                })

            assert result["status"] == "dispatched_waiting"
            assert result["dependency_status"] == "waiting"
            # push_task should NOT have been called
            mock_handle.push_task.assert_not_called()
            # But child node should exist in tree
            child = tree.get_node(result["node_id"])
            assert child is not None
            assert child.depends_on == [dep_node.id]
        finally:
            _reset_context(tok_v, tok_t)

    def test_dispatch_with_met_dep_pushes(self):
        """Task with all deps terminal should be pushed immediately."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree_with_root()
        dep_node = tree.add_child(tree.root_id, "e1", "dep task", [])
        dep_node.status = "accepted"  # Terminal
        tree.task_id_map["task-dep2"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-dep2")

        mock_handle = MagicMock()
        mock_agent_task = MagicMock()
        mock_agent_task.id = "child-task-met"
        mock_handle.push_task.return_value = mock_agent_task
        mock_em = MagicMock()
        mock_em.get_handle.return_value = mock_handle

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.store.load_employee", return_value={"id": "00100", "name": "Test"}),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = dispatch_child.invoke({
                    "employee_id": "00100",
                    "description": "task B",
                    "acceptance_criteria": ["done"],
                    "depends_on": [dep_node.id],
                })

            assert result["status"] == "dispatched"
            assert result["dependency_status"] == "resolved"
            mock_handle.push_task.assert_called_once()
        finally:
            _reset_context(tok_v, tok_t)

    def test_dispatch_invalid_dep_id(self):
        """depends_on with non-existent node ID should error."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree_with_root()
        tree.task_id_map["task-dep3"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-dep3")


        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.store.load_employee", return_value={"id": "00100", "name": "Test"}),
            ):
                result = dispatch_child.invoke({
                    "employee_id": "00100",
                    "description": "task B",
                    "acceptance_criteria": ["done"],
                    "depends_on": ["nonexistent_id"],
                })

            assert result["status"] == "error"
            assert "nonexistent_id" in result["message"]
        finally:
            _reset_context(tok_v, tok_t)

    def test_dispatch_no_deps_still_works(self):
        """dispatch_child without depends_on should work as before."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree_with_root()
        tree.task_id_map["task-dep4"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-dep4")

        mock_handle = MagicMock()
        mock_agent_task = MagicMock()
        mock_agent_task.id = "child-no-dep"
        mock_handle.push_task.return_value = mock_agent_task
        mock_em = MagicMock()
        mock_em.get_handle.return_value = mock_handle

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.store.load_employee", return_value={"id": "00100", "name": "Test"}),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = dispatch_child.invoke({
                    "employee_id": "00100",
                    "description": "task A",
                    "acceptance_criteria": ["done"],
                })

            assert result["status"] == "dispatched"
            mock_handle.push_task.assert_called_once()
        finally:
            _reset_context(tok_v, tok_t)


# ---------------------------------------------------------------------------
# unblock_child
# ---------------------------------------------------------------------------

class TestUnblockChild:
    def test_unblock_resets_to_pending(self):
        """BLOCKED node should transition to PENDING."""
        from onemancompany.agents.tree_tools import unblock_child

        tree = _make_tree_with_root()
        dep = tree.add_child(tree.root_id, "e1", "dep task", [])
        dep.status = "failed"
        child = tree.add_child(tree.root_id, "e2", "blocked task", [],
                               depends_on=[dep.id], fail_strategy="block")
        child.status = "blocked"
        tree.task_id_map["task-ub1"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-ub1")

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
            ):
                result = unblock_child.invoke({"node_id": child.id})

            assert result["status"] == "unblocked_waiting" or result["status"] == "unblocked_and_dispatched"
            assert child.status == "pending"
            # Failed dep should be removed from depends_on
            assert dep.id not in child.depends_on
        finally:
            _reset_context(tok_v, tok_t)

    def test_unblock_with_new_description(self):
        """Unblock should update description if provided."""
        from onemancompany.agents.tree_tools import unblock_child

        tree = _make_tree_with_root()
        dep = tree.add_child(tree.root_id, "e1", "dep task", [])
        dep.status = "failed"
        child = tree.add_child(tree.root_id, "e2", "old desc", [],
                               depends_on=[dep.id])
        child.status = "blocked"
        tree.task_id_map["task-ub2"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-ub2")

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
            ):
                result = unblock_child.invoke({
                    "node_id": child.id,
                    "new_description": "updated desc",
                })

            assert child.description == "updated desc"
        finally:
            _reset_context(tok_v, tok_t)

    def test_unblock_pushes_if_all_deps_met(self):
        """If remaining deps are terminal after unblock, push_task."""
        from onemancompany.agents.tree_tools import unblock_child

        tree = _make_tree_with_root()
        dep1 = tree.add_child(tree.root_id, "e1", "dep1", [])
        dep1.status = "failed"
        dep2 = tree.add_child(tree.root_id, "e3", "dep2", [])
        dep2.status = "accepted"
        child = tree.add_child(tree.root_id, "e2", "task", [],
                               depends_on=[dep1.id, dep2.id])
        child.status = "blocked"
        tree.task_id_map["task-ub3"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-ub3")

        mock_handle = MagicMock()
        mock_agent_task = MagicMock()
        mock_agent_task.id = "unblocked-task"
        mock_handle.push_task.return_value = mock_agent_task
        mock_em = MagicMock()
        mock_em.get_handle.return_value = mock_handle

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = unblock_child.invoke({"node_id": child.id})

            assert result["status"] == "unblocked_and_dispatched"
            mock_handle.push_task.assert_called_once()
        finally:
            _reset_context(tok_v, tok_t)

    def test_unblock_non_blocked_node_errors(self):
        """Unblocking a non-BLOCKED node should return error."""
        from onemancompany.agents.tree_tools import unblock_child

        tree = _make_tree_with_root()
        child = tree.add_child(tree.root_id, "e1", "task", [])
        child.status = "pending"
        tree.task_id_map["task-ub4"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-ub4")

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
            ):
                result = unblock_child.invoke({"node_id": child.id})

            assert result["status"] == "error"
        finally:
            _reset_context(tok_v, tok_t)


# ---------------------------------------------------------------------------
# cancel_child
# ---------------------------------------------------------------------------

class TestCancelChild:
    def test_cancel_sets_cancelled(self):
        """Cancel sets node status to cancelled."""
        from onemancompany.agents.tree_tools import cancel_child

        tree = _make_tree_with_root()
        child = tree.add_child(tree.root_id, "e1", "task A", [])
        child.status = "pending"
        tree.task_id_map["task-cc1"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-cc1")

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
            ):
                result = cancel_child.invoke({
                    "node_id": child.id,
                    "reason": "no longer needed",
                })

            assert result["status"] == "cancelled"
            assert child.status == "cancelled"
            assert child.result == "no longer needed"
        finally:
            _reset_context(tok_v, tok_t)

    def test_cancel_terminal_node_errors(self):
        """Cannot cancel a node that's already terminal."""
        from onemancompany.agents.tree_tools import cancel_child

        tree = _make_tree_with_root()
        child = tree.add_child(tree.root_id, "e1", "task A", [])
        child.status = "accepted"  # Already terminal
        tree.task_id_map["task-cc2"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-cc2")

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
            ):
                result = cancel_child.invoke({
                    "node_id": child.id,
                    "reason": "test",
                })

            assert result["status"] == "error"
        finally:
            _reset_context(tok_v, tok_t)

    def test_cancel_default_reason(self):
        """Cancel without reason uses default."""
        from onemancompany.agents.tree_tools import cancel_child

        tree = _make_tree_with_root()
        child = tree.add_child(tree.root_id, "e1", "task A", [])
        tree.task_id_map["task-cc3"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-cc3")

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
            ):
                result = cancel_child.invoke({"node_id": child.id})

            assert result["status"] == "cancelled"
            assert child.result == "Cancelled by parent"
        finally:
            _reset_context(tok_v, tok_t)


class TestRejectChildNoRetry:
    def test_reject_no_retry(self):
        """Reject with retry=False marks node as failed."""
        from onemancompany.agents.tree_tools import reject_child

        tree = _make_tree_with_root()
        child = tree.add_child(
            parent_id=tree.root_id,
            employee_id="00100",
            description="subtask",
            acceptance_criteria=["criterion"],
        )
        child.status = "completed"
        tree.task_id_map["task-def"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "task-def")

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree") as mock_save,
            ):
                result = reject_child.invoke({
                    "node_id": child.id,
                    "reason": "fundamentally wrong approach",
                    "retry": False,
                })

            assert result["status"] == "rejected_failed"
            assert result["node_id"] == child.id

            # Node should be marked failed
            assert child.status == "failed"
            assert child.acceptance_result == {"passed": False, "notes": "fundamentally wrong approach"}

            mock_save.assert_called_once()
        finally:
            _reset_context(tok_v, tok_t)
