"""Tests for illegal state transition guards in vessel.py.

Reproduces two production errors:
1. finished -> completed: _execute_task tries to COMPLETE a node that's already FINISHED
2. processing -> processing: child failure handler tries to set PROCESSING on already-PROCESSING parent
"""

from __future__ import annotations

import pytest

from onemancompany.core.task_lifecycle import TaskPhase, TaskTransitionError, RESOLVED
from onemancompany.core.task_tree import TaskNode, TaskTree


class TestExecuteTaskSkipsTerminalNodes:
    """Bug 1: _execute_task must not set_status(COMPLETED) on FINISHED/ACCEPTED nodes."""

    def test_finished_node_not_re_completed(self):
        """A FINISHED node should not be transitioned to COMPLETED."""
        node = TaskNode(
            parent_id="",
            employee_id="emp01",
            description="already done",
            acceptance_criteria=[],
            project_id="proj1",
        )
        # Manually set to FINISHED (simulating system node auto-finish)
        node.status = TaskPhase.FINISHED.value

        # The guard in _execute_task should skip the completion block
        # for FINISHED nodes. Verify that set_status(COMPLETED) would fail.
        with pytest.raises(TaskTransitionError, match="finished -> completed"):
            node.set_status(TaskPhase.COMPLETED)

    def test_accepted_node_not_re_completed(self):
        """An ACCEPTED node should not be transitioned to COMPLETED."""
        node = TaskNode(
            parent_id="",
            employee_id="emp01",
            description="already accepted",
            acceptance_criteria=[],
            project_id="proj1",
        )
        node.status = TaskPhase.ACCEPTED.value

        with pytest.raises(TaskTransitionError, match="accepted -> completed"):
            node.set_status(TaskPhase.COMPLETED)


class TestProcessingToProcessingGuard:
    """Bug 2: child failure handler must not set_status(PROCESSING) on already-PROCESSING parent."""

    def test_processing_self_transition_raises(self):
        """PROCESSING -> PROCESSING is not a valid transition."""
        node = TaskNode(
            parent_id="",
            employee_id="emp01",
            description="parent task",
            acceptance_criteria=[],
            project_id="proj1",
        )
        node.status = TaskPhase.PROCESSING.value

        with pytest.raises(TaskTransitionError, match="processing -> processing"):
            node.set_status(TaskPhase.PROCESSING)


class TestOnChildCompleteAutoCompleteGuard:
    """Verify _on_child_complete_inner guards against already-resolved parents."""

    def test_finished_parent_in_resolved_set(self):
        """FINISHED parent must be in RESOLVED set so the guard skips auto-complete."""
        assert TaskPhase.FINISHED in RESOLVED
        assert TaskPhase.ACCEPTED in RESOLVED

    def test_set_status_on_finished_parent_raises(self):
        """Attempting COMPLETED on a FINISHED node raises, proving the guard is necessary."""
        node = TaskNode(
            parent_id="", employee_id="emp01", description="done",
            acceptance_criteria=[], project_id="proj1",
        )
        node.status = TaskPhase.FINISHED.value
        with pytest.raises(TaskTransitionError):
            node.set_status(TaskPhase.COMPLETED)

    def test_set_status_on_accepted_parent_raises(self):
        """Attempting COMPLETED on an ACCEPTED node also raises."""
        node = TaskNode(
            parent_id="", employee_id="emp01", description="done",
            acceptance_criteria=[], project_id="proj1",
        )
        node.status = TaskPhase.ACCEPTED.value
        with pytest.raises(TaskTransitionError):
            node.set_status(TaskPhase.COMPLETED)
