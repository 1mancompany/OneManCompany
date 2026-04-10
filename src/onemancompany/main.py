"""OneManCompany — FastAPI entrypoint."""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from loguru import logger
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# ---------------------------------------------------------------------------
# Single-file constants
# ---------------------------------------------------------------------------
from onemancompany.core.config import ENCODING_UTF8, ENV_OMC_DEBUG, LAUNCH_SH_FILENAME, LogLevel, DATA_DIR_NAME, DOT_ENV_FILENAME, PF_LEVEL, PF_REMOTE, SRC_DIR_NAME, SYSTEM_AGENT
from onemancompany.core.models import EventType, HostingMode

LOG_DIR_NAME = "logs"
FRONTEND_DIR_NAME = "frontend"
LOG_FILE_PATTERN = "omc_{time:YYYY-MM-DD}.log"
LOG_ROTATION = "00:00"
LOG_RETENTION = "7 days"
CONFIG_DEBOUNCE_SECONDS = 0.5
CONFIG_WATCH_EXTENSIONS = {".yaml", ".yml", ".md"}
CODE_DEBOUNCE_SECONDS = 2.0
FRONTEND_EXTENSIONS = {".js", ".css", ".html"}
BACKEND_EXTENSIONS = {".py"}

# Hot-reload result dict keys
RELOAD_KEY_STATUS = "status"
RELOAD_KEY_UPDATED = "employees_updated"
RELOAD_KEY_ADDED = "employees_added"
RELOAD_KEY_CONFIG = "config_reloaded"
WATCHER_SLEEP_SECONDS = 3600
OBSERVER_JOIN_TIMEOUT = 2

# ---------------------------------------------------------------------------

# Configure loguru: DEBUG level when OMC_DEBUG=1, else INFO
_debug_mode = os.environ.get(ENV_OMC_DEBUG, "0") == "1"
_log_level = LogLevel.DEBUG if _debug_mode else LogLevel.INFO
logger.remove()
logger.add(sys.stderr, level=_log_level)

# Always write logs to file
_log_dir = Path.cwd() / DATA_DIR_NAME / LOG_DIR_NAME
_log_dir.mkdir(parents=True, exist_ok=True)
logger.add(
    _log_dir / LOG_FILE_PATTERN,
    level=_log_level,
    rotation=LOG_ROTATION,
    retention=LOG_RETENTION,
    encoding=ENCODING_UTF8,
)

# Load .env from data root (.onemancompany/) first, fall back to source root
_data_root = Path.cwd() / DATA_DIR_NAME
_source_root = Path(__file__).parent.parent.parent

load_dotenv(_data_root / DOT_ENV_FILENAME, override=False)
# Also load from source root for backward compatibility during migration
load_dotenv(_source_root / DOT_ENV_FILENAME, override=False)

