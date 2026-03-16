# System Cron Registry Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ad-hoc `while True` loops in `main.py` with a decorator-based system cron registry, adding review reminder support and unified cron query.

**Architecture:** `@system_cron` decorator registers handlers in a module-level registry. `SystemCronManager` singleton manages asyncio.Task lifecycle per cron. Shared `parse_interval()` utility extracted from `automation.py`. Unified `CronInfo` protocol bridges system and employee crons. Frontend Settings panel gains a "System Crons" section.

**Tech Stack:** Python asyncio, FastAPI, loguru, vanilla JS

**Spec:** `docs/superpowers/specs/2026-03-15-system-cron-registry-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/onemancompany/core/interval.py` | Create | Shared `parse_interval()` utility |
| `src/onemancompany/core/system_cron.py` | Create | Registry, decorator, manager, handler implementations |
| `src/onemancompany/core/automation.py` | Modify | Import `parse_interval` from `interval.py`, add `list_all_crons()` |
| `src/onemancompany/core/vessel.py` | Modify | Move `REVIEW_REMINDER_THRESHOLD_SECONDS` out, keep `scan_overdue_reviews()` |
| `src/onemancompany/main.py` | Modify | Delete `_heartbeat_loop`, `_periodic_reload_loop`, wire `system_cron_manager` |
| `src/onemancompany/api/routes.py` | Modify | Add 4 system cron API endpoints |
| `frontend/index.html` | Modify | Add System Crons settings section |
| `frontend/app.js` | Modify | Add system cron render + start/stop logic |
| `tests/unit/core/test_interval.py` | Create | Tests for `parse_interval` |
| `tests/unit/core/test_system_cron.py` | Create | Tests for registry, manager, handlers |

---

## Chunk 1: Core Infrastructure

### Task 1: Extract `parse_interval` to shared utility

**Files:**
- Create: `src/onemancompany/core/interval.py`
- Modify: `src/onemancompany/core/automation.py:58-72`
- Create: `tests/unit/core/test_interval.py`

- [ ] **Step 1: Write tests for `parse_interval`**

```python
# tests/unit/core/test_interval.py
"""Tests for the shared interval parser."""
import pytest
from onemancompany.core.interval import parse_interval


@pytest.mark.parametrize("input_str,expected", [
    ("30s", 30),
    ("5m", 300),
    ("1h", 3600),
    ("2d", 172800),
    ("  10M  ", 600),  # whitespace + case insensitive
])
def test_parse_valid(input_str, expected):
    assert parse_interval(input_str) == expected


@pytest.mark.parametrize("input_str", [
    "", "abc", "5x", "m5", "0", None,
])
def test_parse_invalid(input_str):
    assert parse_interval(input_str) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/core/test_interval.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'onemancompany.core.interval'`

- [ ] **Step 3: Create `interval.py`**

```python
# src/onemancompany/core/interval.py
"""Shared interval string parser.

Used by both system cron and employee cron (automation.py).
"""
from __future__ import annotations


def parse_interval(interval_str: str | None) -> int | None:
    """Parse interval string like '5m', '1h', '30s', '1d' to seconds.

    Returns None if the string is invalid or empty.
    """
    if not interval_str:
        return None
    interval_str = str(interval_str).strip().lower()
    if not interval_str:
        return None
    unit = interval_str[-1]
    try:
        value = int(interval_str[:-1])
    except ValueError:
        return None
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    mult = multipliers.get(unit)
    if mult is None:
        return None
    return value * mult
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/core/test_interval.py -v`
Expected: All PASS

- [ ] **Step 5: Update `automation.py` to import from `interval.py`**

In `src/onemancompany/core/automation.py`, replace the `_parse_interval` function (lines 58-72) with an import:

```python
# Replace the function definition with:
from onemancompany.core.interval import parse_interval as _parse_interval
```

Remove the old function body (lines 58-72). All existing callsites use `_parse_interval` so the alias keeps them working.

- [ ] **Step 6: Verify existing automation tests still pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -k "automation or cron" -v --timeout=10`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/onemancompany/core/interval.py tests/unit/core/test_interval.py src/onemancompany/core/automation.py
git commit -m "refactor: extract parse_interval to shared core/interval.py"
```

---

### Task 2: System Cron Registry and Manager

**Files:**
- Create: `src/onemancompany/core/system_cron.py`
- Create: `tests/unit/core/test_system_cron.py`

- [ ] **Step 1: Write tests for registry and manager**

