"""Task lifecycle state machine — unified task states and type classification.

Two orthogonal dimensions:
  - TaskType: project vs simple — determines whether retrospective runs
  - TaskPhase: the project/task lifecycle state machine

State flow for SIMPLE tasks:
  pending → processing → complete → finished

State flow for PROJECT tasks:
  pending → processing → (holding →)* complete → reviewing → finished
                                         ↓
                                     acceptance / ea_review / rectification
                                     (sub-states within 'complete')

Dispatch dependency model:
  - Parallel: independent tasks, no depends_on
  - Sequential: task B depends_on task A — B stays 'pending' until A is 'complete'
  - Failure propagation: if A fails, all tasks depending on A are marked 'blocked'
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
      HOLDING     — blocked, waiting for a dependency to complete
      COMPLETE    — all execution done (for SIMPLE: ready to finish)
      FINISHED    — fully done, archived

    Project-only sub-states (between COMPLETE and FINISHED):
      NEEDS_ACCEPTANCE  — waiting for responsible officer to accept
      ACCEPTED          — officer accepted, waiting for EA review
      REJECTED          — officer/EA rejected, needs rectification
      RECTIFICATION     — being fixed after rejection
      REVIEWING         — acceptance passed, in retrospective
    """
    # --- Core states ---
    PENDING = "pending"
    PROCESSING = "processing"
    HOLDING = "holding"
    COMPLETE = "complete"
    FINISHED = "finished"

    # --- Project sub-states (between COMPLETE and FINISHED) ---
    NEEDS_ACCEPTANCE = "needs_acceptance"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    RECTIFICATION = "rectification"
    REVIEWING = "reviewing"

    # --- Error state ---
    FAILED = "failed"
    BLOCKED = "blocked"      # dependency failed, cannot proceed
    CANCELLED = "cancelled"


# Valid state transitions
VALID_TRANSITIONS: dict[TaskPhase, list[TaskPhase]] = {
    # Core flow
    TaskPhase.PENDING: [TaskPhase.PROCESSING, TaskPhase.CANCELLED, TaskPhase.HOLDING],
    TaskPhase.PROCESSING: [TaskPhase.COMPLETE, TaskPhase.HOLDING, TaskPhase.FAILED, TaskPhase.CANCELLED],
    TaskPhase.HOLDING: [TaskPhase.PROCESSING, TaskPhase.BLOCKED, TaskPhase.CANCELLED],
    TaskPhase.COMPLETE: [TaskPhase.FINISHED, TaskPhase.NEEDS_ACCEPTANCE],

    # Project acceptance flow
    TaskPhase.NEEDS_ACCEPTANCE: [TaskPhase.ACCEPTED, TaskPhase.REJECTED],
    TaskPhase.ACCEPTED: [TaskPhase.REVIEWING, TaskPhase.REJECTED, TaskPhase.FINISHED],
    TaskPhase.REJECTED: [TaskPhase.RECTIFICATION],
    TaskPhase.RECTIFICATION: [TaskPhase.PROCESSING],

    # Retrospective
    TaskPhase.REVIEWING: [TaskPhase.FINISHED],

    # Terminal states
    TaskPhase.FINISHED: [],
    TaskPhase.FAILED: [TaskPhase.PROCESSING],   # allow retry
    TaskPhase.BLOCKED: [TaskPhase.CANCELLED],
    TaskPhase.CANCELLED: [],
}


# States visible to employees as "this task is active"
ACTIVE_STATES = {
    TaskPhase.PENDING, TaskPhase.PROCESSING, TaskPhase.HOLDING,
    TaskPhase.COMPLETE, TaskPhase.NEEDS_ACCEPTANCE, TaskPhase.ACCEPTED,
    TaskPhase.REJECTED, TaskPhase.RECTIFICATION, TaskPhase.REVIEWING,
}

# Terminal states
TERMINAL_STATES = {TaskPhase.FINISHED, TaskPhase.FAILED, TaskPhase.BLOCKED, TaskPhase.CANCELLED}


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
| holding | 等待依赖任务完成 |
| complete | 所有子任务完成（简单任务直接结束，项目任务进入验收） |
| needs_acceptance | 等待负责人验收 |
| accepted | 验收通过，等待EA审核 |
| rejected | 验收/EA审核未通过，需要整改 |
| rectification | 整改中 |
| reviewing | 验收通过，复盘中 |
| finished | 全部完成（含复盘），已归档 |
| failed | 执行失败 |
| blocked | 依赖任务失败，无法继续 |
| cancelled | 已取消 |

Task types:
- **simple**: 单一操作任务（发邮件、查信息等），EA可自行完成或直接分发给最合适的员工，不触发复盘
- **project**: 项目级任务（开发、设计等），完整验收+可选复盘

Dependency rules:
- 并行任务：无 depends_on，可同时执行
- 序列任务：有 depends_on，必须等依赖完成才开始
- 依赖失败：依赖任务 failed → 本任务 blocked，需负责人处理
"""
