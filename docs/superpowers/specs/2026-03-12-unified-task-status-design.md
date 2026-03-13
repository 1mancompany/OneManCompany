# Unified Task Status System Design

## Goal

Eliminate the dual-object status problem: currently AgentTask (in-memory) and TaskNode (on-disk) both hold `status` for the same task, manually synced. This spec:

1. Deletes AgentTask entirely — TaskNode is the sole task object
2. Unifies the status enum (one `TaskPhase`, one `set_status()` method)
3. Replaces AgentTaskBoard with a lightweight scheduling index (`ScheduleEntry`)
4. Enforces all status changes through validated `set_status()` — no direct assignment

## Architecture Change

### Before (dual-object, dual-status)

```text
TaskNode (disk, YAML)              AgentTask (memory)
  status: "completed"    ←sync→     status: TaskPhase.COMPLETE
  result: "..."          ←sync→     result: "..."
  cost_usd               ←sync→     estimated_cost_usd
  input_tokens            ←sync→     input_tokens
  ...                               logs, model_used, project_dir, ...

AgentTaskBoard (memory) — per-employee task queue holding AgentTask objects
Bridged by: tree.task_id_map[agent_task.id] = node.id
Synced manually in: vessel.py _update_tree_node_on_completion()
```

### After (single source of truth)

```text
TaskNode (disk, YAML) — SSOT for ALL task data
  status, result, cost, tokens, timestamps, description, task_type, model_used, project_dir

ScheduleEntry (memory only) — pure pointer, no business data
  node_id, tree_path → locates the TaskNode

EmployeeManager._schedule[employee_id] → list[ScheduleEntry]
EmployeeManager._task_logs[node_id] → list[dict]  (temporary log buffer)
```

## What Gets Removed

- `AgentTask` dataclass — deleted entirely
- `AgentTaskBoard` — replaced by `_schedule` index on EmployeeManager
- `task_id_map` on TaskTree — no longer needed
- `_node_id_for_task()` helper — no longer needed
- `task_persistence.py` AgentTask serialization — TaskNode in tree YAML is the persistent record
- `employees/{id}/tasks/*.yaml` — AgentTask persistence files
- `TaskPhase.COMPLETE` — renamed to `COMPLETED`
- `_TERMINAL` frozenset in `task_tree.py` — replaced by shared constants
- `ACTIVE_STATES` / `TERMINAL_STATES` — replaced by new category sets

## Unified Status Enum

```python
class TaskPhase(str, Enum):
    PENDING     = "pending"      # Created, waiting to start
    PROCESSING  = "processing"   # Agent actively executing
    HOLDING     = "holding"      # Waiting for child tasks / CEO response
    COMPLETED   = "completed"    # Execution done, awaiting review
    ACCEPTED    = "accepted"     # Supervisor approved result (unblocks dependents)
    FINISHED    = "finished"     # Archived (after retrospective if project type)
    FAILED      = "failed"       # Execution failed (retryable)
    BLOCKED     = "blocked"      # Dependency failed, cannot proceed
    CANCELLED   = "cancelled"    # Cancelled by CEO/supervisor
```

## State Transitions

```text
pending → processing → completed → accepted → finished
              ↕
           holding
              ↓
           failed ──(retry)──→ processing

pending/holding → blocked (dependency failed)
any non-terminal → cancelled
```

```python
VALID_TRANSITIONS: dict[TaskPhase, list[TaskPhase]] = {
    TaskPhase.PENDING:    [TaskPhase.PROCESSING, TaskPhase.HOLDING, TaskPhase.BLOCKED, TaskPhase.CANCELLED],
    TaskPhase.PROCESSING: [TaskPhase.COMPLETED, TaskPhase.HOLDING, TaskPhase.FAILED, TaskPhase.CANCELLED],
    TaskPhase.HOLDING:    [TaskPhase.PROCESSING, TaskPhase.BLOCKED, TaskPhase.CANCELLED],
    TaskPhase.COMPLETED:  [TaskPhase.ACCEPTED, TaskPhase.FAILED, TaskPhase.CANCELLED],
    TaskPhase.ACCEPTED:   [TaskPhase.FINISHED],
    TaskPhase.FINISHED:   [],
    TaskPhase.FAILED:     [TaskPhase.PROCESSING, TaskPhase.CANCELLED],
    TaskPhase.BLOCKED:    [TaskPhase.PENDING, TaskPhase.CANCELLED],
    TaskPhase.CANCELLED:  [],
}
```