```python
# tests/unit/core/test_system_cron.py
"""Tests for the system cron registry and manager."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from onemancompany.core.system_cron import (
    SystemCronDef,
    SystemCronManager,
    system_cron,
    _registry,
)


# --- Decorator registration tests ---

def test_decorator_registers_handler():
    """@system_cron registers the handler in the module-level _registry."""
    test_registry: dict[str, SystemCronDef] = {}

    @system_cron("test_cron_1", interval="5m", description="Test cron", registry=test_registry)
    async def my_handler():
        return None

    assert "test_cron_1" in test_registry
    defn = test_registry["test_cron_1"]
    assert defn.name == "test_cron_1"
    assert defn.default_interval == "5m"
    assert defn.description == "Test cron"
    assert defn.handler is my_handler


def test_decorator_rejects_invalid_interval():
    """@system_cron raises ValueError for unparseable intervals."""
    test_registry: dict[str, SystemCronDef] = {}
    with pytest.raises(ValueError, match="Invalid interval"):
        @system_cron("bad", interval="xyz", description="Bad", registry=test_registry)
        async def bad_handler():
            return None


# --- Manager lifecycle tests ---

@pytest.mark.asyncio
async def test_manager_start_stop():
    """Manager starts asyncio tasks for all registered crons and stops them."""
    call_count = 0

    async def counting_handler():
        nonlocal call_count
        call_count += 1
        return None

    test_registry = {
        "counter": SystemCronDef(
            name="counter",
            default_interval="1s",
            description="Counter",
            handler=counting_handler,
        ),
    }
    mgr = SystemCronManager(registry=test_registry)
    mgr.start_all()

    await asyncio.sleep(1.5)  # Let it fire at least once
    assert call_count >= 1

    await mgr.stop_all()
    final_count = call_count
    await asyncio.sleep(1.5)
    assert call_count == final_count  # No more runs after stop


@pytest.mark.asyncio
async def test_manager_get_all():
    """get_all returns CronInfo for all registered crons."""
    test_registry = {
        "test_a": SystemCronDef(name="test_a", default_interval="1m", description="A", handler=AsyncMock()),
    }
    mgr = SystemCronManager(registry=test_registry)
    infos = mgr.get_all()
    assert len(infos) == 1
    assert infos[0]["name"] == "test_a"
    assert infos[0]["scope"] == "system"
    assert infos[0]["running"] is False


@pytest.mark.asyncio
async def test_manager_start_stop_single():
    """Manager can start and stop individual crons."""
    test_registry = {
        "single": SystemCronDef(name="single", default_interval="1s", description="S", handler=AsyncMock(return_value=None)),
    }
    mgr = SystemCronManager(registry=test_registry)

    result = mgr.start("single")
    assert result["status"] == "ok"
    infos = mgr.get_all()
    assert infos[0]["running"] is True

    result = mgr.stop("single")
    assert result["status"] == "ok"
    infos = mgr.get_all()
    assert infos[0]["running"] is False

    await mgr.stop_all()


@pytest.mark.asyncio
async def test_manager_update_interval():
    """update_interval changes the interval and restarts the cron."""
    test_registry = {
        "updatable": SystemCronDef(name="updatable", default_interval="1m", description="U", handler=AsyncMock(return_value=None)),
    }
    mgr = SystemCronManager(registry=test_registry)
    mgr.start("updatable")

    result = mgr.update_interval("updatable", "30s")
    assert result["status"] == "ok"
    assert result["interval"] == "30s"

    infos = mgr.get_all()
    assert infos[0]["interval"] == "30s"
    assert infos[0]["running"] is True

    await mgr.stop_all()


@pytest.mark.asyncio
async def test_handler_events_published():
    """Events returned by handler are published to event_bus."""
    from onemancompany.core.events import CompanyEvent

    test_event = CompanyEvent(type="test_event", payload={"x": 1}, agent="TEST")

    async def event_handler():
        return [test_event]

    test_registry = {
        "eventer": SystemCronDef(name="eventer", default_interval="1s", description="E", handler=event_handler),
    }
    mgr = SystemCronManager(registry=test_registry)

    with patch("onemancompany.core.system_cron.event_bus") as mock_bus:
        mock_bus.publish = AsyncMock()
        mgr.start_all()
        await asyncio.sleep(1.5)
        await mgr.stop_all()

        mock_bus.publish.assert_called()
        # At least one call should have our test event
        published_events = [call.args[0] for call in mock_bus.publish.call_args_list]
        assert any(e.type == "test_event" for e in published_events)


@pytest.mark.asyncio
async def test_handler_error_does_not_crash_loop():
    """If a handler raises, the cron continues running."""
    call_count = 0

    async def flaky_handler():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        return None

    test_registry = {
        "flaky": SystemCronDef(name="flaky", default_interval="1s", description="F", handler=flaky_handler),
    }
    mgr = SystemCronManager(registry=test_registry)
    mgr.start_all()
    await asyncio.sleep(2.5)
    await mgr.stop_all()

    assert call_count >= 2  # Continued after error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/core/test_system_cron.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'onemancompany.core.system_cron'`

