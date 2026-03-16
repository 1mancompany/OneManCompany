# Task Tree Optimization Design

## Problem

Task tree (`task_tree.yaml`) grows unbounded as projects run:

1. **File bloat** — `result` (full LLM output) and `description` (including embedded child results in review prompts) are serialized inline on every TaskNode, all into one YAML file.
2. **Context bloat** — employee context injection pulls full text from many tree nodes, overwhelming the LLM context window.
3. **EA/COO death spiral** — review cycles create new nodes endlessly (reject → retry → new review node → reject …), causing unbounded tree depth.
4. **No panic button** — no way to stop all employee tasks at once; only per-project and per-task cancel exist.

## Design

### 1. Task Tree Slimming — Externalize result/description

**Current:** All node data serialized into one `task_tree.yaml`.

**New layout:**

```
{project_dir}/
  task_tree.yaml              # Skeleton: id, status, parent_id, children_ids, etc.
  nodes/
    {node_id}.yaml            # Per-node: description + result full text
```

**TaskNode changes:**

- `description` and `result` become **property descriptors** backed by private `_description` / `_result` fields. The setter auto-sets `_content_dirty = True`.
- `to_dict()` no longer serializes `description` and `result`. Only `description_preview` (first 200 chars) is included in the skeleton.
- `from_dict()` does not load description/result (lazy). They remain empty strings until explicitly loaded.
- New `load_content(project_dir)` method reads `nodes/{id}.yaml` on demand. Idempotent — if already loaded (`_content_loaded = True`), returns immediately.
- New `save_content(project_dir)` method writes `nodes/{id}.yaml` only if `_content_dirty`. Resets dirty flag after write.
- Content is cached on the node object after first `load_content()` — all coroutines sharing the same in-memory TaskNode see the same data.

**`nodes/{node_id}.yaml` format:**

```yaml
description: |
  Full task description text...
result: |
  Full result text...
```

**Backward compatibility:**

- `TaskNode.from_dict()`: if the dict contains `result`/`description` (old format), load them into `_description`/`_result` normally and mark `_content_dirty = True` so next save migrates to new format.
- On next `save()`, the tree writes skeleton-only; dirty nodes get their content written to `nodes/`. Gradual migration, no batch conversion needed.
- `TaskTree.save(path)` iterates nodes and calls `save_content(path.parent)` only for dirty nodes, then writes the skeleton YAML.
- `TaskTree.load(path)` only loads skeleton. Callers that need full text call `node.load_content(path.parent)`.

**All sites that assign `node.result` or `node.description`:**

The property setter handles dirty-tracking automatically. No manual `save_content()` calls needed at these sites — the next `save_tree_async()` will flush dirty nodes.

| Site | File | What it does |
|------|------|--------------|
| `_build_dependency_context()` | vessel.py:113 | Reads `dep.result` — must call `load_content()` first |
| Review prompt builder | vessel.py:1458-1484 | Reads `child.result` / `child.description` — must call `load_content()` first |
| Task execution result | vessel.py:881 | `node.result = launch_result.output` — setter marks dirty |
| CancelledError handler | vessel.py:898 | `node.result = "Cancelled by CEO"` — setter marks dirty |
| TimeoutError handler | vessel.py:905 | `node.result = str(te)` — setter marks dirty |
| Generic error handler | vessel.py:912 | `node.result = f"Error: {e!s}"` — setter marks dirty |
| Holding resume path | vessel.py:1037 | `node.result = result` — setter marks dirty |
| Auto-complete parent | vessel.py:1442 | `parent_node.result = "All child tasks accepted."` — setter marks dirty |
| TreeManager event | tree_manager.py:116 | `node.result = event.data.get(...)` — setter marks dirty |
| `reject_child` reset | tree_tools.py:349-353 | `node.result = ""`, `node.description = ...` — setter marks dirty |
| `cancel_child` | tree_tools.py:468 | `node.result = reason or "Cancelled by parent"` — setter marks dirty |
| Project abort | routes.py:1643 | `node.result = "Cancelled by CEO (project aborted)"` — setter marks dirty |
| Cron cancel | automation.py:170 | `node.result = "Cancelled: cron stopped"` — setter marks dirty |
| CEO inbox scan | routes.py:5175 | Use `description_preview` from skeleton — no `load_content()` needed |

### 2. Employee Context Windowing

