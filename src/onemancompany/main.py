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

    snapshot = {
        "saved_at": time.time(),
        "activity_log": company_state.activity_log[-50:],
        "pending_file_edits": pending_file_edits,
        "pending_candidates": pending_candidates,
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

        print(f"Restored state snapshot ({age:.1f}s old): "
              f"{len(old_log)} log entries, "
              f"{len(restored_edits)} pending edits, "
              f"{len(restored_candidates)} candidate batches")

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

    # Start background WebSocket event broadcaster
    broadcaster_task = asyncio.create_task(ws_manager.event_broadcaster())

    # Start file watcher for soft reload
    watcher_task = asyncio.create_task(_start_file_watcher())

    print(f"🏢 One Man Company HQ is open!")
    print(f"   Frontend: http://localhost:{app.state.port if hasattr(app.state, 'port') else 8000}")
    yield

    # Save ephemeral state before shutdown
    _save_ephemeral_state()

    # Stop sandbox server and cleanup container
    from onemancompany.tools.sandbox import stop_sandbox_server, cleanup_sandbox
    await cleanup_sandbox()
    stop_sandbox_server()

    watcher_task.cancel()
    broadcaster_task.cancel()
    try:
        await asyncio.gather(broadcaster_task, watcher_task, return_exceptions=True)
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