- [ ] **Step 3: Implement `system_cron.py`**

```python
# src/onemancompany/core/system_cron.py
"""System Cron Registry — decorator-based periodic task management.

System crons are infrastructure-level periodic tasks (heartbeat, review
reminders, config reload). They differ from employee crons (automation.py)
in that they run async functions directly (zero token cost) rather than
pushing tasks to AI agents.

Usage:
    @system_cron("heartbeat", interval="1m", description="API connection checks")
    async def heartbeat_check() -> list[CompanyEvent] | None:
        ...

All handlers are co-located in this module. The singleton `system_cron_manager`
manages lifecycle. Wire it in main.py lifespan:

    system_cron_manager.start_all()   # startup
    system_cron_manager.stop_all()    # shutdown
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Coroutine, Literal, TypedDict

from loguru import logger

from onemancompany.core.interval import parse_interval


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SystemCronDef:
    """Definition of a system cron job."""
    name: str
    default_interval: str
    description: str
    handler: Callable[[], Coroutine[Any, Any, list | None]]
    # Runtime state (managed by SystemCronManager)
    current_interval: str = ""
    current_interval_seconds: int = 0
    last_run: datetime | None = None
    run_count: int = 0
    last_error: str | None = None

    def __post_init__(self):
        if not self.current_interval:
            self.current_interval = self.default_interval
            self.current_interval_seconds = parse_interval(self.default_interval) or 60


class CronInfo(TypedDict):
    """Shared protocol for both system and employee crons."""
    name: str
    interval: str
    description: str
    running: bool
    scope: Literal["system", "employee"]
    employee_id: str | None
    last_run: str | None
    run_count: int | None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_registry: dict[str, SystemCronDef] = {}


def system_cron(
    name: str,
    *,
    interval: str,
    description: str,
    registry: dict[str, SystemCronDef] | None = None,
):
    """Decorator to register a system cron handler.

    Args:
        name: Unique cron name.
        interval: Default interval string (e.g. '1m', '5m', '30s').
        description: Human-readable description.
        registry: Override registry (for testing). Defaults to module-level _registry.
    """
    target = registry if registry is not None else _registry
    seconds = parse_interval(interval)
    if seconds is None:
        raise ValueError(f"Invalid interval: {interval!r}")

    def decorator(fn):
        target[name] = SystemCronDef(
            name=name,
            default_interval=interval,
            description=description,
            handler=fn,
        )
        return fn
    return decorator


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class SystemCronManager:
    """Manages lifecycle of all registered system crons."""

    def __init__(self, registry: dict[str, SystemCronDef] | None = None):
        self._registry = registry if registry is not None else _registry
        self._tasks: dict[str, asyncio.Task] = {}

    def start_all(self) -> None:
        """Start asyncio tasks for all registered crons."""
        for name in self._registry:
            if name not in self._tasks or self._tasks[name].done():
                self.start(name)
        logger.info("System crons started: {}", list(self._registry.keys()))

    async def stop_all(self) -> None:
        """Cancel all running cron tasks."""
        for name in list(self._tasks.keys()):
            self.stop(name)
        # Wait for cancellation
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        logger.info("All system crons stopped")

    def start(self, name: str) -> dict:
        """Start or resume a single cron."""
        defn = self._registry.get(name)
        if not defn:
            return {"status": "error", "message": f"Unknown system cron: {name}"}

        # Cancel existing if running
        existing = self._tasks.get(name)
        if existing and not existing.done():
            existing.cancel()

        task = asyncio.create_task(self._loop(defn), name=f"system_cron:{name}")
        self._tasks[name] = task
        return {"status": "ok", "name": name}

    def stop(self, name: str) -> dict:
        """Pause a cron (keeps registration)."""
        task = self._tasks.pop(name, None)
        if task and not task.done():
            task.cancel()
        return {"status": "ok", "name": name}

    def update_interval(self, name: str, new_interval: str) -> dict:
        """Change interval and restart the cron."""
        defn = self._registry.get(name)
        if not defn:
            return {"status": "error", "message": f"Unknown system cron: {name}"}
        seconds = parse_interval(new_interval)
        if seconds is None:
            return {"status": "error", "message": f"Invalid interval: {new_interval}"}

        defn.current_interval = new_interval
        defn.current_interval_seconds = seconds

        # Restart if currently running
        if name in self._tasks and not self._tasks[name].done():
            self.stop(name)
            self.start(name)

        return {"status": "ok", "name": name, "interval": new_interval}

    def get_all(self) -> list[CronInfo]:
        """Return status of all registered system crons."""
        result: list[CronInfo] = []
        for name, defn in self._registry.items():
            task = self._tasks.get(name)
            running = bool(task and not task.done())
            result.append({
                "name": defn.name,
                "interval": defn.current_interval,
                "description": defn.description,
                "running": running,
                "scope": "system",
                "employee_id": None,
                "last_run": defn.last_run.isoformat() if defn.last_run else None,
                "run_count": defn.run_count,
            })
        return result

    async def _loop(self, cron_def: SystemCronDef) -> None:
        """Internal loop: sleep-then-run, no overlap."""
        from onemancompany.core.events import event_bus

        logger.info("[system_cron] Started '{}' every {}",
                     cron_def.name, cron_def.current_interval)
        try:
            while True:
                await asyncio.sleep(cron_def.current_interval_seconds)
                try:
                    events = await cron_def.handler()
                    cron_def.last_run = datetime.now()
                    cron_def.run_count += 1
                    cron_def.last_error = None
                    if events:
                        for event in events:
                            await event_bus.publish(event)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error("[system_cron] '{}' error: {}", cron_def.name, e)
                    cron_def.last_error = str(e)
                    cron_def.last_run = datetime.now()
                    cron_def.run_count += 1
        except asyncio.CancelledError:
            logger.info("[system_cron] Stopped '{}'", cron_def.name)
            raise


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

system_cron_manager = SystemCronManager()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/core/test_system_cron.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/system_cron.py tests/unit/core/test_system_cron.py
git commit -m "feat: add system cron registry with decorator and manager"
```

