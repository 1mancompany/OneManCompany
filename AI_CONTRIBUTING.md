# OneManCompany Coding Guide

> For AI coders and human contributors alike. Read this before writing any code.

## Table of Contents

- [Design Philosophy](#design-philosophy)
- [Architecture Patterns](#architecture-patterns)
- [Code Style](#code-style)
- [Testing](#testing)
- [Code Smells & How to Eliminate Them](#code-smells--how-to-eliminate-them)
- [Development Guides](#development-guides)

---

## Design Philosophy

### 1. Systematic Design, Not Patching

Every change must be a **systematic design**, never a patch. If a bug reveals a structural flaw, fix the structure. If a feature request doesn't fit the current architecture, evolve the architecture — don't duct-tape around it.

**Bad:** Adding `if employee_id == "00003": ...` to handle a special case.
**Good:** Extracting a protocol/registry that handles all cases uniformly.

### 2. Modular, General-Purpose, Common Design

Extract harnesses and protocols. Never hardcode case-by-case.

```python
# Bad: case-by-case in main.py
if tool_type == "gmail":
    render_gmail_ui()
elif tool_type == "roblox":
    render_roblox_ui()

# Good: registry-based, data-driven
_toolSectionRenderers = {
    "oauth": render_oauth_section,
    "env_vars": render_env_section,
}
for section in tool.sections:
    renderers[section.type](section)
```

### 3. Complete Data Packages

Any new state or work item must be designed as a **complete data package**:

- **Serializable** — can be persisted to disk (YAML/JSON)
- **Recoverable** — can be restored after a server restart
- **Registered** — tracked in both company state and the owning employee
- **Terminable** — has a clear lifecycle, will not be stuck forever

### 4. No Silent Exceptions

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

### 5. Single Source of Truth — Disk Is the Only Truth

All business data lives in `.onemancompany/` disk files. Writes go to disk immediately via `core/store.py`. Memory holds only intermediate computation products (layout, counters) — never cached copies of business data.

**Rules:**
- Every piece of data has exactly **one file** that owns it and exactly **one write function** (`store.save_*()`)
- Reads always go to disk (`store.load_*()`) — no in-memory caching of business data
- Frontend is a pure render layer — no `this.state` or cached copies; fetches from REST API on demand
- Frontend-backend sync runs on a 3-second tick: backend accumulates dirty categories, broadcasts `state_changed`, frontend re-fetches
- Real-time chat messages are the exception — pushed immediately via WebSocket for low-latency UX

```python
# Bad: in-memory cache that can diverge from disk
company_state.employees[emp_id].status = "working"

# Good: write to disk immediately, mark dirty for next tick
await store.save_employee_runtime(emp_id, status="working")
```

### 6. Minimal Complexity

Don't over-engineer. The right amount of complexity is the **minimum** needed for the current task. Three similar lines are better than a premature abstraction.

- Don't add features, refactor, or "improve" beyond what was asked
- Don't add error handling for scenarios that can't happen
- Don't create helpers for one-time operations
- Don't design for hypothetical future requirements

---

## Architecture Patterns

### Registry Pattern

The dominant pattern in this codebase. Used for snapshot providers, plugins, tools, event handlers, and UI section renderers.

```python
# Decorator-based registry (see core/snapshot.py)
_providers: dict[str, type] = {}

def snapshot_provider(name: str):
    def decorator(cls):
        _providers[name] = cls
        return cls
    return decorator

@snapshot_provider("my_module")
class MySnapshot:
    @staticmethod
    def save() -> dict: ...
    @staticmethod
    def restore(data: dict) -> None: ...
```

**Where it's used:**
- `core/snapshot.py` — `@snapshot_provider` for state persistence
- `core/plugin_registry.py` — plugin discovery from directories
- `agents/common_tools.py` — `BASE_TOOLS`, `GATED_TOOLS`, `COMMON_TOOLS` lists
- `frontend/app.js` — `_toolSectionRenderers` for dynamic UI

### Event Bus

Async pub-sub for decoupled communication between backend modules and frontend.

```python
from onemancompany.core.events import event_bus, CompanyEvent

# Publish
await event_bus.publish(CompanyEvent(
    type="ceo_report",
    payload={"subject": "...", "report": "..."},
    agent="SYSTEM",
))

# Subscribe (WebSocket handler)
queue = event_bus.subscribe()
while True:
    event = await queue.get()
    await ws.send_json(event.payload)
```

### Manifest-Driven UI

UI sections are **declared in data files**, not hardcoded in templates. The backend resolves runtime state, the frontend renders by type.

```
tool.yaml (declares) → backend (resolves state) → sections[] → frontend (renders by type)
```

To add a new section type:
1. Add the key to `tool.yaml`
2. Add section builder in `routes.py:get_tool_definition()`
3. Add renderer in `app.js:_toolSectionRenderers`

### Launcher Protocol

Employee task execution uses a launcher abstraction:

| Launcher | Hosting | How it works |
| --- | --- | --- |
| `LangChainLauncher` | Company-hosted | `create_react_agent` with LangChain tools |
| `ClaudeSessionLauncher` | Self-hosted | `claude --print` CLI with MCP bridge |
| `ScriptLauncher` | Script | Runs a bash script |

### Three-Tier Tool Permissions

```
BASE_TOOLS      — always available (read, ls, write, edit, list_colleagues, ...)
GATED_TOOLS     — need explicit tool_permissions (bash, use_tool, ...)
COMMON_TOOLS    — founding employees only (all tools + admin tools)
```

### Hot Reload Tiers

| Tier | Trigger | Action |
| --- | --- | --- |
| 1 | `company/` data files changed | Instant state reload |
| 1.5 | `frontend/` files changed | Browser reload notification |
| 2 | `src/` Python files changed | Graceful restart (snapshot → `os.execv`) |

---

## Code Style

### Python

```python
# Imports: stdlib → third-party → local, separated by blank lines
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger

from onemancompany.core.config import EMPLOYEES_DIR
from onemancompany.core.state import company_state
```

- **Lazy imports** inside functions for heavy or circular dependencies
- **Type hints** on function signatures, not on every local variable
- **Dataclasses** for structured data, not dicts
- **f-strings** for formatting, never `.format()` or `%`
- **`loguru.logger`** for logging, not `print()` or stdlib `logging`
- **No SDK dependencies in tools** when possible — prefer `urllib.request` for HTTP

### JavaScript (Frontend)

- **Vanilla JS** — no frameworks, no build step
- **Canvas 2D** for the pixel art office
- **Class-based** controller (`AppController`) with method namespacing
- **Event-driven** via WebSocket messages
- **Pixel-consistent** styling: 7px font, monospace, CSS variables for theming
- **`_escapeHtml()`** for all user-provided content in innerHTML

### YAML

- Configuration and data files use YAML, not JSON
- Employee profiles, tool manifests, workflow definitions — all YAML
- Keep YAML flat where possible, nested only when structurally necessary

### Naming

```python
# Python
employee_id = "00002"          # snake_case for variables
def _private_helper():         # underscore prefix for private
class EmployeeManager:         # PascalCase for classes
EMPLOYEES_DIR = Path(...)      # UPPER_SNAKE for constants
```

```javascript
// JavaScript
viewingEmployeeId              // camelCase for variables
_showCeoReport()               // underscore prefix for private methods
_toolSectionRenderers          // registry objects
```

```yaml
# YAML keys
employee_id: "00002"           # snake_case
allowed_users: []              # snake_case
```

---

## Testing

### TDD Workflow

**Write tests first, then implement.** This is a hard requirement.

```bash
# 1. Write the test
# tests/unit/test_new_feature.py

# 2. Run it — should fail
.venv/bin/python -m pytest tests/unit/test_new_feature.py -x

# 3. Implement the feature

# 4. Run it — should pass
.venv/bin/python -m pytest tests/unit/test_new_feature.py -x

# 5. Verify no regressions
.venv/bin/python -m pytest tests/ -x
```

### Test Structure

```
tests/
  unit/           — Fast (<1s), no external deps, no network
  integration/    — Mock LLM, <30s
  e2e/            — Running server, <120s
  conftest.py     — Shared fixtures
```

### Key Rules

- **Mock at the importing module level**, not the source module:

```python
# Bad: patches where the function is defined
@patch("onemancompany.core.config.load_employee")

# Good: patches where the function is imported
@patch("onemancompany.agents.base.load_employee")
```

- **WebSocket tests**: Don't use Starlette `TestClient` (the `while True` loop hangs). Mock the WebSocket object directly and call async functions.

- **Employee IDs**: Avoid `00002`–`00005` in tests (these are founding executive IDs). Use `00100+` for test employees.

- **Disk isolation**: Unit tests **must never write to the real `.onemancompany/` directory**. The `tests/unit/conftest.py` provides autouse fixtures that redirect disk writes to `tmp_path`. When writing new tests:
  - `persist_task` and `_append_progress` are auto-redirected via `vessel.EMPLOYEES_DIR` and `tp.EMPLOYEES_DIR` patches
  - `store.save_employee()`, `store.save_employee_runtime()`, `store.append_activity()` are auto-intercepted by the bridge fixture — they only write to disk when the test explicitly patches `store.EMPLOYEES_DIR` to `tmp_path`
  - If your test needs disk writes, **explicitly `monkeypatch.setattr(store, "EMPLOYEES_DIR", tmp_path)`** — this signals the bridge to allow writes to the controlled tmp directory
  - If your test does NOT set up in-memory `company_state.employees` and does NOT redirect `store.EMPLOYEES_DIR`, store write calls become no-ops (preventing leaks)

- **Compilation check**: Always verify after editing:

```bash
.venv/bin/python -c "from onemancompany.api.routes import router; print('OK')"
```

### Async Tests

```python
import pytest

@pytest.mark.asyncio
async def test_something():
    result = await some_async_function()
    assert result["status"] == "ok"
```

---

## Code Smells & How to Eliminate Them

### 1. Hardcoded Branching

**Smell:** `if type == "X": ... elif type == "Y": ...`
**Fix:** Registry/dict dispatch. Map types to handlers.

### 2. God Function

**Smell:** A function doing 5+ unrelated things, 100+ lines.
**Fix:** Extract into named sub-functions. Each function does one thing.

### 3. Stringly-Typed Data

**Smell:** Passing dicts with magic string keys everywhere.
**Fix:** Use `@dataclass` with typed fields. The compiler catches typos.

### 4. Duplicate I/O Patterns

**Smell:** Reading/writing YAML in 10 different places with 10 different error handling patterns.
**Fix:** One `_load_yaml(path)` / `_save_yaml(path, data)` helper.

### 5. Silent Exception Swallowing

**Smell:** `except Exception: pass`
**Fix:** Always `logger.exception(...)`. Re-raise `CancelledError`.

### 6. Backward-Compat Ghosts

**Smell:** `_old_var = new_var  # backward compat` lingering for months.
**Fix:** Delete it. If nothing breaks, it wasn't needed. If something breaks, fix the caller.

### 7. Missing Lifecycle

**Smell:** A state/task that can be created but never completed or cleaned up.
**Fix:** Design the full lifecycle: create → active → complete/fail → cleanup. Add timeout/expiry.

### 8. Implicit Coupling

**Smell:** Module A reads Module B's internal `_private_dict` directly.
**Fix:** Module B exposes a public API. Module A calls it.

### 9. Test-After (not TDD)

**Smell:** Writing implementation first, tests later (or never).
**Fix:** Write the test. Watch it fail. Implement. Watch it pass.

### 10. Over-Engineering

**Smell:** Abstract factory pattern for a function called once.
**Fix:** Inline it. Three lines of repeated code is fine. Extract when you hit the third use.

---

## Development Guides

Detailed guides for specific subsystems:

| Guide | Location | Description |
| --- | --- | --- |
| Tool Development | [company/assets/tools/README.md](company/assets/tools/README.md) | Creating custom LangChain tools with OAuth, env vars, and dynamic UI |
| Workflow Rules | [company_rules/README.md](company_rules/README.md) | Writing workflow definitions parsed by the workflow engine |
| Plugin Development | [plugins/README.md](plugins/README.md) | Creating frontend plugins (kanban, timeline, etc.) |

---

## Quick Reference

### File Layout

```
src/onemancompany/
  core/           — Business logic, state, config, events
  agents/         — LangChain agent definitions + tools
  api/            — FastAPI routes + WebSocket
  tools/mcp/      — MCP server bridge for self-hosted employees
  talent_market/  — Hiring + talent system

company/          — Runtime data (employees, projects, assets)
frontend/         — Vanilla JS + Canvas 2D
tests/            — pytest (unit / integration / e2e)
```

### Common Commands

```bash
# Start server
.venv/bin/python -m onemancompany.main

# Verify compilation
.venv/bin/python -c "from onemancompany.api.routes import router; print('OK')"

# Run tests
.venv/bin/python -m pytest tests/unit/ -x

# Check frontend syntax
node -c frontend/app.js
```
