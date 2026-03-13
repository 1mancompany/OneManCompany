# Unified Task Status System Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate dual-object status drift by deleting AgentTask, making TaskNode the sole task object, and unifying the status enum with validated transitions.

**Architecture:** Delete AgentTask/AgentTaskBoard. TaskNode becomes SSOT for all task data. EmployeeManager uses a lightweight ScheduleEntry index (node_id + tree_path) instead of an in-memory task queue. All status changes go through `TaskNode.set_status()` which validates via `transition()`.

**Tech Stack:** Python 3.11+, dataclasses, PyYAML, pytest, asyncio

**Spec:** `docs/superpowers/specs/2026-03-12-unified-task-status-design.md`

**IMPORTANT:** Read `vibe-coding-guide.md` before starting any task.

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/onemancompany/core/task_lifecycle.py` | Modify | Unified TaskPhase enum, category sets, transition validation |
| `src/onemancompany/core/task_tree.py` | Modify | TaskNode as SSOT (new fields, set_status, renamed methods) |
| `src/onemancompany/core/system_tasks.py` | Create | SystemTaskTree for cron/ad-hoc tasks |
| `src/onemancompany/core/vessel.py` | Major refactor | Delete AgentTask/Board, add ScheduleEntry, rewrite task execution |
| `src/onemancompany/core/task_persistence.py` | Major refactor | Remove AgentTask serialization, crash recovery from trees |
| `src/onemancompany/core/vessel_harness.py` | Modify | Update Protocol signatures |
| `src/onemancompany/core/agent_loop.py` | Modify | Update re-exports |
| `src/onemancompany/agents/tree_tools.py` | Modify | Use schedule_node(), set_status(), add retry_child() |
| `src/onemancompany/core/tree_manager.py` | Modify | Use set_status() |
| `src/onemancompany/core/automation.py` | Modify | System TaskNodes for cron |
| `src/onemancompany/api/routes.py` | Modify | Use schedule_node(), set_status() |

---

## Chunk 1: Foundation — TaskPhase Enum & TaskNode SSOT

### Task 1: Update TaskPhase enum and category sets

**Files:**
- Modify: `src/onemancompany/core/task_lifecycle.py`
- Test: `tests/unit/test_task_lifecycle.py`

- [ ] **Step 1: Write failing tests for new enum values and category sets**

```python
# tests/unit/test_task_lifecycle.py — ADD these tests

def test_completed_phase_exists():
    """COMPLETED replaces COMPLETE."""
    assert TaskPhase.COMPLETED == "completed"

def test_accepted_phase_exists():
    assert TaskPhase.ACCEPTED == "accepted"

def test_complete_phase_removed():
    """Old COMPLETE value no longer exists."""
    with pytest.raises(ValueError):
        TaskPhase("complete")

def test_resolved_set():
    from onemancompany.core.task_lifecycle import RESOLVED
    assert TaskPhase.ACCEPTED in RESOLVED
    assert TaskPhase.FINISHED in RESOLVED
    assert TaskPhase.FAILED in RESOLVED
    assert TaskPhase.CANCELLED in RESOLVED
    assert TaskPhase.COMPLETED not in RESOLVED

def test_done_executing_set():
    from onemancompany.core.task_lifecycle import DONE_EXECUTING
    assert TaskPhase.COMPLETED in DONE_EXECUTING
    assert TaskPhase.ACCEPTED in DONE_EXECUTING
    assert TaskPhase.PENDING not in DONE_EXECUTING

def test_unblocks_dependents_set():
    from onemancompany.core.task_lifecycle import UNBLOCKS_DEPENDENTS
    assert TaskPhase.ACCEPTED in UNBLOCKS_DEPENDENTS
    assert TaskPhase.FINISHED in UNBLOCKS_DEPENDENTS
    assert TaskPhase.FAILED not in UNBLOCKS_DEPENDENTS

def test_will_not_deliver_set():
    from onemancompany.core.task_lifecycle import WILL_NOT_DELIVER
    assert TaskPhase.FAILED in WILL_NOT_DELIVER
    assert TaskPhase.BLOCKED in WILL_NOT_DELIVER
    assert TaskPhase.CANCELLED in WILL_NOT_DELIVER

