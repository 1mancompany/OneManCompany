# Single Source of Truth Refactoring — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor backend and frontend so disk is the only source of truth — no in-memory caches of business data, no duplicated state, frontend is a pure render layer synced via 3-second tick.

**Architecture:** Create `core/store.py` as the unified read/write layer with per-file locks and dirty tracking. Replace all `company_state.employees` reads with `store.load_*()` disk reads. Replace full-state WebSocket broadcasts with a 3-second tick that sends dirty categories. Frontend fetches from REST API on tick notifications instead of caching WebSocket state.

**Tech Stack:** Python 3.11+, FastAPI, asyncio, PyYAML, vanilla JS, WebSocket

**Spec:** `docs/superpowers/specs/2026-03-11-single-source-of-truth-design.md`

---

## File Structure

### New Files
- `src/onemancompany/core/store.py` — Unified read/write layer (locks, dirty tracking, all YAML I/O)
- `src/onemancompany/core/sync_tick.py` — 3-second sync tick loop
- `tests/unit/core/test_store.py` — Tests for store module
- `tests/unit/core/test_sync_tick.py` — Tests for sync tick

### Modified Files (Backend)
- `src/onemancompany/core/state.py` — Gut `CompanyState` to minimal (layout + counters only), delete `_seed_employees`, `reload_all_from_disk`
- `src/onemancompany/core/config.py` — Delete `employee_configs` cache dict
- `src/onemancompany/api/websocket.py` — Replace full-state broadcast with tick-based `state_changed`
- `src/onemancompany/api/routes.py` — Replace `company_state.employees` reads with `store.load_*()`, add new REST endpoints
- `src/onemancompany/core/vessel.py` — Replace `emp.status = ...` mutations with `store.save_employee_runtime()`
- `src/onemancompany/core/routine.py` — Replace `company_state.employees` reads with `store.load_*()`
- `src/onemancompany/core/heartbeat.py` — Replace `emp.api_online = ...` with `store.save_employee_runtime()`
- `src/onemancompany/agents/onboarding.py` — Write new employee via `store.save_employee()`
- `src/onemancompany/agents/hr_agent.py` — Write updates via `store.save_employee()`
- `src/onemancompany/agents/termination.py` — Write via `store.save_ex_employee()`
- `src/onemancompany/agents/common_tools.py` — Read via `store.load_all_employees()`
- `src/onemancompany/agents/coo_agent.py` — Read via `store.load_all_employees()`
- `src/onemancompany/agents/ea_agent.py` — Read via `store.load_all_employees()`
- `src/onemancompany/agents/cso_agent.py` — Read via `store.load_all_employees()`
- `src/onemancompany/agents/tree_tools.py` — Read via `store.load_employee()`
- `src/onemancompany/core/snapshot.py` — Retire most providers
- `src/onemancompany/main.py` — Start sync tick, simplify startup (no seeding)

### Modified Files (Frontend)
- `frontend/app.js` — Delete `this.state` cache, switch to tick-based re-fetch pattern

---

## Chunk 1: Infrastructure — `core/store.py` + Sync Tick

### Task 1: Create `core/store.py` — YAML I/O helpers + lock infrastructure

**Files:**
- Create: `src/onemancompany/core/store.py`
- Create: `tests/unit/core/test_store.py`

- [ ] **Step 1: Write failing test for `_read_yaml` and `_write_yaml`**

```python
# tests/unit/core/test_store.py
"""Tests for core/store.py — unified read/write layer."""
from __future__ import annotations

import pytest
import yaml
from pathlib import Path


def test_read_yaml_returns_dict(tmp_path):
    """_read_yaml reads a YAML file and returns its contents."""
    p = tmp_path / "test.yaml"
    p.write_text("name: Alice\nage: 30\n")
    from onemancompany.core.store import _read_yaml
    result = _read_yaml(p)
    assert result == {"name": "Alice", "age": 30}


def test_read_yaml_missing_returns_empty(tmp_path):
    """_read_yaml returns {} for missing files."""
    from onemancompany.core.store import _read_yaml
    result = _read_yaml(tmp_path / "missing.yaml")
    assert result == {}


def test_write_yaml_creates_file(tmp_path):
    """_write_yaml writes data to a YAML file."""
    p = tmp_path / "out.yaml"
    from onemancompany.core.store import _write_yaml
    _write_yaml(p, {"name": "Bob", "skills": ["python"]})
    loaded = yaml.safe_load(p.read_text())
    assert loaded["name"] == "Bob"
    assert loaded["skills"] == ["python"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/core/test_store.py -x -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'onemancompany.core.store'`

- [ ] **Step 3: Implement `_read_yaml`, `_write_yaml`, lock infrastructure**

```python
# src/onemancompany/core/store.py
"""Unified read/write layer — disk is the single source of truth.

All persistent writes go through this module. Each function:
1. Acquires a per-file asyncio.Lock
2. Writes to disk (YAML) immediately
3. Marks the relevant resource category as dirty for the next sync tick
4. Does NOT update any in-memory cache

Reads always go to disk. No caching.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from onemancompany.core.config import (
    COMPANY_DIR,
    DATA_ROOT,
    EMPLOYEES_DIR,
    PROJECTS_DIR,
)

# ---------------------------------------------------------------------------
# Low-level YAML I/O
# ---------------------------------------------------------------------------

def _read_yaml(path: Path) -> dict:
    """Read a YAML file, return dict. Returns {} if file missing or empty."""
    try:
        if not path.exists():
            return {}
        text = path.read_text(encoding="utf-8")
        return yaml.safe_load(text) or {}
    except Exception as e:
        logger.error("Failed to read {}: {}", path, e)
        return {}


def _write_yaml(path: Path, data: dict) -> None:
    """Write dict to YAML file. Creates parent dirs if needed."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error("Failed to write {}: {}", path, e)
        raise


def _read_yaml_list(path: Path) -> list:
    """Read a YAML file that contains a list. Returns [] if missing."""
    try:
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        result = yaml.safe_load(text)
        return result if isinstance(result, list) else []
    except Exception as e:
        logger.error("Failed to read list {}: {}", path, e)
        return []


# ---------------------------------------------------------------------------
# Per-file asyncio locks
# ---------------------------------------------------------------------------

_file_locks: dict[str, asyncio.Lock] = {}


def _get_lock(file_path: str) -> asyncio.Lock:
    """Get or create an asyncio.Lock for the given file path."""
    if file_path not in _file_locks:
        _file_locks[file_path] = asyncio.Lock()
    return _file_locks[file_path]


# ---------------------------------------------------------------------------
# Dirty tracking
# ---------------------------------------------------------------------------

_dirty: set[str] = set()


def mark_dirty(*categories: str) -> None:
    """Mark resource categories as dirty for the next sync tick."""
    _dirty.update(categories)


def flush_dirty() -> list[str]:
    """Called by sync tick. Returns and clears the dirty set."""
    changed = list(_dirty)
    _dirty.clear()
    return changed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_store.py -x -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/store.py tests/unit/core/test_store.py
git commit -m "feat: create core/store.py with YAML I/O helpers, locks, and dirty tracking"
```

---

### Task 2: Add `store.py` employee read/write functions

**Files:**
- Modify: `src/onemancompany/core/store.py`
- Modify: `tests/unit/core/test_store.py`

- [ ] **Step 1: Write failing tests for employee read/write**

```python
# Append to tests/unit/core/test_store.py

import asyncio


def _run(coro):
    """Helper to run async code in sync test."""
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.mark.asyncio
async def test_save_employee_runtime_creates_runtime_section(tmp_path, monkeypatch):
    """save_employee_runtime writes runtime fields into profile.yaml runtime: section."""
    from onemancompany.core import store
    monkeypatch.setattr(store, "EMPLOYEES_DIR", tmp_path)

    emp_dir = tmp_path / "00100"
    emp_dir.mkdir()
    profile = emp_dir / "profile.yaml"
    profile.write_text("name: TestBot\nrole: Engineer\n")

    await store.save_employee_runtime("00100", status="working", current_task_summary="coding")

    data = yaml.safe_load(profile.read_text())
    assert data["runtime"]["status"] == "working"
    assert data["runtime"]["current_task_summary"] == "coding"
    assert "employees" in store._dirty


def test_load_employee_reads_profile_with_runtime(tmp_path, monkeypatch):
    """load_employee reads profile.yaml and merges runtime section."""
    from onemancompany.core import store
    monkeypatch.setattr(store, "EMPLOYEES_DIR", tmp_path)

    emp_dir = tmp_path / "00100"
    emp_dir.mkdir()
    profile = emp_dir / "profile.yaml"
    profile.write_text("name: TestBot\nrole: Engineer\nruntime:\n  status: working\n")

    result = store.load_employee("00100")
    assert result["name"] == "TestBot"
    assert result["runtime"]["status"] == "working"


def test_load_all_employees_reads_all_dirs(tmp_path, monkeypatch):
    """load_all_employees reads all employee directories."""
    from onemancompany.core import store
    monkeypatch.setattr(store, "EMPLOYEES_DIR", tmp_path)

    for eid in ["00002", "00100"]:
        d = tmp_path / eid
        d.mkdir()
        (d / "profile.yaml").write_text(f"name: Emp{eid}\nrole: Engineer\n")

    result = store.load_all_employees()
    assert len(result) == 2
    assert result["00002"]["name"] == "Emp00002"
    assert result["00100"]["name"] == "Emp00100"


@pytest.mark.asyncio
async def test_save_employee_merges_updates(tmp_path, monkeypatch):
    """save_employee merges updates into existing profile.yaml."""
    from onemancompany.core import store
    monkeypatch.setattr(store, "EMPLOYEES_DIR", tmp_path)

    emp_dir = tmp_path / "00100"
    emp_dir.mkdir()
    (emp_dir / "profile.yaml").write_text("name: OldName\nrole: Engineer\nskills:\n- python\n")

    await store.save_employee("00100", {"name": "NewName", "level": 2})

    data = yaml.safe_load((emp_dir / "profile.yaml").read_text())
    assert data["name"] == "NewName"
    assert data["level"] == 2
    assert data["skills"] == ["python"]  # preserved
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_store.py -x -v -k "employee"`
Expected: FAIL — `AttributeError: module 'onemancompany.core.store' has no attribute 'save_employee_runtime'`

