"""Unit tests for task lifecycle state machine."""

import pytest

from onemancompany.core.task_lifecycle import (
    DONE_EXECUTING,
    RESOLVED,
    TERMINAL,
    UNBLOCKS_DEPENDENTS,
    WILL_NOT_DELIVER,
    TaskPhase,
    TaskTransitionError,
    can_transition,
    get_valid_targets,
    transition,
)


class TestTaskLifecycle:
    def test_valid_transition(self):
        result = transition("t1", TaskPhase.PENDING, TaskPhase.PROCESSING)
        assert result == TaskPhase.PROCESSING

    def test_invalid_transition_raises(self):
        with pytest.raises(TaskTransitionError) as exc_info:
            transition("t1", TaskPhase.PENDING, TaskPhase.FINISHED)
        assert "illegal transition" in str(exc_info.value)
        assert "pending" in str(exc_info.value)
        assert "finished" in str(exc_info.value)

    def test_full_happy_path(self):
        """Pending -> Processing -> Completed -> Accepted -> Finished."""
        phases = [
            TaskPhase.PENDING,
            TaskPhase.PROCESSING,
            TaskPhase.COMPLETED,
            TaskPhase.ACCEPTED,
            TaskPhase.FINISHED,
        ]
        current = phases[0]
        for target in phases[1:]:
            current = transition("t1", current, target)
        assert current == TaskPhase.FINISHED

    def test_holding_path(self):
        """Processing -> Holding -> Processing -> Completed."""
        current = transition("t1", TaskPhase.PENDING, TaskPhase.PROCESSING)
        current = transition("t1", current, TaskPhase.HOLDING)
        current = transition("t1", current, TaskPhase.PROCESSING)
        current = transition("t1", current, TaskPhase.COMPLETED)
        assert current == TaskPhase.COMPLETED

    def test_failed_retry(self):
        """Failed tasks can be retried."""
        current = transition("t1", TaskPhase.PROCESSING, TaskPhase.FAILED)
        current = transition("t1", current, TaskPhase.PROCESSING)
        assert current == TaskPhase.PROCESSING

    def test_finished_is_terminal(self):
        """No transitions from FINISHED."""
        assert get_valid_targets(TaskPhase.FINISHED) == []
        with pytest.raises(TaskTransitionError):
            transition("t1", TaskPhase.FINISHED, TaskPhase.PENDING)

    def test_can_transition(self):
        assert can_transition(TaskPhase.PENDING, TaskPhase.PROCESSING) is True
        assert can_transition(TaskPhase.PENDING, TaskPhase.FINISHED) is False

    def test_get_valid_targets_completed(self):
        targets = get_valid_targets(TaskPhase.COMPLETED)
        assert TaskPhase.ACCEPTED in targets
        assert TaskPhase.FAILED in targets

    def test_completed_to_accepted_to_finished(self):
        """Completed -> Accepted -> Finished path."""
        current = transition("t1", TaskPhase.PENDING, TaskPhase.PROCESSING)
        current = transition("t1", current, TaskPhase.COMPLETED)
        current = transition("t1", current, TaskPhase.ACCEPTED)
        current = transition("t1", current, TaskPhase.FINISHED)
        assert current == TaskPhase.FINISHED


class TestNewPhases:
    """Tests for COMPLETED/ACCEPTED phases and new category sets."""

    def test_completed_phase_exists(self):
        """COMPLETED replaces COMPLETE."""
        assert TaskPhase.COMPLETED == "completed"

    def test_accepted_phase_exists(self):
        assert TaskPhase.ACCEPTED == "accepted"

    def test_resolved_set(self):
        assert TaskPhase.ACCEPTED in RESOLVED
        assert TaskPhase.FINISHED in RESOLVED
        assert TaskPhase.FAILED in RESOLVED
        assert TaskPhase.CANCELLED in RESOLVED
        assert TaskPhase.COMPLETED not in RESOLVED

    def test_done_executing_set(self):
        assert TaskPhase.COMPLETED in DONE_EXECUTING
        assert TaskPhase.ACCEPTED in DONE_EXECUTING
        assert TaskPhase.PENDING not in DONE_EXECUTING

    def test_unblocks_dependents_set(self):
        assert TaskPhase.ACCEPTED in UNBLOCKS_DEPENDENTS
        assert TaskPhase.FINISHED in UNBLOCKS_DEPENDENTS
        assert TaskPhase.FAILED not in UNBLOCKS_DEPENDENTS

    def test_will_not_deliver_set(self):
        assert TaskPhase.FAILED in WILL_NOT_DELIVER
        assert TaskPhase.BLOCKED in WILL_NOT_DELIVER
        assert TaskPhase.CANCELLED in WILL_NOT_DELIVER

    def test_transition_completed_to_accepted(self):
        result = transition("t1", TaskPhase.COMPLETED, TaskPhase.ACCEPTED)
        assert result == TaskPhase.ACCEPTED

    def test_transition_completed_to_failed_rejection(self):
        """Supervisor rejection: COMPLETED -> FAILED."""
        result = transition("t1", TaskPhase.COMPLETED, TaskPhase.FAILED)
        assert result == TaskPhase.FAILED

    def test_transition_accepted_to_finished(self):
        result = transition("t1", TaskPhase.ACCEPTED, TaskPhase.FINISHED)
        assert result == TaskPhase.FINISHED

    def test_transition_completed_to_processing_invalid(self):
        with pytest.raises(TaskTransitionError):
            transition("t1", TaskPhase.COMPLETED, TaskPhase.PROCESSING)


class TestTaskNodeProductId:
    """Verify TaskNode carries product_id through serialization round-trips."""

    def test_default_product_id_empty(self):
        from onemancompany.core.task_tree import TaskNode
        node = TaskNode()
        assert node.product_id == ""

    def test_product_id_set(self):
        from onemancompany.core.task_tree import TaskNode
        node = TaskNode(product_id="prod_abc12345")
        assert node.product_id == "prod_abc12345"

    def test_product_id_serializes(self):
        from onemancompany.core.task_tree import TaskNode
        node = TaskNode(product_id="prod_abc12345")
        d = node.to_dict()
        assert d["product_id"] == "prod_abc12345"

    def test_product_id_deserializes(self):
        from onemancompany.core.task_tree import TaskNode
        node = TaskNode(product_id="prod_abc12345")
        d = node.to_dict()
        # from_dict needs description for old-format path
        d["description"] = "test"
        restored = TaskNode.from_dict(d)
        assert restored.product_id == "prod_abc12345"

    def test_product_id_missing_in_dict_defaults_empty(self):
        """Backward compat: old dicts without product_id should default to ''."""
        from onemancompany.core.task_tree import TaskNode
        d = {"id": "abc123", "description": "old task"}
        node = TaskNode.from_dict(d)
        assert node.product_id == ""
