# Background Task System Design

## Goal

Add a system tool that allows agents to launch long-running bash processes (deployments, dev servers, watchers, etc.) in the background, with a global management UI for the CEO to monitor and control them.

## Architecture

### Overview

```
Agent Tool (start/check/stop)
        │
        ▼
BackgroundTaskManager (singleton)
  ├── asyncio.create_subprocess_exec
  ├── stdout/stderr → disk log
  ├── metadata → YAML persistence
  └── WebSocket event broadcast
        │
        ▼
Frontend Split Panel Modal
  ├── Left: task list (status-colored borders)
  └── Right: detail + XTermLog output viewer
```

### Components

1. **`src/onemancompany/core/background_tasks.py`** — `BackgroundTaskManager` singleton
2. **Agent tools** in `src/onemancompany/agents/common_tools.py` — 3 new tools
3. **API routes** in `src/onemancompany/api/routes.py` — REST endpoints
4. **Frontend modal** in `frontend/index.html` + `frontend/app.js`

## Data Model

```python
@dataclass
class BackgroundTask:
    id: str                    # 8-char hex from uuid4
    command: str               # shell command
    description: str           # agent's description of purpose
    working_dir: str           # execution directory
    started_by: str            # employee_id
    started_at: str            # ISO 8601 timestamp
    status: str                # running | completed | failed | stopped
    pid: int                   # OS process ID
    returncode: int | None     # exit code (None while running)
    ended_at: str | None       # ISO 8601 timestamp
    port: int | None           # auto-detected port
    address: str | None        # auto-detected address (e.g. http://localhost:3000)
```

### Persistence

- **Metadata**: `company/background_tasks.yaml` — list of all tasks (YAML, atomic write via tempfile + os.replace)
- **Output logs**: `company/background_tasks/{task_id}/output.log` — combined stdout+stderr
- **On startup recovery**: Tasks with `status: running` are marked as `stopped` (process no longer exists after restart)

## Concurrency Limit

- Global maximum: **5 concurrent running tasks**
- Exceeding the limit returns an error to the agent tool
- Completed/failed/stopped tasks do not count toward the limit

## Port & Address Detection

Two detection strategies, applied in order:

1. **Command argument scan**: regex match `--port[= ](\d+)`, `-p[= ](\d+)` from the command string
2. **Output scan**: regex match `https?://[\w.-]+:(\d+)`, `localhost:(\d+)`, `0\.0\.0\.0:(\d+)` from the first 50 lines of output

Detection runs once at startup (command scan) and periodically for the first 30 seconds (output scan, every 2s). Once a port is found, scanning stops.

## Agent Tools

Registered as **base** category (available to all employees). Tool descriptions emphasize "only use when necessary for long-running processes."

### `start_background_task`

```python
@tool
async def start_background_task(
    command: str,
    description: str,
    working_dir: str = "",
    employee_id: str = "",
) -> dict:
    """Start a long-running background process (e.g. deploy, dev server, watcher).

    ONLY use this for processes that need to keep running. For quick commands, use bash() instead.
    Returns task_id to check status or stop later. Max 5 concurrent tasks globally.
    """
```

Returns: `{"status": "ok", "task_id": "a1b2c3d4", "pid": 12345}` or `{"status": "error", "message": "..."}`

### `check_background_task`

```python
@tool
async def check_background_task(
    task_id: str,
    tail: int = 50,
    employee_id: str = "",
) -> dict:
    """Check status and recent output of a background task."""
```

Returns: `{"status": "running|completed|failed|stopped", "returncode": ..., "port": ..., "address": ..., "output_tail": "last N lines", "uptime_seconds": ...}`

### `stop_background_task`

```python
@tool
async def stop_background_task(
    task_id: str,
    employee_id: str = "",
) -> dict:
    """Stop a running background task. Sends SIGTERM, then SIGKILL after 10s."""
```

Returns: `{"status": "ok", "task_id": "..."}` or `{"status": "error", "message": "..."}`

## BackgroundTaskManager

### Singleton lifecycle

- Created at import time (like `tool_registry`)
- `start()` called at app startup — recovers state from YAML, marks stale `running` tasks as `stopped`
- `stop_all()` called at shutdown — terminates all running processes gracefully

### Process management

```python
async def launch(self, command, description, working_dir, started_by) -> BackgroundTask:
    # 1. Check concurrency limit
    # 2. Create task metadata
    # 3. Open output log file
    # 4. Start subprocess: asyncio.create_subprocess_shell(
    #        command, stdout=log_fd, stderr=STDOUT, cwd=working_dir)
    # 5. Save metadata to YAML
    # 6. Start monitor coroutine (_monitor_task)
    # 7. Broadcast event
    # 8. Return task

async def _monitor_task(self, task):
    # 1. Run port detection (first 30s)
    # 2. await process.wait()
    # 3. Update status (completed/failed based on returncode)
    # 4. Save metadata
    # 5. Broadcast event

async def terminate(self, task_id) -> bool:
    # 1. Send SIGTERM
    # 2. Wait 10s
    # 3. Send SIGKILL if still alive
    # 4. Update status to stopped
    # 5. Save metadata, broadcast event
```

