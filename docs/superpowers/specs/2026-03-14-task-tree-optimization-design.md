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

- `to_dict()` no longer serializes `description` and `result`. These are written to `nodes/{id}.yaml` via a new `save_content(project_dir)` method.
- `from_dict()` does not load description/result (lazy). They remain empty strings until explicitly loaded.
- New `load_content(project_dir)` method reads `nodes/{id}.yaml` on demand.
- Tree skeleton retains `description_preview: str` (first 200 chars) for inbox listings, logs, and other places that don't need full text.

**`nodes/{node_id}.yaml` format:**

```yaml
description: |
  Full task description text...
result: |
  Full result text...
```

**Backward compatibility:**

- `TaskNode.from_dict()`: if the dict contains `result`/`description` (old format), load them normally.
- On next `save()`, the tree writes skeleton-only; `save_content()` writes the node file. Gradual migration, no batch conversion needed.
- `TaskTree.save(path)` calls `node.save_content(path.parent)` for every node before writing the skeleton.
- `TaskTree.load(path)` only loads skeleton. Callers that need full text call `node.load_content(path.parent)`.

**Key callers that need updating:**

| Caller | File | Action |
|--------|------|--------|
| `_build_dependency_context()` | vessel.py:113 | Call `load_content()` before reading `dep.result` |
| Review prompt builder | vessel.py:1458-1484 | Call `load_content()` for children needing review |
| `_post_task_cleanup` result assignment | vessel.py:881 | Call `save_content()` after setting `node.result` |
| `tree_manager.py` event handling | tree_manager.py:116 | Call `save_content()` when result is updated |
| CEO inbox scan | routes.py:5175 | Use `description_preview` instead of full description |
| Task cancel/abort | routes.py, vessel.py | No change needed (only touches status, not content) |

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

- Replaces scattered context assembly across vessel.py.
- Walks up 2 levels with full text, then skeleton only.
- Walks down to children with review-aware truncation.
- Single source of truth for tree context injection.

**New tool: `read_node_detail(node_id: str) -> str`**

- Registered as a common tool for all employees (alongside `list_colleagues`, `pull_meeting`).
- Implementation: loads `nodes/{node_id}.yaml` and returns formatted description + result + acceptance_criteria + status.
- Allows employees to inspect any ancestor/sibling node on demand when the skeleton context isn't enough.
- Added to `common_tools.py`.

### 3. EA/COO Review Circuit Breaker

**Current:** Each review cycle creates a new review node under the parent. No limit on rounds.

**New: max review rounds with CEO escalation.**

**Counting mechanism:**

In `_post_task_cleanup` (vessel.py), before creating a review node, count existing review-type children under the same parent for the same employee. A "review round" = a completed task-type child of the parent with the same employee_id that was a review.

**Threshold behavior (configurable `max_review_rounds`, default 3):**

- Rounds ≤ threshold: create review node as normal.
- Rounds > threshold:
  1. Set parent node to `holding` status.
  2. Call `dispatch_child("00001", ...)` to create a `ceo_request` node.
  3. Message content: summary of the deadlock — which task, how many rounds, last round's disagreement point (last child's result + acceptance_result.notes).
  4. CEO sees it in inbox, intervenes.

**Configuration:**

- `max_review_rounds: int = 3` in `config.py` task configuration section.
- Not hardcoded in vessel.py logic.

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
2. Cancel `_running_tasks[employee_id]` (`asyncio.Task.cancel()`).
3. Traverse all tree files where this employee has nodes → force `CANCELLED`.
4. `stop_all_crons_for_employee(employee_id)`.
5. Reset employee status to `IDLE`.
6. Broadcast state snapshot.

#### `abort_all()`

1. Iterate all registered employees → call `abort_employee()` for each.
2. `stop_all_automations()` — ensure all crons stopped.
3. `stop_all_daemons()` — kill all Claude CLI subprocesses.
4. Broadcast state snapshot.

**Semantics:** "Stop all work, company stays running." Server, WebSocket, frontend all remain up. Employees go IDLE. CEO can re-dispatch tasks immediately.

#### Harden existing `abort_project()`

- Additionally stop associated crons (`reply_{task_id}`, `holding_{task_id}`) for each cancelled node.

#### Frontend

- Add a prominent "Stop All" button in the management panel.
- Requires confirmation dialog: "确定要停止所有员工的所有任务吗？"
- Calls `POST /api/abort-all`.

## Files to Modify

| File | Changes |
|------|---------|
| `core/task_tree.py` | TaskNode: add `description_preview`, `save_content()`, `load_content()`. Modify `to_dict()`/`from_dict()`. TaskTree.save() writes node files. |
| `core/vessel.py` | New `_build_tree_context()`. Update `_post_task_cleanup` for circuit breaker. New `abort_employee()`, `abort_all()`. Update callers to use `load_content()`. |
| `core/config.py` | Add `max_review_rounds` config. |
| `agents/common_tools.py` | Add `read_node_detail()` tool. |
| `api/routes.py` | New endpoints: `POST /api/employee/{id}/abort`, `POST /api/abort-all`. Update inbox scan to use `description_preview`. |
| `frontend/app.js` | Add "Stop All" button + confirmation dialog. |
| `frontend/index.html` | Add button element in management panel. |
| `tests/unit/core/test_task_tree.py` | Tests for externalized content, save/load roundtrip, backward compat migration. |

## Non-Goals

- No batch migration script for existing tree files (gradual migration on save is sufficient).
- No changes to conversation storage (`conversations/{node_id}.yaml` already separated).
- No changes to the task lifecycle state machine itself.
- Server shutdown behavior unchanged.
