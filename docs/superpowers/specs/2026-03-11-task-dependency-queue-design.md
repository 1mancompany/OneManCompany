# Task Dependency Queue Design

## Problem

The task tree currently only supports parent-child hierarchy. There is no way to express that a sibling task must complete before another can start. For example, when CEO submits a task:

1. EA enriches the task (adds acceptance criteria, classifies)
2. COO plans execution based on EA's enrichment result
3. Workers execute based on COO's plan

Steps 2 and 3 should wait for their predecessors, but today all children can start simultaneously.

## Solution

Add explicit `depends_on` edges to TaskNode. When dispatching a child task, agents can declare which sibling nodes must complete first. Dependent tasks stay PENDING until all dependencies reach a terminal state, at which point the dependency results are injected into the task's context and execution begins.

## Data Model Changes

### TaskNode (task_tree.py)

New fields:

```python
depends_on: list[str] = field(default_factory=list)  # node IDs this task waits for
fail_strategy: str = "block"                          # "block" | "continue"
```

- `depends_on`: list of node IDs in the same tree. Task stays PENDING until all are terminal. Dependencies can be any node in the tree (not restricted to siblings), but in practice agents will reference sibling nodes.
- `fail_strategy`: what happens when a dependency fails.
  - `"block"` (default): task is marked BLOCKED, parent employee is notified to handle it.
  - `"continue"`: failed result is injected into context, task proceeds normally.

**Serialization**: `to_dict()` must be updated to include `depends_on` and `fail_strategy`. `from_dict()` uses `__dataclass_fields__` so it auto-loads, but `to_dict()` has an explicit field list that needs extending.

### TaskTree.add_child (task_tree.py)

Update signature to accept new fields:

```python
def add_child(
    self, parent_id: str, employee_id: str, description: str,
    acceptance_criteria: list[str] | None = None,
    depends_on: list[str] | None = None,
    fail_strategy: str = "block",
) -> TaskNode:
```

### TaskPhase (task_lifecycle.py)

BLOCKED already exists. Update `VALID_TRANSITIONS` to add:

```python
TaskPhase.PENDING: [TaskPhase.PROCESSING, TaskPhase.HOLDING, TaskPhase.CANCELLED, TaskPhase.BLOCKED],
TaskPhase.BLOCKED: [TaskPhase.CANCELLED, TaskPhase.PENDING],
```

Changes:
- `PENDING → BLOCKED`: dependency failed with fail_strategy=block
- `BLOCKED → PENDING`: unblock_child resets the task after parent handles the failure

### Terminal State Definition

The existing `_TERMINAL = frozenset({"accepted", "failed", "cancelled"})` in task_tree.py does NOT include "completed". Dependency resolution fires on **accepted, failed, or cancelled** — not on "completed" (which is an intermediate state awaiting parent review). This means a dependent task only unlocks after the dependency is explicitly accepted by its parent, not just when execution finishes.

## dispatch_child Tool Changes (tree_tools.py)

```python
@tool
def dispatch_child(
    employee_id: str,
    description: str,
    acceptance_criteria: list[str],
    depends_on: list[str] = [],
    fail_strategy: str = "block",
    timeout_seconds: int = 3600,
) -> dict:
```

Behavior:
1. Validate `depends_on` node IDs exist in the same tree.
2. Validate no circular dependencies (walk the depends_on graph to detect cycles).
3. Create TaskNode via `tree.add_child()` with `depends_on` and `fail_strategy`.
4. Check if all dependencies are already terminal:
   - Yes: push_task to employee board immediately.
   - No: create node but do NOT push_task. Task stays PENDING waiting for dependencies.
5. Return node info including dependency status.

### Circular Dependency Detection

Before creating a node, walk the dependency graph: for each node in `depends_on`, check if that node (transitively) depends on any node that would create a cycle back to the new node. Since the new node doesn't exist yet, cycles can only occur if the new node's future dependents form a chain back to one of its dependencies. In practice, since nodes are created one at a time and the new node has no dependents yet, the check is: ensure none of the `depends_on` nodes themselves have a transitive depends_on path that would require the new node. This simplifies to: **no depends_on node should be a descendant (in the depends_on graph) of any node that already depends on a node in the same dispatch batch**. For single dispatches, simple validation that `depends_on` nodes exist and are not self-referencing suffices.

## Dependency Resolution (vessel.py)

When a node reaches a terminal state (`accepted`, `failed`, or `cancelled`), `_on_child_complete` triggers dependency resolution:

### Step 1: Find dependents

Scan all nodes in the same tree where `completed_node.id in node.depends_on`.

### Step 2: For each dependent node

**If dependency succeeded (accepted):**
- Record the result in a `dependency_results` context block for the dependent node.

**If dependency failed and fail_strategy="block":**
- Transition dependent node PENDING → BLOCKED.
- Notify parent employee: "Task [description] is blocked because dependency [dep_description] failed: [failure_reason]. Please reassign or modify."

**If dependency failed and fail_strategy="continue":**
- Record the failure result in the `dependency_results` context block.

**If dependency cancelled and fail_strategy="block":**
- Same as failed: PENDING → BLOCKED.

**If dependency cancelled and fail_strategy="continue":**
- Record cancellation in context, proceed.

### Step 3: Check if all dependencies resolved

For each dependent node that is still PENDING (not BLOCKED):
- If ALL `depends_on` nodes are terminal → build context injection, push_task to employee board.
- If some dependencies still pending → do nothing, wait.

### Context Injection Format

When a task is unlocked, prepend dependency results to the task description:

```
=== Dependency Results ===
[Employee Name] ([role]) — "[task description]":
[result text, last 2000 chars if exceeds limit]

[Employee Name] ([role]) — "[task description]":
[result text]
=== End Dependencies ===

[Original task description]
```