---

### Task 3: Register handler implementations

**Files:**
- Modify: `src/onemancompany/core/system_cron.py` (append handlers at bottom)
- Modify: `src/onemancompany/core/vessel.py` (move constant, clean up heartbeat code)

- [ ] **Step 1: Write tests for handlers**

Append to `tests/unit/core/test_system_cron.py`:

```python
# --- Handler tests ---

@pytest.mark.asyncio
async def test_heartbeat_handler_returns_event_on_change():
    """heartbeat_check returns state_snapshot event when status changed."""
    with patch("onemancompany.core.heartbeat.run_heartbeat_cycle", new_callable=AsyncMock) as mock_hb:
        mock_hb.return_value = ["emp_001"]  # changed
        from onemancompany.core.system_cron import heartbeat_check
        events = await heartbeat_check()
        assert events is not None
        assert len(events) == 1
        assert events[0].type == "state_snapshot"


@pytest.mark.asyncio
async def test_heartbeat_handler_returns_none_when_no_change():
    """heartbeat_check returns None when no status changed."""
    with patch("onemancompany.core.heartbeat.run_heartbeat_cycle", new_callable=AsyncMock) as mock_hb:
        mock_hb.return_value = []
        from onemancompany.core.system_cron import heartbeat_check
        events = await heartbeat_check()
        assert events is None


@pytest.mark.asyncio
async def test_review_reminder_handler():
    """review_reminder_check returns event when overdue nodes found."""
    fake_overdue = [{"node_id": "n1", "employee_id": "e1", "waiting_seconds": 600}]
    with patch("onemancompany.core.vessel.scan_overdue_reviews", return_value=fake_overdue):
        from onemancompany.core.system_cron import review_reminder_check
        events = await review_reminder_check()
        assert events is not None
        assert events[0].type == "review_reminder"
        assert events[0].payload["overdue_nodes"] == fake_overdue


@pytest.mark.asyncio
async def test_review_reminder_handler_nothing_overdue():
    """review_reminder_check returns None when no overdue nodes."""
    with patch("onemancompany.core.vessel.scan_overdue_reviews", return_value=[]):
        from onemancompany.core.system_cron import review_reminder_check
        events = await review_reminder_check()
        assert events is None


@pytest.mark.asyncio
async def test_config_reload_handler_when_idle():
    """config_reload_check calls reload_all_from_disk when idle."""
    with patch("onemancompany.core.state.is_idle", return_value=True), \
         patch("onemancompany.core.state.reload_all_from_disk") as mock_reload:
        mock_reload.return_value = {"employees_updated": [], "employees_added": []}
        from onemancompany.core.system_cron import config_reload_check
        events = await config_reload_check()
        assert events is None  # config reload doesn't return events
        mock_reload.assert_called_once()


@pytest.mark.asyncio
async def test_config_reload_handler_when_busy():
    """config_reload_check skips reload when not idle."""
    with patch("onemancompany.core.state.is_idle", return_value=False), \
         patch("onemancompany.core.state.reload_all_from_disk") as mock_reload:
        from onemancompany.core.system_cron import config_reload_check
        events = await config_reload_check()
        assert events is None
        mock_reload.assert_not_called()
```

