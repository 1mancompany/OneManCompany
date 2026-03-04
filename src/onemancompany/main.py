"""OneManCompany — FastAPI entrypoint."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from onemancompany.api.routes import router
from onemancompany.api.websocket import ws_manager


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    """Disable browser caching for frontend static files during development."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        if request.url.path.endswith((".js", ".css", ".html")) or request.url.path == "/":
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

FRONTEND_DIR = Path(__file__).parent.parent.parent / "frontend"

# ---------------------------------------------------------------------------
# State snapshot persistence (Tier 2: survive hard restarts)
# ---------------------------------------------------------------------------
SNAPSHOT_PATH = Path(__file__).parent.parent.parent.parent / "company" / ".state_snapshot.json"
SNAPSHOT_MAX_AGE_SECONDS = 60  # only restore if snapshot is < 60s old


def _save_ephemeral_state() -> None:
    """Serialize ephemeral state to disk before shutdown."""
    from onemancompany.core.state import company_state
    from onemancompany.core.file_editor import pending_file_edits
    from onemancompany.agents.hr_agent import pending_candidates
    from onemancompany.agents.coo_agent import pending_hiring_requests

    snapshot = {
        "saved_at": time.time(),
        "activity_log": company_state.activity_log[-50:],
        "pending_file_edits": pending_file_edits,
        "pending_candidates": pending_candidates,
        "pending_hiring_requests": pending_hiring_requests,
    }
    try:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(snapshot, default=str), encoding="utf-8")
    except Exception as e:
        print(f"Warning: failed to save state snapshot: {e}")


def _restore_ephemeral_state() -> None:
    """Restore ephemeral state from a recent snapshot (< 60s old)."""
    if not SNAPSHOT_PATH.exists():
        return
    try:
        raw = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        saved_at = raw.get("saved_at", 0)
        age = time.time() - saved_at
        if age > SNAPSHOT_MAX_AGE_SECONDS:
            SNAPSHOT_PATH.unlink(missing_ok=True)
            return

        from onemancompany.core.state import company_state
        from onemancompany.core.file_editor import pending_file_edits
        from onemancompany.agents.hr_agent import pending_candidates
        from onemancompany.agents.coo_agent import pending_hiring_requests

        # Restore activity log (prepend old entries)
        old_log = raw.get("activity_log", [])
        if old_log:
            company_state.activity_log = old_log + company_state.activity_log

        # Restore pending file edits
        restored_edits = raw.get("pending_file_edits", {})
        if restored_edits:
            pending_file_edits.update(restored_edits)

        # Restore pending candidates
        restored_candidates = raw.get("pending_candidates", {})
        if restored_candidates:
            pending_candidates.update(restored_candidates)

        # Restore pending hiring requests
        restored_hiring = raw.get("pending_hiring_requests", {})
        if restored_hiring:
            pending_hiring_requests.update(restored_hiring)

        print(f"Restored state snapshot ({age:.1f}s old): "
              f"{len(old_log)} log entries, "
              f"{len(restored_edits)} pending edits, "
              f"{len(restored_candidates)} candidate batches, "
              f"{len(restored_hiring)} hiring requests")

        # Clean up snapshot file after successful restore
        SNAPSHOT_PATH.unlink(missing_ok=True)
    except Exception as e:
        print(f"Warning: failed to restore state snapshot: {e}")


# ---------------------------------------------------------------------------
# Watchdog file watcher (Tier 1: soft reload on data changes)
# ---------------------------------------------------------------------------