def test_transition_completed_to_accepted():
    result = transition("t1", TaskPhase.COMPLETED, TaskPhase.ACCEPTED)
    assert result == TaskPhase.ACCEPTED

def test_transition_completed_to_failed_rejection():
    """Supervisor rejection: COMPLETED → FAILED."""
    result = transition("t1", TaskPhase.COMPLETED, TaskPhase.FAILED)
    assert result == TaskPhase.FAILED

def test_transition_accepted_to_finished():
    result = transition("t1", TaskPhase.ACCEPTED, TaskPhase.FINISHED)
    assert result == TaskPhase.FINISHED

def test_transition_completed_to_processing_invalid():
    with pytest.raises(TaskTransitionError):
        transition("t1", TaskPhase.COMPLETED, TaskPhase.PROCESSING)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_task_lifecycle.py -v`
Expected: FAIL — `TaskPhase.COMPLETED` doesn't exist, `RESOLVED` not importable

- [ ] **Step 3: Update task_lifecycle.py**

In `src/onemancompany/core/task_lifecycle.py`:

1. Rename `COMPLETE = "complete"` to `COMPLETED = "completed"`
2. Add `ACCEPTED = "accepted"` after COMPLETED
3. Update `VALID_TRANSITIONS`:
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
4. Replace `ACTIVE_STATES` and `TERMINAL_STATES` with:
   ```python
   RESOLVED = frozenset({TaskPhase.ACCEPTED, TaskPhase.FINISHED, TaskPhase.FAILED, TaskPhase.CANCELLED})
   DONE_EXECUTING = frozenset({TaskPhase.COMPLETED, TaskPhase.ACCEPTED, TaskPhase.FINISHED, TaskPhase.FAILED, TaskPhase.CANCELLED})
   UNBLOCKS_DEPENDENTS = frozenset({TaskPhase.ACCEPTED, TaskPhase.FINISHED})
   WILL_NOT_DELIVER = frozenset({TaskPhase.FAILED, TaskPhase.BLOCKED, TaskPhase.CANCELLED})
   IN_LIFECYCLE = frozenset({TaskPhase.PENDING, TaskPhase.PROCESSING, TaskPhase.HOLDING, TaskPhase.COMPLETED, TaskPhase.ACCEPTED})
   TERMINAL = frozenset({TaskPhase.FINISHED, TaskPhase.CANCELLED})
   ```
5. Update `TASK_LIFECYCLE_DOC` string per spec

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_task_lifecycle.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/task_lifecycle.py tests/unit/test_task_lifecycle.py
git commit -m "refactor: unify TaskPhase enum — add COMPLETED, ACCEPTED, new category sets"
```

---

### Task 2: Update TaskNode with new fields and set_status()

**Files:**
- Modify: `src/onemancompany/core/task_tree.py`
- Test: `tests/unit/core/test_task_tree.py`

- [ ] **Step 1: Write failing tests for set_status() and new properties**

