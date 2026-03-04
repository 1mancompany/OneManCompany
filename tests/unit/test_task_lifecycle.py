"""Unit tests for task lifecycle state machine."""

import pytest

from onemancompany.core.task_lifecycle import (
    TaskPhase,
    TaskTransitionError,
    can_transition,
    get_valid_targets,
    transition,
)


class TestTaskLifecycle:
    def test_valid_transition(self):
        result = transition("t1", TaskPhase.CREATED, TaskPhase.ROUTED)
        assert result == TaskPhase.ROUTED

    def test_invalid_transition_raises(self):
        with pytest.raises(TaskTransitionError) as exc_info:
            transition("t1", TaskPhase.CREATED, TaskPhase.SETTLED)
        assert "illegal transition" in str(exc_info.value)
        assert "created" in str(exc_info.value)
        assert "settled" in str(exc_info.value)

    def test_full_happy_path(self):
        """CEO -> EA -> Agent -> COO -> EA -> CEO -> Settled."""
        phases = [
            TaskPhase.CREATED,
            TaskPhase.ROUTED,
            TaskPhase.IN_PROGRESS,
            TaskPhase.COMPLETED,
            TaskPhase.NEEDS_ACCEPTANCE,
            TaskPhase.ACCEPTED,
            TaskPhase.EA_REVIEW,
            TaskPhase.EA_APPROVED,
            TaskPhase.CEO_APPROVAL,
            TaskPhase.SETTLED,
        ]
        current = phases[0]
        for target in phases[1:]:
            current = transition("t1", current, target)
        assert current == TaskPhase.SETTLED

    def test_rectification_path(self):
        """Rejected by COO -> rectification -> retry."""
        current = transition("t1", TaskPhase.CREATED, TaskPhase.ROUTED)
        current = transition("t1", current, TaskPhase.IN_PROGRESS)
        current = transition("t1", current, TaskPhase.NEEDS_ACCEPTANCE)
        current = transition("t1", current, TaskPhase.REJECTED_BY_COO)
        current = transition("t1", current, TaskPhase.RECTIFICATION)
        current = transition("t1", current, TaskPhase.IN_PROGRESS)
        assert current == TaskPhase.IN_PROGRESS

    def test_ea_rejection_path(self):
        """EA rejects -> rectification -> retry."""
        current = TaskPhase.EA_REVIEW
        current = transition("t1", current, TaskPhase.EA_REJECTED)
        current = transition("t1", current, TaskPhase.RECTIFICATION)
        current = transition("t1", current, TaskPhase.IN_PROGRESS)
        assert current == TaskPhase.IN_PROGRESS

    def test_settled_is_terminal(self):
        """No transitions from SETTLED."""
        assert get_valid_targets(TaskPhase.SETTLED) == []
        with pytest.raises(TaskTransitionError):
            transition("t1", TaskPhase.SETTLED, TaskPhase.CREATED)

    def test_can_transition(self):
        assert can_transition(TaskPhase.CREATED, TaskPhase.ROUTED) is True
        assert can_transition(TaskPhase.CREATED, TaskPhase.SETTLED) is False

    def test_get_valid_targets(self):
        targets = get_valid_targets(TaskPhase.COMPLETED)
        assert TaskPhase.NEEDS_ACCEPTANCE in targets
        assert TaskPhase.EA_REVIEW in targets
        assert TaskPhase.SETTLED in targets

    def test_skip_to_settled(self):
        """Simple task can go directly from COMPLETED to SETTLED."""
        current = transition("t1", TaskPhase.CREATED, TaskPhase.ROUTED)
        current = transition("t1", current, TaskPhase.IN_PROGRESS)
        current = transition("t1", current, TaskPhase.COMPLETED)
        current = transition("t1", current, TaskPhase.SETTLED)
        assert current == TaskPhase.SETTLED