### State persistence

```yaml
# company/background_tasks.yaml
tasks:
  - id: a1b2c3d4
    command: "npm run dev --port 3000"
    description: "Deploy frontend dev server"
    working_dir: "/path/to/project"
    started_by: "f7c3f7"
    started_at: "2026-03-26T14:30:00"
    status: running
    pid: 42831
    returncode: null
    ended_at: null
    port: 3000
    address: "http://localhost:3000"
```

## API Routes

### `GET /api/background-tasks`

Returns list of all tasks (newest first).

Response: `{"tasks": [BackgroundTask, ...], "running_count": 2, "max_concurrent": 5}`

### `GET /api/background-tasks/{task_id}`

Returns task detail + tail of output log.

Query params: `?tail=100` (default 50)

Response: `{"task": BackgroundTask, "output_tail": "..."}`

### `POST /api/background-tasks/{task_id}/stop`

Terminates a running task. Returns 409 if task is not running.

Response: `{"status": "ok", "task_id": "..."}`

## WebSocket Events

New event type: `BACKGROUND_TASK_UPDATE`

Payload:
```json
{
  "type": "background_task_update",
  "payload": {
    "task_id": "a1b2c3d4",
    "status": "running",
    "port": 3000,
    "address": "http://localhost:3000"
  }
}
```

Triggered on: task start, port detected, task complete/fail/stop.

Frontend handler: refresh task list on any `background_task_update` event.

## Frontend UI

### Toolbar Button

New button in the toolbar row: `&#9654;` (or terminal icon) labeled "Tasks".

### Modal: Split Panel Layout

Brutalist + xterm + pixel cyberpunk aesthetic (consistent with trace viewer, activity log).

**Structure**:
```
┌──────────────────────────────────────────────┐
│ ██ BACKGROUND TASKS              2/5 SLOTS   │
├──────────────────┬───────────────────────────┤
│ Task List        │ Detail Panel              │
│                  │                           │
│ ██ RUNNING 2m34s │ npm run dev --port 3000   │
│ npm run dev      │ Deploy frontend dev server│
│ ▶ :3000          │                           │
│                  │ ██ RUNNING  ▶ :3000       │
│ ██ RUNNING 18m   │ PID 42831  by f7c3f7     │
│ pytest --watch   │                           │
│                  │ ┌─────────────────────┐   │
│ ░ COMPLETED      │ │ xterm.js output     │   │
│ docker build     │ │ (read-only terminal)│   │
│                  │ │                     │   │
│ ╳ FAILED exit 1  │ │                     │   │
│ deploy.sh prod   │ └─────────────────────┘   │
│                  │                           │
│                  │              [■ STOP]      │
└──────────────────┴───────────────────────────┘
```

**Left panel** (task list):
- Each task: status-colored left border (green=running, gray=completed, red=failed/stopped)
- Shows: status + uptime/exit code, command (truncated), port if detected
- Click to select → loads detail in right panel
- Running tasks sorted first, then by started_at desc

**Right panel** (detail):
- Full command, description
- Status badge, port/address (clickable link if address detected), PID, started_by employee
- XTermLog instance rendering output.log (read-only, auto-scroll)
- STOP button (only for running tasks, red border, confirms on click)
- Auto-refresh output every 3 seconds while task is running

### Auto-refresh

- Output tail: poll `GET /api/background-tasks/{id}?tail=200` every 3 seconds for the selected running task
- Task list: re-fetch on `background_task_update` WebSocket event
- Stop polling when modal is closed or task is not running

## Testing

- Unit tests for `BackgroundTaskManager`: launch, terminate, concurrency limit, port detection, YAML persistence, startup recovery
- Unit tests for API routes: list, detail, stop, 409 on stop non-running
- Unit tests for agent tools: start, check, stop
- Mock subprocess for all tests (no real processes)

## Files to Create/Modify

| Action | File | Purpose |
|--------|------|---------|
| Create | `src/onemancompany/core/background_tasks.py` | BackgroundTaskManager + BackgroundTask dataclass |
| Modify | `src/onemancompany/agents/common_tools.py` | 3 new tools: start/check/stop_background_task |
| Modify | `src/onemancompany/core/models.py` | Add BACKGROUND_TASK_UPDATE to EventType |
| Modify | `src/onemancompany/api/routes.py` | 3 new API endpoints |
| Modify | `frontend/index.html` | Modal HTML + toolbar button |
| Modify | `frontend/app.js` | Modal logic, XTermLog rendering, WebSocket handler |
| Modify | `frontend/style.css` | Modal styles (brutalist theme) |
| Create | `tests/unit/core/test_background_tasks.py` | Manager unit tests |
| Create | `tests/unit/api/test_background_task_routes.py` | API route tests |