async def _start_file_watcher() -> None:
    """Watch company/ directory and config.yaml for changes, trigger soft reload.

    Uses request_reload() which defers if agents are busy.
    config.yaml watching is controlled by the ``hot_reload`` flag in config.yaml itself.
    """
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    from onemancompany.core.config import APP_CONFIG_PATH, COMPANY_DIR, is_hot_reload_enabled
    from onemancompany.core.state import request_reload

    DEBOUNCE_SECONDS = 0.5
    WATCH_EXTENSIONS = {".yaml", ".yml", ".md"}

    class _ReloadHandler(FileSystemEventHandler):
        def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
            self._loop = loop
            self._pending: asyncio.TimerHandle | None = None

        def _schedule_reload(self) -> None:
            if self._pending:
                self._pending.cancel()
            self._pending = self._loop.call_later(DEBOUNCE_SECONDS, self._do_reload)

        def _do_reload(self) -> None:
            self._pending = None
            try:
                result = request_reload()
                if result.get("status") == "deferred":
                    print("[hot-reload] Deferred: agents are busy, will reload when idle")
                else:
                    updated = result.get("employees_updated", [])
                    added = result.get("employees_added", [])
                    if updated or added:
                        print(f"[hot-reload] Reloaded from disk: {len(updated)} updated, {len(added)} added")
                    if result.get("config_reloaded"):
                        print("[hot-reload] config.yaml reloaded")
            except Exception as e:
                print(f"[hot-reload] Error during reload: {e}")

        def on_modified(self, event):
            if event.is_directory:
                return
            if Path(event.src_path).suffix in WATCH_EXTENSIONS:
                self._schedule_reload()

        def on_created(self, event):
            if event.is_directory:
                return
            if Path(event.src_path).suffix in WATCH_EXTENSIONS:
                self._schedule_reload()

    class _ConfigReloadHandler(FileSystemEventHandler):
        """Watches config.yaml specifically; only fires if hot_reload is on."""

        def __init__(self, loop: asyncio.AbstractEventLoop, reload_handler: _ReloadHandler) -> None:
            self._loop = loop
            self._reload_handler = reload_handler

        def on_modified(self, event):
            if event.is_directory:
                return
            if Path(event.src_path).resolve() == APP_CONFIG_PATH.resolve():
                if is_hot_reload_enabled():
                    self._reload_handler._schedule_reload()

    loop = asyncio.get_running_loop()
    reload_handler = _ReloadHandler(loop)
    observer = Observer()

    # Watch company/ directory (employees, workflows, assets, etc.)
    watch_dir = str(COMPANY_DIR)
    observer.schedule(reload_handler, watch_dir, recursive=True)

    # Watch config.yaml at project root
    config_handler = _ConfigReloadHandler(loop, reload_handler)
    observer.schedule(config_handler, str(APP_CONFIG_PATH.parent), recursive=False)

    observer.daemon = True
    observer.start()
    print(f"[hot-reload] Watching {watch_dir} for changes")
    if is_hot_reload_enabled():
        print(f"[hot-reload] Watching {APP_CONFIG_PATH} (hot_reload: true)")

    try:
        # Keep the task alive until cancelled
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        observer.stop()
        observer.join(timeout=2)


# ---------------------------------------------------------------------------
# Periodic reload fallback (safety net for when watchdog misses events)
# ---------------------------------------------------------------------------

async def _periodic_reload_loop() -> None:
    """Periodically check for disk changes and reload if idle.

    macOS watchdog can miss file events (atomic writes, IDE temp files),
    so this acts as a fallback to keep memory in sync with disk.
    """
    from onemancompany.core.state import is_idle, reload_all_from_disk

    INTERVAL = 30  # seconds

    while True:
        await asyncio.sleep(INTERVAL)
        try:
            if is_idle():
                result = reload_all_from_disk()
                updated = result.get("employees_updated", [])
                added = result.get("employees_added", [])
                if updated or added:
                    print(f"[periodic-reload] {len(updated)} updated, {len(added)} added")
        except Exception as e:
            print(f"[periodic-reload] Error: {e}")