Truncation: keep the **last** 2000 chars of each result (the conclusion is more relevant than preamble). If a task has more than 3 dependencies, summarize each to 1000 chars to avoid excessive context.

### Tree Mutation Safety

The existing codebase loads tree from YAML, mutates, and saves back. With dependency resolution potentially touching multiple nodes on concurrent completions, there is a race risk. Mitigation: **use an asyncio.Lock per project_id** when performing tree mutations in `_on_child_complete`. This serializes tree updates for the same project without blocking unrelated projects.

```python
# In vessel.py, module-level
_tree_locks: dict[str, asyncio.Lock] = {}

def _get_tree_lock(project_id: str) -> asyncio.Lock:
    if project_id not in _tree_locks:
        _tree_locks[project_id] = asyncio.Lock()
    return _tree_locks[project_id]
```

## Handling BLOCKED Tasks

When a task is BLOCKED, the parent employee (whoever dispatched it) receives a notification and can:

1. **Retry the failed dependency**: `reject_child(failed_node_id, reason, retry=True)` — resets the failed dependency to PENDING, re-executes. When the retried dependency reaches a terminal state, dependency resolution re-fires and dependents are re-evaluated.
2. **Modify and unblock**: New tool `unblock_child(node_id, new_description="")` — transitions BLOCKED → PENDING, optionally updates description, then checks if remaining dependencies are met.
3. **Cancel**: New tool `cancel_child(node_id)` — cancels the blocked task. Propagates: any task that depends_on this cancelled node also triggers dependency resolution.

### unblock_child Tool (tree_tools.py)

```python
@tool
def unblock_child(node_id: str, new_description: str = "") -> dict:
    """Unblock a BLOCKED task, optionally with updated instructions.

    Args:
        node_id: The blocked task node ID.
        new_description: Updated task description (optional).

    Returns:
        Status dict with node info.
    """
```

Behavior:
1. Validate node is in BLOCKED state.
2. Remove the failed dependency from `depends_on` (it's been handled by the parent).
3. If `new_description` provided, update node description.
4. If all remaining dependencies are terminal → push_task.
5. Else → set back to PENDING, wait for remaining dependencies.

### cancel_child Tool (tree_tools.py)

```python
@tool
def cancel_child(node_id: str, reason: str = "") -> dict:
    """Cancel a task node. Propagates to dependents.

    Args:
        node_id: The task node ID to cancel.
        reason: Cancellation reason (optional).

    Returns:
        Status dict with cancelled node IDs.
    """
```

Behavior:
1. Set node status to "cancelled".
2. If node has an associated AgentTask, cancel it via EmployeeManager.
3. Trigger dependency resolution for this node (since cancelled is terminal), which may BLOCK or unlock downstream tasks.
4. Return list of affected node IDs.

## Snapshot & Restart Recovery

On startup, after `restore_task_queue`, scan all active task trees:
- For each PENDING node with non-empty `depends_on`: re-check if all dependencies are now terminal. If yes, push_task. This handles the case where a restart happened after a dependency completed but before the dependent was unlocked.
- For each BLOCKED node: re-notify the parent employee so they can act on it.

## Frontend Changes

### Task Tree Visualization (task-tree.js)

**Dependency Arrows:**
- Render as dashed lines with arrowheads, separate from solid parent-child lines.
- Direction: from dependency → dependent (arrow points to the task that waits).
- Color follows dependency node status:
  - Gray (#666): dependency pending/processing
  - Green (#00ff88): dependency accepted
  - Red (#ff4444): dependency failed/cancelled
- Layout: dependents are positioned to the right of their dependencies at the same tree depth to minimize arrow crossings.

**Node Labels:**
- PENDING nodes with unresolved dependencies show a tag below the status pill: "Waiting: [dep employee name]"
- BLOCKED nodes show a red tag: "Blocked: [failed dep description]"

**Detail Drawer:**
- New "Dependencies" section listing each depends_on node with status badge and employee name.
- New "Dependents" section (reverse lookup) listing nodes that depend on this one.

### API Response Changes (routes.py GET /api/projects/{id}/tree)

Each node in the response gains:
```json
{
  "depends_on": ["node_id_1", "node_id_2"],
  "fail_strategy": "block",
  "dependency_status": "waiting" | "resolved" | "blocked"
}
```

`dependency_status` is computed server-side:
- `"waiting"`: has unresolved dependencies (some depends_on nodes not terminal)
- `"resolved"`: all dependencies terminal and successful (or fail_strategy=continue)
- `"blocked"`: at least one dependency failed/cancelled with fail_strategy=block

## Persistence

TaskNode already persists to `task_tree.yaml`. The new fields (`depends_on`, `fail_strategy`) are added to `to_dict()` serialization. `from_dict()` handles them automatically via `__dataclass_fields__`. No new files needed.

## Testing

1. **dispatch_child with depends_on**: verify task stays PENDING when dependency not complete.
2. **Dependency resolution on accepted**: verify task unlocks when dependency is accepted (not just completed), with result injected.
3. **Failed dependency + block**: verify PENDING → BLOCKED transition, parent notified.
4. **Failed dependency + continue**: verify task proceeds with failure context.
5. **unblock_child**: verify BLOCKED → PENDING transition and re-evaluation.
6. **cancel_child**: verify cancellation propagates to dependents.
7. **Circular dependency**: verify dispatch_child rejects circular depends_on.
8. **Restart recovery**: verify PENDING tasks with resolved dependencies are pushed on startup.
9. **Tree lock**: verify concurrent completions don't corrupt tree state.
10. **to_dict/from_dict**: verify depends_on and fail_strategy survive save/load cycle.
11. **Frontend**: verify dashed arrows render, labels show, detail drawer displays dependencies.