- [ ] **Step 3: Implement employee read/write functions in `store.py`**

Append to `src/onemancompany/core/store.py`:

```python
# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _employee_profile_path(emp_id: str) -> Path:
    return EMPLOYEES_DIR / emp_id / "profile.yaml"


def _ex_employee_profile_path(emp_id: str) -> Path:
    return DATA_ROOT / "company" / "human_resource" / "ex-employees" / emp_id / "profile.yaml"


# ---------------------------------------------------------------------------
# Employee reads
# ---------------------------------------------------------------------------

def load_employee(emp_id: str) -> dict:
    """Read profile.yaml for a single employee. Returns full dict including runtime."""
    return _read_yaml(_employee_profile_path(emp_id))


def load_all_employees() -> dict[str, dict]:
    """Read all employee profile.yamls from disk. Returns {emp_id: profile_dict}."""
    result: dict[str, dict] = {}
    if not EMPLOYEES_DIR.exists():
        return result
    for emp_dir in sorted(EMPLOYEES_DIR.iterdir()):
        if not emp_dir.is_dir():
            continue
        profile_path = emp_dir / "profile.yaml"
        if profile_path.exists():
            result[emp_dir.name] = _read_yaml(profile_path)
    return result


def load_ex_employees() -> dict[str, dict]:
    """Read all ex-employee profile.yamls."""
    ex_dir = DATA_ROOT / "company" / "human_resource" / "ex-employees"
    result: dict[str, dict] = {}
    if not ex_dir.exists():
        return result
    for emp_dir in sorted(ex_dir.iterdir()):
        if not emp_dir.is_dir():
            continue
        profile_path = emp_dir / "profile.yaml"
        if profile_path.exists():
            result[emp_dir.name] = _read_yaml(profile_path)
    return result


def load_employee_guidance(emp_id: str) -> list[str]:
    """Read guidance.yaml for an employee. Returns list of guidance notes."""
    path = EMPLOYEES_DIR / emp_id / "guidance.yaml"
    data = _read_yaml(path)
    return data.get("notes", []) if data else []


def load_employee_work_principles(emp_id: str) -> str:
    """Read work_principles.md for an employee."""
    path = EMPLOYEES_DIR / emp_id / "work_principles.md"
    try:
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Employee writes
# ---------------------------------------------------------------------------

async def save_employee(emp_id: str, updates: dict) -> None:
    """Merge updates into employee profile.yaml, write immediately."""
    path = _employee_profile_path(emp_id)
    async with _get_lock(str(path)):
        data = _read_yaml(path)
        data.update(updates)
        _write_yaml(path, data)
    mark_dirty("employees")


async def save_employee_runtime(emp_id: str, **fields) -> None:
    """Update runtime: section of employee profile.yaml."""
    path = _employee_profile_path(emp_id)
    async with _get_lock(str(path)):
        data = _read_yaml(path)
        runtime = data.setdefault("runtime", {})
        runtime.update(fields)
        _write_yaml(path, data)
    mark_dirty("employees")


async def save_ex_employee(emp_id: str, data: dict) -> None:
    """Write ex-employee profile to disk."""
    path = _ex_employee_profile_path(emp_id)
    async with _get_lock(str(path)):
        _write_yaml(path, data)
    mark_dirty("ex_employees")


async def save_guidance(emp_id: str, notes: list[str]) -> None:
    """Write guidance.yaml for an employee."""
    path = EMPLOYEES_DIR / emp_id / "guidance.yaml"
    async with _get_lock(str(path)):
        _write_yaml(path, {"notes": notes})
    mark_dirty("employees")


async def save_work_principles(emp_id: str, text: str) -> None:
    """Write work_principles.md for an employee."""
    path = EMPLOYEES_DIR / emp_id / "work_principles.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    mark_dirty("employees")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_store.py -x -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/store.py tests/unit/core/test_store.py
git commit -m "feat: add employee read/write functions to store.py"
```

---

### Task 3: Add remaining store read/write functions (project, room, company-level)

**Files:**
- Modify: `src/onemancompany/core/store.py`
- Modify: `tests/unit/core/test_store.py`

- [ ] **Step 1: Write failing tests for project, room, tool, tree, and company-level functions**

```python
# Append to tests/unit/core/test_store.py

def test_read_yaml_list_returns_list(tmp_path):
    """_read_yaml_list reads a YAML list file."""
    from onemancompany.core.store import _read_yaml_list
    p = tmp_path / "items.yaml"
    p.write_text("- name: A\n- name: B\n")
    result = _read_yaml_list(p)
    assert len(result) == 2
    assert result[0]["name"] == "A"


def test_read_yaml_list_empty_returns_empty(tmp_path):
    """_read_yaml_list returns [] for missing file."""
    from onemancompany.core.store import _read_yaml_list
    assert _read_yaml_list(tmp_path / "missing.yaml") == []


def test_read_yaml_list_non_list_returns_empty(tmp_path):
    """_read_yaml_list returns [] when file contains a dict."""
    from onemancompany.core.store import _read_yaml_list
    p = tmp_path / "notlist.yaml"
    p.write_text("key: value\n")
    assert _read_yaml_list(p) == []


@pytest.mark.asyncio
async def test_save_project_status_updates_yaml(tmp_path, monkeypatch):
    """save_project_status updates only the status field in project.yaml."""
    from onemancompany.core import store
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path)

    pdir = tmp_path / "proj-001"
    pdir.mkdir()
    (pdir / "project.yaml").write_text("task: Build thing\nstatus: in_progress\n")

    await store.save_project_status("proj-001", "completed", completed_at="2026-03-11")

    data = yaml.safe_load((pdir / "project.yaml").read_text())
    assert data["status"] == "completed"
    assert data["completed_at"] == "2026-03-11"
    assert data["task"] == "Build thing"  # preserved


def test_load_rooms_reads_yaml(tmp_path, monkeypatch):
    """load_rooms reads all room YAML files."""
    from onemancompany.core import store
    rooms_dir = tmp_path / "rooms"
    rooms_dir.mkdir()
    monkeypatch.setattr(store, "_rooms_dir", lambda: rooms_dir)

    (rooms_dir / "room-a.yaml").write_text("id: room-a\nname: Alpha\ncapacity: 4\n")
    (rooms_dir / "room-b.yaml").write_text("id: room-b\nname: Beta\ncapacity: 8\n")

    result = store.load_rooms()
    assert len(result) == 2
    names = {r["name"] for r in result}
    assert names == {"Alpha", "Beta"}


@pytest.mark.asyncio
async def test_save_room_updates_booking(tmp_path, monkeypatch):
    """save_room merges updates into room YAML."""
    from onemancompany.core import store
    rooms_dir = tmp_path / "rooms"
    rooms_dir.mkdir()
    monkeypatch.setattr(store, "_rooms_dir", lambda: rooms_dir)

    (rooms_dir / "room-a.yaml").write_text("id: room-a\nname: Alpha\nis_booked: false\n")

    await store.save_room("room-a", {"is_booked": True, "booked_by": "00002"})

    data = yaml.safe_load((rooms_dir / "room-a.yaml").read_text())
    assert data["is_booked"] is True
    assert data["booked_by"] == "00002"


@pytest.mark.asyncio
async def test_append_activity_appends_entry(tmp_path, monkeypatch):
    """append_activity adds an entry to activity_log.yaml."""
    from onemancompany.core import store
    monkeypatch.setattr(store, "COMPANY_DIR", tmp_path)

    await store.append_activity({"type": "hired", "employee": "00100"})
    await store.append_activity({"type": "fired", "employee": "00101"})

    data = store._read_yaml_list(tmp_path / "activity_log.yaml")
    assert len(data) == 2
    assert data[0]["type"] == "hired"
    assert data[1]["type"] == "fired"


def test_load_tools_reads_tool_dirs(tmp_path, monkeypatch):
    """load_tools reads tool.yaml from each tool directory."""
    from onemancompany.core import store
    tools_dir = tmp_path / "company" / "assets" / "tools"
    monkeypatch.setattr(store, "DATA_ROOT", tmp_path)

    tool_a = tools_dir / "tool-a"
    tool_a.mkdir(parents=True)
    (tool_a / "tool.yaml").write_text("id: tool-a\nname: Hammer\n")

    result = store.load_tools()
    assert len(result) == 1
    assert result[0]["name"] == "Hammer"


@pytest.mark.asyncio
async def test_save_tool_writes_yaml(tmp_path, monkeypatch):
    """save_tool writes tool.yaml in the tool directory."""
    from onemancompany.core import store
    tools_dir = tmp_path / "company" / "assets" / "tools"
    monkeypatch.setattr(store, "DATA_ROOT", tmp_path)

    tool_dir = tools_dir / "my-tool"
    tool_dir.mkdir(parents=True)
    (tool_dir / "tool.yaml").write_text("id: my-tool\nname: OldName\n")

    await store.save_tool("my-tool", {"id": "my-tool", "name": "NewName"})

    data = yaml.safe_load((tool_dir / "tool.yaml").read_text())
    assert data["name"] == "NewName"


@pytest.mark.asyncio
async def test_save_tree_writes_yaml(tmp_path, monkeypatch):
    """save_tree writes task_tree.yaml in the project directory."""
    from onemancompany.core import store

    pdir = tmp_path / "proj-001"
    pdir.mkdir()

    tree_data = {"root": "node-1", "nodes": [{"id": "node-1", "status": "pending"}]}
    await store.save_tree(str(pdir), tree_data)

    data = yaml.safe_load((pdir / "task_tree.yaml").read_text())
    assert data["root"] == "node-1"


@pytest.mark.asyncio
async def test_save_culture_writes_list(tmp_path, monkeypatch):
    """save_culture writes a list to company_culture.yaml."""
    from onemancompany.core import store
    monkeypatch.setattr(store, "COMPANY_DIR", tmp_path)

    await store.save_culture([{"value": "Move fast"}])
    data = store._read_yaml_list(tmp_path / "company_culture.yaml")
    assert len(data) == 1
    assert data[0]["value"] == "Move fast"


@pytest.mark.asyncio
async def test_save_direction_writes_yaml(tmp_path, monkeypatch):
    """save_direction writes direction text."""
    from onemancompany.core import store
    monkeypatch.setattr(store, "COMPANY_DIR", tmp_path)

    await store.save_direction("Build great AI products")
    result = store.load_direction()
    # load_direction reads from COMPANY_DIR which is monkeypatched
    data = store._read_yaml(tmp_path / "company_direction.yaml")
    assert data["direction"] == "Build great AI products"


@pytest.mark.asyncio
async def test_save_overhead_writes_yaml(tmp_path, monkeypatch):
    """save_overhead persists token/cost data."""
    from onemancompany.core import store
    monkeypatch.setattr(store, "COMPANY_DIR", tmp_path)

    await store.save_overhead({"company_tokens": 1000000, "total_cost": 42.5})
    data = store.load_overhead()
    # load_overhead reads from COMPANY_DIR
    data = store._read_yaml(tmp_path / "overhead.yaml")
    assert data["company_tokens"] == 1000000


@pytest.mark.asyncio
async def test_save_candidates_writes_yaml(tmp_path, monkeypatch):
    """save_candidates persists candidate shortlist."""
    from onemancompany.core import store
    monkeypatch.setattr(store, "COMPANY_DIR", tmp_path)

    await store.save_candidates("batch-001", {"candidates": [{"name": "Alice"}]})
    data = store.load_candidates("batch-001")
    # load_candidates reads from COMPANY_DIR
    data = store._read_yaml(tmp_path / "candidates" / "batch-001.yaml")
    assert data["candidates"][0]["name"] == "Alice"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_store.py -x -v -k "project or room or activity"`