async def _heartbeat_loop() -> None:
    """Periodically check employee API connections (zero token cost)."""
    from onemancompany.core.heartbeat import run_heartbeat_cycle
    from onemancompany.core.events import CompanyEvent, event_bus

    INTERVAL = 60  # seconds

    while True:
        await asyncio.sleep(INTERVAL)
        try:
            changed = await run_heartbeat_cycle()
            if changed:
                await event_bus.publish(
                    CompanyEvent(type="state_snapshot", payload={}, agent="HEARTBEAT")
                )
        except Exception as e:
            print(f"[heartbeat] Error: {e}")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Eagerly load assets (tools, meeting rooms) into company_state
    from onemancompany.agents.coo_agent import _load_assets_from_disk
    _load_assets_from_disk()

    # Start sandbox server if enabled
    from onemancompany.tools.sandbox import start_sandbox_server
    start_sandbox_server()

    # Restore ephemeral state from a recent snapshot (hot restart)
    _restore_ephemeral_state()

    # Register employees with the centralized EmployeeManager
    from onemancompany.core.agent_loop import register_agent, register_self_hosted, start_all_loops, stop_all_loops
    from onemancompany.core.config import HR_ID as _HR_ID, COO_ID as _COO_ID, EA_ID as _EA_ID, CSO_ID as _CSO_ID
    from onemancompany.agents.hr_agent import HRAgent
    from onemancompany.agents.coo_agent import COOAgent
    from onemancompany.agents.ea_agent import EAAgent
    from onemancompany.agents.cso_agent import CSOAgent

    # Start Boss Online MCP server (persistent subprocess)
    from onemancompany.agents.hr_agent import start_boss_online, stop_boss_online
    await start_boss_online()

    # Founding employees — LangChain agents
    register_agent(_HR_ID, HRAgent())
    register_agent(_COO_ID, COOAgent())
    register_agent(_EA_ID, EAAgent())
    register_agent(_CSO_ID, CSOAgent())

    # Non-founding employees — register ALL in EmployeeManager (unified dispatch)
    from onemancompany.agents.base import EmployeeAgent
    from onemancompany.core.config import FOUNDING_LEVEL, employee_configs as _emp_cfgs
    from onemancompany.core.state import company_state
    founding_ids = {_HR_ID, _COO_ID, _EA_ID, _CSO_ID, "00001"}
    for emp_id, emp in company_state.employees.items():
        if emp_id in founding_ids:
            continue
        if emp.level >= FOUNDING_LEVEL:
            continue
        if emp.remote:
            continue
        _cfg = _emp_cfgs.get(emp_id)
        if _cfg and _cfg.hosting == "self":
            # Self-hosted: register with ClaudeSessionLauncher (on-demand CLI sessions)
            register_self_hosted(emp_id)
            print(f"[startup] Registered self-hosted {emp.name} ({emp_id}) — on-demand sessions")
            continue
        register_agent(emp_id, EmployeeAgent(emp_id))
        print(f"[startup] Registered {emp.name} ({emp_id}) — LangChain agent")

    await start_all_loops()

    # Start background WebSocket event broadcaster
    broadcaster_task = asyncio.create_task(ws_manager.event_broadcaster())

    # Start file watcher for soft reload
    watcher_task = asyncio.create_task(_start_file_watcher())

    # Start periodic reload as watchdog fallback
    periodic_task = asyncio.create_task(_periodic_reload_loop())

    # Start heartbeat loop (API connection checks every 60s)
    heartbeat_task = asyncio.create_task(_heartbeat_loop())

    print(f"🏢 One Man Company HQ is open!")
    print(f"   Frontend: http://localhost:{app.state.port if hasattr(app.state, 'port') else 8000}")
    yield

    # Stop agent loops
    await stop_all_loops()

    # Stop Boss Online MCP server
    await stop_boss_online()

    # Save ephemeral state before shutdown
    _save_ephemeral_state()

    # Stop sandbox server and cleanup container
    from onemancompany.tools.sandbox import stop_sandbox_server, cleanup_sandbox
    await cleanup_sandbox()
    stop_sandbox_server()

    watcher_task.cancel()
    broadcaster_task.cancel()
    heartbeat_task.cancel()
    try:
        await asyncio.gather(broadcaster_task, watcher_task, heartbeat_task, return_exceptions=True)
    except asyncio.CancelledError:
        pass


app = FastAPI(title="One Man Company", lifespan=lifespan)
app.add_middleware(NoCacheStaticMiddleware)
app.include_router(router)
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


def run() -> None:
    from onemancompany.core.config import settings, PROJECT_ROOT

    app.state.port = settings.port
    uvicorn.run(
        "onemancompany.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        reload_dirs=[str(PROJECT_ROOT / "src"), str(PROJECT_ROOT / "frontend")],
        reload_includes=["*.py", "*.js", "*.css", "*.html"],
    )


if __name__ == "__main__":
    run()