`COMPLETED → FAILED` covers supervisor rejection via `reject_child()`. Child can be retried via `FAILED → PROCESSING`.

## Status Categories

```python
# Resolved — task won't produce more work (parent-wake trigger)
RESOLVED = frozenset({TaskPhase.ACCEPTED, TaskPhase.FINISHED, TaskPhase.FAILED, TaskPhase.CANCELLED})

# Done executing — completed (awaiting review) or resolved
# Used for parent-wake: parent wakes when all children are done executing
DONE_EXECUTING = frozenset({TaskPhase.COMPLETED, TaskPhase.ACCEPTED, TaskPhase.FINISHED, TaskPhase.FAILED, TaskPhase.CANCELLED})

# Unblocks dependents (dependency graph — successful completion only)
UNBLOCKS_DEPENDENTS = frozenset({TaskPhase.ACCEPTED, TaskPhase.FINISHED})

# Will not deliver results — dependents should be blocked or cancelled
WILL_NOT_DELIVER = frozenset({TaskPhase.FAILED, TaskPhase.BLOCKED, TaskPhase.CANCELLED})

# In lifecycle — not yet fully done
IN_LIFECYCLE = frozenset({TaskPhase.PENDING, TaskPhase.PROCESSING, TaskPhase.HOLDING, TaskPhase.COMPLETED, TaskPhase.ACCEPTED})

# Terminal — will never change again
TERMINAL = frozenset({TaskPhase.FINISHED, TaskPhase.CANCELLED})
```

## TaskNode Changes

New fields migrated from AgentTask:

```python
@dataclass
class TaskNode:
    # ... existing fields ...
    status: str = TaskPhase.PENDING.value
    task_type: str = "simple"         # NEW: "simple" | "project"
    model_used: str = ""              # NEW: which LLM executed
    project_dir: str = ""             # NEW: workspace path

    def set_status(self, target: TaskPhase) -> None:
        """Validated status transition. Raises TaskTransitionError if invalid."""
        current = TaskPhase(self.status)
        transition(self.id, current, target)
        self.status = target.value

    @property
    def is_resolved(self) -> bool:
        return TaskPhase(self.status) in RESOLVED

    @property
    def is_done_executing(self) -> bool:
        return TaskPhase(self.status) in DONE_EXECUTING

    @property
    def unblocks_dependents(self) -> bool:
        return TaskPhase(self.status) in UNBLOCKS_DEPENDENTS
```

Rename methods on TaskTree:

- `is_terminal` → `is_resolved`
- `all_children_terminal()` → `all_children_done()` — uses `is_done_executing` (parent wakes when all children done, including `COMPLETED`)
- `all_deps_terminal()` → `all_deps_resolved()` — uses `is_resolved` (deps must be fully resolved)
- `has_failed_deps()` — uses `WILL_NOT_DELIVER` set

## ScheduleEntry — Replaces AgentTask + AgentTaskBoard

```python
@dataclass
class ScheduleEntry:
    """Pure pointer to a TaskNode. No business data."""
    node_id: str
    tree_path: str  # path to the tree YAML (project or system tree)
```

EmployeeManager holds the scheduling index:

```python
class EmployeeManager:
    _schedule: dict[str, list[ScheduleEntry]] = {}  # employee_id → pending nodes
    _task_logs: dict[str, list[dict]] = {}           # node_id → temporary log buffer

    def schedule_node(self, employee_id: str, node_id: str, tree_path: str) -> None:
        """Add a node to the employee's schedule."""
        entry = ScheduleEntry(node_id=node_id, tree_path=tree_path)
        self._schedule.setdefault(employee_id, []).append(entry)

    def get_next_pending(self, employee_id: str) -> ScheduleEntry | None:
        """Find next scheduled node that is PENDING with deps resolved."""
        for entry in self._schedule.get(employee_id, []):
            tree = TaskTree.load(Path(entry.tree_path))
            node = tree.get_node(entry.node_id)
            if node and TaskPhase(node.status) == TaskPhase.PENDING and tree.all_deps_resolved(node.id):
                return entry
        return None

    def unschedule(self, employee_id: str, node_id: str) -> None:
        """Remove a completed/failed node from schedule."""
        entries = self._schedule.get(employee_id, [])
        self._schedule[employee_id] = [e for e in entries if e.node_id != node_id]
```