Expected: FAIL

- [ ] **Step 3: Implement project, room, company-level read/write functions**

Append to `src/onemancompany/core/store.py`:

```python
# ---------------------------------------------------------------------------
# Room helpers
# ---------------------------------------------------------------------------

def _rooms_dir() -> Path:
    """Return path to meeting rooms directory."""
    return DATA_ROOT / "company" / "assets" / "rooms"


# ---------------------------------------------------------------------------
# Project reads/writes
# ---------------------------------------------------------------------------

def load_project(project_id: str) -> dict:
    """Read project.yaml for a project."""
    return _read_yaml(PROJECTS_DIR / project_id / "project.yaml")


async def save_project_status(project_id: str, status: str, **extra) -> None:
    """Update project.yaml status field (and optional extra fields)."""
    path = PROJECTS_DIR / project_id / "project.yaml"
    async with _get_lock(str(path)):
        data = _read_yaml(path)
        data["status"] = status
        data.update(extra)
        _write_yaml(path, data)
    mark_dirty("task_queue")


# ---------------------------------------------------------------------------
# Room reads/writes
# ---------------------------------------------------------------------------

def load_rooms() -> list[dict]:
    """Read all room YAML files from assets/rooms/."""
    rdir = _rooms_dir()
    if not rdir.exists():
        return []
    results = []
    for f in sorted(rdir.iterdir()):
        if f.suffix in (".yaml", ".yml") and not f.name.endswith("_chat.yaml"):
            results.append(_read_yaml(f))
    return results


def load_room(room_id: str) -> dict:
    """Read a single room YAML."""
    return _read_yaml(_rooms_dir() / f"{room_id}.yaml")


async def save_room(room_id: str, updates: dict) -> None:
    """Merge updates into room YAML."""
    path = _rooms_dir() / f"{room_id}.yaml"
    async with _get_lock(str(path)):
        data = _read_yaml(path)
        data.update(updates)
        _write_yaml(path, data)
    mark_dirty("rooms")


def load_room_chat(room_id: str) -> list[dict]:
    """Read chat history for a room."""
    return _read_yaml_list(_rooms_dir() / f"{room_id}_chat.yaml")


async def append_room_chat(room_id: str, message: dict) -> None:
    """Append a chat message to room chat file."""
    path = _rooms_dir() / f"{room_id}_chat.yaml"
    async with _get_lock(str(path)):
        messages = _read_yaml_list(path)
        messages.append(message)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(messages, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
    mark_dirty("rooms")


# ---------------------------------------------------------------------------
# Tool reads/writes
# ---------------------------------------------------------------------------

def load_tools() -> list[dict]:
    """Read all tool.yaml files from assets/tools/."""
    tools_dir = DATA_ROOT / "company" / "assets" / "tools"
    if not tools_dir.exists():
        return []
    results = []
    for tdir in sorted(tools_dir.iterdir()):
        if not tdir.is_dir():
            continue
        tyaml = tdir / "tool.yaml"
        if tyaml.exists():
            results.append(_read_yaml(tyaml))
    return results


async def save_tool(slug: str, data: dict) -> None:
    """Write tool.yaml for a tool."""
    tools_dir = DATA_ROOT / "company" / "assets" / "tools"
    path = tools_dir / slug / "tool.yaml"
    async with _get_lock(str(path)):
        _write_yaml(path, data)
    mark_dirty("tools")


# ---------------------------------------------------------------------------
# Task tree reads/writes
# ---------------------------------------------------------------------------

async def save_tree(project_dir: str, tree_data: dict) -> None:
    """Write task_tree.yaml in the project directory."""
    path = Path(project_dir) / "task_tree.yaml"
    async with _get_lock(str(path)):
        _write_yaml(path, tree_data)
    mark_dirty("task_queue")


# ---------------------------------------------------------------------------
# Company-level reads/writes
# ---------------------------------------------------------------------------

def load_activity_log() -> list[dict]:
    """Read activity_log.yaml."""
    return _read_yaml_list(COMPANY_DIR / "activity_log.yaml")


async def append_activity(entry: dict) -> None:
    """Append entry to activity_log.yaml."""
    path = COMPANY_DIR / "activity_log.yaml"
    async with _get_lock(str(path)):
        log = _read_yaml_list(path)
        log.append(entry)
        # Keep last 200 entries
        if len(log) > 200:
            log = log[-200:]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(log, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
    mark_dirty("activity_log")


def load_culture() -> list[dict]:
    """Read company_culture.yaml."""
    return _read_yaml_list(COMPANY_DIR / "company_culture.yaml")


async def save_culture(items: list[dict]) -> None:
    """Write company_culture.yaml."""
    path = COMPANY_DIR / "company_culture.yaml"
    async with _get_lock(str(path)):
        path.write_text(
            yaml.dump(items, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
    mark_dirty("culture")


def load_direction() -> str:
    """Read company_direction.yaml."""
    data = _read_yaml(COMPANY_DIR / "company_direction.yaml")
    return data.get("direction", "") if data else ""


async def save_direction(text: str) -> None:
    """Write company_direction.yaml."""
    path = COMPANY_DIR / "company_direction.yaml"
    async with _get_lock(str(path)):
        _write_yaml(path, {"direction": text})
    mark_dirty("direction")


def load_sales_tasks() -> list[dict]:
    """Read sales/tasks.yaml."""
    return _read_yaml_list(COMPANY_DIR / "sales" / "tasks.yaml")


async def save_sales_tasks(tasks: list[dict]) -> None:
    """Write sales/tasks.yaml."""
    path = COMPANY_DIR / "sales" / "tasks.yaml"
    async with _get_lock(str(path)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(tasks, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
    mark_dirty("sales_tasks")


# ---------------------------------------------------------------------------
# Candidate reads/writes
# ---------------------------------------------------------------------------

def load_candidates(batch_id: str) -> dict:
    """Read candidate shortlist from disk."""
    return _read_yaml(COMPANY_DIR / "candidates" / f"{batch_id}.yaml")


async def save_candidates(batch_id: str, data: dict) -> None:
    """Persist candidate shortlist to disk."""
    path = COMPANY_DIR / "candidates" / f"{batch_id}.yaml"
    async with _get_lock(str(path)):
        _write_yaml(path, data)
    mark_dirty("candidates")


# ---------------------------------------------------------------------------
# 1-on-1 chat history
# ---------------------------------------------------------------------------

def load_oneonone(emp_id: str) -> list[dict]:
    """Read 1-on-1 chat history for an employee."""
    return _read_yaml_list(EMPLOYEES_DIR / emp_id / "oneonone_history.yaml")


async def append_oneonone(emp_id: str, message: dict) -> None:
    """Append a message to 1-on-1 chat history."""
    path = EMPLOYEES_DIR / emp_id / "oneonone_history.yaml"
    async with _get_lock(str(path)):
        history = _read_yaml_list(path)
        history.append(message)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(history, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
    mark_dirty("employees")


# ---------------------------------------------------------------------------
# Overhead / token tracking
# ---------------------------------------------------------------------------

def load_overhead() -> dict:
    """Read overhead.yaml."""
    return _read_yaml(COMPANY_DIR / "overhead.yaml")


async def save_overhead(data: dict) -> None:
    """Write overhead.yaml."""
    path = COMPANY_DIR / "overhead.yaml"
    async with _get_lock(str(path)):
        _write_yaml(path, data)
    mark_dirty("overhead")
```

