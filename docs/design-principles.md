# OneManCompany Design Principles

> The load-bearing walls of this codebase. Violate them and things break in subtle, hard-to-debug ways.

## 1. Single Source of Truth — Disk Is the Only Truth

Every piece of data has exactly **one file** that owns it and exactly **one write function** (`store.save_*()`). Memory holds only intermediate computation products — never cached copies of business data.

- Reads always go to disk (`store.load_*()`)
- Frontend is a pure render layer — fetches from REST API, no local state cache
- Backend-frontend sync runs on a 3-second tick: dirty categories → `state_changed` broadcast → frontend re-fetches

**Test**: Can you restart the server and lose nothing? If yes, you're doing it right.

## 2. Systematic Design, Not Patching

Every change must be a **systematic design**, never a patch. If a bug reveals a structural flaw, fix the structure. If a feature doesn't fit the current architecture, evolve the architecture — don't duct-tape around it.

**Bad**: Adding `if employee_id == "00003": ...` to handle a special case.
**Good**: Extracting a protocol/registry that handles all cases uniformly.

**Test**: Would a second similar request require touching the same code? If yes, you're patching, not designing.

## 3. Modular, General-Purpose, Common Design

Extract harnesses and protocols. Never hardcode case-by-case.

```python
# Bad: case-by-case detection
def _find_pending_ceo_children(tree, node_id):
    return [c for c in children if c.node_type == "ceo_request" and ...]

# Good: generic field that any tool can set
class TaskNode:
    hold_reason: str = ""  # tools set this to request HOLDING
```

New capabilities should be **addable without modifying existing code** — just register a new entry, set a new field, implement a new handler.

**Test**: Can a new use case be added by only writing new code (not editing existing code)? If yes, the design is modular.

## 4. Complete Data Packages

Any new state or work item must be designed as a **complete data package**:

| Property | Requirement | How to verify |
|---|---|---|
| **Serializable** | Can be persisted to disk (YAML/JSON) | `to_dict()` / `from_dict()` round-trip test |
| **Recoverable** | Survives server restart | Kill the server mid-operation → restart → state intact? |
| **Registered** | Tracked in both company state and the owning employee | `store.load_*()` can find it? |
| **Terminable** | Has a clear lifecycle, will not be stuck forever | What transitions lead to a terminal state? |

**Example**: `hold_reason` on TaskNode — serialized to YAML, recovered via `_parse_holding_metadata` on restart, registered on the node itself, consumed by vessel.py's HOLDING flow.

**Counter-example**: An in-memory flag that's lost on restart → violates "Recoverable".

## 5. No Silent Exceptions

Never write `except Exception: pass`. Always log errors. Always re-raise `asyncio.CancelledError`.

```python
# Bad
try:
    await do_work()
except Exception:
    pass

# Good
try:
    await do_work()
except asyncio.CancelledError:
    raise
except Exception:
    logger.exception("do_work failed")
```

**Test**: Grep for `except.*pass`. If you find any, it's a bug.

## 6. Registry/Dispatch Over If-Elif

If you're writing `if/elif/else` to dispatch by type, you're doing it wrong. Use a dict or registry.

```python
# Bad
if tool_type == "gmail":
    render_gmail_ui()
elif tool_type == "roblox":
    render_roblox_ui()

# Good
_renderers = {"gmail": render_gmail_ui, "roblox": render_roblox_ui}
_renderers[tool_type]()
```

**Where it's used**: snapshot providers, plugin registry, tool permissions, UI section renderers, event handlers.

## 7. Status Changes Through transition()

All task status changes MUST go through `transition()` (or `set_status()` which calls it). Direct assignment to `node.status = "completed"` is banned — it bypasses validation and lifecycle hooks.

```python
# Bad
node.status = TaskPhase.COMPLETED.value

# Good
node.set_status(TaskPhase.COMPLETED)
```

## 8. Minimal Complexity

The right amount of complexity is the **minimum** needed for the current task. Three similar lines are better than a premature abstraction.

- Don't add features, refactor, or "improve" beyond what was asked
- Don't add error handling for scenarios that can't happen
- Don't create helpers for one-time operations
- Don't design for hypothetical future requirements

**Test**: Remove the abstraction and inline everything. Is the code simpler and still correct? If yes, the abstraction was premature.

---

## Applying These Principles: The PR Review Checklist

Every PR must be reviewed against these principles before merge. See [PR Review Checklist](../vibe-coding-guide.md#pr-review-checklist) in the Vibe Coding Guide.
