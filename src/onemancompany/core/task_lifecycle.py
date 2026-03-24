"""Task lifecycle state machine — unified task states.

State flow:
  pending → processing → (holding →)* completed → accepted → finished

Task tree model:
  - Parent dispatches children via dispatch_child()
  - Children complete → parent woken to review via accept_child() / reject_child()
  - All children accepted → parent auto-completes
"""

from __future__ import annotations

from enum import Enum


# ---------------------------------------------------------------------------
# Task phase — unified state machine
# ---------------------------------------------------------------------------

class TaskPhase(str, Enum):
    """Task lifecycle phases — explicit, no ambiguity.

    Core states:
      PENDING     — created, not yet being worked on
      PROCESSING  — actively being worked on by an employee
      HOLDING     — paused, waiting for sub-tasks or external input
      COMPLETED   — employee finished execution, awaiting supervisor review
      ACCEPTED    — supervisor approved the deliverable
      FINISHED    — fully done, archived

    Error states:
      FAILED      — execution failed or supervisor rejected
      BLOCKED     — dependency failed, cannot proceed
      CANCELLED   — task was cancelled
    """
    # --- Core states ---
    PENDING = "pending"
    PROCESSING = "processing"
    HOLDING = "holding"
    COMPLETED = "completed"
    ACCEPTED = "accepted"
    FINISHED = "finished"

    # --- Error states ---
    FAILED = "failed"
    BLOCKED = "blocked"      # dependency failed, cannot proceed
    CANCELLED = "cancelled"


# Valid state transitions
VALID_TRANSITIONS: dict[TaskPhase, list[TaskPhase]] = {
    TaskPhase.PENDING:    [TaskPhase.PROCESSING, TaskPhase.HOLDING, TaskPhase.BLOCKED, TaskPhase.CANCELLED],
    TaskPhase.PROCESSING: [TaskPhase.COMPLETED, TaskPhase.HOLDING, TaskPhase.FAILED, TaskPhase.CANCELLED],
    TaskPhase.HOLDING:    [TaskPhase.PROCESSING, TaskPhase.COMPLETED, TaskPhase.BLOCKED, TaskPhase.CANCELLED],
    TaskPhase.COMPLETED:  [TaskPhase.ACCEPTED, TaskPhase.FAILED, TaskPhase.PENDING, TaskPhase.HOLDING, TaskPhase.CANCELLED],
    TaskPhase.ACCEPTED:   [TaskPhase.FINISHED],
    TaskPhase.FINISHED:   [],
    TaskPhase.FAILED:     [TaskPhase.PROCESSING, TaskPhase.PENDING, TaskPhase.CANCELLED],
    TaskPhase.BLOCKED:    [TaskPhase.PENDING, TaskPhase.CANCELLED],
    TaskPhase.CANCELLED:  [],
}


# ---------------------------------------------------------------------------
# Category sets — semantic groupings of phases
# ---------------------------------------------------------------------------

# Resolved: decision has been made, no more work expected from this task
RESOLVED = frozenset({TaskPhase.ACCEPTED, TaskPhase.FINISHED, TaskPhase.FAILED, TaskPhase.CANCELLED})

# Done executing: the employee has stopped working (succeeded or failed)
DONE_EXECUTING = frozenset({TaskPhase.COMPLETED, TaskPhase.ACCEPTED, TaskPhase.FINISHED, TaskPhase.FAILED, TaskPhase.CANCELLED})

# Unblocks dependents: downstream tasks can proceed
UNBLOCKS_DEPENDENTS = frozenset({TaskPhase.ACCEPTED, TaskPhase.FINISHED})

# Will not deliver: task will never produce output
WILL_NOT_DELIVER = frozenset({TaskPhase.FAILED, TaskPhase.BLOCKED, TaskPhase.CANCELLED})

# In lifecycle: task is still being actively managed
IN_LIFECYCLE = frozenset({TaskPhase.PENDING, TaskPhase.PROCESSING, TaskPhase.HOLDING, TaskPhase.COMPLETED, TaskPhase.ACCEPTED})

# Terminal: absolutely final, no transitions out
TERMINAL = frozenset({TaskPhase.FINISHED, TaskPhase.CANCELLED})


# ---------------------------------------------------------------------------
# Node type — classifies task tree nodes
# ---------------------------------------------------------------------------

class NodeType(str, Enum):
    """Classifies task tree nodes for dispatch and lifecycle decisions."""
    TASK = "task"                       # Regular work task assigned to an employee
    REVIEW = "review"                   # Supervisor review of children results
    CEO_PROMPT = "ceo_prompt"           # CEO's original task prompt (container root)
    CEO_FOLLOWUP = "ceo_followup"       # CEO follow-up message in existing tree
    CEO_REQUEST = "ceo_request"         # CEO confirmation request
    WATCHDOG_NUDGE = "watchdog_nudge"   # System watchdog probe for stuck projects
    ADHOC = "adhoc"                     # Ad-hoc notification/system task (not project work)
    SYSTEM = "system"                   # Infrastructure system task


# System node types that auto-skip COMPLETED → ACCEPTED → FINISHED
SYSTEM_NODE_TYPES = frozenset({
    NodeType.REVIEW, NodeType.CEO_REQUEST, NodeType.WATCHDOG_NUDGE,
    NodeType.ADHOC, NodeType.SYSTEM,
})

# Node types that should NOT trigger project completion checks
SKIP_COMPLETION_TYPES = frozenset({NodeType.WATCHDOG_NUDGE, NodeType.ADHOC, NodeType.SYSTEM})


# ---------------------------------------------------------------------------
# State transition helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Documentation string for agents
# ---------------------------------------------------------------------------

TASK_LIFECYCLE_DOC = """## Task Lifecycle States
Tasks follow: pending → processing → completed → accepted → finished.
Full state machine documentation is available in the SOPs list as task_lifecycle_states — read() it for details.
"""
