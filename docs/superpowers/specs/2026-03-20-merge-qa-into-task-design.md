# Merge Q&A into Task Flow with Simple Mode

## Problem

Q&A mode (`/api/ceo/qa`) is a bare LLM call — no EA agent, no tools, no task tree, no project. This means:
- Q&A answers can't trigger real work (dispatch tasks, use tools)
- No audit trail for Q&A interactions
- Two separate code paths for CEO input

## Design

### TaskTree `mode` Field

Add `mode: Literal["simple", "standard"]` to the TaskTree data model (in `task_tree.py`), defaulting to `"standard"`.

- **standard**: Current behavior — EA restricted to O-level dispatch, retrospective on completion
- **simple**: EA can dispatch to any employee directly, no retrospective on completion

The mode is set at project creation time and persisted to disk with the tree.

**Serialization**: `TaskTree.save()` and `TaskTree.load()` must include `mode`. Existing persisted trees without `mode` default to `"standard"` for backward compatibility.

### Mode Threading: HTTP Request → TaskTree

The `mode` value flows through the system as follows:

1. `POST /api/ceo/task` receives `mode` in request body
2. `ceo_submit_task` passes `mode` directly to `TaskTree(project_id=ctx_id, mode=mode)`
3. TaskTree stores `mode` as instance field, persisted to `task_tree.yaml` via `save()`

Note: `mode` is NOT stored in `project.yaml` — the TaskTree is constructed in the same function that reads the request, so no intermediate metadata step is needed.

### Endpoint Changes

**Delete** `POST /api/ceo/qa` — remove entirely.

**Delete** `EventType.CEO_QA` from `models.py` — dead code after endpoint removal.

**Modify** `POST /api/ceo/task`:
- Accept optional `mode` field in request body (default: `"standard"`)
- Pass mode through to project metadata and TaskTree initialization
- All other logic unchanged — project creation, EA dispatch, etc.

### EA Dispatch Restriction

**File**: `src/onemancompany/agents/tree_tools.py`

Current logic in `dispatch_child` checks EA dispatches against an `allowed_targets` set of O-level IDs. Change to: skip this check when `tree.mode == "simple"`. The tree object is already loaded at this point.

Add a self-dispatch guard: EA cannot dispatch to itself regardless of mode.

### Skip Retrospective

**File**: `src/onemancompany/core/vessel.py`

The retrospective flag is set at the **call site** in `_request_ceo_confirmation` (not inside `_full_cleanup` itself). Change:

```python
# Before
run_retrospective = not is_system_node
# After
run_retrospective = not is_system_node and tree.mode != "simple"
```

### Frontend

- Remove Q&A input/button and `<option value="__qa__">` — CEO console has only one input that submits tasks
- Add a toggle or similar control for CEO to choose simple vs standard mode before submitting
- Remove `qa` category label from cost dashboard `catLabels`
- Task progress display unchanged — simple mode tasks appear in the same console feed

### Performance Tradeoff

Simple mode queries that were previously bare LLM calls (~1s) will now create full projects with disk persistence and EA agent invocation. This is intentionally accepted — the benefit of unified audit trail, tool access, and real task dispatch outweighs the latency increase.

### What Stays the Same

- EA system prompt unchanged (routing table remains as guidance, code enforces)
- Task lifecycle state machine unchanged
- Project creation, task tree persistence, all audit trails — same for both modes
- Standard mode behavior is 100% unchanged

## Files to Modify

| File | Change |
|------|--------|
| `src/onemancompany/core/task_tree.py` | Add `mode` field to TaskTree, update `save()`/`load()` |
| `src/onemancompany/agents/tree_tools.py` | Conditional O-level restriction based on tree mode; self-dispatch guard |
| `src/onemancompany/core/vessel.py` | Skip retrospective when mode=simple (at `_request_ceo_confirmation` call site) |
| `src/onemancompany/api/routes.py` | Remove `ceo_qa`, add `mode` param to `ceo_submit_task`, thread to project.yaml |
| `src/onemancompany/core/models.py` | Remove `EventType.CEO_QA` |
| `frontend/app.js` | Remove Q&A UI, add mode toggle, remove `qa` cost label |
| `frontend/index.html` | Remove Q&A related DOM elements |
| Tests | New tests for mode field, conditional dispatch, skip retro, self-dispatch guard |

## Out of Scope

- Changing EA system prompt
- Changing task lifecycle states
- Any changes to standard mode behavior