```python
# tests/unit/core/test_task_tree.py — ADD these tests

from onemancompany.core.task_lifecycle import TaskPhase, TaskTransitionError

def test_node_set_status_valid():
    node = TaskNode(employee_id="e1", description="test")
    assert node.status == "pending"
    node.set_status(TaskPhase.PROCESSING)
    assert node.status == "processing"

def test_node_set_status_invalid():
    node = TaskNode(employee_id="e1", description="test")
    with pytest.raises(TaskTransitionError):
        node.set_status(TaskPhase.ACCEPTED)  # can't go pending → accepted

def test_node_is_resolved():
    node = TaskNode(employee_id="e1", description="test", status="accepted")
    assert node.is_resolved is True
    node2 = TaskNode(employee_id="e1", description="test", status="completed")
    assert node2.is_resolved is False

def test_node_is_done_executing():
    node = TaskNode(employee_id="e1", description="test", status="completed")
    assert node.is_done_executing is True
    node2 = TaskNode(employee_id="e1", description="test", status="processing")
    assert node2.is_done_executing is False

def test_node_unblocks_dependents():
    node = TaskNode(employee_id="e1", description="test", status="accepted")
    assert node.unblocks_dependents is True
    node2 = TaskNode(employee_id="e1", description="test", status="failed")
    assert node2.unblocks_dependents is False

def test_node_new_fields_in_dict():
    node = TaskNode(employee_id="e1", description="test", task_type="project", model_used="gpt-4", project_dir="/tmp/proj")
    d = node.to_dict()
    assert d["task_type"] == "project"
    assert d["model_used"] == "gpt-4"
    assert d["project_dir"] == "/tmp/proj"

def test_node_from_dict_new_fields():
    d = {"employee_id": "e1", "description": "test", "task_type": "project", "model_used": "gpt-4", "project_dir": "/p"}
    node = TaskNode.from_dict(d)
    assert node.task_type == "project"
    assert node.model_used == "gpt-4"

def test_tree_all_children_done():
    tree = TaskTree(project_id="p1")
    root = tree.create_root("e1", "root")
    c1 = tree.add_child(root.id, "e2", "child1", [])
    c2 = tree.add_child(root.id, "e3", "child2", [])
    c1.status = "completed"
    c2.status = "accepted"
    assert tree.all_children_done(root.id) is True

def test_tree_all_children_done_false_when_processing():
    tree = TaskTree(project_id="p1")
    root = tree.create_root("e1", "root")
    c1 = tree.add_child(root.id, "e2", "child1", [])
    c1.status = "processing"
    assert tree.all_children_done(root.id) is False

def test_tree_all_deps_resolved():
    tree = TaskTree(project_id="p1")
    root = tree.create_root("e1", "root")
    c1 = tree.add_child(root.id, "e2", "child1", [])
    c2 = tree.add_child(root.id, "e3", "child2", [], depends_on=[c1.id])
    c1.status = "accepted"
    assert tree.all_deps_resolved(c2.id) is True

def test_status_migration_complete_to_completed():
    """Old 'complete' status should be normalized to 'completed' on load."""
    d = {"employee_id": "e1", "description": "test", "status": "complete"}
    node = TaskNode.from_dict(d)
    assert node.status == "completed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py -v -k "set_status or is_resolved or is_done or unblocks or new_fields or all_children_done or all_deps_resolved or migration"`
Expected: FAIL — `set_status` not defined, properties not defined

- [ ] **Step 3: Update task_tree.py**

1. Remove `_TERMINAL` frozenset (line 17)
2. Add imports:
   ```python
   from onemancompany.core.task_lifecycle import (
       TaskPhase, TaskTransitionError, transition,
       RESOLVED, DONE_EXECUTING, UNBLOCKS_DEPENDENTS, WILL_NOT_DELIVER,
   )
   ```
3. Add new fields to TaskNode:
   ```python
   task_type: str = "simple"
   model_used: str = ""
   project_dir: str = ""
   ```
4. Add `_STATUS_MIGRATION` and update `from_dict()`:
   ```python
   _STATUS_MIGRATION = {"complete": "completed"}

   @classmethod
   def from_dict(cls, d: dict) -> TaskNode:
       filtered = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
       if "status" in filtered:
           filtered["status"] = _STATUS_MIGRATION.get(filtered["status"], filtered["status"])
       return cls(**filtered)
   ```
5. Add `set_status()` method:
   ```python
   def set_status(self, target: TaskPhase) -> None:
       current = TaskPhase(self.status)
       transition(self.id, current, target)
       self.status = target.value
   ```
6. Replace `is_terminal` property with `is_resolved`:
   ```python
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
7. Add `task_type`, `model_used`, `project_dir` to `to_dict()` method
8. Rename tree methods:
   - `all_children_terminal()` → `all_children_done()` using `is_done_executing`
   - `all_deps_terminal()` → `all_deps_resolved()` using `is_resolved`
   - `has_failed_deps()` — update to use `WILL_NOT_DELIVER`
9. Keep old method names as aliases temporarily for the migration (remove in Task 6):
   ```python
   def all_children_terminal(self, node_id): return self.all_children_done(node_id)
   def all_deps_terminal(self, node_id): return self.all_deps_resolved(node_id)
   ```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py -v`
