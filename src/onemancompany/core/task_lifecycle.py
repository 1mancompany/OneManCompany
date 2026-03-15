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
    TaskPhase.COMPLETED:  [TaskPhase.ACCEPTED, TaskPhase.FAILED, TaskPhase.PENDING, TaskPhase.CANCELLED],
    TaskPhase.ACCEPTED:   [TaskPhase.FINISHED],
    TaskPhase.FINISHED:   [],
    TaskPhase.FAILED:     [TaskPhase.PROCESSING, TaskPhase.CANCELLED],
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
Every task in the system follows this state machine:

| State | Meaning |
|-------|---------|
| pending | 已创建，等待处理 |
| processing | 正在被某员工执行 |
| holding | 等待子任务完成或外部输入 |
| completed | 员工完成执行，等待上级审核 |
| accepted | 上级审核通过 |
| finished | 全部完成，已归档 |
| failed | 执行失败或审核驳回 |
| blocked | 依赖任务失败，无法继续 |
| cancelled | 已取消 |

State flow:
  pending → processing → completed → accepted → finished
                ↕ holding (pause/resume)
  completed → failed (rejection) → processing (retry)

Key distinctions:
- completed = employee says "I'm done" (awaiting review)
- accepted = supervisor says "looks good" (deliverable approved)
- Only accepted/finished unblock downstream dependent tasks

Task tree model:
- 父任务通过 dispatch_child() 分发子任务给员工
- 子任务完成后，系统自动唤醒父任务进行审核
- 父任务通过 accept_child() / reject_child() 审核每个子任务
- 全部子任务通过 → 父任务自动完成并向上汇报
"""
