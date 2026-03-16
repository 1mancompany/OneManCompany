# System Cron Registry Design

## Goal

Replace ad-hoc `while True` loops in `main.py` with a decorator-based registry for system-level periodic tasks. Separate from employee crons (`automation.py`) but sharing a unified query protocol (`CronInfo`).

## Background

System-level periodic tasks are currently scattered as raw asyncio loops in `main.py`:
- `_heartbeat_loop()` — 60s, employee API connection checks + review reminder (recently added)
- `_periodic_reload_loop()` — 30s, fallback disk reload when idle

These share the same pattern (interval + async handler) but lack unified management, discoverability, and runtime control.

**Behavioral change:** The review reminder scan is currently bundled in `_heartbeat_loop` at 60s cadence. This design extracts it into its own system cron at 5m interval — intentionally reducing frequency since review reminders don't need per-minute urgency.

## Architecture

### New file: `core/system_cron.py`

Single module containing the registry, decorator, manager, and all handler implementations (co-located, no auto-discovery needed).

#### Decorator: `@system_cron`

```python
@system_cron("heartbeat", interval="1m", description="员工 API 连接检测")
async def heartbeat_check() -> list[CompanyEvent] | None:
    """Return events to publish, or None if nothing happened."""
    ...
```

- Registers the handler in a module-level `_registry: dict[str, SystemCronDef]`
- `SystemCronDef` holds: name, default_interval, description, handler (async callable)
- Handler returns `list[CompanyEvent] | None` — manager publishes returned events via `event_bus`
- Handler must NOT call `event_bus.publish` directly
- Interval parsing reuses `_parse_interval()` extracted from `automation.py` into a shared utility

#### Manager: `SystemCronManager`

Singleton (`system_cron_manager`) managing runtime lifecycle.

**Methods:**
- `start_all()` — start asyncio.Tasks for all registered crons (called at server startup)
- `stop_all()` — cancel all running tasks (called at server shutdown)
- `start(name)` — start/resume a single cron
- `stop(name)` — pause a single cron (keeps registration)
- `update_interval(name, new_interval)` — change interval at runtime (restarts the loop)
- `get_all() -> list[CronInfo]` — return status of all registered system crons

**Internal loop per cron (sleep-then-run, no overlap):**
```python
async def _loop(self, cron_def: SystemCronDef):
    while True:
        await asyncio.sleep(cron_def.current_interval_seconds)
        try:
            events = await cron_def.handler()
            if events:
                for event in events:
                    await event_bus.publish(event)
            cron_def.last_run = datetime.now()
            cron_def.run_count += 1
            cron_def.last_error = None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("System cron '{}' error: {}", cron_def.name, e)
            cron_def.last_error = str(e)
```

**Overlap policy:** Sleep-then-run (same as current `main.py` behavior). If a handler takes longer than its interval, the next run starts immediately after completion — no concurrent runs of the same handler. This is inherent in the single-task-per-cron loop structure.

**Events use cron name as agent:** `CompanyEvent(type=..., agent=cron_def.name.upper())`

**State tracking per cron:**
- `running: bool`
- `current_interval: str` (may differ from default after `update_interval`)
- `last_run: datetime | None`
- `run_count: int`
- `last_error: str | None`

### Shared utility: `core/interval.py`

Extract `_parse_interval()` from `automation.py` into a shared module. Both `automation.py` and `system_cron.py` import from here.

```python
def parse_interval(interval_str: str) -> int | None:
    """Parse '5m', '1h', '30s', '1d' to seconds. Returns None if invalid."""
```

### Data model: `CronInfo`

Shared protocol for both system and employee crons:

```python
class CronInfo(TypedDict):
    name: str
    interval: str
    description: str
    running: bool
    scope: Literal["system", "employee"]
    employee_id: str | None           # only for employee crons
    last_run: str | None              # ISO timestamp, system crons only
    run_count: int | None             # system crons only
```

### Unified query: `list_all_crons()`

Added to `automation.py`:

```python
def list_all_crons() -> list[CronInfo]:
    from onemancompany.core.system_cron import system_cron_manager
    result = system_cron_manager.get_all()  # system scope
    for emp_dir in EMPLOYEES_DIR.iterdir():
        if not emp_dir.is_dir():
            continue
        for cron in list_crons(emp_dir.name):
            result.append({
                "name": cron["name"],
                "interval": cron.get("interval", "?"),
                "description": cron.get("task_description", ""),
                "running": cron.get("running", False),
                "scope": "employee",
                "employee_id": emp_dir.name,
                "last_run": None,
                "run_count": None,
            })
    return result
```

## Initial system crons

### 1. `heartbeat` (interval: 1m)
Migrated from `main.py:_heartbeat_loop`. Calls `run_heartbeat_cycle()`.
Returns `[CompanyEvent(type="state_snapshot", ...)]` if any employee status changed, else `None`.

### 2. `review_reminder` (interval: 5m)
Extracted from `_heartbeat_loop` (was at 60s, now 5m — intentional). Calls `scan_overdue_reviews()` from `vessel.py`.
Returns `[CompanyEvent(type="review_reminder", payload={"overdue_nodes": [...]})]` if any found, else `None`.

### 3. `config_reload` (interval: 30s)
Migrated from `main.py:_periodic_reload_loop`. Calls `reload_all_from_disk()` only when `is_idle()`.
Returns `None` (reload triggers dirty marks, sync tick handles frontend push).

## Changes to existing files

### `main.py`
- Delete `_heartbeat_loop()` and `_periodic_reload_loop()` functions
- Delete their `asyncio.create_task()` calls in lifespan (including the `periodic_task` variable which was never cancelled — latent bug)
- Add in lifespan:
  ```python
  from onemancompany.core import system_cron  # triggers @system_cron registrations
  system_cron.system_cron_manager.start_all()   # startup
  system_cron.system_cron_manager.stop_all()    # shutdown
  ```

### `automation.py`
- Extract `_parse_interval()` → import from `core/interval.py`
- Add `list_all_crons()` function

### `vessel.py`
- Keep `scan_overdue_reviews()` as a helper, called by the `review_reminder` handler in `system_cron.py`
- Remove `REVIEW_REMINDER_THRESHOLD_SECONDS` constant (move to handler)

### `api/routes.py`
Add system cron management endpoints:
- `GET /api/system/crons` — list all system crons with status
- `POST /api/system/crons/{name}/start` — resume a paused cron
- `POST /api/system/crons/{name}/stop` — pause a cron
- `PATCH /api/system/crons/{name}` — update interval (`{"interval": "30s"}`)

### `frontend/app.js`
Settings page: add "System Crons" section with table (name, interval, description, status, start/stop button).

## What we do NOT do

- Do not merge employee cron registry into system cron — different execution model (push_task vs direct function)
- Do not migrate `_start_code_watcher` — event-driven, not periodic
- Do not persist system cron state to YAML — code is the config; runtime changes (e.g. `update_interval`) reset on restart. Graceful restart (`os.execv`) re-registers all crons at default intervals. This is acceptable because interval tuning is rare and the defaults should be correct.
- Do not add system cron creation from frontend — only code-registered crons exist
- No snapshot provider — system cron state is ephemeral and cheap to reconstruct

## Testing

- Unit test `@system_cron` decorator registration
- Unit test `SystemCronManager.start/stop/get_all/update_interval`
- Unit test handler return → event_bus publish
- Unit test `list_all_crons()` aggregation (system + employee)
- Unit test `parse_interval()` shared utility
- Integration test: verify heartbeat and review_reminder produce correct events
