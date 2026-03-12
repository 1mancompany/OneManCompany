# Single Source of Truth Refactoring — Design Spec

## Core Principles

1. **Disk is the only source of truth.** All business data lives in `.onemancompany/` files. Writes go to disk immediately.
2. **Memory holds only intermediate computation products.** No caching of business data in memory. Need data? Read from disk.
3. **Every piece of data has exactly one file that owns it** and exactly one write function.
4. **Frontend is a pure render layer.** No local business state. Fetches data from API on demand.
5. **Frontend-backend sync runs on 3-second tick frames.** Backend accumulates a dirty set; every 3 seconds broadcasts what changed; frontend re-fetches those resources. Dirty set is transient coordination state (not business data) — loss on crash is acceptable as the next tick rebuilds it.

---

## 1. Data Ownership Table

| Data | Sole Owner File | Write Function | Eliminated Duplicates |
|------|----------------|----------------|----------------------|
| Employee profile (name, role, skills, etc.) | `employees/{id}/profile.yaml` | `store.save_employee()` | `employee_configs` cache dict in config.py |
| Employee runtime (status, is_listening, task_summary, api_online) | `employees/{id}/profile.yaml` → `runtime:` section | `store.save_employee_runtime()` | Pure in-memory fields on Employee dataclass |
| Ex-employee record | `ex-employees/{id}/profile.yaml` | `store.save_ex_employee()` | In-memory `company_state.ex_employees` dict |
| Project status | `projects/{id}/project.yaml` → `status` field | `store.save_project_status()` | Tree aggregation override in `_aggregate_tree_status()` |
| Task tree nodes | `{project_dir}/task_tree.yaml` | `store.save_tree()` | None (already single source) |
| Per-employee task | `employees/{id}/tasks/{id}.yaml` | `persist_task()` (existing) | None (already single source) |
| Task cost | `employees/{id}/tasks/{id}.yaml` | `persist_task()` (existing) | project iteration `cost.breakdown[]` copy — change to aggregate on read |
| Meeting room state (including bookings) | `assets/rooms/{id}.yaml` | `store.save_room()` | Pure in-memory booking fields (is_booked, booked_by, participants) |
| Meeting chat history | `assets/rooms/{id}_chat.yaml` (new) | `store.append_room_chat()` | Frontend-only `meetingChats[]` dict |
| Office tools | `assets/tools/{slug}/tool.yaml` | `store.save_tool()` | Stale in-memory entries after disk deletion |
| Guidance notes | `employees/{id}/guidance.yaml` | `store.save_guidance()` | In-memory copy on Employee.guidance_notes |
| Work principles | `employees/{id}/work_principles.md` | `store.save_work_principles()` | In-memory copy on Employee.work_principles |
| 1-on-1 chat history | `employees/{id}/oneonone_history.yaml` (new) | `store.append_oneonone()` | Frontend-only `_oneononeHistory[]` |
| Candidate shortlists | `candidates/{batch_id}.yaml` (new) | `store.save_candidates()` | In-memory `pending_candidates` dict in recruitment.py |
| Sales tasks | `sales/tasks.yaml` (new) | `store.save_sales_tasks()` | In-memory `company_state.sales_tasks` dict |
| Activity log | `activity_log.yaml` (new) | `store.append_activity()` | In-memory `company_state.activity_log` list |
| Company culture | `company_culture.yaml` (existing) | `store.save_culture()` | In-memory `company_state.company_culture` |
| Company direction | `company_direction.yaml` (existing) | `store.save_direction()` | In-memory `company_state.company_direction` |
| Token usage / overhead | `overhead.yaml` (new) | `store.save_overhead()` | In-memory `company_state.company_tokens`, `overhead_costs` |

---

## 2. Unified Write Layer — `core/store.py`

New module. All persistent writes go through here. Each function:
1. Acquires a per-file lock (prevents concurrent read-modify-write races)
2. Writes to disk (YAML) immediately
3. Marks the relevant resource category as dirty for the next sync tick
4. Does NOT update any in-memory cache

### 2.1 Concurrency Control

Backend is async (FastAPI + asyncio). Multiple coroutines can write the same file (e.g., heartbeat updating `api_online` while task updates `status`). Each file gets its own `asyncio.Lock`:

```python
_file_locks: dict[str, asyncio.Lock] = {}

def _get_lock(file_path: str) -> asyncio.Lock:
    if file_path not in _file_locks:
        _file_locks[file_path] = asyncio.Lock()
    return _file_locks[file_path]

async def save_employee_runtime(emp_id: str, **fields) -> None:
    path = _employee_profile_path(emp_id)
    async with _get_lock(str(path)):
        data = _read_yaml(path)
        runtime = data.setdefault("runtime", {})
        runtime.update(fields)
        _write_yaml(path, data)
    mark_dirty("employees")
```