## Parent-Wake and Review Flow

```text
Parent dispatches 3 children → parent set_status(HOLDING)
  Children execute → each set_status(COMPLETED)
  all_children_done() returns True → parent woken (HOLDING → PROCESSING)
  Parent reviews each child:
    - accept_child(id) → child COMPLETED → ACCEPTED
    - reject_child(id) → child COMPLETED → FAILED
  If rejected child needs retry:
    - retry_child(id) → child FAILED → PROCESSING, parent → HOLDING again
  When all children ACCEPTED → parent auto-completes → COMPLETED
```

## System Tasks

**Cron-dispatched tasks** (go through `_execute_task`) → create TaskNode in per-employee SystemTaskTree:

- `node_type: "system"`, `task_type: "simple"`, auto-FINISHED on completion
- Persist to `employees/{id}/system_tasks.yaml`
- Auto-cleans finished/cancelled nodes older than 24h on save

**`schedule_system_task` coroutines** (routine, all-hands) → remain as-is in `_system_tasks` dict. Not task-lifecycle objects.

```python
class SystemTaskTree:
    """Lightweight tree for non-project tasks (cron, holding checks, ad-hoc).
    One per employee, persisted to employees/{id}/system_tasks.yaml.
    Auto-cleans finished/cancelled nodes older than 24h on save."""
```

## Task Execution Flow

### 1. CEO submits task

```text
CEO → POST /api/task
  → Create TaskNode in project tree (status: pending)
  → employee_manager.schedule_node(employee_id, node.id, tree_path)
  → EmployeeManager picks up → node.set_status(PROCESSING)
  → Agent executes → node.set_status(COMPLETED)
  → If simple: auto set_status(ACCEPTED) → set_status(FINISHED)
  → If project: wait for accept_child()
```

### 2. dispatch_child()

```text
Parent agent calls dispatch_child(employee_id, description)
  → Create child TaskNode in tree (status: pending)
  → Parent node.set_status(HOLDING)
  → schedule_node(child_employee_id, child.id, tree_path)
  → Child executes → PENDING → PROCESSING → COMPLETED
  → all_children_done() → parent woken → HOLDING → PROCESSING
  → Parent calls accept_child(id) → child COMPLETED → ACCEPTED
```

### 3. System/cron task

```text
Cron fires → Create TaskNode in SystemTaskTree (node_type: system)
  → schedule_node(employee_id, node.id, system_tree_path)
  → Execute → PENDING → PROCESSING → COMPLETED → ACCEPTED → FINISHED (all auto)
```

## Simple vs Project Task Behavior

| Transition           | Simple Task                    | Project Task                        |
| -------------------- | ------------------------------ | ----------------------------------- |
| completed → accepted | Automatic (skip review)        | Requires `accept_child()` by parent |
| accepted → finished  | Automatic (skip retrospective) | After EA retrospective (if flagged) |

## Enforcement

All status changes via `TaskNode.set_status()`:

```python
def set_status(self, target: TaskPhase) -> None:
    current = TaskPhase(self.status)
    transition(self.id, current, target)
    self.status = target.value
```

Direct `node.status = "x"` is banned. `set_status()` encapsulates validation + assignment.

## Crash Recovery

On server restart:

1. Load all task trees (project + system) from disk
2. Nodes with `status: processing` → `set_status(PENDING)` (re-execute)
3. Nodes with `status: holding` → leave as-is
4. Rebuild `_schedule`: only nodes in `PENDING` with deps resolved, plus `HOLDING` nodes
5. Do NOT schedule `PENDING` nodes with unmet deps — `_resolve_dependencies()` handles them when deps complete

## Disk Migration