Expected: ALL PASS (both new and existing tests via aliases)

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/task_tree.py tests/unit/core/test_task_tree.py
git commit -m "refactor: TaskNode as SSOT — add set_status, new fields, renamed methods"
```

---

### Task 3: Create SystemTaskTree

**Files:**
- Create: `src/onemancompany/core/system_tasks.py`
- Test: `tests/unit/core/test_system_tasks.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/core/test_system_tasks.py

import pytest
from pathlib import Path
from onemancompany.core.system_tasks import SystemTaskTree
from onemancompany.core.task_lifecycle import TaskPhase

def test_create_system_node():
    tree = SystemTaskTree("emp1")
    node = tree.create_system_node("emp1", "[cron:daily] Run report")
    assert node.node_type == "system"
    assert node.task_type == "simple"
    assert node.employee_id == "emp1"
    assert node.status == "pending"

def test_save_and_load(tmp_path):
    tree = SystemTaskTree("emp1")
    tree.create_system_node("emp1", "task1")
    path = tmp_path / "system_tasks.yaml"
    tree.save(path)
    loaded = SystemTaskTree.load(path, "emp1")
    assert len(loaded.get_all_nodes()) == 1

def test_auto_cleanup_old_finished(tmp_path):
    from datetime import datetime, timedelta
    tree = SystemTaskTree("emp1")
    node = tree.create_system_node("emp1", "old task")
    node.status = TaskPhase.FINISHED.value
    node.completed_at = (datetime.now() - timedelta(hours=25)).isoformat()
    path = tmp_path / "system_tasks.yaml"
    tree.save(path)  # should auto-clean
    loaded = SystemTaskTree.load(path, "emp1")
    assert len(loaded.get_all_nodes()) == 0

def test_keeps_recent_finished(tmp_path):
    tree = SystemTaskTree("emp1")
    node = tree.create_system_node("emp1", "recent task")
    node.status = TaskPhase.FINISHED.value
    node.completed_at = datetime.now().isoformat()
    path = tmp_path / "system_tasks.yaml"
    tree.save(path)
    loaded = SystemTaskTree.load(path, "emp1")
    assert len(loaded.get_all_nodes()) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_system_tasks.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement SystemTaskTree**

```python
# src/onemancompany/core/system_tasks.py
"""System task tree — lightweight tree for cron/ad-hoc tasks per employee."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import yaml

from onemancompany.core.task_lifecycle import TaskPhase, RESOLVED
from onemancompany.core.task_tree import TaskNode

_CLEANUP_AGE = timedelta(hours=24)


class SystemTaskTree:
    """Per-employee tree for non-project tasks. Auto-cleans old resolved nodes on save."""

    def __init__(self, employee_id: str) -> None:
        self.employee_id = employee_id
        self._nodes: dict[str, TaskNode] = {}

    def create_system_node(self, employee_id: str, description: str) -> TaskNode:
        node = TaskNode(
            employee_id=employee_id,
            description=description,
            node_type="system",
            task_type="simple",
        )
        self._nodes[node.id] = node
        return node

    def get_node(self, node_id: str) -> TaskNode | None:
        return self._nodes.get(node_id)

    def get_all_nodes(self) -> list[TaskNode]:
        return list(self._nodes.values())

    def get_pending_nodes(self) -> list[TaskNode]:
        return [n for n in self._nodes.values() if n.status == TaskPhase.PENDING.value]

    def save(self, path: Path) -> None:
        self._cleanup_old()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "employee_id": self.employee_id,
            "nodes": [n.to_dict() for n in self._nodes.values()],
        }
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path, employee_id: str = "") -> SystemTaskTree:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tree = cls(employee_id=employee_id or data.get("employee_id", ""))
        for nd in data.get("nodes", []):
            node = TaskNode.from_dict(nd)
            tree._nodes[node.id] = node
        return tree

    def _cleanup_old(self) -> None:
        now = datetime.now()
        to_remove = []
        for nid, node in self._nodes.items():
            if TaskPhase(node.status) in RESOLVED and node.completed_at:
                try:
                    completed = datetime.fromisoformat(node.completed_at)
                    if now - completed > _CLEANUP_AGE:
                        to_remove.append(nid)
                except ValueError:
                    pass
        for nid in to_remove:
            del self._nodes[nid]
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_system_tasks.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/system_tasks.py tests/unit/core/test_system_tasks.py
git commit -m "feat: add SystemTaskTree for cron/ad-hoc tasks"
```

