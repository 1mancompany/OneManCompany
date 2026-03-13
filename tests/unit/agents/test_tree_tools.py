"""Tests for tree tools — dispatch_child, accept_child, reject_child."""
from __future__ import annotations

from collections import defaultdict
from unittest.mock import MagicMock, patch

import pytest

from onemancompany.core.task_tree import TaskNode, TaskTree
from onemancompany.core.vessel import ScheduleEntry, _current_task_id, _current_vessel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vessel_and_task(project_dir: str = "/tmp/proj", project_id: str = "proj1"):
    """Create a mock vessel for context vars."""
    vessel = MagicMock()
    return vessel


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


def _make_mock_em(tree_root_id: str, tree_path: str = "/tmp/proj/task_tree.yaml"):
    """Create a mock EmployeeManager with _schedule containing the root entry."""
    mock_em = MagicMock()
    entry = ScheduleEntry(node_id=tree_root_id, tree_path=tree_path)
    mock_em._schedule = defaultdict(list)
    mock_em._schedule["_any_"] = [entry]  # any employee key will match via iteration
    return mock_em


# ---------------------------------------------------------------------------
# dispatch_child
# ---------------------------------------------------------------------------

class TestDispatchChild:
    def test_happy_path(self):
        """Creates child node, schedules task to employee, saves tree."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree_with_root()
        root_id = tree.root_id
        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

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

            # Verify task was scheduled
            mock_em.schedule_node.assert_called_once()
            call_args = mock_em.schedule_node.call_args
            assert call_args[0][0] == "00100"  # employee_id
            assert call_args[0][1] == result["node_id"]  # node_id

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
        root_id = tree.root_id

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

        try:
            with (
                patch("onemancompany.core.store.load_employee", return_value={}),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
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
        root_id = tree.root_id

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

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
        root_id = tree.root_id
        child = tree.add_child(
            parent_id=root_id,
            employee_id="00100",
            description="subtask",
            acceptance_criteria=["criterion"],
        )
        child.status = "completed"  # Must be completed before acceptance

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree") as mock_save,
                patch("onemancompany.core.vessel.employee_manager", mock_em),
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
        """Reject with retry=True resets to pending and schedules correction task."""
        from onemancompany.agents.tree_tools import reject_child

        tree = _make_tree_with_root()
        root_id = tree.root_id
        child = tree.add_child(
            parent_id=root_id,
            employee_id="00100",
            description="subtask",
            acceptance_criteria=["criterion A", "criterion B"],
        )
        child.status = "completed"
        child.result = "some result"

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)
        mock_em.executors = {"00100": MagicMock()}

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

            # schedule_node should have been called
            mock_em.schedule_node.assert_called_once()

            # Save called once for status reset + schedule
            assert mock_save.call_count == 1
        finally:
            _reset_context(tok_v, tok_t)

    def test_reject_retry_no_handle_returns_error(self):
        """Reject with retry=True returns error when employee executor is missing."""
        from onemancompany.agents.tree_tools import reject_child

        tree = _make_tree_with_root()
        root_id = tree.root_id
        child = tree.add_child(
            parent_id=root_id,
            employee_id="00100",
            description="subtask",
            acceptance_criteria=["criterion A"],
        )
        child.status = "completed"
        child.result = "some result"

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)
        mock_em.executors = {}  # No executor for 00100

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
        root_id = tree.root_id

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.store.load_employee", return_value={"id": "00006", "name": "Test"}),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
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
        root_id = tree.root_id

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

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
        root_id = tree.root_id

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

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
# Dependency tests
# ---------------------------------------------------------------------------

class TestDispatchChildDependency:
    def test_dispatch_with_unmet_dep_skips_push(self):
        """Task with unmet dependency should not be scheduled."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree_with_root()
        root_id = tree.root_id
        # Create a dependency node that's still pending
        dep_node = tree.add_child(root_id, "e1", "dep task", [])

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

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
            # schedule_node should NOT have been called
            mock_em.schedule_node.assert_not_called()
            # But child node should exist in tree
            child = tree.get_node(result["node_id"])
            assert child is not None
            assert child.depends_on == [dep_node.id]
        finally:
            _reset_context(tok_v, tok_t)

    def test_dispatch_with_met_dep_pushes(self):
        """Task with all deps terminal should be scheduled immediately."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree_with_root()
        root_id = tree.root_id
        dep_node = tree.add_child(root_id, "e1", "dep task", [])
        dep_node.status = "accepted"  # Terminal

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

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
            mock_em.schedule_node.assert_called_once()
        finally:
            _reset_context(tok_v, tok_t)

    def test_dispatch_invalid_dep_id(self):
        """depends_on with non-existent node ID should error."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree_with_root()
        root_id = tree.root_id

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

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
        root_id = tree.root_id

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

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
            mock_em.schedule_node.assert_called_once()
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
        root_id = tree.root_id
        dep = tree.add_child(root_id, "e1", "dep task", [])
        dep.status = "failed"
        child = tree.add_child(root_id, "e2", "blocked task", [],
                               depends_on=[dep.id], fail_strategy="block")
        child.status = "blocked"

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
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
        root_id = tree.root_id
        dep = tree.add_child(root_id, "e1", "dep task", [])
        dep.status = "failed"
        child = tree.add_child(root_id, "e2", "old desc", [],
                               depends_on=[dep.id])
        child.status = "blocked"

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = unblock_child.invoke({
                    "node_id": child.id,
                    "new_description": "updated desc",
                })

            assert child.description == "updated desc"
        finally:
            _reset_context(tok_v, tok_t)

    def test_unblock_pushes_if_all_deps_met(self):
        """If remaining deps are terminal after unblock, schedule_node."""
        from onemancompany.agents.tree_tools import unblock_child

        tree = _make_tree_with_root()
        root_id = tree.root_id
        dep1 = tree.add_child(root_id, "e1", "dep1", [])
        dep1.status = "failed"
        dep2 = tree.add_child(root_id, "e3", "dep2", [])
        dep2.status = "accepted"
        child = tree.add_child(root_id, "e2", "task", [],
                               depends_on=[dep1.id, dep2.id])
        child.status = "blocked"

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = unblock_child.invoke({"node_id": child.id})

            assert result["status"] == "unblocked_and_dispatched"
            mock_em.schedule_node.assert_called_once()
        finally:
            _reset_context(tok_v, tok_t)

    def test_unblock_non_blocked_node_errors(self):
        """Unblocking a non-BLOCKED node should return error."""
        from onemancompany.agents.tree_tools import unblock_child

        tree = _make_tree_with_root()
        root_id = tree.root_id
        child = tree.add_child(root_id, "e1", "task", [])
        child.status = "pending"

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
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
        root_id = tree.root_id
        child = tree.add_child(root_id, "e1", "task A", [])
        child.status = "pending"

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
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
        root_id = tree.root_id
        child = tree.add_child(root_id, "e1", "task A", [])
        child.status = "accepted"  # Already terminal

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
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
        root_id = tree.root_id
        child = tree.add_child(root_id, "e1", "task A", [])

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
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
        root_id = tree.root_id
        child = tree.add_child(
            parent_id=root_id,
            employee_id="00100",
            description="subtask",
            acceptance_criteria=["criterion"],
        )
        child.status = "completed"

        vessel = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, root_id)

        mock_em = _make_mock_em(root_id)

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree") as mock_save,
                patch("onemancompany.core.vessel.employee_manager", mock_em),
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
