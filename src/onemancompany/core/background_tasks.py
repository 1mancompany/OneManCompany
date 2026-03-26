"""Background task manager — launch and manage long-running processes.

Singleton `background_task_manager` manages async subprocesses.
State persisted to company/background_tasks.yaml.
Output logs at company/background_tasks/{task_id}/output.log.
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml
from loguru import logger

MAX_CONCURRENT = 5
_MAX_RETAINED = 50
_TASKS_FILENAME = "background_tasks.yaml"


@dataclass
class BackgroundTask:
    id: str
    command: str
    description: str
    working_dir: str
    started_by: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "running"  # running | completed | failed | stopped
    pid: int | None = None
    returncode: int | None = None
    ended_at: str | None = None
    port: int | None = None
    address: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "command": self.command,
            "description": self.description,
            "working_dir": self.working_dir,
            "started_by": self.started_by,
            "started_at": self.started_at,
            "status": self.status,
            "pid": self.pid,
            "returncode": self.returncode,
            "ended_at": self.ended_at,
            "port": self.port,
            "address": self.address,
        }

    @classmethod
    def from_dict(cls, d: dict) -> BackgroundTask:
        return cls(
            id=d["id"],
            command=d["command"],
            description=d.get("description", ""),
            working_dir=d.get("working_dir", ""),
            started_by=d.get("started_by", ""),
            started_at=d.get("started_at", ""),
            status=d.get("status", "running"),
            pid=d.get("pid"),
            returncode=d.get("returncode"),
            ended_at=d.get("ended_at"),
            port=d.get("port"),
            address=d.get("address"),
        )


class BackgroundTaskManager:
    """Manages long-running background processes."""

    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            from onemancompany.core.config import COMPANY_DIR
            data_dir = COMPANY_DIR
        self._data_dir = Path(data_dir)
        self._tasks: dict[str, BackgroundTask] = {}
        self._processes: dict[str, "asyncio.subprocess.Process"] = {}
        self._monitors: dict[str, "asyncio.Task"] = {}

    @property
    def running_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == "running")

    @property
    def can_launch(self) -> bool:
        return self.running_count < MAX_CONCURRENT

    def get_task(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def get_all(self) -> list[BackgroundTask]:
        return sorted(self._tasks.values(), key=lambda t: t.started_at, reverse=True)

    def output_log_path(self, task_id: str) -> Path:
        return self._data_dir / "background_tasks" / task_id / "output.log"

    def _yaml_path(self) -> Path:
        return self._data_dir / _TASKS_FILENAME

    def _cleanup_old_tasks(self) -> None:
        """Remove oldest non-running tasks if we exceed retention limit."""
        if len(self._tasks) <= _MAX_RETAINED:
            return
        # Sort non-running tasks by started_at ascending (oldest first)
        removable = sorted(
            [t for t in self._tasks.values() if t.status != "running"],
            key=lambda t: t.started_at,
        )
        to_remove = len(self._tasks) - _MAX_RETAINED
        for t in removable[:to_remove]:
            del self._tasks[t.id]
            # Clean up log directory
            log_dir = self.output_log_path(t.id).parent
            if log_dir.exists():
                import shutil
                shutil.rmtree(log_dir, ignore_errors=True)
            logger.debug("[bg_tasks] Cleaned up old task {}", t.id)

    def _save(self) -> None:
        """Atomic save to YAML."""
        self._cleanup_old_tasks()
        import tempfile
        path = self._yaml_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"tasks": [t.to_dict() for t in self._tasks.values()]}
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".yaml")
        try:
            with os.fdopen(fd, "w") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            os.replace(tmp, str(path))
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _load(self) -> None:
        """Load tasks from YAML. Mark stale running tasks as stopped."""
        path = self._yaml_path()
        if not path.exists():
            return
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("Failed to load background tasks: {}", e)
            return
        for d in data.get("tasks", []):
            task = BackgroundTask.from_dict(d)
            if task.status == "running":
                if not self._is_pid_alive(task.pid):
                    task.status = "stopped"
                    task.ended_at = datetime.now(timezone.utc).isoformat()
                    logger.info("[bg_tasks] Marked stale task {} as stopped (PID {} gone)",
                                task.id, task.pid)
            self._tasks[task.id] = task
        self._save()

    @staticmethod
    def _is_pid_alive(pid: int | None) -> bool:
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    @staticmethod
    def _detect_port_from_command(command: str) -> int | None:
        """Extract port from command arguments like --port 3000, -p 8080, or -p3000."""
        m = re.search(r"(?:--port|--PORT|-p)[= ]?(\d{2,5})", command)
        return int(m.group(1)) if m else None

    def read_output_tail(self, task_id: str, lines: int = 50) -> str:
        """Read last N lines of a task's output log."""
        from collections import deque
        log_path = self.output_log_path(task_id)
        if not log_path.exists():
            return ""
        try:
            with open(log_path) as f:
                tail = deque(f, maxlen=lines)
            return "".join(tail)  # lines already have \n
        except Exception as e:
            logger.debug("[bg_tasks] Failed to read output for {}: {}", task_id, e)
            return ""

    def _broadcast_update(self, task: BackgroundTask) -> None:
        """Publish event via EventBus (fire-and-forget)."""
        try:
            from onemancompany.core.events import event_bus, CompanyEvent
            from onemancompany.core.models import EventType
            from onemancompany.core.async_utils import spawn_background
            spawn_background(event_bus.publish(CompanyEvent(
                type=EventType.BACKGROUND_TASK_UPDATE,
                payload=task.to_dict(),
                agent="SYSTEM",
            )))
        except Exception as e:
            logger.warning("[bg_tasks] Broadcast failed (no event loop?): {}", e)

    async def launch(
        self,
        command: str,
        description: str,
        working_dir: str,
        started_by: str,
    ) -> "BackgroundTask":
        """Launch a background process. Raises RuntimeError if at limit."""
        if not self.can_launch:
            raise RuntimeError(
                f"Background task limit reached ({MAX_CONCURRENT} max). "
                f"Stop a running task first."
            )

        task_id = uuid.uuid4().hex[:8]
        task = BackgroundTask(
            id=task_id,
            command=command,
            description=description,
            working_dir=working_dir or str(self._data_dir),
            started_by=started_by,
        )

        # Prepare output log directory
        log_path = self.output_log_path(task_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fd = open(log_path, "w")
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=log_fd,
                stderr=asyncio.subprocess.STDOUT,
                cwd=working_dir or None,
            )
        except Exception:
            log_fd.close()
            raise
        task.pid = proc.pid
        self._tasks[task_id] = task
        self._processes[task_id] = proc
        self._save()

        # Port detection from command args
        task.port = self._detect_port_from_command(command)
        if task.port:
            task.address = f"http://localhost:{task.port}"

        # Start monitor coroutine
        monitor = asyncio.create_task(self._monitor(task_id, proc, log_fd))
        self._monitors[task_id] = monitor

        logger.info("[bg_tasks] Launched {} (PID {}): {}", task_id, proc.pid, command[:80])
        self._broadcast_update(task)
        return task

    async def _monitor(self, task_id: str, proc, log_fd) -> None:
        """Wait for process to exit, detect port from output, update status."""
        task = self._tasks.get(task_id)
        if not task:
            return

        try:
            # Port detection from output (first 30s) — fire and forget
            if not task.port:
                try:
                    from onemancompany.core.async_utils import spawn_background
                    spawn_background(self._detect_port_from_output(task_id))
                except Exception as e:
                    logger.debug("[bg_tasks] Could not start port detection: {}", e)

            returncode = await proc.wait()
            task.returncode = returncode
            task.status = "completed" if returncode == 0 else "failed"
            task.ended_at = datetime.now(timezone.utc).isoformat()
            logger.info("[bg_tasks] Task {} finished: exit {}", task_id, returncode)
        except asyncio.CancelledError:
            raise  # Must re-raise per project rules
        finally:
            log_fd.close()
            self._processes.pop(task_id, None)
            self._monitors.pop(task_id, None)
            self._save()
            self._broadcast_update(task)

    async def _detect_port_from_output(self, task_id: str) -> None:
        """Scan output log for port patterns during the first 30 seconds."""
        port_re = re.compile(
            r"(?:https?://[\w.-]+:|localhost:|0\.0\.0\.0:|127\.0\.0\.1:)(\d{2,5})"
        )
        deadline = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(2)
            task = self._tasks.get(task_id)
            if not task or task.status != "running" or task.port:
                return
            log_path = self.output_log_path(task_id)
            if not log_path.exists():
                continue
            try:
                text = log_path.read_text()
                match = port_re.search(text)
                if match:
                    task.port = int(match.group(1))
                    task.address = f"http://localhost:{task.port}"
                    self._save()
                    self._broadcast_update(task)
                    logger.info("[bg_tasks] Detected port {} for task {}", task.port, task_id)
                    return
            except Exception as e:
                logger.debug("[bg_tasks] Port detection read error for {}: {}", task_id, e)

    async def terminate(self, task_id: str) -> bool:
        """Terminate a running task. Returns True if terminated, False if not found."""
        task = self._tasks.get(task_id)
        if not task or task.status != "running":
            return False

        proc = self._processes.get(task_id)
        if proc:
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            except ProcessLookupError:
                logger.debug("[bg_tasks] Process for task {} already gone (ProcessLookupError)", task_id)

        # Cancel monitor
        monitor = self._monitors.pop(task_id, None)
        if monitor:
            monitor.cancel()

        task.status = "stopped"
        task.ended_at = datetime.now(timezone.utc).isoformat()
        task.returncode = proc.returncode if proc else None
        self._processes.pop(task_id, None)
        self._save()
        self._broadcast_update(task)
        logger.info("[bg_tasks] Terminated task {}", task_id)
        return True

    async def stop_all(self) -> None:
        """Terminate all running tasks. Called on shutdown."""
        for task_id in list(self._processes.keys()):
            await self.terminate(task_id)

    def start(self) -> None:
        """Load persisted state on startup."""
        self._load()
        logger.info("[bg_tasks] Loaded {} tasks ({} were running)",
                    len(self._tasks), sum(1 for t in self._tasks.values() if t.status == "stopped"))


# Singleton — import-time creation, call start() during app startup
background_task_manager = BackgroundTaskManager()