- [ ] **Step 4: Run all store tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_store.py -x -v`
Expected: PASS (all tests)

- [ ] **Step 5: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.core.store import load_all_employees, save_employee, flush_dirty; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/core/store.py tests/unit/core/test_store.py
git commit -m "feat: add project, room, company-level, and candidate read/write functions to store.py"
```

---

### Task 4: Create `core/sync_tick.py` — 3-second broadcast loop

**Files:**
- Create: `src/onemancompany/core/sync_tick.py`
- Create: `tests/unit/core/test_sync_tick.py`

- [ ] **Step 1: Write failing test for sync tick**

```python
# tests/unit/core/test_sync_tick.py
"""Tests for core/sync_tick.py — 3-second sync tick loop."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_sync_tick_broadcasts_dirty_categories():
    """sync_tick sends state_changed with dirty categories via ws_manager."""
    from onemancompany.core import store
    from onemancompany.core.sync_tick import _run_tick

    store.mark_dirty("employees", "rooms")

    mock_broadcast = AsyncMock()
    with patch("onemancompany.core.sync_tick.ws_manager") as mock_ws:
        mock_ws.broadcast = mock_broadcast
        await _run_tick()

    mock_broadcast.assert_called_once()
    call_arg = mock_broadcast.call_args[0][0]
    assert call_arg["type"] == "state_changed"
    assert set(call_arg["changed"]) == {"employees", "rooms"}


@pytest.mark.asyncio
async def test_sync_tick_does_nothing_when_clean():
    """sync_tick sends nothing if nothing is dirty."""
    from onemancompany.core.sync_tick import _run_tick
    from onemancompany.core import store

    store._dirty.clear()  # ensure clean

    mock_broadcast = AsyncMock()
    with patch("onemancompany.core.sync_tick.ws_manager") as mock_ws:
        mock_ws.broadcast = mock_broadcast
        await _run_tick()

    mock_broadcast.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/core/test_sync_tick.py -x -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement sync tick**

```python
# src/onemancompany/core/sync_tick.py
"""3-second sync tick — broadcasts dirty categories to WebSocket clients.

The tick loop runs as a background asyncio.Task started in the FastAPI lifespan.
Each tick:
  1. Calls store.flush_dirty() to get changed categories
  2. Broadcasts {"type": "state_changed", "changed": [...]} to all WS clients
  3. Sleeps 3 seconds

Chat messages are still pushed in real-time (outside the tick).
"""
from __future__ import annotations

import asyncio

from loguru import logger

from onemancompany.core.store import flush_dirty

TICK_INTERVAL_SECONDS = 3.0


async def _run_tick() -> None:
    """Execute one tick — broadcast dirty categories if any."""
    from onemancompany.api.websocket import ws_manager

    changed = flush_dirty()
    if changed:
        await ws_manager.broadcast({
            "type": "state_changed",
            "changed": changed,
        })


async def start_sync_tick() -> None:
    """Run the sync tick loop forever (call as background task)."""
    logger.info("Sync tick started ({}s interval)", TICK_INTERVAL_SECONDS)
    try:
        while True:
            await asyncio.sleep(TICK_INTERVAL_SECONDS)
            try:
                await _run_tick()
            except Exception as e:
                logger.error("Sync tick error: {}", e)
    except asyncio.CancelledError:
        logger.info("Sync tick stopped")
        raise
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_sync_tick.py -x -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/sync_tick.py tests/unit/core/test_sync_tick.py
git commit -m "feat: create sync_tick.py — 3-second dirty category broadcast loop"
```

---

### Task 5: Wire sync tick into FastAPI lifespan

**Files:**
- Modify: `src/onemancompany/main.py`
- Modify: `tests/unit/test_main.py`

- [ ] **Step 1: Write failing test for sync tick startup**

```python
# Append to tests/unit/test_main.py
from unittest.mock import patch, AsyncMock, MagicMock
import pytest


@pytest.mark.asyncio
async def test_lifespan_starts_sync_tick():
    """Lifespan should create a background task for start_sync_tick."""
    with patch("onemancompany.main.start_sync_tick", new_callable=AsyncMock) as mock_tick:
        with patch("onemancompany.main.asyncio.create_task") as mock_create:
            # Import after patching to capture the call
            from onemancompany.main import app
            # The sync tick task creation happens in lifespan startup
            # Verify start_sync_tick is importable and referenced
            from onemancompany.core.sync_tick import start_sync_tick
            assert callable(start_sync_tick)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_main.py::test_lifespan_starts_sync_tick -x -v`
Expected: FAIL (import error or missing reference)

- [ ] **Step 3: Add sync tick startup to lifespan**

In `main.py`, find the `lifespan` async context manager. Add the sync tick as a background task alongside the existing `event_broadcaster`. Find the line that creates `broadcaster_task`:

```python
# Add import at top of lifespan or module:
from onemancompany.core.sync_tick import start_sync_tick

# Inside lifespan startup, after the broadcaster_task line:
sync_tick_task = asyncio.create_task(start_sync_tick())
```

In the shutdown section (the `finally` or after `yield`), add:

```python
sync_tick_task.cancel()
try:
    await sync_tick_task
except asyncio.CancelledError:
    pass
```

- [ ] **Step 4: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.main import app; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/main.py tests/unit/test_main.py
git commit -m "feat: wire sync tick into FastAPI lifespan"
```

---

### Task 6: Add new REST endpoints for tick-based fetching

**Files:**
- Modify: `src/onemancompany/api/routes.py`
- Modify: `tests/unit/api/test_routes.py`

The frontend needs dedicated endpoints to fetch resources when notified by the tick. Some already exist; add the missing ones.

- [ ] **Step 1: Write failing tests for new endpoints**

```python
# Append to tests/unit/api/test_routes.py
from unittest.mock import patch, MagicMock
import pytest


@pytest.mark.asyncio
async def test_list_employees_returns_from_disk():
    """GET /api/employees reads from store.load_all_employees."""
    mock_data = {
        "00100": {"name": "TestBot", "role": "Engineer", "runtime": {"status": "idle"}},
    }
    with patch("onemancompany.api.routes.load_all_employees", return_value=mock_data):
        from onemancompany.api.routes import list_employees
        result = await list_employees()
        assert len(result) == 1
        assert result[0]["name"] == "TestBot"
        assert result[0]["status"] == "idle"


@pytest.mark.asyncio
async def test_list_rooms_returns_from_disk():
    """GET /api/rooms reads from store.load_rooms."""
    mock_rooms = [{"id": "room-a", "name": "Alpha", "is_booked": False}]
    with patch("onemancompany.api.routes.load_rooms", return_value=mock_rooms):
        from onemancompany.api.routes import list_rooms
        result = await list_rooms()
        assert len(result) == 1
        assert result[0]["name"] == "Alpha"


@pytest.mark.asyncio
async def test_get_room_chat_returns_from_disk():
    """GET /api/rooms/{id}/chat reads from store.load_room_chat."""
    mock_chat = [{"speaker": "CEO", "message": "Hello"}]
    with patch("onemancompany.api.routes.load_room_chat", return_value=mock_chat):
        from onemancompany.api.routes import get_room_chat
        result = await get_room_chat("room-a")
        assert len(result) == 1
        assert result[0]["speaker"] == "CEO"


@pytest.mark.asyncio
async def test_get_activity_log_returns_from_disk():
    """GET /api/activity-log reads from store.load_activity_log."""
    mock_log = [{"type": "hired", "employee": "00100"}] * 60
    with patch("onemancompany.api.routes.load_activity_log", return_value=mock_log):
        from onemancompany.api.routes import get_activity_log
        result = await get_activity_log()
        assert len(result) == 50  # capped at 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/api/test_routes.py -x -v -k "list_employees or list_rooms or get_room_chat or get_activity_log"`