---

## Chunk 2: Core Migration — Delete AgentTask, Rewrite Vessel

### Task 4: Update vessel_harness.py and agent_loop.py

**Files:**
- Modify: `src/onemancompany/core/vessel_harness.py`
- Modify: `src/onemancompany/core/agent_loop.py`

- [ ] **Step 1: Update vessel_harness.py Protocol signatures**

Replace all `"AgentTask"` type hints with `node_id: str, tree_path: str` parameters or remove them where no longer applicable.

Key changes:
- `TaskHarness.push()` → `schedule_node(node_id: str, tree_path: str) -> None`
- `TaskHarness.get_next_pending()` → `get_next_pending() -> tuple[str, str] | None` (returns node_id, tree_path)
- `EventHarness` methods: replace `task: "AgentTask"` with `node_id: str`
- `StorageHarness.append_history`: replace `task: "AgentTask"` with `node_id: str`
- `ContextHarness.build_task_context`: replace `task: "AgentTask"` with `node_id: str`
- `LifecycleHarness.call_post_task`: replace `task: "AgentTask"` with `node_id: str`

- [ ] **Step 2: Update agent_loop.py re-exports**

Remove `AgentTask`, `AgentTaskBoard` from exports. Add `ScheduleEntry`. Keep all other exports (Vessel, EmployeeManager, etc.) unchanged.

- [ ] **Step 3: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.core.vessel_harness import TaskHarness; print('OK')"`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add src/onemancompany/core/vessel_harness.py src/onemancompany/core/agent_loop.py
git commit -m "refactor: update harness protocols — remove AgentTask references"
```

---

### Task 5: Rewrite vessel.py — Delete AgentTask, add ScheduleEntry

This is the largest task. It touches vessel.py (1700+ lines). Work incrementally:

**Files:**
- Modify: `src/onemancompany/core/vessel.py`
- Modify: `tests/unit/core/test_vessel_holding.py`

- [ ] **Step 1: Add ScheduleEntry dataclass to vessel.py**

```python
@dataclass
class ScheduleEntry:
    """Pure pointer to a TaskNode. No business data."""
    node_id: str
    tree_path: str
```

Add `_schedule` and `_task_logs` to `EmployeeManager.__init__()`:
```python
self._schedule: dict[str, list[ScheduleEntry]] = {}
self._task_logs: dict[str, list[dict]] = {}
```

Add helper methods:
```python
def schedule_node(self, employee_id: str, node_id: str, tree_path: str) -> None:
    entry = ScheduleEntry(node_id=node_id, tree_path=tree_path)
    self._schedule.setdefault(employee_id, []).append(entry)

def unschedule(self, employee_id: str, node_id: str) -> None:
    entries = self._schedule.get(employee_id, [])
    self._schedule[employee_id] = [e for e in entries if e.node_id != node_id]

def get_next_scheduled(self, employee_id: str) -> ScheduleEntry | None:
    for entry in self._schedule.get(employee_id, []):
        tree = _load_tree(entry.tree_path)
        if not tree:
            continue
        node = tree.get_node(entry.node_id)
        if node and TaskPhase(node.status) == TaskPhase.PENDING and tree.all_deps_resolved(node.id):
            return entry
    return None
```

- [ ] **Step 2: Migrate `_run_task` to use TaskNode instead of AgentTask**

Replace every `task.status`, `task.result`, `task.description` etc. with tree load + node access:

```python
async def _run_task(self, employee_id: str, entry: ScheduleEntry) -> None:
    tree = _load_tree(entry.tree_path)
    node = tree.get_node(entry.node_id)
    # ... use node.status, node.description, node.result ...
    # ... save tree after each status change ...
```

This is a large mechanical replacement. Key patterns:
- `task.status = TaskPhase.PROCESSING` → `node.set_status(TaskPhase.PROCESSING); _save_tree(entry.tree_path, tree)`
- `task.result = "..."` → `node.result = "..."; _save_tree(entry.tree_path, tree)`
- `task.input_tokens += n` → `node.input_tokens += n`
- `task.logs.append(...)` → `self._task_logs.setdefault(entry.node_id, []).append(...)`

- [ ] **Step 3: Delete AgentTask and AgentTaskBoard classes**

Remove the `AgentTask` dataclass (lines ~220-265) and `AgentTaskBoard` class (lines ~267-314). Remove `_node_id_for_task()` helper. Remove `task_id_map` references.

- [ ] **Step 4: Update `_post_task_cleanup` to include ACCEPTED step**

```python
# After agent execution completes:
node.set_status(TaskPhase.COMPLETED)