**Current:** Context injection pulls full result/description from many nodes indiscriminately.

**New rule — distance-based truncation:**

| Distance from current node | Content injected |
|----------------------------|------------------|
| Current node | Full description + result (via `load_content`) |
| Parent | Full description + result (via `load_content`) |
| Grandparent and above | `id + status + description_preview` (from skeleton) |
| Children needing review | Full result (via `load_content`) |
| Already-accepted children | `id + status + description_preview[:100]` |
| `depends_on` nodes | Full result (existing behavior, keep) |

**New function: `_build_tree_context(tree, node, project_dir) -> str`**

- New functionality (not replacing existing scattered code — current context is built in a single block at vessel.py:796-820, but does not walk the tree).
- Walks up 2 levels with full text (calls `load_content()`), then skeleton only for higher ancestors.
- Walks down to children with review-aware truncation.
- Injected alongside existing context (project identity, workspace, history, etc.).

**New tool: `read_node_detail(node_id: str) -> str`**

- Registered as a common tool for all employees (alongside `list_colleagues`, `pull_meeting`).
- Implementation: derives `project_dir` from current task context (via `_current_vessel` / `_current_task_id` → look up `tree_path` from `employee_manager._schedule`), then loads `nodes/{node_id}.yaml`.
- Returns formatted description + result + acceptance_criteria + status.
- Allows employees to inspect any ancestor/sibling node on demand when the skeleton context isn't enough.
- Added to `common_tools.py`.

### 3. Tree Growth Circuit Breaker (General)

**Current:** No limits on tree growth. Review cycles, dispatch loops, and deep nesting can all cause unbounded expansion.

**New: three-layer protection, all with CEO escalation.**

#### 3a. Review round limit

**Problem:** Review cycles (reject → retry → new review node → reject …) create unlimited nodes.

**Counting mechanism:**

- Introduce `node_type = "review"` to explicitly tag review nodes (replacing fragile string-prefix detection of `"以下子任务"`).
- In `_full_cleanup` (vessel.py), before creating a review node, count children with `node_type == "review"` under the same parent.

**Threshold behavior (`max_review_rounds`, default 3):**