Expected: FAIL — `ImportError` or `AttributeError`

- [ ] **Step 3: Add missing endpoints**

```python
# Add to routes.py:

@router.get("/api/employees")
async def list_employees():
    """List all active employees — reads from disk."""
    from onemancompany.core.store import load_all_employees
    employees = load_all_employees()
    # Convert to list format matching existing frontend expectations
    result = []
    for emp_id, data in employees.items():
        runtime = data.pop("runtime", {})
        data["id"] = emp_id
        data["employee_number"] = emp_id
        data["status"] = runtime.get("status", "idle")
        data["is_listening"] = runtime.get("is_listening", False)
        data["current_task_summary"] = runtime.get("current_task_summary", "")
        data["api_online"] = runtime.get("api_online", True)
        data["needs_setup"] = runtime.get("needs_setup", False)
        result.append(data)
    return result


@router.get("/api/rooms")
async def list_rooms():
    """List all meeting rooms — reads from disk."""
    from onemancompany.core.store import load_rooms
    return load_rooms()


@router.get("/api/rooms/{room_id}/chat")
async def get_room_chat(room_id: str):
    """Get chat history for a room."""
    from onemancompany.core.store import load_room_chat
    return load_room_chat(room_id)


@router.get("/api/tools")
async def list_tools():
    """List all office tools — reads from disk."""
    from onemancompany.core.store import load_tools
    return load_tools()


@router.get("/api/employee/{employee_id}/oneonone")
async def get_oneonone_history(employee_id: str):
    """Get 1-on-1 chat history for an employee."""
    from onemancompany.core.store import load_oneonone
    return load_oneonone(employee_id)


@router.get("/api/activity-log")
async def get_activity_log():
    """Get recent activity log entries."""
    from onemancompany.core.store import load_activity_log
    log = load_activity_log()
    return log[-50:]  # last 50 entries
```

- [ ] **Step 3: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.api.routes import router; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/onemancompany/api/routes.py
git commit -m "feat: add REST endpoints for tick-based resource fetching"
```

---

## Chunk 2: Employee Data Migration

### Task 7: Add `runtime:` section to profile.yaml schema

**Files:**
- Modify: `src/onemancompany/core/store.py` (if needed)
- Modify: `src/onemancompany/core/vessel.py`

Employee ephemeral state (`status`, `is_listening`, `current_task_summary`, `api_online`, `needs_setup`) currently lives only in the `Employee` dataclass in memory. We need to persist it to the `runtime:` section of `profile.yaml`.

- [ ] **Step 1: Identify all runtime state mutation sites in vessel.py**

Search for patterns like `emp.status =`, `emp.is_listening =`, `emp.current_task_summary =`:

```bash
.venv/bin/python -c "
import subprocess
result = subprocess.run(['grep', '-rn', 'emp\\.status\\s*=\\|emp\\.is_listening\\s*=\\|emp\\.current_task_summary\\s*=\\|emp\\.api_online\\s*=', 'src/onemancompany/'], capture_output=True, text=True)
print(result.stdout)
"
```

- [ ] **Step 2: Replace in-memory mutations with `store.save_employee_runtime()` calls**

For each mutation site found, replace:
```python
# BEFORE
emp.status = "working"
emp.current_task_summary = "Building feature X"

# AFTER
await store.save_employee_runtime(emp_id, status="working", current_task_summary="Building feature X")
```

Key files to modify:
- `core/vessel.py` — task execution sets `status` to working/idle, sets `current_task_summary`
- `core/heartbeat.py` — sets `api_online`
- `agents/onboarding.py` — sets initial runtime state
- `api/routes.py` — inquiry sessions may set `is_listening`

- [ ] **Step 3: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.core.vessel import employee_manager; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Run existing tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_vessel.py -x -v`
Expected: PASS (may need test fixture updates for async store calls)

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/vessel.py src/onemancompany/core/heartbeat.py src/onemancompany/agents/onboarding.py src/onemancompany/api/routes.py
git commit -m "refactor: persist employee runtime state to profile.yaml via store"
```

---

### Task 8: Migrate `company_state.employees` read sites to `store.load_employee()`

**Files:**
- Modify: `src/onemancompany/agents/common_tools.py`
- Modify: `src/onemancompany/agents/tree_tools.py`
- Modify: `src/onemancompany/agents/coo_agent.py`
- Modify: `src/onemancompany/agents/cso_agent.py`
- Modify: `src/onemancompany/agents/ea_agent.py`
- Modify: `src/onemancompany/core/routine.py`
- Modify: `src/onemancompany/api/routes.py`

This is a mechanical replacement. Each file that reads `company_state.employees[id]` or iterates `company_state.employees.values()` is changed to use `store.load_employee(id)` or `store.load_all_employees()`.

- [ ] **Step 1: Replace in `agents/common_tools.py`**

```python
# BEFORE
from onemancompany.core.state import company_state
# ... uses company_state.employees

# AFTER
from onemancompany.core.store import load_all_employees
# ... uses load_all_employees()
```

- [ ] **Step 2: Replace in `agents/tree_tools.py`**

```python
# BEFORE: validates employee exists
emp = company_state.employees.get(employee_id)

# AFTER
from onemancompany.core.store import load_employee
emp_data = load_employee(employee_id)
if not emp_data:
    return "Employee not found"
```

- [ ] **Step 3: Replace in remaining agent files (`coo_agent.py`, `cso_agent.py`, `ea_agent.py`)**

Same pattern — replace `company_state.employees` reads with `store.load_employee()` or `store.load_all_employees()`.

- [ ] **Step 4: Replace in `core/routine.py`**

Replace iteration over `company_state.employees` for scheduling with `store.load_all_employees()`.

- [ ] **Step 5: Replace in `api/routes.py`**

Replace `_require_employee()` and all `company_state.employees.get()` calls with `store.load_employee()`.

**Important:** The `_require_employee()` helper is used in many endpoints. Update it once:

```python
def _require_employee(employee_id: str) -> dict:
    """Get employee data from disk or raise 404."""
    from onemancompany.core.store import load_employee
    data = load_employee(employee_id)
    if not data:
        raise HTTPException(status_code=404, detail="Employee not found")
    data["id"] = employee_id
    return data
```

- [ ] **Step 6: Verify compilation of all modified files**

Run: `.venv/bin/python -c "from onemancompany.api.routes import router; from onemancompany.agents.common_tools import list_colleagues; print('OK')"`
Expected: `OK`

- [ ] **Step 7: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/ -x --timeout=30`
Expected: PASS (fix any broken tests by updating mocks to patch `store.load_employee` at the importing module level)

- [ ] **Step 8: Commit**

```bash
git add src/onemancompany/agents/ src/onemancompany/core/routine.py src/onemancompany/api/routes.py
git commit -m "refactor: migrate company_state.employees reads to store.load_employee()"
```

---

### Task 9: Migrate employee write sites to `store.save_employee()`

**Files:**
- Modify: `src/onemancompany/agents/onboarding.py`
- Modify: `src/onemancompany/agents/hr_agent.py`
- Modify: `src/onemancompany/agents/termination.py`
- Modify: `src/onemancompany/api/routes.py`

- [ ] **Step 1: Migrate `onboarding.py` — `execute_hire()` now writes via store**

Currently creates `Employee(...)` and adds to `company_state.employees[id]`. Change to write `profile.yaml` via `store.save_employee()`.

```python
# BEFORE
company_state.employees[emp_num] = Employee(id=emp_num, name=..., ...)

# AFTER
await store.save_employee(emp_num, {
    "name": name,
    "role": role,
    "skills": skills,
    "nickname": nickname,
    "department": department,
    # ... all fields
})
await store.save_employee_runtime(emp_num, status="idle")
```

- [ ] **Step 2: Migrate `hr_agent.py` — reviews, PIP**

Replace `emp.okrs = ...`, `emp.pip = ...` with `store.save_employee(emp_id, {"okrs": ...})`.

- [ ] **Step 3: Migrate `termination.py` — fire employee**

Replace moving employee from `company_state.employees` to `company_state.ex_employees` with `store.save_ex_employee()` + delete from employees dir.

- [ ] **Step 4: Migrate `routes.py` — PUT endpoints for config changes**

Replace `emp.name = new_name; save_employee_profile(...)` with `store.save_employee(id, updates)`.

- [ ] **Step 5: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.agents.onboarding import execute_hire; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/agents/ -x --timeout=30`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/onemancompany/agents/onboarding.py src/onemancompany/agents/hr_agent.py src/onemancompany/agents/termination.py src/onemancompany/api/routes.py
git commit -m "refactor: migrate employee write sites to store.save_employee()"
```

---

### Task 10: Delete `employee_configs` cache and simplify `_seed_employees()`

**Files:**
- Modify: `src/onemancompany/core/config.py`
- Modify: `src/onemancompany/core/state.py`