from onemancompany.api.routes import router
from onemancompany.api.websocket import ws_manager


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    """Disable browser caching for frontend static files during development."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        if request.url.path.endswith((".js", ".css", ".html")) or request.url.path == "/":
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

FRONTEND_DIR = Path(__file__).parent.parent.parent / FRONTEND_DIR_NAME

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
    import onemancompany.api.routes  # noqa: F401 — COO hire queue, remote workers


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
    from onemancompany.core.models import DecisionStatus
    from onemancompany.core.state import request_reload

    class _ReloadHandler(FileSystemEventHandler):
        def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
            self._loop = loop
            self._pending: asyncio.TimerHandle | None = None

        def _schedule_reload(self) -> None:
            if self._pending:
                self._pending.cancel()
            self._pending = self._loop.call_later(CONFIG_DEBOUNCE_SECONDS, self._do_reload)

        def _do_reload(self) -> None:
            self._pending = None
            try:
                result = request_reload()
                if result.get(RELOAD_KEY_STATUS) == DecisionStatus.DEFERRED:
                    print("[hot-reload] Deferred: agents are busy, will reload when idle")
                else:
                    updated = result.get(RELOAD_KEY_UPDATED, [])
                    added = result.get(RELOAD_KEY_ADDED, [])
                    if updated or added:
                        print(f"[hot-reload] Reloaded from disk: {len(updated)} updated, {len(added)} added")
                    if result.get(RELOAD_KEY_CONFIG):
                        print("[hot-reload] config.yaml reloaded")
            except Exception as e:
                print(f"[hot-reload] Error during reload: {e}")

        def on_modified(self, event):
            if event.is_directory:
                return
            if Path(event.src_path).suffix in CONFIG_WATCH_EXTENSIONS:
                self._schedule_reload()

        def on_created(self, event):
            if event.is_directory:
                return
            if Path(event.src_path).suffix in CONFIG_WATCH_EXTENSIONS:
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
            await asyncio.sleep(WATCHER_SLEEP_SECONDS)
    except asyncio.CancelledError:
        observer.stop()
        observer.join(timeout=OBSERVER_JOIN_TIMEOUT)


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

    from onemancompany.core.config import SOURCE_ROOT
    from onemancompany.core.events import CompanyEvent, event_bus
    from onemancompany.core.vessel import employee_manager


    # Build set of founding employee manifest paths to watch
    from onemancompany.core.config import EMPLOYEES_DIR, EXEC_IDS, MANIFEST_FILENAME, invalidate_manifest_cache
    _founding_manifest_paths = {
        str(EMPLOYEES_DIR / eid / MANIFEST_FILENAME) for eid in EXEC_IDS
    }

    class _CodeChangeHandler(FileSystemEventHandler):
        def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
            self._loop = loop
            self._pending_frontend: asyncio.TimerHandle | None = None
            self._pending_backend: asyncio.TimerHandle | None = None
            self._pending_manifest: asyncio.TimerHandle | None = None
            self._frontend_changes: set[str] = set()
            self._backend_changes: set[str] = set()
            self._manifest_changes: set[str] = set()

        def _on_change(self, path: str) -> None:
            p = Path(path)
            # Founding employee manifest.json — invalidate cache + graceful restart
            if path in _founding_manifest_paths:
                self._manifest_changes.add(path)
                if self._pending_manifest:
                    self._pending_manifest.cancel()
                self._pending_manifest = self._loop.call_later(CODE_DEBOUNCE_SECONDS, self._handle_manifest)
                return
            # Determine if frontend or backend
            frontend_dir_str = str(FRONTEND_DIR)
            if path.startswith(frontend_dir_str) and p.suffix in FRONTEND_EXTENSIONS:
                self._frontend_changes.add(path)
                if self._pending_frontend:
                    self._pending_frontend.cancel()
                self._pending_frontend = self._loop.call_later(CODE_DEBOUNCE_SECONDS, self._notify_frontend)
            elif p.suffix in BACKEND_EXTENSIONS:
                self._backend_changes.add(path)
                _pending_code_changes.add(path)
                if self._pending_backend:
                    self._pending_backend.cancel()
                self._pending_backend = self._loop.call_later(CODE_DEBOUNCE_SECONDS, self._handle_backend)

        def _notify_frontend(self) -> None:
            self._pending_frontend = None
            files = sorted(self._frontend_changes)
            self._frontend_changes.clear()
            if not files:
                return
            asyncio.ensure_future(event_bus.publish(
                CompanyEvent(
                    type=EventType.FRONTEND_UPDATE_AVAILABLE,
                    payload={"changed_files": files, "count": len(files)},
                    agent=SYSTEM_AGENT,
                )
            ))
            print(f"[code-watcher] {len(files)} frontend file(s) changed, notifying browser")

        def _handle_manifest(self) -> None:
            self._pending_manifest = None
            files = sorted(self._manifest_changes)
            self._manifest_changes.clear()
            if not files:
                return

            # Invalidate manifest cache for changed employees
            for f in files:
                emp_id = Path(f).parent.name
                invalidate_manifest_cache(emp_id)
                print(f"[code-watcher] Invalidated manifest cache for {emp_id}")

            # Notify and schedule graceful restart (same as backend changes)
            asyncio.ensure_future(event_bus.publish(
                CompanyEvent(
                    type=EventType.CODE_UPDATE_AVAILABLE,
                    payload={"changed_files": files, "count": len(files), "reason": "Founding employee manifest changed"},
                    agent=SYSTEM_AGENT,
                )
            ))
            if employee_manager.is_idle():
                print(f"[code-watcher] Founding manifest changed, restarting now (idle)")
                asyncio.ensure_future(employee_manager._trigger_graceful_restart())
            else:
                employee_manager._restart_pending = True
                print(f"[code-watcher] Founding manifest changed, restart deferred (tasks running)")
                asyncio.ensure_future(event_bus.publish(
                    CompanyEvent(
                        type=EventType.BACKEND_RESTART_SCHEDULED,
                        payload={"reason": "Founding employee config changed, waiting for tasks to complete", "immediate": False},
                        agent=SYSTEM_AGENT,
                    )
                ))

        def _handle_backend(self) -> None:
            self._pending_backend = None
            files = sorted(self._backend_changes)
            self._backend_changes.clear()
            if not files:
                return

            # Notify CEO of pending changes
            asyncio.ensure_future(event_bus.publish(
                CompanyEvent(
                    type=EventType.CODE_UPDATE_AVAILABLE,
                    payload={"changed_files": files, "count": len(files)},
                    agent=SYSTEM_AGENT,
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
                        type=EventType.BACKEND_RESTART_SCHEDULED,
                        payload={"reason": "Waiting for tasks to complete", "immediate": False},
                        agent=SYSTEM_AGENT,
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

    src_dir = str(SOURCE_ROOT / SRC_DIR_NAME)
    frontend_dir = str(FRONTEND_DIR)
    employees_dir = str(EMPLOYEES_DIR)
    observer.schedule(handler, src_dir, recursive=True)
    observer.schedule(handler, frontend_dir, recursive=True)
    observer.schedule(handler, employees_dir, recursive=True)

    observer.daemon = True
    observer.start()
    print(f"[code-watcher] Watching {src_dir} (backend), {frontend_dir} (frontend), and founding manifests")

    try:
        while True:
            await asyncio.sleep(WATCHER_SLEEP_SECONDS)
    except asyncio.CancelledError:
        observer.stop()
        observer.join(timeout=OBSERVER_JOIN_TIMEOUT)


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
        "  Run the setup process first:\n\n"
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

    # Validate AUTH_CHOICE_GROUPS ↔ PROVIDER_REGISTRY consistency
    from onemancompany.core.auth_choices import validate_registry_consistency
    _auth_warnings = validate_registry_consistency()
    for _w in _auth_warnings:
        logger.warning("Auth config: {}", _w)

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

    # Rebuild ConversationService index from disk + recover stuck conversations
    from onemancompany.api.routes import _conversation_service
    _conversation_service.rebuild_index()
    logger.info("[startup] ConversationService index rebuilt: {} conversations", len(_conversation_service._index))
    _conv_recovered = await _conversation_service.recover()
    if _conv_recovered:
        logger.info("[startup] Recovered {} stuck conversation(s)", _conv_recovered)

    # Register employees with the centralized EmployeeManager
    from onemancompany.core.agent_loop import register_agent, register_self_hosted, start_all_loops, stop_all_loops
    from onemancompany.core.config import HR_ID as _HR_ID, COO_ID as _COO_ID, EA_ID as _EA_ID, CSO_ID as _CSO_ID
    from onemancompany.agents.hr_agent import HRAgent
    from onemancompany.agents.coo_agent import COOAgent
    from onemancompany.agents.ea_agent import EAAgent
    from onemancompany.agents.cso_agent import CSOAgent

    # Start Talent Market MCP connection (skips gracefully if no API key)
    from onemancompany.agents.recruitment import start_talent_market, stop_talent_market
    try:
        await start_talent_market()
    except Exception as e:
        logger.warning("Talent Market connection failed (configure in Settings): {}", e)

    from onemancompany.core.vessel_config import load_vessel_config
    from onemancompany.core.config import EMPLOYEES_DIR as _EMPLOYEES_DIR, employee_configs as _emp_cfgs

    # Founding employees — hosting-aware registration
    from onemancompany.core.vessel import register_founding_employee
    _founding_agents = {
        _HR_ID: HRAgent, _COO_ID: COOAgent,
        _EA_ID: EAAgent, _CSO_ID: CSOAgent,
    }
    _registered_founding = set()
    for _fid, _agent_cls in _founding_agents.items():
        register_founding_employee(_fid, _agent_cls, _emp_cfgs, _EMPLOYEES_DIR)
        _registered_founding.add(_fid)

    # Sync default skills (SKILL.md) for all existing employees on startup
    from onemancompany.agents.onboarding import _inject_default_skills
    for _emp_dir in sorted(_EMPLOYEES_DIR.iterdir()):
        if _emp_dir.is_dir() and (_emp_dir / "profile.yaml").exists():
            _skills_dir = _emp_dir / "skills"
            _skills_dir.mkdir(exist_ok=True)
            _inject_default_skills(_skills_dir)

    # Register CeoExecutor for CEO (virtual employee — routes to TUI, no LLM)
    from onemancompany.core.ceo_broker import CeoExecutor
    from onemancompany.core.config import CEO_ID
    from onemancompany.core.vessel import employee_manager as _ceo_em
    _ceo_em.executors[CEO_ID] = CeoExecutor()
    logger.info("[startup] Registered CEO ({}) — CeoExecutor (TUI routing)", CEO_ID)

    # Non-founding employees — register ALL in EmployeeManager (unified dispatch)
    from onemancompany.agents.base import EmployeeAgent
    from onemancompany.core.config import FOUNDING_LEVEL, FOUNDING_IDS
    from onemancompany.core import store as _store_mod
    for emp_id, emp_data in _store_mod.load_all_employees().items():
        if emp_id in FOUNDING_IDS:
            continue
        if emp_data.get(PF_LEVEL, 0) >= FOUNDING_LEVEL:
            continue
        if emp_data.get(PF_REMOTE, False):
            continue

        # Load VesselConfig for per-employee DNA
        _emp_dir = _EMPLOYEES_DIR / emp_id
        _vessel_cfg = load_vessel_config(_emp_dir) if _emp_dir.exists() else None

        _cfg = _emp_cfgs.get(emp_id)
        if _cfg and _cfg.hosting == HostingMode.SELF:
            # Self-hosted: register with ClaudeSessionExecutor (on-demand CLI sessions)
            register_self_hosted(emp_id, config=_vessel_cfg)
            print(f"[startup] Registered self-hosted {emp_data.get('name', emp_id)} ({emp_id}) — on-demand sessions")
            continue

        # Company-hosted with launch.sh → SubprocessExecutor (foreground per-task)
        _launch_sh = _emp_dir / LAUNCH_SH_FILENAME
        if _launch_sh.exists():
            from onemancompany.core.subprocess_executor import SubprocessExecutor
            from onemancompany.core.vessel import employee_manager as _em_mgr
            _executor = SubprocessExecutor(emp_id, script_path=str(_launch_sh))
            _em_mgr.register(emp_id, _executor, config=_vessel_cfg)
            logger.info("[startup] Registered {} ({}) — SubprocessExecutor (launch.sh)", emp_data.get('name', emp_id), emp_id)
            continue

        _runner = EmployeeAgent(emp_id)
        register_agent(emp_id, _runner, config=_vessel_cfg)
        print(f"[startup] Registered {emp_data.get('name', emp_id)} ({emp_id}) — LangChain agent")

    # Load skill hooks for all registered employees
    from onemancompany.core.skill_hooks import load_hooks_from_skills
    _total_hooks = 0
    for _emp_dir in sorted(_EMPLOYEES_DIR.iterdir()):
        if _emp_dir.is_dir() and (_emp_dir / "profile.yaml").exists():
            _total_hooks += load_hooks_from_skills(_emp_dir.name)
    if _total_hooks:
        logger.info("[startup] Loaded {} skill hook(s) across all employees", _total_hooks)

    await start_all_loops()

    # Restore persisted tasks from per-employee task files
    from onemancompany.core.vessel import employee_manager as _em
    restored_count = _em.restore_persisted_tasks()
    if restored_count:
        print(f"[startup] Restored {restored_count} task(s) from disk — auto-resuming")
        _em.drain_pending()

    # Recover projects stuck in pending_confirmation (legacy state from old CEO inbox).
    # Complete the iteration and archive the project.
    from onemancompany.core.project_archive import (
        archive_project,
        complete_project,
        list_projects,
        ITER_STATUS_PENDING_CONFIRMATION,
        ITER_STATUS_COMPLETED,
        PROJECT_STATUS_ARCHIVED,
        load_iteration,
        update_project_status,
    )
    for _proj in list_projects():
        if _proj.get("status") == PROJECT_STATUS_ARCHIVED:
            continue
        _iters = _proj.get("iterations", [])
        if not _iters:
            continue
        _latest = load_iteration(_proj.get("project_id", "").split("/")[0], _iters[-1])
        if _latest and _latest.get("status") == ITER_STATUS_PENDING_CONFIRMATION:
            _pid = _proj.get("project_id", "")
            _iter_key = f"{_pid}/{_iters[-1]}" if "/" not in _pid else _pid
            # 1. Complete the iteration (sets status=completed, completed_at, etc.)
            complete_project(_iter_key, "Auto-confirmed on restart")
            # 2. Archive the project (sets project.yaml status=archived)
            _slug = _pid.split("/")[0] if "/" in _pid else _pid
            archive_project(_slug)
            print(f"[startup] Auto-confirmed and archived pending project: {_pid}")

    # Start background WebSocket event broadcaster
    broadcaster_task = asyncio.create_task(ws_manager.event_broadcaster())

    # Start file watcher for soft reload
    watcher_task = asyncio.create_task(_start_file_watcher())

    # Start system cron registry (heartbeat, review_reminder, config_reload)
    from onemancompany.core import system_cron as _system_cron_mod  # triggers @system_cron registrations
    _system_cron_mod.system_cron_manager.start_all()

    # Start background task manager (restores persisted state, marks stale tasks stopped)
    from onemancompany.core.background_tasks import background_task_manager
    background_task_manager.start()

    # Start code change watcher (CEO-controlled hot reload)
    code_watcher_task = asyncio.create_task(_start_code_watcher())

    # Start sync tick (broadcasts dirty state categories every 3s)
    from onemancompany.core.sync_tick import start_sync_tick
    sync_tick_task = asyncio.create_task(start_sync_tick())

    # Restore persisted automations (crons + webhooks)
    from onemancompany.core.automation import restore_all_crons, restore_all_webhooks
    _crons_restored = restore_all_crons()
    _webhooks_restored = restore_all_webhooks()
    if _crons_restored or _webhooks_restored:
        print(f"[startup] Restored {_crons_restored} cron(s), {_webhooks_restored} webhook(s)")

    from onemancompany.core.config import settings as _settings
    from importlib.metadata import version as _pkg_version
    try:
        _app_ver = _pkg_version("onemancompany")
    except Exception:
        _app_ver = "dev"
    print(f"🏢 One Man Company HQ v{_app_ver} is running!")
    print(f"   Frontend: http://localhost:{_settings.port}")

    # Background update checker
    from onemancompany.core.update_checker import start_update_checker
    asyncio.create_task(start_update_checker())

    yield

    # Stop agent loops
    await stop_all_loops()

    # Stop system crons
    await _system_cron_mod.system_cron_manager.stop_all()

    # Stop background task manager (terminates all running processes gracefully)
    await background_task_manager.stop_all()

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

    # Stop Talent Market MCP connection
    await stop_talent_market()

    # Save ephemeral state before shutdown
    _save_ephemeral_state()

    # Stop sandbox server and cleanup container
    from onemancompany.tools.sandbox import stop_sandbox_server, cleanup_sandbox
    await cleanup_sandbox()
    stop_sandbox_server()

    # Cancel active conversation adapter tasks
    from onemancompany.api.routes import _active_adapter_tasks
    if _active_adapter_tasks:
        logger.info("[shutdown] Cancelling {} active adapter task(s)", len(_active_adapter_tasks))
        for t in _active_adapter_tasks:
            t.cancel()
        await asyncio.gather(*_active_adapter_tasks, return_exceptions=True)
        _active_adapter_tasks.clear()

    watcher_task.cancel()
    broadcaster_task.cancel()
    code_watcher_task.cancel()
    sync_tick_task.cancel()
    try:
        await asyncio.gather(broadcaster_task, watcher_task, code_watcher_task, sync_tick_task, return_exceptions=True)
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
        loop="asyncio",
    )


if __name__ == "__main__":  # pragma: no cover
    run()