- [ ] **Step 2: Run tests to verify new handler tests fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/core/test_system_cron.py -k "handler" -v`
Expected: FAIL — handler functions not defined

- [ ] **Step 3: Append handler implementations to `system_cron.py`**

Add at the bottom of `src/onemancompany/core/system_cron.py`:

```python
# ---------------------------------------------------------------------------
# Handler implementations
# ---------------------------------------------------------------------------

REVIEW_REMINDER_THRESHOLD_SECONDS = 300  # 5 minutes


@system_cron("heartbeat", interval="1m", description="员工 API 连接检测")
async def heartbeat_check() -> list | None:
    from onemancompany.core.heartbeat import run_heartbeat_cycle
    from onemancompany.core.events import CompanyEvent

    changed = await run_heartbeat_cycle()
    if changed:
        return [CompanyEvent(type="state_snapshot", payload={}, agent="HEARTBEAT")]
    return None


@system_cron("review_reminder", interval="5m", description="审批超时提醒")
async def review_reminder_check() -> list | None:
    from onemancompany.core.vessel import scan_overdue_reviews
    from onemancompany.core.events import CompanyEvent

    overdue = scan_overdue_reviews()
    if overdue:
        return [CompanyEvent(
            type="review_reminder",
            payload={"overdue_nodes": overdue},
            agent="REVIEW_REMINDER",
        )]
    return None


@system_cron("config_reload", interval="30s", description="磁盘配置定期重载")
async def config_reload_check() -> list | None:
    from onemancompany.core.state import is_idle, reload_all_from_disk

    if is_idle():
        result = reload_all_from_disk()
        updated = result.get("employees_updated", [])
        added = result.get("employees_added", [])
        if updated or added:
            logger.info("[config_reload] {} updated, {} added", len(updated), len(added))
    return None
```

- [ ] **Step 4: Update `vessel.py` — remove `REVIEW_REMINDER_THRESHOLD_SECONDS`**

In `src/onemancompany/core/vessel.py`, remove the `REVIEW_REMINDER_THRESHOLD_SECONDS = 300` constant from the `scan_overdue_reviews()` function area. Update `scan_overdue_reviews()` to accept it as a parameter:

```python
def scan_overdue_reviews(threshold_seconds: int = 300) -> list[dict]:
```

Replace the line `if elapsed < REVIEW_REMINDER_THRESHOLD_SECONDS:` with `if elapsed < threshold_seconds:`.

- [ ] **Step 5: Run all handler tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/core/test_system_cron.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/core/system_cron.py src/onemancompany/core/vessel.py tests/unit/core/test_system_cron.py
git commit -m "feat: register heartbeat, review_reminder, config_reload as system crons"
```

---

## Chunk 2: Migration and API

### Task 4: Migrate `main.py` — delete old loops, wire manager

**Files:**
- Modify: `src/onemancompany/main.py:185-240` (delete old loops)
- Modify: `src/onemancompany/main.py:573-638` (lifespan wiring)

- [ ] **Step 1: Delete `_heartbeat_loop` and `_periodic_reload_loop` from `main.py`**

Remove the following functions entirely from `main.py`:
- `_periodic_reload_loop()` (lines 185-205)
- `_heartbeat_loop()` (lines 208-240)

- [ ] **Step 2: Update lifespan startup — replace old task creation with system_cron_manager**