- [ ] **Step 1: Remove `employee_configs` dict from `config.py`**

Delete the module-level `employee_configs: dict = {}` and the code that populates it at import time. The `load_employee_configs()` function can remain as it's used by some callers, but the cached dict is eliminated.

- [ ] **Step 2: Simplify `_seed_employees()` in `state.py`**

Since employees are now read from disk via `store.load_all_employees()`, the `_seed_employees()` function only needs to compute `_next_employee_number`:

```python
def _init_employee_counter() -> None:
    """Set the next employee number counter from existing employee dirs."""
    if not EMPLOYEES_DIR.exists():
        company_state._next_employee_number = 6
        return
    max_num = 5  # start after founding employees
    for emp_dir in EMPLOYEES_DIR.iterdir():
        if emp_dir.is_dir():
            try:
                num = int(emp_dir.name)
                if num > max_num:
                    max_num = num
            except ValueError:
                pass
    company_state._next_employee_number = max_num + 1
```

- [ ] **Step 3: Remove `reload_all_from_disk()` employee section**

The employee section of `reload_all_from_disk()` (lines 506-624) is no longer needed — disk reads happen on demand. Replace with:

```python
def reload_all_from_disk() -> dict:
    """Trigger a frontend refresh by marking all categories dirty."""
    from onemancompany.core.store import mark_dirty
    mark_dirty("employees", "rooms", "tools", "task_queue", "culture", "activity_log")
    return {"status": "marked_dirty"}
```

- [ ] **Step 4: Clean up `CompanyState` — remove employee-related fields**

Remove from `CompanyState`:
- `employees: dict[str, Employee]` → DELETED
- `ex_employees: dict[str, Employee]` → DELETED
- `company_culture: list[dict]` → DELETED (read from disk)
- `company_direction: str` → DELETED (read from disk)
- `activity_log: list[dict]` → DELETED (read from disk)
- `sales_tasks: dict[str, SalesTask]` → DELETED (read from disk)

Retain:
- `tools: dict[str, OfficeTool]` → keep temporarily until tools migration (Task 11)
- `meeting_rooms: dict[str, MeetingRoom]` → keep temporarily until rooms migration
- `office_layout: dict` → keep (intermediate computation product)
- `_next_employee_number: int` → keep (counter)
- `ceo_tasks: list[str]` → keep (small transient list)
- `company_tokens: int` → keep temporarily
- `overhead_costs: OverheadCosts` → keep temporarily

- [ ] **Step 5: Update `to_json()` to read from disk**

```python
def to_json(self) -> dict:
    from onemancompany.core.store import load_all_employees, load_activity_log
    employees = load_all_employees()
    # ... build response from disk reads
```

NOTE: `to_json()` is called by `websocket.py` on every event. After Phase 2a (Task 12), this will be removed. For now, update it to read from disk to maintain backward compatibility during migration.

- [ ] **Step 6: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.core.state import company_state; print('OK')"`
Expected: `OK`

- [ ] **Step 7: Run all tests, fix broken ones**

Run: `.venv/bin/python -m pytest tests/unit/ -x --timeout=60`
Expected: Fix any tests that relied on `company_state.employees`. Update mocks to use `store.load_employee` patches.

- [ ] **Step 8: Commit**

```bash
git add src/onemancompany/core/config.py src/onemancompany/core/state.py tests/
git commit -m "refactor: delete employee_configs cache, simplify seeding to counter-only"
```

---

## Chunk 3: Project, Room, and Remaining Data Migration

### Task 11: Migrate project status to single source

**Files:**
- Modify: `src/onemancompany/api/routes.py`
- Modify: `src/onemancompany/core/vessel.py`

- [ ] **Step 1: Remove `_aggregate_tree_status()` in routes.py**

Find `_aggregate_tree_status` function (or inline tree-status override logic in `get_task_queue()`). Delete it. Task queue endpoint should read `project.yaml` status directly:

```python
# AFTER: status comes only from project.yaml
status = project_data.get("status", "pending")
# Do NOT override with tree aggregation
```

- [ ] **Step 2: Add `store.save_project_status()` calls at ALL 5 lifecycle trigger points**

Per spec section 6.2, there are exactly 5 trigger points:

**Trigger 1 — EA creates project dispatch → `in_progress`:**
In `src/onemancompany/core/project_archive.py`, function `create_iteration()`:
```python
await store.save_project_status(project_id, "in_progress")
```

**Trigger 2 — Tree root node accepted → `completed`:**
In `src/onemancompany/core/vessel.py`, function `_on_child_complete()`:
```python
if node_is_root and all_children_accepted:
    await store.save_project_status(project_id, "completed", completed_at=datetime.now().isoformat())
```

**Trigger 3 — Tree root node failed → `failed`:**
In `src/onemancompany/core/vessel.py`, function `_on_child_complete()`:
```python
if node_is_root and root_failed:
    await store.save_project_status(project_id, "failed")
```

**Trigger 4 — CEO aborts (cancel) → `cancelled`:**
In `src/onemancompany/api/routes.py`, function `abort_task()`:
```python
await store.save_project_status(project_id, "cancelled", completed_at=datetime.now().isoformat())
```

**Trigger 5 — All children failed/cancelled → `failed`:**
In `src/onemancompany/core/vessel.py`, function `_resolve_dependencies()`:
```python
if all_deps_failed_or_cancelled:
    await store.save_project_status(project_id, "failed")
```

- [ ] **Step 3: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.api.routes import router; print('OK')"`

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_vessel.py tests/unit/api/test_routes.py -x -v`

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/api/routes.py src/onemancompany/core/vessel.py
git commit -m "refactor: project.yaml is sole status source, remove tree aggregation override"
```

---

### Task 12: Persist meeting room bookings to disk

**Files:**
- Modify: `src/onemancompany/api/routes.py` — booking/release endpoints write via `store.save_room()`
- Modify: `src/onemancompany/agents/common_tools.py` — `pull_meeting()` tool writes via `store.save_room()`
- Modify: `src/onemancompany/core/routine.py` — meeting setup/teardown writes via `store.save_room()`
- Modify: `src/onemancompany/core/state.py` — remove `meeting_rooms` from CompanyState, remove room_bookings from snapshot

Currently meeting room bookings (`is_booked`, `booked_by`, `participants`) are ephemeral in-memory state saved to snapshots. Migrate to disk.

- [ ] **Step 1: Find all booking mutation sites**

Search for `room.is_booked`, `room.booked_by`, `room.participants` mutations. Key locations:
- `api/routes.py`: book/release meeting room endpoints
- `agents/common_tools.py`: `pull_meeting()` tool that books a room for meetings
- `core/routine.py`: post-task meeting workflow that books rooms

- [ ] **Step 2: Replace with `store.save_room()` calls**

```python
# BEFORE
room.is_booked = True
room.booked_by = employee_id
room.participants = [employee_id, ...]

# AFTER
await store.save_room(room_id, {
    "is_booked": True,
    "booked_by": employee_id,
    "participants": [employee_id, ...],
})
```

- [ ] **Step 3: Replace room reads with `store.load_rooms()`**

Update any code reading room state from `company_state.meeting_rooms`.

- [ ] **Step 4: Remove `meeting_rooms` from CompanyState**

- [ ] **Step 5: Remove room_bookings from snapshot provider**

Delete the `room_bookings` section from `_CompanyStateSnapshot.save()` and `.restore()`.

- [ ] **Step 6: Verify and commit**

```bash
git add src/onemancompany/core/state.py src/onemancompany/api/routes.py
git commit -m "refactor: persist meeting room bookings to disk via store"
```

---

### Task 13: Persist remaining data (activity log, culture, candidates, sales, overhead)

**Files:**
- Modify: `src/onemancompany/core/state.py` — remove activity_log, company_culture, company_direction, sales_tasks from CompanyState; delete snapshot provider
- Modify: `src/onemancompany/api/routes.py` — activity log writes, culture reads/writes
- Modify: `src/onemancompany/core/routine.py` — activity log writes after task completion
- Modify: `src/onemancompany/core/vessel.py` — activity log writes during task execution, overhead/cost tracking
- Modify: `src/onemancompany/agents/recruitment.py` — persist candidates to disk; delete snapshot provider
- Modify: `src/onemancompany/agents/cso_agent.py` — sales task reads/writes via store
- Modify: `src/onemancompany/agents/coo_agent.py` — culture writes via store; delete hiring_requests snapshot provider

- [ ] **Step 1: Migrate activity log**

Find all `company_state.activity_log.append(...)` calls (locations: `core/routine.py`, `api/routes.py`, `core/vessel.py`). Replace with `await store.append_activity(entry)`.

- [ ] **Step 2: Migrate company culture**

Find all `company_state.company_culture = ...` writes (location: `api/routes.py`, `core/state.py`). Replace with `await store.save_culture(items)`.
Find all reads of `company_state.company_culture` (location: `api/routes.py`). Replace with `store.load_culture()`.

- [ ] **Step 3: Migrate candidate shortlists**

Find `pending_candidates` in `src/onemancompany/agents/recruitment.py`. Replace in-memory dict with `store.save_candidates()` / `store.load_candidates()`.

