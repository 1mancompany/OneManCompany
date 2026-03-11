"""OneManCompany — FastAPI entrypoint."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from loguru import logger
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# Load .env from data root (.onemancompany/) first, fall back to source root
_data_root = Path.cwd() / ".onemancompany"
_source_root = Path(__file__).parent.parent.parent

load_dotenv(_data_root / ".env", override=False)
# Also load from source root for backward compatibility during migration
load_dotenv(_source_root / ".env", override=False)

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
# Pending code changes (CEO-controlled hot reload)
# ---------------------------------------------------------------------------
_pending_code_changes: set[str] = set()

# ---------------------------------------------------------------------------
# State snapshot persistence (Tier 2: survive hard restarts)
#
# Each module registers its own snapshot provider via @snapshot_provider.
# main.py just calls save_snapshot() / restore_snapshot() — no per-module
# knowledge needed here.  See core/snapshot.py for the harness.
# ---------------------------------------------------------------------------

def _ensure_snapshot_providers_loaded() -> None:
    """Import modules that register @snapshot_provider decorators.

    Provider registration happens at module import time.  Most of these
    modules are imported elsewhere during startup, but we import them
    explicitly here to guarantee registration order is deterministic.
    """
    import onemancompany.core.state  # noqa: F401 — company_state provider
    import onemancompany.core.file_editor  # noqa: F401 — pending_file_edits
    import onemancompany.core.resolutions  # noqa: F401 — _task_edits
    import onemancompany.core.routine  # noqa: F401 — pending_reports
    import onemancompany.agents.recruitment  # noqa: F401 — candidates + project ctx
    import onemancompany.agents.coo_agent  # noqa: F401 — hiring requests
    import onemancompany.api.routes  # noqa: F401 — inquiry sessions, COO hire queue, remote workers


def _save_ephemeral_state() -> None:
    """Serialize all ephemeral state to disk via the snapshot harness."""
    from onemancompany.core.snapshot import save_snapshot
    _ensure_snapshot_providers_loaded()
    save_snapshot()


def _restore_ephemeral_state() -> None:
    """Restore ephemeral state from a recent snapshot via the snapshot harness."""
    from onemancompany.core.snapshot import restore_snapshot
    _ensure_snapshot_providers_loaded()
    restore_snapshot()


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
# Code change watcher (CEO-controlled hot reload)
# ---------------------------------------------------------------------------

async def _start_code_watcher() -> None:
    """Watch src/ and frontend/ for code changes.

    - Frontend files (.js/.css/.html in frontend/) → notify frontend to reload (no backend restart)
    - Backend files (.py in src/) → auto-schedule graceful restart when idle
    """
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    from onemancompany.core.config import PROJECT_ROOT
    from onemancompany.core.events import CompanyEvent, event_bus
    from onemancompany.core.vessel import employee_manager

    DEBOUNCE_SECONDS = 2.0
    FRONTEND_EXTENSIONS = {".js", ".css", ".html"}
    BACKEND_EXTENSIONS = {".py"}

    class _CodeChangeHandler(FileSystemEventHandler):
        def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
            self._loop = loop
            self._pending_frontend: asyncio.TimerHandle | None = None
            self._pending_backend: asyncio.TimerHandle | None = None
            self._frontend_changes: set[str] = set()
            self._backend_changes: set[str] = set()

        def _on_change(self, path: str) -> None:
            p = Path(path)
            # Determine if frontend or backend
            frontend_dir_str = str(FRONTEND_DIR)
            if path.startswith(frontend_dir_str) and p.suffix in FRONTEND_EXTENSIONS:
                self._frontend_changes.add(path)
                if self._pending_frontend:
                    self._pending_frontend.cancel()
                self._pending_frontend = self._loop.call_later(DEBOUNCE_SECONDS, self._notify_frontend)
            elif p.suffix in BACKEND_EXTENSIONS:
                self._backend_changes.add(path)
                _pending_code_changes.add(path)
                if self._pending_backend:
                    self._pending_backend.cancel()
                self._pending_backend = self._loop.call_later(DEBOUNCE_SECONDS, self._handle_backend)

        def _notify_frontend(self) -> None:
            self._pending_frontend = None
            files = sorted(self._frontend_changes)
            self._frontend_changes.clear()
            if not files:
                return
            asyncio.ensure_future(event_bus.publish(
                CompanyEvent(
                    type="frontend_update_available",
                    payload={"changed_files": files, "count": len(files)},
                    agent="SYSTEM",
                )
            ))
            print(f"[code-watcher] {len(files)} frontend file(s) changed, notifying browser")

        def _handle_backend(self) -> None:
            self._pending_backend = None
            files = sorted(self._backend_changes)
            self._backend_changes.clear()
            if not files:
                return

            # Notify CEO of pending changes
            asyncio.ensure_future(event_bus.publish(
                CompanyEvent(
                    type="code_update_available",
                    payload={"changed_files": files, "count": len(files)},
                    agent="SYSTEM",
                )
            ))

            # Auto-schedule graceful restart
            if employee_manager.is_idle():
                print(f"[code-watcher] {len(files)} backend file(s) changed, restarting now (idle)")
                asyncio.ensure_future(employee_manager._trigger_graceful_restart())
            else:
                employee_manager._restart_pending = True
                print(f"[code-watcher] {len(files)} backend file(s) changed, restart deferred (tasks running)")
                asyncio.ensure_future(event_bus.publish(
                    CompanyEvent(
                        type="backend_restart_scheduled",
                        payload={"reason": "Waiting for tasks to complete", "immediate": False},
                        agent="SYSTEM",
                    )
                ))

        def on_modified(self, event):
            if event.is_directory:
                return
            self._on_change(event.src_path)

        def on_created(self, event):
            if event.is_directory:
                return
            self._on_change(event.src_path)

    loop = asyncio.get_running_loop()
    handler = _CodeChangeHandler(loop)
    observer = Observer()

    src_dir = str(PROJECT_ROOT / "src")
    frontend_dir = str(FRONTEND_DIR)
    observer.schedule(handler, src_dir, recursive=True)
    observer.schedule(handler, frontend_dir, recursive=True)

    observer.daemon = True
    observer.start()
    print(f"[code-watcher] Watching {src_dir} (backend) and {frontend_dir} (frontend)")

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        observer.stop()
        observer.join(timeout=2)


# ---------------------------------------------------------------------------
# Data directory bootstrap
# ---------------------------------------------------------------------------

def _bootstrap_data_dir() -> None:
    """Check that .onemancompany/ exists; abort with hint if not.

    Users should run ``onemancompany-init`` to set up the workspace
    interactively before starting the server.
    """
    from onemancompany.core.config import DATA_ROOT

    if DATA_ROOT.exists():
        return  # already initialised

    print(
        "\n  \033[1;33m⚠  .onemancompany/ not found.\033[0m\n\n"
        "  Run the setup wizard first:\n\n"
        "    \033[1;36monemancompany-init\033[0m\n\n"
        "  Or:  python -m onemancompany.onboard\n"
    )
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bootstrap data directory on first run
    _bootstrap_data_dir()

    # Eagerly load assets (tools, meeting rooms) into company_state
    from onemancompany.agents.coo_agent import _load_assets_from_disk
    _load_assets_from_disk()
    from onemancompany.core.layout import compute_asset_layout
    from onemancompany.core.state import company_state as _cs
    compute_asset_layout(_cs, _cs.office_layout)

    # Register internal tools (base + gated) into tool_registry
    import onemancompany.agents.common_tools  # noqa: F401 — triggers _register_all_internal_tools()

    # Register asset tools (gmail, roblox, etc.) from company/assets/tools/
    from onemancompany.core.tool_registry import tool_registry
    tool_registry.load_asset_tools()

    # Discover and load view plugins
    from onemancompany.core.plugin_registry import plugin_registry
    plugin_registry.discover_and_load()

    # Start sandbox server if enabled
    from onemancompany.tools.sandbox import start_sandbox_server
    start_sandbox_server()

    # Kill orphaned claude session processes from a previous server run.
    # Session IDs are preserved in sessions.json so --resume works for future tasks.
    from onemancompany.core.claude_session import cleanup_orphan_sessions
    orphans_killed = cleanup_orphan_sessions()
    if orphans_killed:
        print(f"[startup] Killed {orphans_killed} orphaned claude session(s) — sessions preserved for --resume")

    # Restore ephemeral state from a recent snapshot (hot restart)
    _restore_ephemeral_state()

    # Register employees with the centralized EmployeeManager
    from onemancompany.core.agent_loop import register_agent, register_self_hosted, start_all_loops, stop_all_loops
    from onemancompany.core.config import HR_ID as _HR_ID, COO_ID as _COO_ID, EA_ID as _EA_ID, CSO_ID as _CSO_ID
    from onemancompany.agents.hr_agent import HRAgent
    from onemancompany.agents.coo_agent import COOAgent
    from onemancompany.agents.ea_agent import EAAgent
    from onemancompany.agents.cso_agent import CSOAgent

    # Start Talent Market MCP connection (skips gracefully if no API key)
    from onemancompany.agents.recruitment import start_boss_online, stop_boss_online
    try:
        await start_boss_online()
    except Exception as e:
        logger.warning("Talent Market connection failed (configure in Settings): {}", e)

    # Migrate existing employees from agent/ to vessel/ directory structure
    from onemancompany.core.vessel_config import migrate_agent_to_vessel, load_vessel_config
    from onemancompany.core.config import EMPLOYEES_DIR as _EMPLOYEES_DIR, employee_configs as _emp_cfgs

    # Founding employees — hosting-aware registration
    _founding_agents = {
        _HR_ID: HRAgent, _COO_ID: COOAgent,
        _EA_ID: EAAgent, _CSO_ID: CSOAgent,
    }
    _registered_founding = set()
    for _fid, _agent_cls in _founding_agents.items():
        _fcfg = _emp_cfgs.get(_fid)
        _emp_dir_f = _EMPLOYEES_DIR / _fid
        _f_vessel = load_vessel_config(_emp_dir_f) if _emp_dir_f.exists() else None
        if _fcfg and _fcfg.hosting == "self":
            register_self_hosted(_fid, config=_f_vessel)
            print(f"[startup] Registered self-hosted founding {_fid}")
        else:
            register_agent(_fid, _agent_cls(), config=_f_vessel)
        _registered_founding.add(_fid)
    for _emp_dir in _EMPLOYEES_DIR.iterdir():
        if _emp_dir.is_dir():
            if migrate_agent_to_vessel(_emp_dir):
                print(f"[startup] Migrated {_emp_dir.name} agent/ → vessel/")

    # Non-founding employees — register ALL in EmployeeManager (unified dispatch)
    from onemancompany.agents.base import EmployeeAgent
    from onemancompany.core.config import FOUNDING_LEVEL
    from onemancompany.core.state import company_state
    from onemancompany.core.config import FOUNDING_IDS
    for emp_id, emp in company_state.employees.items():
        if emp_id in FOUNDING_IDS:
            continue
        if emp.level >= FOUNDING_LEVEL:
            continue
        if emp.remote:
            continue

        # Load VesselConfig for per-employee DNA
        _emp_dir = _EMPLOYEES_DIR / emp_id
        _vessel_cfg = load_vessel_config(_emp_dir) if _emp_dir.exists() else None

        _cfg = _emp_cfgs.get(emp_id)
        if _cfg and _cfg.hosting == "self":
            # Self-hosted: register with ClaudeSessionExecutor (on-demand CLI sessions)
            register_self_hosted(emp_id, config=_vessel_cfg)
            print(f"[startup] Registered self-hosted {emp.name} ({emp_id}) — on-demand sessions")
            continue
        _runner = EmployeeAgent(emp_id)
        register_agent(emp_id, _runner, config=_vessel_cfg)
        print(f"[startup] Registered {emp.name} ({emp_id}) — LangChain agent")

    await start_all_loops()

    # Restore persisted tasks from per-employee task files
    from onemancompany.core.vessel import employee_manager as _em
    restored_count = _em.restore_persisted_tasks()
    if restored_count:
        print(f"[startup] Restored {restored_count} task(s) from disk — auto-resuming")
        _em.drain_pending()

    # Recover PENDING dependency tasks whose deps resolved before restart
    await _em._recover_pending_dependencies()

    # Start background WebSocket event broadcaster
    broadcaster_task = asyncio.create_task(ws_manager.event_broadcaster())

    # Start file watcher for soft reload
    watcher_task = asyncio.create_task(_start_file_watcher())

    # Start periodic reload as watchdog fallback
    periodic_task = asyncio.create_task(_periodic_reload_loop())

    # Start heartbeat loop (API connection checks every 60s)
    heartbeat_task = asyncio.create_task(_heartbeat_loop())

    # Start code change watcher (CEO-controlled hot reload)
    code_watcher_task = asyncio.create_task(_start_code_watcher())

    # Restore persisted automations (crons + webhooks)
    from onemancompany.core.automation import restore_all_crons, restore_all_webhooks
    _crons_restored = restore_all_crons()
    _webhooks_restored = restore_all_webhooks()
    if _crons_restored or _webhooks_restored:
        print(f"[startup] Restored {_crons_restored} cron(s), {_webhooks_restored} webhook(s)")

    print(f"🏢 One Man Company HQ is open!")
    print(f"   Frontend: http://localhost:{app.state.port if hasattr(app.state, 'port') else 8000}")
    yield

    # Stop agent loops
    await stop_all_loops()

    # Stop automations (crons + webhooks)
    from onemancompany.core.automation import stop_all_automations
    automations_stopped = await stop_all_automations()
    if automations_stopped:
        print(f"[shutdown] Stopped {automations_stopped} automation(s)")

    # Stop persistent Claude daemons
    from onemancompany.core.claude_session import stop_all_daemons
    daemons_stopped = await stop_all_daemons()
    if daemons_stopped:
        print(f"[shutdown] Stopped {daemons_stopped} Claude daemon(s)")

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
    code_watcher_task.cancel()
    try:
        await asyncio.gather(broadcaster_task, watcher_task, heartbeat_task, code_watcher_task, return_exceptions=True)
    except asyncio.CancelledError:
        print("[shutdown] Background tasks cancelled")


app = FastAPI(title="One Man Company", lifespan=lifespan)
app.add_middleware(NoCacheStaticMiddleware)
app.include_router(router)
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


def run() -> None:
    from onemancompany.core.config import settings

    app.state.port = settings.port
    uvicorn.run(
        "onemancompany.main:app",
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":  # pragma: no cover
    run()