In the lifespan function, replace:

```python
    # Start periodic reload as watchdog fallback
    periodic_task = asyncio.create_task(_periodic_reload_loop())

    # Start heartbeat loop (API connection checks every 60s)
    heartbeat_task = asyncio.create_task(_heartbeat_loop())
```

With:

```python
    # Start system cron registry (heartbeat, review_reminder, config_reload)
    from onemancompany.core import system_cron as _system_cron_mod  # triggers @system_cron registrations
    _system_cron_mod.system_cron_manager.start_all()
```

- [ ] **Step 3: Update lifespan shutdown — replace old task cancellation**

In the shutdown section, add before the existing `stop_all_automations`:

```python
    # Stop system crons
    await _system_cron_mod.system_cron_manager.stop_all()
```

Remove `heartbeat_task.cancel()` (line 632) and remove `heartbeat_task` from the `asyncio.gather(...)` call (line 636). Also remove `periodic_task` references (it was never cancelled — latent bug now fixed).

The final gather should be:

```python
    await asyncio.gather(broadcaster_task, watcher_task, code_watcher_task, sync_tick_task, return_exceptions=True)
```

- [ ] **Step 4: Verify the server starts**

Run: `PYTHONPATH=src .venv/bin/python -c "from onemancompany.main import app; print('OK')"`
Expected: OK (no import errors)

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/main.py
git commit -m "refactor: migrate heartbeat and config_reload loops to system cron registry"
```

---

### Task 5: Add `list_all_crons()` to `automation.py`

**Files:**
- Modify: `src/onemancompany/core/automation.py`

- [ ] **Step 1: Write test**

```python
# Append to tests/unit/core/test_system_cron.py