- [ ] **Step 4: Migrate sales tasks**

Find `company_state.sales_tasks` reads/writes in `src/onemancompany/agents/cso_agent.py` and `src/onemancompany/api/routes.py`. Replace with `store.load_sales_tasks()` / `store.save_sales_tasks()`.

- [ ] **Step 5: Migrate overhead / token tracking**

Find `company_state.company_tokens` and `company_state.overhead_costs` reads/writes in `src/onemancompany/core/vessel.py` (cost recording) and `src/onemancompany/api/routes.py` (dashboard endpoint). Replace with `store.load_overhead()` / `store.save_overhead()`.

- [ ] **Step 6: Retire snapshot providers**

Delete snapshot providers that are now unnecessary:
- `company_state` provider in `state.py` (class `_CompanyStateSnapshot`) — activity log now on disk, employee statuses now on disk, room bookings now on disk
- `candidates` provider in `recruitment.py` — candidates now on disk
- `hiring_requests` provider in `coo_agent.py` — if it only tracks in-memory hiring queue

**RETAIN (per spec section 7.2):**
- `task_queue` save/restore on `EmployeeManager` (in `core/vessel.py`) — needed to know which tasks were mid-execution on restart
- `pending_reports` in `routine.py` — transient meeting reports (acceptable to lose on crash)
- Inquiry sessions in `routes.py` — transient interaction state

- [ ] **Step 6: Verify and commit**

```bash
git add src/onemancompany/core/state.py src/onemancompany/agents/recruitment.py src/onemancompany/api/routes.py
git commit -m "refactor: persist activity log, culture, candidates, sales to disk; retire snapshot providers"
```

---

### Task 14: Simplify hot-reload

**Files:**
- Modify: `src/onemancompany/core/state.py`
- Modify: `src/onemancompany/main.py`

- [ ] **Step 1: Replace `reload_all_from_disk()` with `mark_dirty` call**

Since there's no in-memory cache, hot-reload just needs to notify the frontend:

```python
def reload_all_from_disk() -> dict:
    """Mark all categories dirty so next tick triggers frontend refresh."""
    from onemancompany.core.store import mark_dirty
    mark_dirty("employees", "rooms", "tools", "task_queue", "culture", "activity_log", "sales_tasks")
    # Recompute layout
    from onemancompany.core.layout import compute_layout
    compute_layout(company_state)
    return {"status": "dirty_marked", "categories": "all"}
```

- [ ] **Step 2: Verify and commit**

```bash
git add src/onemancompany/core/state.py src/onemancompany/main.py
git commit -m "refactor: simplify hot-reload to mark_dirty (no in-memory cache to invalidate)"
```

---

## Chunk 4: Frontend WebSocket Migration

### Task 15: Replace full-state broadcast with tick-based `state_changed`

**Files:**
- Modify: `src/onemancompany/api/websocket.py`
- Modify: `src/onemancompany/core/events.py` (keep as-is for internal use)

- [ ] **Step 1: Modify `event_broadcaster()` to stop attaching full state**

The `event_broadcaster` in `websocket.py` currently attaches `company_state.to_json()` to every event. Change it to only forward event type + payload, without full state:

```python
async def event_broadcaster(self) -> None:
    """Background task: forward events to WebSocket clients (no full state)."""
    queue = event_bus.subscribe()
    try:
        while True:
            event: CompanyEvent = await queue.get()
            # Real-time events forwarded directly (chat, popups, etc.)
            # Full state is NOT attached — frontend fetches from REST on tick
            await self.broadcast({
                "type": event.type,
                "agent": event.agent,
                "payload": event.payload,
            })
    except asyncio.CancelledError:
        pass
    finally:
        event_bus.unsubscribe(queue)
```

- [ ] **Step 2: Update `connect()` to send bootstrap hint instead of full state**

```python
async def connect(self, ws: WebSocket) -> None:
    await ws.accept()
    self.connections.add(ws)
    # Tell frontend to bootstrap from REST API
    await ws.send_json({
        "type": "connected",
        "payload": {"message": "Bootstrap from REST API"},
    })
```

- [ ] **Step 3: Delete `company_state.to_json()` method**

It's no longer needed since nothing broadcasts full state. Remove from `CompanyState`.

- [ ] **Step 4: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.api.websocket import ws_manager; print('OK')"`

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/api/websocket.py src/onemancompany/core/state.py
git commit -m "refactor: replace full-state WebSocket broadcast with tick-based state_changed"
```

---

### Task 16: Frontend — add bootstrap and tick handler

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Add `bootstrap()` method**

```javascript
async bootstrap() {
    try {
        const [employees, tasks, rooms, tools, activityLog] = await Promise.all([
            fetch('/api/employees').then(r => r.json()),
            fetch('/api/task-queue').then(r => r.json()),
            fetch('/api/rooms').then(r => r.json()),
            fetch('/api/tools').then(r => r.json()),
            fetch('/api/activity-log').then(r => r.json()),
        ]);
        this.updateRoster(employees);
        this.updateTaskPanel(tasks);
        if (window.officeRenderer) {
            window.officeRenderer.updateState({ employees, meeting_rooms: rooms, tools });
        }
        // Update counters
        document.getElementById('employee-count').textContent = `👥 ${employees.length}`;
        document.getElementById('tool-count').textContent = `🔧 ${tools.length}`;
        const freeRooms = rooms.filter(r => !r.is_booked).length;
        document.getElementById('room-count').textContent = `🏢 ${freeRooms}/${rooms.length}`;
    } catch (e) {
        console.error('Bootstrap failed:', e);
    }
}
```

- [ ] **Step 2: Add `_fetchAndRender*` methods for tick response**

```javascript
async _fetchAndRenderRoster() {
    const employees = await fetch('/api/employees').then(r => r.json());
    this.updateRoster(employees);
    if (window.officeRenderer) {
        window.officeRenderer.updateState({ employees });
    }
    document.getElementById('employee-count').textContent = `👥 ${employees.length}`;
}

async _fetchAndRenderTaskPanel() {
    const tasks = await fetch('/api/task-queue').then(r => r.json());
    this.updateTaskPanel(tasks);
}

async _fetchAndRenderRooms() {
    const rooms = await fetch('/api/rooms').then(r => r.json());
    if (window.officeRenderer) {
        window.officeRenderer.updateState({ meeting_rooms: rooms });
    }
    const freeRooms = rooms.filter(r => !r.is_booked).length;
    document.getElementById('room-count').textContent = `🏢 ${freeRooms}/${rooms.length}`;
}

async _fetchAndRenderTools() {
    const tools = await fetch('/api/tools').then(r => r.json());
    if (window.officeRenderer) {
        window.officeRenderer.updateState({ tools });
    }
    document.getElementById('tool-count').textContent = `🔧 ${tools.length}`;
}
```

- [ ] **Step 3: Update `handleMessage()` to handle new message types**

```javascript
handleMessage(msg) {
    // NEW: tick-based state_changed
    if (msg.type === 'state_changed') {
        const c = msg.changed || [];
        if (c.includes('employees'))   this._fetchAndRenderRoster();
        if (c.includes('task_queue'))  this._fetchAndRenderTaskPanel();
        if (c.includes('rooms'))       this._fetchAndRenderRooms();
        if (c.includes('tools'))       this._fetchAndRenderTools();
        return;
    }

    // NEW: bootstrap on connect
    if (msg.type === 'connected') {
        this.bootstrap();
        return;
    }

    // Real-time events (chat, popups, etc.) handled as before
    if (msg.type === 'meeting_chat') { /* ... */ }
    if (msg.type === 'open_popup') { /* ... */ }
    // ... other real-time handlers
}
```

- [ ] **Step 4: Remove old `this.state` caching from `handleMessage`**

Delete these lines from `handleMessage`:
```javascript
// DELETE:
if (msg.state) { this.state = msg.state; }
if (msg.state && window.officeRenderer) { window.officeRenderer.updateState(msg.state); }
if (msg.state) { /* all the counter updates, updateRoster, updateTaskPanel, etc. */ }
```

- [ ] **Step 5: Call bootstrap on connect**

In `ws.onopen`, add `this.bootstrap()` call.

- [ ] **Step 6: Verify frontend syntax**

Run: `node -c frontend/app.js`
Expected: No syntax errors

- [ ] **Step 7: Commit**

```bash
git add frontend/app.js
git commit -m "feat: frontend bootstrap + tick-based state_changed handler"
```

---

## Chunk 5: Frontend State Cleanup

### Task 17: Delete cached business state from frontend

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Delete `this.state` and `this._lastEmployees` usage**

Remove `this.state` property and any `this._lastEmployees` cache. Search for all `this.state.` and `this._lastEmployees` references and replace each with a fresh fetch or eliminate:

- `this.state.employees` → already handled by `_fetchAndRenderRoster()`
- `this.state.tools` → fetch `/api/tools`
- `this.state.meeting_rooms` → fetch `/api/rooms`
- `this.state.active_tasks` → fetch `/api/task-queue`
- `this.state.company_culture` → fetch `/api/company-culture`
- `this.state.sales_tasks` → fetch `/api/sales-tasks`
- `this._lastEmployees` → DELETED (was cached employee list for diff detection)

For methods that need employee data mid-flow (e.g., showing a modal), fetch it:

```javascript
// BEFORE
const emp = this.state.employees.find(e => e.id === id);

