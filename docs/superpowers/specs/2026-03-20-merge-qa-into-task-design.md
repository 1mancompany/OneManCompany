# Merge Q&A into Task Flow with Simple Mode

## Problem

Q&A mode (`/api/ceo/qa`) is a bare LLM call — no EA agent, no tools, no task tree, no project. This means:
- Q&A answers can't trigger real work (dispatch tasks, use tools)
- No audit trail for Q&A interactions
- Two separate code paths for CEO input

## Design

### TaskTree `mode` Field

Add `mode: Literal["simple", "standard"]` to the TaskTree data model, defaulting to `"standard"`.

- **standard**: Current behavior — EA restricted to O-level dispatch, retrospective on completion
- **simple**: EA can dispatch to any employee directly, no retrospective on completion

The mode is set at project creation time and persisted to disk with the tree.

### Endpoint Changes

**Delete** `POST /api/ceo/qa` — remove entirely.

**Modify** `POST /api/ceo/task`:
- Accept optional `mode` field in request body (default: `"standard"`)
- Pass mode through to `TaskTree` initialization
- All other logic unchanged — project creation, EA dispatch, etc.

### EA Dispatch Restriction

**File**: `src/onemancompany/agents/tree_tools.py`

Current logic in `dispatch_child`:
```python
if current_node.employee_id == EA_ID:
    if target_id not in {HR_ID, COO_ID, CSO_ID}:
        return {"status": "error", "message": "EA can only dispatch to O-level"}
```

Change to:
```python
if current_node.employee_id == EA_ID and tree.mode == "standard":
    if target_id not in {HR_ID, COO_ID, CSO_ID}:
        return {"status": "error", "message": "EA can only dispatch to O-level"}
```

When `mode == "simple"`, the check is skipped entirely.

### Skip Retrospective

**File**: `src/onemancompany/core/vessel.py`

In `_full_cleanup()`, read the tree's mode. If `mode == "simple"`, pass `run_retrospective=False`.

### Frontend

- Remove Q&A input/button — CEO console has only one input that submits tasks
- Add a toggle or similar control for CEO to choose simple vs standard mode before submitting
- Task progress display unchanged — simple mode tasks appear in the same console feed

### What Stays the Same

- EA system prompt unchanged (routing table remains as guidance, code enforces)
- Task lifecycle state machine unchanged
- Project creation, task tree persistence, all audit trails — same for both modes
- Standard mode behavior is 100% unchanged

## Files to Modify

| File | Change |
|------|--------|
| `src/onemancompany/core/task_lifecycle.py` | Add `mode` field to TaskTree |
| `src/onemancompany/agents/tree_tools.py` | Conditional O-level restriction based on tree mode |
| `src/onemancompany/core/vessel.py` | Skip retrospective when mode=simple |
| `src/onemancompany/api/routes.py` | Remove `ceo_qa`, add `mode` param to `ceo_submit_task` |
| `frontend/app.js` | Remove Q&A UI, add mode toggle to task submit |
| `frontend/index.html` | Remove Q&A related DOM elements |
| Tests | New tests for mode field, conditional dispatch, skip retro |

## Out of Scope

- Changing EA system prompt
- Changing task lifecycle states
- Any changes to standard mode behavior