# Simple tasks: auto-accept and finish
if node.task_type == "simple":
    node.set_status(TaskPhase.ACCEPTED)
    node.set_status(TaskPhase.FINISHED)

# Project tasks: stop at COMPLETED, wait for accept_child()
```

- [ ] **Step 5: Update crash recovery**

Replace `load_all_active_tasks()` with tree-based recovery:
```python
def _recover_from_trees(self):
    # Scan all project trees and system trees
    # PROCESSING → set_status(PENDING)
    # HOLDING → leave as-is
    # Rebuild _schedule for PENDING (deps met) and HOLDING nodes
```

- [ ] **Step 6: Run existing tests, fix breakage**

Run: `.venv/bin/python -m pytest tests/unit/core/ -v`
Expected: Many failures — update tests to use new API

- [ ] **Step 7: Update test_vessel_holding.py**

Replace `AgentTask` references with `ScheduleEntry` + `TaskNode`. The holding tests need to create TaskNodes and use `set_status()` instead of directly setting `task.status`.

- [ ] **Step 8: Commit**

```bash
git add src/onemancompany/core/vessel.py tests/unit/core/
git commit -m "refactor: delete AgentTask — vessel uses TaskNode + ScheduleEntry"
```

---

### Task 6: Update tree_tools.py

**Files:**
- Modify: `src/onemancompany/agents/tree_tools.py`
- Test: `tests/unit/agents/test_tree_tools.py`

- [ ] **Step 1: Update dispatch_child()**

Replace:
```python
agent_task = handle.push_task(description, project_id=..., project_dir=...)
tree.task_id_map[agent_task.id] = child.id
```

With:
```python
employee_manager.schedule_node(employee_id, child.id, tree_path)
```

- [ ] **Step 2: Update accept_child() to use set_status()**

Replace `node.status = "accepted"` with `node.set_status(TaskPhase.ACCEPTED)`.

- [ ] **Step 3: Update reject_child() to use set_status()**

Replace `node.status = "failed"` with `node.set_status(TaskPhase.FAILED)`.

- [ ] **Step 4: Add retry_child() tool**

```python
def retry_child(node_id: str, correction: str = "") -> str:
    """Retry a failed child task. Transitions FAILED → PROCESSING."""
    node = tree.get_node(node_id)
    if not node:
        return f"Node {node_id} not found"
    node.set_status(TaskPhase.PROCESSING)
    if correction:
        node.description += f"\n\n[Correction] {correction}"
    # Re-schedule the node
    employee_manager.schedule_node(node.employee_id, node.id, tree_path)
    tree.save(path)
    return f"Retrying task {node_id}"
```

- [ ] **Step 5: Update unblock_child() and cancel_child()**

Replace string assignments with `set_status()` calls.

- [ ] **Step 6: Remove old method aliases from task_tree.py**

Remove `all_children_terminal` and `all_deps_terminal` aliases added in Task 2.

- [ ] **Step 7: Run tests and fix**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tree_tools.py -v`
Fix any breakage from API changes.

- [ ] **Step 8: Commit**

```bash
git add src/onemancompany/agents/tree_tools.py src/onemancompany/core/task_tree.py tests/unit/agents/test_tree_tools.py
git commit -m "refactor: tree_tools uses set_status() and schedule_node()"
```

---

## Chunk 3: Remaining Migrations

### Task 7: Update tree_manager.py

**Files:**
- Modify: `src/onemancompany/core/tree_manager.py`
- Test: `tests/unit/core/test_tree_manager.py`

- [ ] **Step 1: Replace string status assignments with set_status()**

Find all `node.status = "..."` in tree_manager.py and replace with `node.set_status(TaskPhase.XXX)`.