def test_list_all_crons_aggregates():
    """list_all_crons returns both system and employee crons."""
    from unittest.mock import patch, MagicMock

    fake_system = [
        {"name": "heartbeat", "interval": "1m", "description": "HB",
         "running": True, "scope": "system", "employee_id": None,
         "last_run": None, "run_count": 0},
    ]
    fake_emp_crons = [
        {"name": "my_cron", "interval": "5m", "task_description": "Do stuff", "running": True},
    ]

    with patch("onemancompany.core.automation.system_cron_manager") as mock_mgr, \
         patch("onemancompany.core.automation.EMPLOYEES_DIR") as mock_dir:
        mock_mgr.get_all.return_value = fake_system

        # Simulate one employee dir with crons
        emp_dir = MagicMock()
        emp_dir.is_dir.return_value = True
        emp_dir.name = "emp_001"
        mock_dir.exists.return_value = True
        mock_dir.iterdir.return_value = [emp_dir]

        with patch("onemancompany.core.automation.list_crons", return_value=fake_emp_crons):
            from onemancompany.core.automation import list_all_crons
            result = list_all_crons()

    assert len(result) == 2
    assert result[0]["scope"] == "system"
    assert result[1]["scope"] == "employee"
    assert result[1]["employee_id"] == "emp_001"
    assert result[1]["description"] == "Do stuff"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/core/test_system_cron.py::test_list_all_crons_aggregates -v`
Expected: FAIL — `ImportError: cannot import name 'list_all_crons'`

- [ ] **Step 3: Add `list_all_crons()` to `automation.py`**

Append at the end of `src/onemancompany/core/automation.py`, before the shutdown section:

```python
def list_all_crons() -> list[dict]:
    """Unified query: system crons + all employee crons."""
    from onemancompany.core.system_cron import system_cron_manager

    result = system_cron_manager.get_all()

    if not EMPLOYEES_DIR.exists():
        return result

    for emp_dir in sorted(EMPLOYEES_DIR.iterdir()):
        if not emp_dir.is_dir():
            continue
        employee_id = emp_dir.name
        for cron in list_crons(employee_id):
            result.append({
                "name": cron["name"],
                "interval": cron.get("interval", "?"),
                "description": cron.get("task_description", ""),
                "running": cron.get("running", False),
                "scope": "employee",
                "employee_id": employee_id,
                "last_run": None,
                "run_count": None,
            })

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/core/test_system_cron.py::test_list_all_crons_aggregates -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/automation.py tests/unit/core/test_system_cron.py
git commit -m "feat: add list_all_crons() unified query for system + employee crons"
```

---

### Task 6: Add API endpoints for system cron management

**Files:**
- Modify: `src/onemancompany/api/routes.py`

- [ ] **Step 1: Write tests**

```python
# tests/unit/api/test_system_cron_api.py
"""Tests for system cron management API endpoints."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from onemancompany.main import app
    return TestClient(app, raise_server_exceptions=False)


def test_list_system_crons(client):
    """GET /api/system/crons returns system cron list."""
    fake = [{"name": "heartbeat", "interval": "1m", "description": "HB",
             "running": True, "scope": "system", "employee_id": None,
             "last_run": None, "run_count": 5}]
    with patch("onemancompany.api.routes.system_cron_manager") as mock:
        mock.get_all.return_value = fake
        resp = client.get("/api/system/crons")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "heartbeat"


def test_stop_system_cron(client):
    """POST /api/system/crons/{name}/stop pauses a cron."""
    with patch("onemancompany.api.routes.system_cron_manager") as mock:
        mock.stop.return_value = {"status": "ok", "name": "heartbeat"}
        resp = client.post("/api/system/crons/heartbeat/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_start_system_cron(client):
    """POST /api/system/crons/{name}/start resumes a cron."""
    with patch("onemancompany.api.routes.system_cron_manager") as mock:
        mock.start.return_value = {"status": "ok", "name": "heartbeat"}
        resp = client.post("/api/system/crons/heartbeat/start")
    assert resp.status_code == 200


def test_update_system_cron_interval(client):
    """PATCH /api/system/crons/{name} updates interval."""
    with patch("onemancompany.api.routes.system_cron_manager") as mock:
        mock.update_interval.return_value = {"status": "ok", "name": "heartbeat", "interval": "30s"}
        resp = client.patch("/api/system/crons/heartbeat", json={"interval": "30s"})
    assert resp.status_code == 200
    assert resp.json()["interval"] == "30s"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/api/test_system_cron_api.py -v`
Expected: FAIL — 404 (routes don't exist yet)

- [ ] **Step 3: Add endpoints to `routes.py`**

Find the end of the routes file and add before the last line. Import `system_cron_manager` at the top of the new section:

```python
# ---------------------------------------------------------------------------
# System Cron management
# ---------------------------------------------------------------------------

@router.get("/api/system/crons")
async def list_system_crons() -> list[dict]:
    from onemancompany.core.system_cron import system_cron_manager
    return system_cron_manager.get_all()


@router.post("/api/system/crons/{name}/start")
async def start_system_cron(name: str) -> dict:
    from onemancompany.core.system_cron import system_cron_manager
    return system_cron_manager.start(name)


@router.post("/api/system/crons/{name}/stop")
async def stop_system_cron(name: str) -> dict:
    from onemancompany.core.system_cron import system_cron_manager
    return system_cron_manager.stop(name)


@router.patch("/api/system/crons/{name}")
async def update_system_cron(name: str, body: dict) -> dict:
    from onemancompany.core.system_cron import system_cron_manager
    interval = body.get("interval")
    if not interval:
        return {"status": "error", "message": "interval is required"}
    return system_cron_manager.update_interval(name, interval)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/api/test_system_cron_api.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/api/routes.py tests/unit/api/test_system_cron_api.py
git commit -m "feat: add system cron management API endpoints"
```

---

## Chunk 3: Frontend

### Task 7: Add System Crons section to Settings panel

**Files:**
- Modify: `frontend/index.html:136-144`
- Modify: `frontend/app.js`

- [ ] **Step 1: Add HTML section in `index.html`**

In `frontend/index.html`, after the existing API Connections settings section (after line 143 `</div>` closing `settings-api-body`), add:

```html
      <div class="settings-section-header" data-target="settings-crons-body">
        <span class="settings-collapse-arrow">&#9654;</span>
        <span class="settings-section-title">&#9200; System Crons</span>
      </div>
      <div id="settings-crons-body" class="settings-section-body collapsed">
        <div id="system-crons-content"></div>
      </div>
```

- [ ] **Step 2: Add render and control methods in `app.js`**

Find the `_renderApiSettings()` method in `app.js`. After the `_saveProviderKey` / `_testProviderKey` methods block, add:

```javascript
  // ===== System Crons Settings =====
  async _renderSystemCrons() {
    const container = document.getElementById('system-crons-content');
    if (!container) return;
    container.innerHTML = '<div style="color:var(--text-dim);font-size:7px;padding:6px;">Loading...</div>';
    try {
      const resp = await fetch('/api/system/crons');
      const crons = await resp.json();

      if (!crons.length) {
        container.innerHTML = '<div style="color:var(--text-dim);font-size:7px;padding:6px;">No system crons registered.</div>';
        return;
      }

      let html = '<table class="pixel-table" style="width:100%;font-size:6.5px;"><thead><tr>';
      html += '<th>Name</th><th>Interval</th><th>Description</th><th>Runs</th><th>Status</th><th></th>';
      html += '</tr></thead><tbody>';

      for (const c of crons) {
        const statusDot = c.running
          ? '<span class="api-status-dot online"></span>'
          : '<span class="api-status-dot offline"></span>';
        const btnLabel = c.running ? 'Stop' : 'Start';
        const btnAction = c.running ? 'stop' : 'start';
        html += `<tr>
          <td>${this._escHtml(c.name)}</td>
          <td><input type="text" class="cron-interval-input" id="cron-interval-${c.name}"
               value="${this._escHtml(c.interval)}" style="width:36px;font-size:6px;text-align:center;" /></td>
          <td>${this._escHtml(c.description)}</td>
          <td>${c.run_count ?? '-'}</td>
          <td>${statusDot}</td>
          <td>
            <button class="pixel-btn small" onclick="app._toggleSystemCron('${c.name}', '${btnAction}')">${btnLabel}</button>
            <button class="pixel-btn small" onclick="app._updateCronInterval('${c.name}')">Set</button>
          </td>
        </tr>`;
      }
      html += '</tbody></table>';
      container.innerHTML = html;
    } catch (e) {
      container.innerHTML = `<div style="color:var(--pixel-red);font-size:7px;padding:6px;">Error: ${e.message}</div>`;
    }
  }

  async _toggleSystemCron(name, action) {
    try {
      await fetch(`/api/system/crons/${name}/${action}`, { method: 'POST' });
      this._renderSystemCrons();
    } catch (e) {
      console.error('Toggle system cron failed:', e);
    }
  }

  async _updateCronInterval(name) {
    const input = document.getElementById(`cron-interval-${name}`);
    if (!input) return;
    const interval = input.value.trim();
    if (!interval) return;
    try {
      const resp = await fetch(`/api/system/crons/${name}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ interval }),
      });
      const result = await resp.json();
      if (result.status === 'error') {
        alert(result.message);
      } else {
        this._renderSystemCrons();
      }
    } catch (e) {
      console.error('Update cron interval failed:', e);
    }
  }
```

- [ ] **Step 3: Trigger render when Settings panel opens**

In the Settings panel toggle handler (around line 1100-1103), the current code only renders on first open (`if (!this._settingsLoaded)`). Change the logic so that `_renderSystemCrons()` is called on every panel open (system cron state changes frequently), while `_renderApiSettings()` stays lazy:

```javascript
        if (!settingsPanel.classList.contains('hidden')) {
          const rect = settingsBtn.getBoundingClientRect();
          settingsPanel.style.top = (rect.bottom + 4) + 'px';
          settingsPanel.style.right = (window.innerWidth - rect.right) + 'px';
          if (!this._settingsLoaded) {
            this._settingsLoaded = true;
            this._renderApiSettings();
          }
          this._renderSystemCrons();  // Always refresh cron status
        }
```

- [ ] **Step 4: Verify frontend loads without JS errors**

Open the app in a browser, click the Settings button. The "System Crons" section should appear with a table showing heartbeat, review_reminder, and config_reload.

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/app.js
git commit -m "feat: add System Crons section to Settings panel"
```

---

### Task 8: Final cleanup and verification

**Files:**
- Modify: `src/onemancompany/core/vessel.py` (clean up old review_reminder code from heartbeat era)

- [ ] **Step 1: Verify all tests pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/core/test_interval.py tests/unit/core/test_system_cron.py tests/unit/api/test_system_cron_api.py -v`
Expected: All PASS

- [ ] **Step 2: Verify no import errors in full app**

Run: `PYTHONPATH=src .venv/bin/python -c "from onemancompany.core.system_cron import system_cron_manager; print(system_cron_manager.get_all())"`
Expected: Prints list of 3 CronInfo dicts (heartbeat, review_reminder, config_reload)

- [ ] **Step 3: Run broader test suite to catch regressions**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -x --timeout=30 -q`
Expected: All PASS (no regressions from heartbeat/reload migration)

- [ ] **Step 4: Commit**

```bash
git add src/onemancompany/core/vessel.py
git commit -m "chore: system cron registry — final cleanup and verification"
```