```python
_STATUS_MIGRATION = {"complete": "completed"}

def _normalize_status(raw: str) -> str:
    return _STATUS_MIGRATION.get(raw, raw)
```

Apply in `TaskNode.from_dict()`. Old `employees/{id}/tasks/*.yaml` files archived.

## Scope Exclusions

- `cso_agent.py` sales statuses — domain-specific, not task lifecycle
- `schedule_system_task` coroutines — infrastructure operations, not task objects

## Updated TASK_LIFECYCLE_DOC

```python
TASK_LIFECYCLE_DOC = """## Task Lifecycle States
Every task in the system follows this state machine:

| State | Meaning |
|-------|---------|
| pending | 已创建，等待处理 |
| processing | 正在被某员工执行 |
| holding | 等待子任务完成 |
| completed | 执行完成，等待上级验收 |
| accepted | 上级验收通过（解除依赖阻塞） |
| finished | 全部完成，已归档 |
| failed | 执行失败（可重试） |
| blocked | 依赖任务失败，无法继续 |
| cancelled | 已取消 |

Key rules:
- completed ≠ accepted: 你提交结果后状态变为 completed，需要上级 accept 才能变为 accepted
- accepted 解除依赖阻塞: 依赖你的兄弟任务只有在你 accepted 后才会开始执行
- simple 任务自动跳过验收: completed → accepted → finished 自动完成
- project 任务需要上级手动验收: completed → 等待 accept_child() → accepted

Task tree model:
- 父任务通过 dispatch_child() 分发子任务给员工
- 子任务完成后，系统自动唤醒父任务进行审核
- 父任务通过 accept_child() / reject_child() 审核每个子任务
- 全部子任务通过 → 父任务自动完成并向上汇报
"""
```

## Files Affected

**Delete:**

- `employees/{id}/tasks/*.yaml` — AgentTask persistence files

**Major refactor:**

- `src/onemancompany/core/vessel.py` — Delete `AgentTask` and `AgentTaskBoard`, add `ScheduleEntry` and `_schedule`/`_task_logs` to EmployeeManager, all task operations go through TaskNode directly, update `_post_task_cleanup` to include `ACCEPTED` step, update crash recovery to rebuild from trees
- `src/onemancompany/core/task_persistence.py` — Remove AgentTask serialization, replace with crash recovery that scans trees and rebuilds `_schedule`

**Modify:**

- `src/onemancompany/core/task_lifecycle.py` — Rename `COMPLETE` → `COMPLETED`, add `ACCEPTED`, update transitions, add new category sets (`RESOLVED`, `DONE_EXECUTING`, `UNBLOCKS_DEPENDENTS`, `WILL_NOT_DELIVER`, `IN_LIFECYCLE`, `TERMINAL`), update `TASK_LIFECYCLE_DOC`
- `src/onemancompany/core/task_tree.py` — Remove `_TERMINAL`, import from `task_lifecycle`, add `set_status()`, rename `is_terminal` → `is_resolved`, add `is_done_executing`/`unblocks_dependents`, rename tree methods, add `task_type`/`model_used`/`project_dir` fields
- `src/onemancompany/agents/tree_tools.py` — Use `schedule_node()` instead of `board.push()`, status via `set_status()`, add `retry_child()`
- `src/onemancompany/core/tree_manager.py` — Status changes via `set_status()`
- `src/onemancompany/api/routes.py` — Use `schedule_node()`, status via `set_status()`
- `src/onemancompany/core/automation.py` — Create system TaskNodes for cron tasks, use `schedule_node()`
- `src/onemancompany/core/vessel_harness.py` — Update Protocol signatures: remove AgentTask references, use node_id + tree_path
- `src/onemancompany/core/agent_loop.py` — Update re-exports: remove AgentTask/AgentTaskBoard, export ScheduleEntry
- `src/onemancompany/core/models.py` — Verify consistency with new enum values
- `README.md` — Already updated

**New:**

- `src/onemancompany/core/system_tasks.py` — `SystemTaskTree` class

**Out of scope:**

- `src/onemancompany/agents/cso_agent.py` — Sales pipeline statuses
- `schedule_system_task` in vessel.py — Infrastructure coroutines