- [ ] **Step 2: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_tree_manager.py -v`

- [ ] **Step 3: Commit**

```bash
git add src/onemancompany/core/tree_manager.py
git commit -m "refactor: tree_manager uses set_status()"
```

---

### Task 8: Update automation.py for system tasks

**Files:**
- Modify: `src/onemancompany/core/automation.py`

- [ ] **Step 1: Update cron task dispatch**

Replace `loop.push_task(description)` with:
```python
from onemancompany.core.system_tasks import SystemTaskTree
sys_tree = SystemTaskTree.load_or_create(employee_id)
node = sys_tree.create_system_node(employee_id, f"[cron:{cron_name}] {description}")
sys_tree.save(path)
employee_manager.schedule_node(employee_id, node.id, str(path))
```

- [ ] **Step 2: Update stop_cron task cancellation**

Replace `t.status = TaskPhase.CANCELLED` with loading the system tree and calling `node.set_status(TaskPhase.CANCELLED)`.

- [ ] **Step 3: Compile check**

Run: `.venv/bin/python -c "from onemancompany.core.automation import start_cron; print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add src/onemancompany/core/automation.py
git commit -m "refactor: automation uses SystemTaskTree for cron tasks"
```

---

### Task 9: Update task_persistence.py

**Files:**
- Modify: `src/onemancompany/core/task_persistence.py`
- Test: `tests/unit/core/test_task_persistence.py`

- [ ] **Step 1: Remove AgentTask serialization functions**

Delete: `_task_to_dict()`, `_dict_to_task()`, `persist_task()`, `archive_task()`, `load_active_tasks()`, `load_all_active_tasks()`.

- [ ] **Step 2: Add tree-based crash recovery**

```python
def recover_schedule_from_trees(employee_manager) -> None:
    """Scan all project and system trees, rebuild EmployeeManager._schedule."""
    # 1. Find all task_tree.yaml and system_tasks.yaml files
    # 2. For PROCESSING nodes: set_status(PENDING)
    # 3. For PENDING nodes with deps resolved: schedule_node()
    # 4. For HOLDING nodes: schedule_node() (to monitor children)
```

- [ ] **Step 3: Update tests**

Remove tests for deleted functions. Add tests for `recover_schedule_from_trees()`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_persistence.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/task_persistence.py tests/unit/core/test_task_persistence.py
git commit -m "refactor: task_persistence — remove AgentTask serialization, add tree-based recovery"
```

---

### Task 10: Update routes.py

**Files:**
- Modify: `src/onemancompany/api/routes.py`

- [ ] **Step 1: Replace all AgentTask usage with TaskNode**

Key changes:
- Task dispatch endpoints: use `schedule_node()` instead of `board.push()`
- Status assignments: use `node.set_status()` instead of string assignment
- Task cancellation: use `node.set_status(TaskPhase.CANCELLED)`
- Remove `task_id_map` references
- Replace `from onemancompany.core.vessel import AgentTask` with TaskNode imports

- [ ] **Step 2: Compile check**

Run: `.venv/bin/python -c "from onemancompany.api.routes import app; print('OK')"`

- [ ] **Step 3: Run route tests**

Run: `.venv/bin/python -m pytest tests/unit/api/ -v`

- [ ] **Step 4: Commit**

```bash
git add src/onemancompany/api/routes.py
git commit -m "refactor: routes use TaskNode + schedule_node() — no more AgentTask"
```

---

### Task 11: Final cleanup and full test suite

**Files:**
- All modified files

- [ ] **Step 1: Search for any remaining AgentTask references**

Run: `grep -r "AgentTask\|AgentTaskBoard\|task_id_map\|_node_id_for_task" src/onemancompany/ --include="*.py"`
Expected: No matches (except possibly comments or docstrings to update)

- [ ] **Step 2: Search for any remaining direct status assignments**

Run: `grep -rn "\.status\s*=" src/onemancompany/ --include="*.py" | grep -v "set_status\|_STATUS_MIGRATION\|__init__\|test_"`
Expected: No direct assignments outside of `set_status()` implementation and tests

- [ ] **Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 4: Update README Task Status System section if needed**

Verify the README section already written matches the final implementation.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "refactor: complete unified task status system — AgentTask eliminated, TaskNode is SSOT"
```
