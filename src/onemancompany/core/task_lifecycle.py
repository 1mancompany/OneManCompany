"""Task lifecycle state machine — explicit phases replace implicit dict checks.

Replaces the 93-line nested if-else in agent_loop._post_task_cleanup()
with a declarative state transition table.
"""

from __future__ import annotations

from enum import Enum


class TaskPhase(str, Enum):
    """Explicit task phases — no more inferring from dict field existence."""
    CREATED = "created"
    ROUTED = "routed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    NEEDS_ACCEPTANCE = "needs_acceptance"
    ACCEPTED = "accepted"
    REJECTED_BY_COO = "rejected_by_coo"
    EA_REVIEW = "ea_review"
    EA_APPROVED = "ea_approved"
    EA_REJECTED = "ea_rejected"
    RECTIFICATION = "rectification"
    CEO_APPROVAL = "ceo_approval"
    SETTLED = "settled"


# Valid state transitions
VALID_TRANSITIONS: dict[TaskPhase, list[TaskPhase]] = {
    TaskPhase.CREATED: [TaskPhase.ROUTED],
    TaskPhase.ROUTED: [TaskPhase.IN_PROGRESS],
    TaskPhase.IN_PROGRESS: [TaskPhase.COMPLETED, TaskPhase.NEEDS_ACCEPTANCE],
    TaskPhase.COMPLETED: [TaskPhase.NEEDS_ACCEPTANCE, TaskPhase.EA_REVIEW, TaskPhase.SETTLED],
    TaskPhase.NEEDS_ACCEPTANCE: [TaskPhase.ACCEPTED, TaskPhase.REJECTED_BY_COO],
    TaskPhase.ACCEPTED: [TaskPhase.EA_REVIEW, TaskPhase.SETTLED],
    TaskPhase.REJECTED_BY_COO: [TaskPhase.RECTIFICATION],
    TaskPhase.EA_REVIEW: [TaskPhase.EA_APPROVED, TaskPhase.EA_REJECTED],
    TaskPhase.EA_APPROVED: [TaskPhase.CEO_APPROVAL, TaskPhase.SETTLED],
    TaskPhase.EA_REJECTED: [TaskPhase.RECTIFICATION],
    TaskPhase.RECTIFICATION: [TaskPhase.IN_PROGRESS],
    TaskPhase.CEO_APPROVAL: [TaskPhase.SETTLED],
    TaskPhase.SETTLED: [],
}


class TaskTransitionError(Exception):
    """Raised when attempting an illegal state transition."""

    def __init__(self, task_id: str, current: TaskPhase, attempted: TaskPhase) -> None:
        self.task_id = task_id
        self.current = current
        self.attempted = attempted
        valid = [t.value for t in VALID_TRANSITIONS.get(current, [])]
        super().__init__(
            f"Task {task_id}: illegal transition {current.value} -> {attempted.value}. "
            f"Valid targets: {valid}"
        )


def transition(task_id: str, current: TaskPhase, target: TaskPhase) -> TaskPhase:
    """Validate and execute a state transition. Raises TaskTransitionError if invalid."""
    valid = VALID_TRANSITIONS.get(current, [])
    if target not in valid:
        raise TaskTransitionError(task_id, current, target)
    return target


def can_transition(current: TaskPhase, target: TaskPhase) -> bool:
    """Check if a transition is valid without raising."""
    return target in VALID_TRANSITIONS.get(current, [])


def get_valid_targets(current: TaskPhase) -> list[TaskPhase]:
    """Return all valid target phases from the current phase."""
    return list(VALID_TRANSITIONS.get(current, []))