- Rounds ≤ threshold: create review node as normal.
- Rounds > threshold:
  1. Set parent node to `holding` status.
  2. Create a `ceo_request` node (`dispatch_child("00001", ...)`).
  3. Message: summary of deadlock — task description, round count, last disagreement (last child's `acceptance_result.notes`).
  4. CEO sees it in inbox, intervenes via conversation.

**CEO intervention flow after escalation:**

- CEO opens the inbox item, reads the deadlock summary.
- CEO replies via conversation (e.g., "accept as-is" / "cancel this task" / specific guidance).
- The conversation result is written to the `ceo_request` node's result.
- Parent node transitions from `holding` back to `processing` and receives the CEO's guidance as context for the next action.

#### 3b. Children count limit per parent

**Problem:** An employee can `dispatch_child()` endlessly, creating unlimited siblings.

**Mechanism:**

- In `dispatch_child()` (tree_tools.py), before creating a child, count active children under the parent.
- Threshold: `max_children_per_node`, default 10.
- Exceeded: return error to the agent: "已达子任务上限 ({max})，请整合现有任务或向上汇报。" Agent must consolidate or escalate.

#### 3c. Tree depth limit

**Problem:** Nested delegation (A dispatches to B, B dispatches to C, C dispatches to D…) creates unbounded depth.

**Mechanism:**

- In `dispatch_child()` (tree_tools.py), walk up from current node to root, counting depth.
- Threshold: `max_tree_depth`, default 6.
- Exceeded: return error to the agent: "任务树已达最大深度 ({max})，无法继续下派，请直接完成或向上汇报。"

#### Escalation target

All escalations go directly to CEO (`employee_id = "00001"`). Company hierarchy is shallow (CEO → EA/COO → employees, max 3 layers), intermediate escalation adds no value.

#### Configuration

All thresholds in `config.py` task configuration section:

```python
max_review_rounds: int = 3
max_children_per_node: int = 10
max_tree_depth: int = 6
```

### 4. Three-Granularity Resource Recovery

**Current:** Only `abort_project(project_id)` and single-task cancel.

**New: three levels sharing the same underlying cancel mechanism.**

| Granularity | Method | API Endpoint |
|-------------|--------|--------------|
| Per-project | `abort_project(project_id)` | `POST /api/task/{project_id}/abort` (existing, hardened) |
| Per-employee | `abort_employee(employee_id)` | `POST /api/employee/{employee_id}/abort` |
| Global | `abort_all()` | `POST /api/abort-all` |

#### `abort_employee(employee_id)`

1. Clear `_schedule[employee_id]` (all queued tasks).
2. Clear `_deferred_schedule` for this employee.
3. Cancel `_running_tasks[employee_id]` (`asyncio.Task.cancel()`).
4. Traverse all tree files where this employee has **non-terminal** nodes (`pending`, `processing`, `holding`) → force `CANCELLED`. Do NOT touch `completed`/`accepted`/`finished` nodes — they may be part of active projects with valid dependency graphs.
5. `stop_all_crons_for_employee(employee_id)`.
6. Reset employee status to `IDLE`.
7. Broadcast state snapshot.

#### `abort_all()`

1. Iterate all registered employees → call `abort_employee()` for each.
2. `stop_all_automations()` — ensure all crons stopped.
3. `stop_all_daemons()` — kill all Claude CLI subprocesses.
4. Broadcast state snapshot.

**Semantics:** "Stop all work, company stays running." Server, WebSocket, frontend all remain up. Employees go IDLE. CEO can re-dispatch tasks immediately. Claude CLI daemons will auto-restart on next task dispatch (via `_get_or_start_daemon`).

#### Harden existing `abort_project()`

- Additionally stop associated crons (`reply_{task_id}`, `holding_{task_id}`) for each cancelled node.

#### Frontend

- Add a prominent "Stop All" button in the management panel.
- Requires confirmation dialog: "确定要停止所有员工的所有任务吗？"
- Calls `POST /api/abort-all`.

## Files to Modify

| File | Changes |
|------|---------|
| `core/task_tree.py` | TaskNode: property descriptors for `description`/`result`, `_content_dirty`/`_content_loaded` flags, `description_preview`, `save_content()`, `load_content()`. Add `node_type = "review"`. Modify `to_dict()`/`from_dict()`. `TaskTree.save()` flushes dirty node files. |
| `core/vessel.py` | New `_build_tree_context()`. Update `_full_cleanup` for review circuit breaker. New `abort_employee()`, `abort_all()`. Add `load_content()` calls where `node.result`/`node.description` are read. |
| `core/tree_manager.py` | Update `_save()` and event handlers to work with externalized content. |
| `core/config.py` | Add `max_review_rounds`, `max_children_per_node`, `max_tree_depth` config. |
| `agents/common_tools.py` | Add `read_node_detail()` tool. |
| `agents/tree_tools.py` | Add depth/children count checks in `dispatch_child()`. |
| `api/routes.py` | New endpoints: `POST /api/employee/{id}/abort`, `POST /api/abort-all`. Update inbox scan to use `description_preview`. |
| `frontend/app.js` | Add "Stop All" button + confirmation dialog. Per-employee abort button in employee detail panel. |
| `frontend/index.html` | Add button elements. |

## Testing

| Area | Test file | Cases |
|------|-----------|-------|
| Content externalization | `test_task_tree.py` | save/load roundtrip, backward compat migration (old format → new), dirty tracking, `load_content` idempotency |
| Context windowing | `test_task_tree.py` | `_build_tree_context` truncation at each distance level |
| Circuit breaker — review | `test_vessel.py` (new) | Count review rounds, escalation trigger, CEO request node creation |
| Circuit breaker — children | `test_tree_tools.py` | `dispatch_child` blocked at max children |
| Circuit breaker — depth | `test_tree_tools.py` | `dispatch_child` blocked at max depth |
| `abort_employee` | `test_vessel.py` (new) | Only non-terminal nodes cancelled, crons stopped, status reset |
| `abort_all` | `test_vessel.py` (new) | All employees aborted, daemons stopped |
| `read_node_detail` | `test_common_tools.py` | Loads content, handles missing node |

## Non-Goals

- No batch migration script for existing tree files (gradual migration on save is sufficient).
- No changes to conversation storage (`conversations/{node_id}.yaml` already separated).
- No changes to the task lifecycle state machine itself.
- Server shutdown behavior unchanged.
- Snapshot/restore system (`core/snapshot.py`) — may need future update if snapshot captures tree content, but not in scope for this change.
