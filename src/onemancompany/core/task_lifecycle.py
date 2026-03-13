"""Task lifecycle state machine — unified task states and type classification.

Two orthogonal dimensions:
  - TaskType: project vs simple — determines whether retrospective runs
  - TaskPhase: the project/task lifecycle state machine

State flow (both SIMPLE and PROJECT):
  pending → processing → (holding →)* completed → accepted → finished

Task tree model:
  - Parent dispatches children via dispatch_child()
  - Children complete → parent woken to review via accept_child() / reject_child()
  - All children accepted → parent auto-completes
"""

from __future__ import annotations

from enum import Enum


# ---------------------------------------------------------------------------
# Task type — determines lifecycle complexity
# ---------------------------------------------------------------------------

class TaskType(str, Enum):
    """Task classification — determines whether acceptance/retrospective applies.

    SIMPLE: single-action tasks (send email, look up info, etc.)
             No retrospective, no acceptance criteria needed.
    PROJECT: multi-step deliverable tasks (build feature, write report, etc.)
             Full lifecycle: acceptance → EA review → optional retrospective.
    """
    SIMPLE = "simple"
    PROJECT = "project"


# ---------------------------------------------------------------------------
# Task phase — unified state machine
# ---------------------------------------------------------------------------

class TaskPhase(str, Enum):
    """Task lifecycle phases — explicit, no ambiguity.

    Core states (both SIMPLE and PROJECT):
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
# Task type classification
# ---------------------------------------------------------------------------

# Keywords that suggest a project-level task (checked against CEO's task description)
_PROJECT_KEYWORDS = {
    "项目", "开发", "构建", "build", "develop", "implement", "设计", "design",
    "重构", "refactor", "迁移", "migrate", "系统", "system", "架构", "architecture",
    "feature", "功能", "模块", "module", "平台", "platform", "产品", "product",
}

# Keywords that suggest a simple task
_SIMPLE_KEYWORDS = {
    "发送", "send", "查询", "query", "look up", "查看", "check", "告诉", "tell",
    "回复", "reply", "转发", "forward", "提醒", "remind", "通知", "notify",
    "搜索", "search", "列出", "list",
}


def classify_task_type(task_description: str) -> TaskType:
    """Classify a task as PROJECT or SIMPLE based on description.

    This is the default heuristic. EA can override via set_acceptance_criteria
    (setting criteria implies PROJECT) or explicitly.
    """
    desc_lower = task_description.lower()

    # If acceptance criteria are set, it's always PROJECT (handled elsewhere)

    # Check for project keywords
    for kw in _PROJECT_KEYWORDS:
        if kw in desc_lower:
            return TaskType.PROJECT

    # Check for simple keywords
    for kw in _SIMPLE_KEYWORDS:
        if kw in desc_lower:
            return TaskType.SIMPLE

    # Default: simple (conservative — retrospective only when clearly needed)
    return TaskType.SIMPLE


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
