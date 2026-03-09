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
        result = transition("t1", TaskPhase.PENDING, TaskPhase.PROCESSING)
        assert result == TaskPhase.PROCESSING

    def test_invalid_transition_raises(self):
        with pytest.raises(TaskTransitionError) as exc_info:
            transition("t1", TaskPhase.PENDING, TaskPhase.FINISHED)
        assert "illegal transition" in str(exc_info.value)
        assert "pending" in str(exc_info.value)
        assert "finished" in str(exc_info.value)

    def test_full_happy_path(self):
        """Pending -> Processing -> Complete -> Finished."""
        phases = [
            TaskPhase.PENDING,
            TaskPhase.PROCESSING,
            TaskPhase.COMPLETE,
            TaskPhase.FINISHED,
        ]
        current = phases[0]
        for target in phases[1:]:
            current = transition("t1", current, target)
        assert current == TaskPhase.FINISHED

    def test_holding_path(self):
        """Processing -> Holding -> Processing -> Complete."""
        current = transition("t1", TaskPhase.PENDING, TaskPhase.PROCESSING)
        current = transition("t1", current, TaskPhase.HOLDING)
        current = transition("t1", current, TaskPhase.PROCESSING)
        current = transition("t1", current, TaskPhase.COMPLETE)
        assert current == TaskPhase.COMPLETE

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

    def test_get_valid_targets(self):
        targets = get_valid_targets(TaskPhase.COMPLETE)
        assert TaskPhase.FINISHED in targets
        assert len(targets) == 1

    def test_skip_to_finished(self):
        """Simple task can go directly from COMPLETE to FINISHED."""
        current = transition("t1", TaskPhase.PENDING, TaskPhase.PROCESSING)
        current = transition("t1", current, TaskPhase.COMPLETE)
        current = transition("t1", current, TaskPhase.FINISHED)
        assert current == TaskPhase.FINISHED
