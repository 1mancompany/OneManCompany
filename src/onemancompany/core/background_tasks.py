"""Background task manager — launch and manage long-running processes.

Singleton `background_task_manager` manages async subprocesses.
State persisted to company/background_tasks.yaml.
Output logs at company/background_tasks/{task_id}/output.log.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml
from loguru import logger

MAX_CONCURRENT = 5
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

    def _save(self) -> None:
        """Atomic save to YAML."""
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
        """Extract port from command arguments like --port 3000 or -p 8080."""
        m = re.search(r"(?:--port|--PORT|-p)[= ](\d{2,5})", command)
        return int(m.group(1)) if m else None

    def read_output_tail(self, task_id: str, lines: int = 50) -> str:
        """Read last N lines of a task's output log."""
        log_path = self.output_log_path(task_id)
        if not log_path.exists():
            return ""
        try:
            all_lines = log_path.read_text().splitlines()
            return "\n".join(all_lines[-lines:])
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
            logger.debug("[bg_tasks] Broadcast failed (no event loop?): {}", e)

    def start(self) -> None:
        """Load persisted state on startup."""
        self._load()
        logger.info("[bg_tasks] Loaded {} tasks ({} were running)",
                    len(self._tasks), sum(1 for t in self._tasks.values() if t.status == "stopped"))


# Singleton — import-time creation, call start() during app startup
background_task_manager = BackgroundTaskManager()