### 2.2 Dirty Tracking

```python
_dirty: set[str] = set()

def mark_dirty(*categories: str) -> None:
    _dirty.update(categories)

def flush_dirty() -> list[str]:
    """Called by sync tick. Returns and clears dirty set."""
    changed = list(_dirty)
    _dirty.clear()
    return changed
```

### 2.3 Write Functions

```python
# --- Employee ---
async def save_employee(emp_id: str, updates: dict) -> None:
    """Merge updates into profile.yaml, write immediately."""

async def save_employee_runtime(emp_id: str, **fields) -> None:
    """Update runtime: section of profile.yaml."""

async def save_ex_employee(emp_id: str, data: dict) -> None:
    """Write ex-employee profile."""

# --- Project ---
async def save_project_status(project_id: str, status: str, **extra) -> None:
    """Update project.yaml status field."""

# --- Room ---
async def save_room(room_id: str, updates: dict) -> None:
    """Update room yaml (including booking state)."""

async def append_room_chat(room_id: str, message: dict) -> None:
    """Append chat message to room chat file."""

# --- Tree ---
async def save_tree(project_dir: str, tree) -> None:
    """Write task_tree.yaml."""

# --- Candidates ---
async def save_candidates(batch_id: str, data: dict) -> None:
    """Persist candidate shortlist to disk."""

# --- Company-level ---
async def append_activity(entry: dict) -> None:
    """Append to activity_log.yaml."""

async def save_culture(items: list[dict]) -> None:
    """Write company_culture.yaml."""

async def save_direction(text: str) -> None:
    """Write company_direction.yaml."""

async def save_overhead(data: dict) -> None:
    """Write overhead.yaml."""

async def save_sales_tasks(tasks: dict) -> None:
    """Write sales/tasks.yaml."""
```

---

## 3. Read Layer — Always From Disk

Every API call reads from disk. No in-memory cache. Every 3-second tick, the frontend re-fetches changed resources — those fetches hit the API which reads from disk.

### 3.1 Read Functions

```python
def load_employee(emp_id: str) -> dict:
    """Read profile.yaml, return full employee dict including runtime."""

def load_all_employees() -> dict[str, dict]:
    """Read all employee profile.yamls from disk."""

def load_ex_employees() -> dict[str, dict]:
    """Read all ex-employee profile.yamls."""

def load_project(project_id: str) -> dict:
    """Read project.yaml."""

def load_rooms() -> list[dict]:
    """Read all room yamls (including booking state)."""

def load_room_chat(room_id: str) -> list[dict]:
    """Read room chat history file."""

def load_tools() -> list[dict]:
    """Read all tool yamls from assets/tools/."""

def load_candidates(batch_id: str) -> dict:
    """Read candidate shortlist from disk."""

def load_activity_log() -> list[dict]:
    """Read activity_log.yaml."""

def load_sales_tasks() -> dict:
    """Read sales/tasks.yaml."""
```

### 3.2 What Happens to `company_state`

`CompanyState` singleton is **eliminated or reduced to**:
- `office_layout` — computed layout data (intermediate product, recomputed from employee desk positions on tick)
- `_next_employee_number` — counter derived from disk employee folders on startup
- Transient interaction state (active inquiry sessions, CEO chat context) — not business data, acceptable to lose on restart

Everything else is read from disk via `store.load_*()`.

### 3.3 Performance

Reading YAML from disk every API call:
- Employee count: <50, profile.yaml: <1KB each — total <50KB
- Project count: <100, project.yaml: <2KB each
- Room count: <10, tool count: <20
- SSD small file I/O: <1ms per file

No cache needed. If scale increases, revisit.

---

## 4. Sync Tick — 3-Second Frame

### 4.1 Backend Tick Loop

```python
async def sync_tick():
    """Runs every 3 seconds. Broadcasts dirty categories to all WebSocket clients."""
    changed = store.flush_dirty()
    if changed:
        await ws_broadcast({"type": "state_changed", "changed": changed})
```

Tick is started in the FastAPI lifespan as a background `asyncio.Task`.

### 4.2 Event Bus Transition

**Current model:** `event_bus.publish(CompanyEvent(...))` → `websocket.py` calls `company_state.to_json()` → broadcasts full state to all clients on every single event.

**New model:**
- `event_bus` is **retained** for internal backend coordination (agent-to-agent communication, task lifecycle hooks)
- Event handlers that currently broadcast to WebSocket are **replaced** by `store.mark_dirty()` calls
- The only WebSocket broadcast path is the 3-second tick
- **Exception:** Real-time chat messages (meeting chat, 1-on-1, inquiry) are still pushed immediately via WebSocket for low-latency UX. These are fire-and-forget notifications; the data is already persisted to disk by the time they're sent.