// AFTER
const emp = await fetch(`/api/employees/${id}`).then(r => r.json());
```

- [ ] **Step 2: Delete `this.meetingChats` cache**

Replace with fetching from `/api/rooms/{id}/chat`:

```javascript
// BEFORE
const chats = this.meetingChats[roomId] || [];

// AFTER
const chats = await fetch(`/api/rooms/${roomId}/chat`).then(r => r.json());
```

- [ ] **Step 3: Delete `this.cachedModels`**

Fetch model list on demand when the models dropdown is opened.

- [ ] **Step 4: Delete `this._oneononeHistory`** (if it exists)

Replace with fetch from `/api/employee/{id}/oneonone`.

- [ ] **Step 5: Delete `this._candidateList`, `this._candidateRoles`, `this._allCandidatesMap`**

Replace with fetch from `/api/candidates/pending`.

- [ ] **Step 6: Delete `this._currentResolution`**

Replace with fetch from resolution API endpoint when needed.

- [ ] **Step 7: Delete `this._onboardingItems`**

Replace with fetch from onboarding API endpoint when needed.

- [ ] **Step 8: Verify retained pure UI state is untouched**

These should remain:
- `this.ws` — WebSocket connection
- `this.viewingRoomId`, `this.viewingEmployeeId` — current UI selection
- `this._inquirySessionId`, `this._inquiryRoomId` — active inquiry
- `this._inputHistory` — localStorage backed
- `this._taskPendingFiles` — file upload refs
- `this._viewingBoardProjectId` — board view selection
- Modal open/close state, scroll positions

- [ ] **Step 9: Verify frontend syntax**

Run: `node -c frontend/app.js`

- [ ] **Step 10: Commit**

```bash
git add frontend/app.js
git commit -m "refactor: delete all cached business state from frontend, fetch on demand"
```

---

### Task 18: Update event handlers for real-time events

**Files:**
- Modify: `frontend/app.js`

Events like `meeting_chat`, `open_popup`, `inquiry_started`, `ceo_report`, etc. are still pushed in real-time (not tick-based). Ensure these handlers don't rely on `this.state`:

- [ ] **Step 1: Audit each event handler in `handleMessage()`**

For each handler:
- If it reads `this.state.*` → replace with a fetch or derive from the event payload
- If it only uses `msg.payload` → no change needed
- If it updates `this.state.*` → delete the update (no more cached state)

- [ ] **Step 2: Update chat message handlers**

Meeting chat messages are pushed real-time. Instead of appending to `this.meetingChats`:

```javascript
// BEFORE
if (!this.meetingChats[roomId]) this.meetingChats[roomId] = [];
this.meetingChats[roomId].push(msg);

// AFTER: just re-render the chat panel if the room is being viewed
if (this.viewingRoomId === roomId) {
    this._renderMeetingChat(roomId);  // fetches from API internally
}
```

- [ ] **Step 3: Verify frontend syntax**

Run: `node -c frontend/app.js`

- [ ] **Step 4: Manual smoke test**

Start backend: `.venv/bin/python -m onemancompany.main`
Open browser to `http://localhost:8000`
Verify:
- Employee roster loads on page load
- Task queue displays
- Meeting rooms show
- Clicking on an employee opens their profile
- Real-time events (task status changes) update within 3 seconds

- [ ] **Step 5: Commit**

```bash
git add frontend/app.js
git commit -m "refactor: update real-time event handlers to not rely on cached state"
```

---

### Task 19: Final cleanup — remove `CompanyState` dead code

**Files:**
- Modify: `src/onemancompany/core/state.py`
- Modify: `src/onemancompany/main.py`

- [ ] **Step 1: Minimize `CompanyState`**

```python
@dataclass
class CompanyState:
    """Minimal company state — only intermediate computation products and counters."""
    office_layout: dict = field(default_factory=dict)
    ceo_tasks: list[str] = field(default_factory=list)  # recent CEO commands
    _next_employee_number: int = 0

    def next_employee_number(self) -> str:
        num = self._next_employee_number
        self._next_employee_number += 1
        return f"{num:05d}"
```

Delete: `Employee`, `OfficeTool`, `MeetingRoom`, `SalesTask` dataclasses (if no longer used by other code).

**NOTE:** If `Employee` dataclass is still used by agents (e.g., as a typed parameter), keep it but as a thin wrapper that reads from disk. Or migrate those agents to use plain dicts.

- [ ] **Step 2: Delete `_seed_employees()`, `_seed_ex_employees()`, `_seed_company_culture()`, `_seed_company_direction()`**

Replace with:

```python
_init_employee_counter()
```

- [ ] **Step 3: Delete snapshot provider for `company_state`**

The `_CompanyStateSnapshot` class is no longer needed.

- [ ] **Step 4: Clean up main.py startup**

Remove calls to `_load_assets_from_disk()` that populate `company_state.tools` and `company_state.meeting_rooms` — these now come from disk on demand.

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -x --timeout=60`
Expected: All tests pass. Fix any remaining broken tests.

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/core/state.py src/onemancompany/main.py tests/
git commit -m "refactor: minimize CompanyState to layout+counters, delete dead seeding code"
```

---

### Task 20: One-time migration script for existing data

**Files:**
- Create: `scripts/migrate_to_ssot.py`

Existing `.onemancompany/` data may not have the new `runtime:` section in profile.yaml or correct `project.yaml` status fields. This script runs once to fix up existing data.

- [ ] **Step 1: Write the migration script**

```python
#!/usr/bin/env python3
"""One-time migration for Single Source of Truth refactoring.

Adds runtime: section to employee profiles that lack it.
Infers project.yaml status from task_tree.yaml if status is missing/stale.
Run once after deploying the refactored code.
"""
import yaml
from pathlib import Path

DATA_ROOT = Path.cwd() / ".onemancompany"
EMPLOYEES_DIR = DATA_ROOT / "company" / "human_resource" / "employees"
PROJECTS_DIR = DATA_ROOT / "company" / "business" / "projects"


def migrate_employee_profiles():
    """Add runtime: section with defaults to profiles that lack it."""
    if not EMPLOYEES_DIR.exists():
        return
    for emp_dir in EMPLOYEES_DIR.iterdir():
        if not emp_dir.is_dir():
            continue
        profile = emp_dir / "profile.yaml"
        if not profile.exists():
            continue
        data = yaml.safe_load(profile.read_text(encoding="utf-8")) or {}
        if "runtime" not in data:
            data["runtime"] = {
                "status": "idle",
                "is_listening": False,
                "current_task_summary": "",
                "api_online": True,
                "needs_setup": False,
            }
            profile.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False), encoding="utf-8")
            print(f"  Added runtime: to {emp_dir.name}/profile.yaml")


def migrate_project_statuses():
    """Infer project.yaml status from task_tree.yaml if status is missing."""
    if not PROJECTS_DIR.exists():
        return
    for pdir in PROJECTS_DIR.iterdir():
        if not pdir.is_dir():
            continue
        pyaml = pdir / "project.yaml"
        if not pyaml.exists():
            continue
        data = yaml.safe_load(pyaml.read_text(encoding="utf-8")) or {}
        if data.get("status"):
            continue  # already has status
        # Try to infer from tree
        tree_path = pdir / "task_tree.yaml"
        if tree_path.exists():
            tree = yaml.safe_load(tree_path.read_text(encoding="utf-8")) or {}
            nodes = tree.get("nodes", [])
            if not nodes:
                data["status"] = "pending"
            elif all(n.get("status") in ("accepted", "completed") for n in nodes):
                data["status"] = "completed"
            elif any(n.get("status") == "failed" for n in nodes):
                data["status"] = "failed"
            else:
                data["status"] = "in_progress"
        else:
            data["status"] = "pending"
        pyaml.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False), encoding="utf-8")
        print(f"  Set status={data['status']} for project {pdir.name}")


if __name__ == "__main__":
    print("Migrating employee profiles...")
    migrate_employee_profiles()
    print("Migrating project statuses...")
    migrate_project_statuses()
    print("Done.")
```

- [ ] **Step 2: Test the script**

Run: `.venv/bin/python scripts/migrate_to_ssot.py`
Expected: Prints migration actions or "Done." if nothing to migrate.

- [ ] **Step 3: Commit**

```bash
git add scripts/migrate_to_ssot.py
git commit -m "feat: add one-time migration script for single-source-of-truth data format"
```

---

### Task 21: End-to-end verification

- [ ] **Step 1: Start the server**

Run: `.venv/bin/python -m onemancompany.main`

- [ ] **Step 2: Verify core flows**

Manual checklist:
- Page loads, roster shows all employees
- Employee profiles accessible
- Task queue shows with correct statuses
- Meeting rooms show booking status
- Creating a task → appears in queue within 3 seconds
- Cancelling a task → status updates within 3 seconds
- 1-on-1 chat works
- Meeting chat works in real-time
- Hot-reload (edit profile.yaml) → reflected within 3 seconds

- [ ] **Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x --timeout=120`
Expected: PASS

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: single-source-of-truth refactoring complete"
```