```python
# BEFORE: every event broadcasts full state
async def _on_event(event):
    await ws_broadcast(company_state.to_json())

# AFTER: events mark dirty, tick broadcasts
async def _on_employee_changed(event):
    store.mark_dirty("employees")

# Chat is still real-time
async def _on_chat_message(event):
    await store.append_room_chat(room_id, msg)  # persist first
    await ws_broadcast({"type": "chat_message", "payload": msg})  # then notify
```

### 4.3 Frontend Bootstrap (Initial Load)

When the page loads or WebSocket reconnects, frontend fetches all resources:

```javascript
async bootstrap() {
    const [employees, tasks, rooms, tools] = await Promise.all([
        fetch('/api/employees').then(r => r.json()),
        fetch('/api/task-queue').then(r => r.json()),
        fetch('/api/rooms').then(r => r.json()),
        fetch('/api/tools').then(r => r.json()),
    ]);
    this.updateRoster(employees);
    this._renderTaskPanel(tasks);
    this.office.updateState({ employees, rooms, tools });
}
```

No `state_snapshot` message needed. Frontend bootstraps from REST, then stays updated via tick notifications.

### 4.4 Frontend Handler

```javascript
ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);

    if (data.type === 'state_changed') {
        const c = data.changed;
        if (c.includes('employees'))  this._fetchAndRenderRoster();
        if (c.includes('task_queue')) this._fetchAndRenderTaskPanel();
        if (c.includes('rooms'))      this._fetchAndRenderRooms();
        if (c.includes('tools'))      this._fetchAndRenderTools();
    }

    // Real-time messages (chat, errors) handled directly
    if (data.type === 'chat_message') this._appendChatMessage(data.payload);
};
```

---

## 5. Eliminated Frontend State

| Variable | Current Purpose | After |
|----------|----------------|-------|
| `this.state` | Full snapshot cache | DELETED — components fetch independently |
| `this._lastEmployees` | Cached employee list | DELETED — fetch `/api/employees` |
| `this._candidateList` | Cached candidates | DELETED — fetch `/api/candidates/pending` |
| `this._candidateRoles` | Cached role groups | DELETED — included in API response |
| `this._allCandidatesMap` | Derived lookup | DELETED — derived in render function |
| `this.meetingChats[]` | Chat history per room | DELETED — fetch `/api/rooms/{id}/chat` |
| `this._oneononeHistory` | 1-on-1 conversation | DELETED — fetch `/api/employee/{id}/oneonone` |
| `this.cachedModels` | OpenRouter model list | DELETED — fetch each time |
| `this._currentResolution` | Current resolution | DELETED — fetch from API |
| `this._onboardingItems` | Onboarding progress | DELETED — fetch from API |

**Retained (pure UI state):**
- Modal open/close state, selected IDs, scroll positions
- Input history (localStorage)
- D3 renderer instance, canvas animation state
- File upload refs, pending form data

---

## 6. Project Status — Single Source

### 6.1 Remove Tree Aggregation Override

Current `get_task_queue()`:
```python
# CURRENT: tree status overrides project.yaml
file_status = project.yaml["status"]
tree_status = _aggregate_tree_status(tree)
status = tree_status if tree_status else file_status
```

After:
```python
# AFTER: project.yaml is the only source
status = project.yaml["status"]
```

`_aggregate_tree_status()` is deleted.

### 6.2 Automatic project.yaml Status Updates

| Event | New Status | Trigger Point |
|-------|-----------|---------------|
| EA creates project dispatch | `in_progress` | `project_archive.create_iteration()` |
| Tree root node accepted | `completed` | `vessel._on_child_complete()` → `store.save_project_status()` |
| Tree root node failed | `failed` | `vessel._on_child_complete()` → `store.save_project_status()` |
| CEO aborts (cancel) | `cancelled` | `routes.abort_task()` → `store.save_project_status()` |
| All children failed/cancelled | `failed` | dep resolution in `_resolve_dependencies()` |

---

## 7. Snapshot System

### 7.1 Current State

`core/snapshot.py` provides `@snapshot_provider` decorators for saving/restoring in-memory state during graceful restart. Used by ~7 providers (company_state, recruitment, routes, etc.).

### 7.2 After Refactoring

Since disk is the source of truth, most snapshot providers become **unnecessary**:
- `company_state` provider → DELETED (rebuild from disk)
- `recruitment` provider (pending_candidates) → DELETED (candidates persisted to disk)
- `routes` provider → DELETED (no in-memory state to save)

**Retained:**
- `task_queue` save/restore for `EmployeeManager._running_tasks` — needed to know which tasks were mid-execution on restart. These are re-dispatched from per-employee task YAMLs.

### 7.3 Hot-Reload Simplification

Current `reload_all_from_disk()` does field-by-field diff comparison (100+ lines). After refactoring, hot-reload is simply:
- Re-read disk files on next tick
- Since there's no in-memory cache to invalidate, "reload" is a no-op — the next API read automatically gets fresh data
- The only thing hot-reload needs to do is `store.mark_dirty("employees", "rooms", "tools")` to trigger a frontend refresh

---

## 8. Agent Code Migration

### 8.1 Call Sites Using `company_state.employees`

These files read or write `company_state.employees` and must be migrated:

**Read-only (change to `store.load_employee()` / `store.load_all_employees()`):**
- `agents/tree_tools.py` — validates employee exists
- `agents/common_tools.py` — `list_colleagues()` reads employee list
- `agents/coo_agent.py` — reads employee data for ops
- `agents/cso_agent.py` — reads employee data
- `agents/ea_agent.py` — reads employee data for routing
- `core/routine.py` — iterates employees for scheduling
- `core/heartbeat.py` — checks api_online status
- `api/routes.py` — multiple GET endpoints

**Read-write (change reads to `store.load_*()`, writes to `store.save_*()`):**
- `core/state.py` — `_seed_employees()`, `reload_all_from_disk()` → simplified/deleted
- `agents/onboarding.py` — `execute_hire()` creates employee → `store.save_employee()`
- `agents/hr_agent.py` — reviews, PIP → `store.save_employee()`
- `agents/termination.py` — fires employee → `store.save_ex_employee()`
- `core/vessel.py` — updates employee status/task_summary → `store.save_employee_runtime()`
- `api/routes.py` — PUT endpoints for employee config → `store.save_employee()`

### 8.2 Migration Strategy

1. Create `store.py` with all read/write functions
2. Replace `company_state.employees[id]` reads with `store.load_employee(id)` — mechanical replacement
3. Replace `company_state.employees[id] = Employee(...)` writes with `store.save_employee(id, data)` — requires extracting the data dict
4. Replace `emp.status = "working"` in-memory mutations with `store.save_employee_runtime(id, status="working")`
5. Delete `_seed_employees()`, `employee_configs`, and `reload_all_from_disk()` employee section

---

## 9. Implementation Phases

### Phase 1a: Infrastructure
1. Create `core/store.py` — file locks, dirty tracking, read/write functions
2. Add sync tick loop (3-second `asyncio.Task` in lifespan)
3. Add new API endpoints: `GET /api/employees`, `GET /api/rooms`, `GET /api/rooms/{id}/chat`

### Phase 1b: Employee Data Migration
4. Add `runtime:` section to profile.yaml schema
5. Migrate all `company_state.employees` read sites → `store.load_employee()`
6. Migrate all employee write sites → `store.save_employee()` / `store.save_employee_runtime()`
7. Delete `employee_configs` cache, simplify `_seed_employees()`

### Phase 1c: Project & Task Migration
8. Add `store.save_project_status()` calls at all status-change points (Section 6.2)
9. Remove `_aggregate_tree_status()` override in task queue endpoint
10. Persist candidate shortlists, meeting room bookings to disk

### Phase 1d: Remaining Data
11. Persist meeting chat, 1-on-1 history, activity log, sales tasks to disk
12. Eliminate snapshot providers (except task queue)
13. Simplify hot-reload

### Phase 2a: Frontend WebSocket
14. Replace `state_snapshot` broadcast with tick-based `state_changed`
15. Add `bootstrap()` on page load / reconnect
16. Remove `company_state.to_json()` full-state broadcast

### Phase 2b: Frontend State Cleanup
17. Delete `this.state`, `this._lastEmployees`, all cached business data
18. Replace with fetch-and-render pattern per resource
19. Task queue reads project.yaml status directly (no tree aggregation)

### Phase 2c: Frontend Chat & Candidates
20. Meeting chat: fetch from `/api/rooms/{id}/chat` instead of in-memory
21. 1-on-1: fetch from `/api/employee/{id}/oneonone` instead of in-memory
22. Candidates: fetch from `/api/candidates/pending` instead of in-memory

---

## 10. Migration Notes

- Existing `profile.yaml` without `runtime:` section → defaults (status=idle, is_listening=false)
- Existing `project.yaml` without correct status → one-time migration script infers from tree
- Frontend changes backward-compatible: new API endpoints return same data shape
- Real-time chat messages still pushed via WebSocket (persisted first, then notified)
- `company_state` singleton reduced to layout computation + counters only
