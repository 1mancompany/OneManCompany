# Background Task System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add agent tools to launch/check/stop long-running background processes, with a global management UI.

**Architecture:** `BackgroundTaskManager` singleton manages async subprocesses with stdout→disk logging. Three agent tools (start/check/stop) registered as base category. Split-panel modal with XTermLog output viewer.

**Tech Stack:** Python asyncio subprocess, YAML persistence, vanilla JS modal, xterm.js

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/onemancompany/core/background_tasks.py` | BackgroundTaskManager singleton + BackgroundTask dataclass |
| Create | `tests/unit/core/test_background_tasks.py` | Manager unit tests |
| Create | `tests/unit/api/test_background_task_routes.py` | API route tests |
| Modify | `src/onemancompany/core/models.py` | Add BACKGROUND_TASK_UPDATE to EventType |
| Modify | `src/onemancompany/agents/common_tools.py` | 3 new tools + registration |
| Modify | `src/onemancompany/api/routes.py` | 3 new REST endpoints |
| Modify | `frontend/index.html` | Modal HTML + toolbar button |
| Modify | `frontend/app.js` | Modal logic, XTermLog rendering, WebSocket handler |
| Modify | `frontend/style.css` | Modal styles |

---

### Task 1: BackgroundTaskManager — Data Model + Persistence

**Files:**
- Create: `src/onemancompany/core/background_tasks.py`
- Create: `tests/unit/core/test_background_tasks.py`

- [ ] **Step 1: Write failing tests for data model and persistence**

```python
# tests/unit/core/test_background_tasks.py
"""Tests for BackgroundTaskManager."""
import pytest
from pathlib import Path
from unittest.mock import patch
import yaml


class TestBackgroundTaskDataModel:
    """BackgroundTask dataclass + YAML serialization."""

    def test_create_task(self):
        from onemancompany.core.background_tasks import BackgroundTask
        task = BackgroundTask(
            id="abc12345",
            command="npm run dev",
            description="Dev server",
            working_dir="/tmp",
            started_by="emp001",
        )
        assert task.id == "abc12345"
        assert task.status == "running"
        assert task.pid is None
        assert task.port is None

    def test_to_dict(self):
        from onemancompany.core.background_tasks import BackgroundTask
        task = BackgroundTask(
            id="abc12345",
            command="npm run dev",
            description="Dev server",
            working_dir="/tmp",
            started_by="emp001",
        )
        d = task.to_dict()
        assert d["id"] == "abc12345"
        assert d["command"] == "npm run dev"
        assert d["status"] == "running"

    def test_from_dict(self):
        from onemancompany.core.background_tasks import BackgroundTask
        d = {
            "id": "abc12345",
            "command": "npm run dev",
            "description": "Dev server",
            "working_dir": "/tmp",
            "started_by": "emp001",
            "started_at": "2026-03-26T14:00:00",
            "status": "completed",
            "pid": 12345,
            "returncode": 0,
            "ended_at": "2026-03-26T14:05:00",
            "port": 3000,
            "address": "http://localhost:3000",
        }
        task = BackgroundTask.from_dict(d)
        assert task.id == "abc12345"
        assert task.status == "completed"
        assert task.port == 3000


class TestPortDetection:
    """Port extraction from command and output."""

    def test_detect_port_from_command_long_flag(self):
        from onemancompany.core.background_tasks import BackgroundTaskManager
        assert BackgroundTaskManager._detect_port_from_command("npm run dev --port 3000") == 3000

    def test_detect_port_from_command_short_flag(self):
        from onemancompany.core.background_tasks import BackgroundTaskManager
        assert BackgroundTaskManager._detect_port_from_command("python -m http.server -p 8080") == 8080

    def test_detect_port_from_command_equals(self):
        from onemancompany.core.background_tasks import BackgroundTaskManager
        assert BackgroundTaskManager._detect_port_from_command("serve --port=4200") == 4200

    def test_detect_port_from_command_none(self):
        from onemancompany.core.background_tasks import BackgroundTaskManager
        assert BackgroundTaskManager._detect_port_from_command("echo hello") is None


class TestBackgroundTaskManagerPersistence:
    """YAML save/load."""

    def test_save_and_load(self, tmp_path):
        from onemancompany.core.background_tasks import BackgroundTaskManager, BackgroundTask

        mgr = BackgroundTaskManager(data_dir=tmp_path)
        task = BackgroundTask(
            id="t1", command="echo hi", description="test",
            working_dir="/tmp", started_by="emp001",
        )
        task.pid = 999
        mgr._tasks["t1"] = task
        mgr._save()

        mgr2 = BackgroundTaskManager(data_dir=tmp_path)
        mgr2._load()
        assert "t1" in mgr2._tasks
        assert mgr2._tasks["t1"].command == "echo hi"

    def test_load_marks_stale_running_as_stopped(self, tmp_path):
        from onemancompany.core.background_tasks import BackgroundTaskManager, BackgroundTask

        mgr = BackgroundTaskManager(data_dir=tmp_path)
        task = BackgroundTask(
            id="t1", command="sleep 999", description="stale",
            working_dir="/tmp", started_by="emp001",
        )
        task.status = "running"
        task.pid = 999999  # non-existent PID
        mgr._tasks["t1"] = task
        mgr._save()

        mgr2 = BackgroundTaskManager(data_dir=tmp_path)
        mgr2._load()
        assert mgr2._tasks["t1"].status == "stopped"


class TestConcurrencyLimit:
    """Max 5 concurrent tasks."""

    def test_running_count(self, tmp_path):
        from onemancompany.core.background_tasks import BackgroundTaskManager, BackgroundTask

        mgr = BackgroundTaskManager(data_dir=tmp_path)
        for i in range(5):
            t = BackgroundTask(id=f"t{i}", command=f"cmd{i}", description="",
                               working_dir="/tmp", started_by="emp001")
            t.status = "running"
            mgr._tasks[t.id] = t
        assert mgr.running_count == 5
        assert mgr.can_launch is False

    def test_completed_tasks_dont_count(self, tmp_path):
        from onemancompany.core.background_tasks import BackgroundTaskManager, BackgroundTask

        mgr = BackgroundTaskManager(data_dir=tmp_path)
        t = BackgroundTask(id="t1", command="cmd", description="",
                           working_dir="/tmp", started_by="emp001")
        t.status = "completed"
        mgr._tasks["t1"] = t
        assert mgr.running_count == 0
        assert mgr.can_launch is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_background_tasks.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement BackgroundTask dataclass + BackgroundTaskManager persistence**

```python
# src/onemancompany/core/background_tasks.py
"""Background task manager — launch and manage long-running processes.

Singleton `background_task_manager` manages async subprocesses.
State persisted to company/background_tasks.yaml.
Output logs at company/background_tasks/{task_id}/output.log.
"""
from __future__ import annotations

import os
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
                # Process can't survive restart — mark as stopped
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_background_tasks.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/background_tasks.py tests/unit/core/test_background_tasks.py
git commit -m "feat: BackgroundTaskManager data model + YAML persistence"
```

---

### Task 2: BackgroundTaskManager — Process Launch + Monitor + Terminate

**Files:**
- Modify: `src/onemancompany/core/background_tasks.py`
- Modify: `src/onemancompany/core/models.py`
- Modify: `tests/unit/core/test_background_tasks.py`

- [ ] **Step 1: Add BACKGROUND_TASK_UPDATE to EventType**

In `src/onemancompany/core/models.py`, add to the `EventType` enum:

```python
    BACKGROUND_TASK_UPDATE = "background_task_update"
```

- [ ] **Step 2: Write failing tests for launch + terminate**

Append to `tests/unit/core/test_background_tasks.py`:

```python
import asyncio


class TestLaunchTask:
    """BackgroundTaskManager.launch()."""

    @pytest.mark.asyncio
    async def test_launch_returns_task(self, tmp_path):
        from onemancompany.core.background_tasks import BackgroundTaskManager

        mgr = BackgroundTaskManager(data_dir=tmp_path)
        task = await mgr.launch(
            command="echo hello",
            description="test echo",
            working_dir=str(tmp_path),
            started_by="emp001",
        )
        assert task.status == "running"
        assert task.pid is not None
        assert task.id in mgr._tasks
        # Wait for process to finish
        await asyncio.sleep(0.5)
        assert mgr._tasks[task.id].status == "completed"
        assert mgr._tasks[task.id].returncode == 0

    @pytest.mark.asyncio
    async def test_launch_writes_output_log(self, tmp_path):
        from onemancompany.core.background_tasks import BackgroundTaskManager

        mgr = BackgroundTaskManager(data_dir=tmp_path)
        task = await mgr.launch(
            command="echo hello_world",
            description="test output",
            working_dir=str(tmp_path),
            started_by="emp001",
        )
        await asyncio.sleep(0.5)
        log_path = mgr.output_log_path(task.id)
        assert log_path.exists()
        content = log_path.read_text()
        assert "hello_world" in content

    @pytest.mark.asyncio
    async def test_launch_rejects_when_at_limit(self, tmp_path):
        from onemancompany.core.background_tasks import BackgroundTaskManager, BackgroundTask

        mgr = BackgroundTaskManager(data_dir=tmp_path)
        # Fill up slots with fake running tasks
        for i in range(5):
            t = BackgroundTask(id=f"fake{i}", command="x", description="",
                               working_dir="/tmp", started_by="emp001")
            t.status = "running"
            mgr._tasks[t.id] = t

        with pytest.raises(RuntimeError, match="limit"):
            await mgr.launch(
                command="echo nope",
                description="over limit",
                working_dir=str(tmp_path),
                started_by="emp001",
            )


class TestTerminateTask:
    """BackgroundTaskManager.terminate()."""

    @pytest.mark.asyncio
    async def test_terminate_running_task(self, tmp_path):
        from onemancompany.core.background_tasks import BackgroundTaskManager

        mgr = BackgroundTaskManager(data_dir=tmp_path)
        task = await mgr.launch(
            command="sleep 60",
            description="long running",
            working_dir=str(tmp_path),
            started_by="emp001",
        )
        assert task.status == "running"
        result = await mgr.terminate(task.id)
        assert result is True
        assert mgr._tasks[task.id].status == "stopped"

    @pytest.mark.asyncio
    async def test_terminate_nonexistent_task(self, tmp_path):
        from onemancompany.core.background_tasks import BackgroundTaskManager

        mgr = BackgroundTaskManager(data_dir=tmp_path)
        result = await mgr.terminate("nope")
        assert result is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_background_tasks.py::TestLaunchTask -v`
Expected: FAIL — launch method not found

- [ ] **Step 4: Implement launch + monitor + terminate + port detection**

Add to `BackgroundTaskManager` in `src/onemancompany/core/background_tasks.py`:

```python
    import asyncio
    import re
    import uuid

    async def launch(
        self,
        command: str,
        description: str,
        working_dir: str,
        started_by: str,
    ) -> BackgroundTask:
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

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=log_fd,
            stderr=asyncio.subprocess.STDOUT,
            cwd=working_dir or None,
        )
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
            # Port detection from output (first 30s)
            if not task.port:
                from onemancompany.core.async_utils import spawn_background
                spawn_background(self._detect_port_from_output(task_id))

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
        import re
        port_re = re.compile(
            r"(?:https?://[\w.-]+:|localhost:|0\.0\.0\.0:|127\.0\.0\.1:)(\d{2,5})"
        )
        deadline = asyncio.get_event_loop().time() + 30
        while asyncio.get_event_loop().time() < deadline:
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
                pass

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

    @staticmethod
    def _detect_port_from_command(command: str) -> int | None:
        """Extract port from command arguments like --port 3000 or -p 8080."""
        import re
        m = re.search(r"(?:--port|--PORT|-p)[= ](\d{2,5})", command)
        return int(m.group(1)) if m else None

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

    def read_output_tail(self, task_id: str, lines: int = 50) -> str:
        """Read last N lines of a task's output log."""
        log_path = self.output_log_path(task_id)
        if not log_path.exists():
            return ""
        try:
            all_lines = log_path.read_text().splitlines()
            return "\n".join(all_lines[-lines:])
        except Exception:
            return ""


# Singleton — import-time creation, call start() during app startup
background_task_manager = BackgroundTaskManager()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_background_tasks.py -v`
Expected: All 11 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/core/background_tasks.py src/onemancompany/core/models.py tests/unit/core/test_background_tasks.py
git commit -m "feat: BackgroundTaskManager launch/monitor/terminate + port detection"
```

---

### Task 3: Agent Tools — start/check/stop_background_task

**Files:**
- Modify: `src/onemancompany/agents/common_tools.py`
- Create: `tests/unit/agents/test_background_task_tools.py`

- [ ] **Step 1: Write failing tests for agent tools**

```python
# tests/unit/agents/test_background_task_tools.py
"""Tests for background task agent tools."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestStartBackgroundTask:

    @pytest.mark.asyncio
    @patch("onemancompany.agents.common_tools.background_task_manager")
    async def test_start_returns_task_id(self, mock_mgr):
        from onemancompany.agents.common_tools import start_background_task

        mock_task = MagicMock()
        mock_task.id = "abc12345"
        mock_task.pid = 999
        mock_mgr.launch = AsyncMock(return_value=mock_task)

        result = await start_background_task.ainvoke({
            "command": "npm run dev",
            "description": "Dev server",
            "employee_id": "emp001",
        })
        assert result["status"] == "ok"
        assert result["task_id"] == "abc12345"

    @pytest.mark.asyncio
    @patch("onemancompany.agents.common_tools.background_task_manager")
    async def test_start_returns_error_at_limit(self, mock_mgr):
        from onemancompany.agents.common_tools import start_background_task

        mock_mgr.launch = AsyncMock(side_effect=RuntimeError("limit reached"))
        result = await start_background_task.ainvoke({
            "command": "npm run dev",
            "description": "Dev server",
            "employee_id": "emp001",
        })
        assert result["status"] == "error"
        assert "limit" in result["message"]


class TestCheckBackgroundTask:

    @pytest.mark.asyncio
    @patch("onemancompany.agents.common_tools.background_task_manager")
    async def test_check_returns_status(self, mock_mgr):
        from onemancompany.agents.common_tools import check_background_task

        mock_task = MagicMock()
        mock_task.status = "running"
        mock_task.port = 3000
        mock_task.address = "http://localhost:3000"
        mock_task.returncode = None
        mock_task.started_at = "2026-03-26T14:00:00"
        mock_mgr.get_task.return_value = mock_task
        mock_mgr.read_output_tail.return_value = "server started"

        result = await check_background_task.ainvoke({
            "task_id": "abc12345",
            "employee_id": "emp001",
        })
        assert result["status"] == "running"
        assert result["port"] == 3000
        assert "server started" in result["output_tail"]

    @pytest.mark.asyncio
    @patch("onemancompany.agents.common_tools.background_task_manager")
    async def test_check_not_found(self, mock_mgr):
        from onemancompany.agents.common_tools import check_background_task

        mock_mgr.get_task.return_value = None
        result = await check_background_task.ainvoke({
            "task_id": "nope",
            "employee_id": "emp001",
        })
        assert result["status"] == "error"


class TestStopBackgroundTask:

    @pytest.mark.asyncio
    @patch("onemancompany.agents.common_tools.background_task_manager")
    async def test_stop_running_task(self, mock_mgr):
        from onemancompany.agents.common_tools import stop_background_task

        mock_mgr.terminate = AsyncMock(return_value=True)
        result = await stop_background_task.ainvoke({
            "task_id": "abc12345",
            "employee_id": "emp001",
        })
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    @patch("onemancompany.agents.common_tools.background_task_manager")
    async def test_stop_not_found(self, mock_mgr):
        from onemancompany.agents.common_tools import stop_background_task

        mock_mgr.terminate = AsyncMock(return_value=False)
        result = await stop_background_task.ainvoke({
            "task_id": "nope",
            "employee_id": "emp001",
        })
        assert result["status"] == "error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_background_task_tools.py -v`
Expected: FAIL — tools not defined

- [ ] **Step 3: Implement tools and register them**

Add to `src/onemancompany/agents/common_tools.py`, before `_register_all_internal_tools`:

```python
# ---------------------------------------------------------------------------
# Background task tools
# ---------------------------------------------------------------------------


@tool
async def start_background_task(
    command: str,
    description: str,
    working_dir: str = "",
    employee_id: str = "",
) -> dict:
    """Start a long-running background process (deploy, dev server, watcher, build).

    ONLY use for processes that need to keep running after this tool returns.
    For quick commands (< 2 minutes), use bash() instead.
    Max 5 concurrent background tasks globally.

    Args:
        command: Shell command to run.
        description: Brief description of what this does and why.
        working_dir: Directory to run in (defaults to project root).
        employee_id: Your employee ID.
    """
    try:
        from onemancompany.core.config import SOURCE_ROOT
        from onemancompany.core.background_tasks import background_task_manager
        wd = working_dir or str(SOURCE_ROOT)
        task = await background_task_manager.launch(
            command=command,
            description=description,
            working_dir=wd,
            started_by=employee_id,
        )
        return {"status": "ok", "task_id": task.id, "pid": task.pid}
    except RuntimeError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to start: {e}"}


@tool
async def check_background_task(
    task_id: str,
    tail: int = 50,
    employee_id: str = "",
) -> dict:
    """Check status and recent output of a background task.

    Args:
        task_id: The task ID returned by start_background_task.
        tail: Number of output lines to return (default 50).
        employee_id: Your employee ID.
    """
    from onemancompany.core.background_tasks import background_task_manager
    task = background_task_manager.get_task(task_id)
    if not task:
        return {"status": "error", "message": f"Task {task_id} not found"}
    output = background_task_manager.read_output_tail(task_id, lines=tail)
    return {
        "status": task.status,
        "returncode": task.returncode,
        "port": task.port,
        "address": task.address,
        "output_tail": output,
        "started_at": task.started_at,
        "ended_at": task.ended_at,
        "pid": task.pid,
    }


@tool
async def stop_background_task(
    task_id: str,
    employee_id: str = "",
) -> dict:
    """Stop a running background task. Sends SIGTERM, then SIGKILL after 10s.

    Args:
        task_id: The task ID to stop.
        employee_id: Your employee ID.
    """
    from onemancompany.core.background_tasks import background_task_manager
    result = await background_task_manager.terminate(task_id)
    if result:
        return {"status": "ok", "task_id": task_id}
    return {"status": "error", "message": f"Task {task_id} not found or not running"}
```

Add to the `_base` list in `_register_all_internal_tools`:

```python
    _base = [
        # ... existing tools ...
        start_background_task, check_background_task, stop_background_task,
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_background_task_tools.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/agents/common_tools.py tests/unit/agents/test_background_task_tools.py
git commit -m "feat: agent tools — start/check/stop_background_task"
```

---

### Task 4: API Routes

**Files:**
- Modify: `src/onemancompany/api/routes.py`
- Create: `tests/unit/api/test_background_task_routes.py`

- [ ] **Step 1: Write failing tests for API routes**

```python
# tests/unit/api/test_background_task_routes.py
"""Tests for background task API endpoints."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


def _make_client():
    from starlette.testclient import TestClient
    from onemancompany.api.routes import router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestListBackgroundTasks:

    @patch("onemancompany.api.routes.background_task_manager")
    def test_returns_tasks(self, mock_mgr):
        client = _make_client()
        mock_task = MagicMock()
        mock_task.to_dict.return_value = {
            "id": "t1", "command": "npm dev", "status": "running",
        }
        mock_mgr.get_all.return_value = [mock_task]
        mock_mgr.running_count = 1

        resp = client.get("/api/background-tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tasks"]) == 1
        assert data["running_count"] == 1
        assert data["max_concurrent"] == 5


class TestGetBackgroundTask:

    @patch("onemancompany.api.routes.background_task_manager")
    def test_returns_task_with_output(self, mock_mgr):
        client = _make_client()
        mock_task = MagicMock()
        mock_task.to_dict.return_value = {"id": "t1", "status": "running"}
        mock_mgr.get_task.return_value = mock_task
        mock_mgr.read_output_tail.return_value = "hello world"

        resp = client.get("/api/background-tasks/t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task"]["id"] == "t1"
        assert data["output_tail"] == "hello world"

    @patch("onemancompany.api.routes.background_task_manager")
    def test_not_found(self, mock_mgr):
        client = _make_client()
        mock_mgr.get_task.return_value = None

        resp = client.get("/api/background-tasks/nope")
        assert resp.status_code == 404


class TestStopBackgroundTask:

    @patch("onemancompany.api.routes.background_task_manager")
    def test_stop_running(self, mock_mgr):
        client = _make_client()
        mock_mgr.terminate = AsyncMock(return_value=True)

        resp = client.post("/api/background-tasks/t1/stop")
        assert resp.status_code == 200

    @patch("onemancompany.api.routes.background_task_manager")
    def test_stop_not_running(self, mock_mgr):
        client = _make_client()
        mock_mgr.terminate = AsyncMock(return_value=False)

        resp = client.post("/api/background-tasks/nope/stop")
        assert resp.status_code == 409
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/api/test_background_task_routes.py -v`
Expected: FAIL — routes not defined

- [ ] **Step 3: Implement API routes**

Add to `src/onemancompany/api/routes.py`:

```python
# ---------------------------------------------------------------------------
# Background Tasks
# ---------------------------------------------------------------------------


@router.get("/api/background-tasks")
async def list_background_tasks():
    """List all background tasks."""
    from onemancompany.core.background_tasks import background_task_manager, MAX_CONCURRENT
    tasks = background_task_manager.get_all()
    return {
        "tasks": [t.to_dict() for t in tasks],
        "running_count": background_task_manager.running_count,
        "max_concurrent": MAX_CONCURRENT,
    }


@router.get("/api/background-tasks/{task_id}")
async def get_background_task(task_id: str, tail: int = 50):
    """Get background task detail + output tail."""
    from onemancompany.core.background_tasks import background_task_manager
    task = background_task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    output = background_task_manager.read_output_tail(task_id, lines=tail)
    return {"task": task.to_dict(), "output_tail": output}


@router.post("/api/background-tasks/{task_id}/stop")
async def stop_background_task_api(task_id: str):
    """Stop a running background task."""
    from onemancompany.core.background_tasks import background_task_manager
    result = await background_task_manager.terminate(task_id)
    if not result:
        raise HTTPException(status_code=409, detail="Task not found or not running")
    return {"status": "ok", "task_id": task_id}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/api/test_background_task_routes.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/api/routes.py tests/unit/api/test_background_task_routes.py
git commit -m "feat: background tasks API — list, detail, stop endpoints"
```

---

### Task 5: Frontend — Modal HTML + Toolbar Button + Styles

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/style.css`

- [ ] **Step 1: Add toolbar button to index.html**

In `frontend/index.html`, add after the `dashboard-toolbar-btn` button:

```html
<button id="bg-tasks-toolbar-btn" class="toolbar-icon-btn" title="Background Tasks">&#9641;</button>
```

- [ ] **Step 2: Add modal HTML to index.html**

Add before the closing `</body>` tag (near other modals):

```html
<!-- Background Tasks Modal -->
<div id="bg-tasks-modal" class="modal-overlay hidden">
  <div class="modal-content bg-tasks-modal-content">
    <div class="modal-header">
      <h3 class="pixel-title">&#9608; BACKGROUND TASKS</h3>
      <span id="bg-tasks-slots" class="bg-tasks-slots"></span>
      <button id="bg-tasks-close-btn" class="modal-close">&#10005;</button>
    </div>
    <div class="modal-body bg-tasks-body">
      <div id="bg-tasks-list" class="bg-tasks-list"></div>
      <div id="bg-tasks-detail" class="bg-tasks-detail">
        <div class="bg-tasks-detail-empty">Select a task</div>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Add styles to style.css**

Append to `frontend/style.css`:

```css
/* ── Background Tasks Modal ── */
.bg-tasks-modal-content {
  width: 90vw;
  max-width: 900px;
  height: 70vh;
  max-height: 600px;
}
.bg-tasks-slots {
  color: #555;
  font-size: 9px;
  font-family: var(--font-mono);
  margin-left: auto;
  margin-right: 12px;
}
.bg-tasks-body {
  display: flex;
  gap: 0;
  height: calc(100% - 40px);
  overflow: hidden;
}
.bg-tasks-list {
  flex: 0 0 280px;
  border-right: 1px solid var(--border);
  overflow-y: auto;
  background: #0a0a0a;
}
.bg-tasks-detail {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #0a0a0a;
  padding: 8px;
}
.bg-tasks-detail-empty {
  color: #555;
  font-size: 11px;
  font-family: var(--font-mono);
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}

/* Task list items */
.bg-task-item {
  padding: 6px 8px;
  border-left: 2px solid #333;
  margin: 2px 4px;
  cursor: pointer;
  font-family: var(--font-mono);
  font-size: 10px;
  transition: background 0.1s;
}
.bg-task-item:hover { background: #111; }
.bg-task-item.selected { background: #111; }
.bg-task-item.status-running { border-left-color: #44aa44; }
.bg-task-item.status-completed { border-left-color: #555; opacity: 0.6; }
.bg-task-item.status-failed { border-left-color: #ff4444; opacity: 0.6; }
.bg-task-item.status-stopped { border-left-color: #aa4444; opacity: 0.6; }
.bg-task-item-status { font-size: 9px; }
.bg-task-item-cmd { color: #44aaff; margin: 2px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bg-task-item-port { color: #aa44ff; font-size: 9px; }

/* Detail panel */
.bg-tasks-detail-header { margin-bottom: 6px; }
.bg-tasks-detail-cmd { color: #44aaff; font-size: 11px; font-weight: bold; font-family: var(--font-mono); word-break: break-all; }
.bg-tasks-detail-desc { color: #666; font-size: 10px; font-family: var(--font-mono); margin: 4px 0; }
.bg-tasks-detail-meta { display: flex; flex-wrap: wrap; gap: 10px; font-size: 9px; font-family: var(--font-mono); margin-bottom: 8px; }
.bg-tasks-detail-output { flex: 1; min-height: 0; border: 1px solid #222; }
.bg-tasks-detail-actions { margin-top: 6px; text-align: right; }
.bg-tasks-stop-btn {
  color: #ff4444;
  border: 1px solid #ff4444;
  background: transparent;
  padding: 3px 12px;
  font-size: 10px;
  font-family: var(--font-mono);
  cursor: pointer;
  letter-spacing: 1px;
}
.bg-tasks-stop-btn:hover { background: #ff444422; }
```

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html frontend/style.css
git commit -m "feat: background tasks modal HTML + CSS (brutalist split panel)"
```

---

### Task 6: Frontend — JavaScript Logic + XTermLog Rendering

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Add event binding in constructor**

In `app.js`, find where toolbar buttons are bound (near `dashboard-toolbar-btn` listener) and add:

```javascript
document.getElementById('bg-tasks-toolbar-btn').addEventListener('click', () => this.openBackgroundTasks());
document.getElementById('bg-tasks-close-btn').addEventListener('click', () => this.closeBackgroundTasks());
document.getElementById('bg-tasks-modal').addEventListener('click', (e) => {
  if (e.target.id === 'bg-tasks-modal') this.closeBackgroundTasks();
});
```

- [ ] **Step 2: Add WebSocket event handler**

In the WebSocket message handler map (where `ceo_inbox_updated`, `background_task_update` etc. are), add:

```javascript
'background_task_update': (p) => {
  if (document.getElementById('bg-tasks-modal') && !document.getElementById('bg-tasks-modal').classList.contains('hidden')) {
    this._fetchBackgroundTasks();
  }
  return { text: `BG Task ${p.task_id}: ${p.status}`, cls: 'system', agent: 'SYSTEM' };
},
```

- [ ] **Step 3: Implement open/close/render methods**

Add to `AppController`:

```javascript
  // ===== Background Tasks =====

  openBackgroundTasks() {
    document.getElementById('bg-tasks-modal').classList.remove('hidden');
    this._bgTaskSelected = null;
    this._bgTaskXterm = null;
    this._fetchBackgroundTasks();
  }

  closeBackgroundTasks() {
    document.getElementById('bg-tasks-modal').classList.add('hidden');
    clearInterval(this._bgTaskPollTimer);
    this._bgTaskPollTimer = null;
    if (this._bgTaskXterm) { this._bgTaskXterm.dispose(); this._bgTaskXterm = null; }
  }

  async _fetchBackgroundTasks() {
    try {
      const resp = await fetch('/api/background-tasks');
      const data = await resp.json();
      this._renderBgTaskList(data.tasks);
      document.getElementById('bg-tasks-slots').textContent =
        `${data.running_count}/${data.max_concurrent} SLOTS`;
      // If selected task is in the list, refresh detail (re-fetch with output)
      if (this._bgTaskSelected) {
        const current = data.tasks.find(t => t.id === this._bgTaskSelected);
        if (current) this._fetchBgTaskDetail(this._bgTaskSelected);
      }
    } catch (e) {
      console.error('[bg-tasks] fetch error:', e);
    }
  }

  _renderBgTaskList(tasks) {
    const el = document.getElementById('bg-tasks-list');
    if (!tasks.length) {
      el.innerHTML = '<div style="color:#555;font-size:10px;padding:12px;font-family:var(--font-mono);">No background tasks</div>';
      return;
    }
    const statusIcon = { running: '\u2588', completed: '\u2591', failed: '\u2573', stopped: '\u2592' };
    const statusColor = { running: '#44aa44', completed: '#666', failed: '#ff4444', stopped: '#aa4444' };
    let html = '';
    for (const t of tasks) {
      const selected = t.id === this._bgTaskSelected ? ' selected' : '';
      const dur = this._bgTaskDuration(t);
      const cmd = this._escHtml(t.command.length > 30 ? t.command.substring(0, 30) + '...' : t.command);
      html += `<div class="bg-task-item status-${t.status}${selected}" data-id="${t.id}">`;
      html += `<div class="bg-task-item-status" style="color:${statusColor[t.status] || '#666'}">${statusIcon[t.status] || '\u2591'} ${t.status.toUpperCase()} ${dur}</div>`;
      html += `<div class="bg-task-item-cmd">${cmd}</div>`;
      if (t.port) html += `<div class="bg-task-item-port">\u25B6 :${t.port}</div>`;
      html += '</div>';
    }
    el.innerHTML = html;
    // Bind click events
    el.querySelectorAll('.bg-task-item').forEach(item => {
      item.addEventListener('click', () => {
        this._bgTaskSelected = item.dataset.id;
        this._fetchBgTaskDetail(item.dataset.id);
        // Update selected state
        el.querySelectorAll('.bg-task-item').forEach(i => i.classList.remove('selected'));
        item.classList.add('selected');
      });
    });
  }

  async _fetchBgTaskDetail(taskId) {
    try {
      const resp = await fetch(`/api/background-tasks/${taskId}?tail=200`);
      if (!resp.ok) return;
      const data = await resp.json();
      this._renderBgTaskDetail(data.task, data.output_tail);
      // Start/stop polling
      clearInterval(this._bgTaskPollTimer);
      if (data.task.status === 'running') {
        this._bgTaskPollTimer = setInterval(() => this._fetchBgTaskDetail(taskId), 3000);
      }
    } catch (e) {
      console.error('[bg-tasks] detail fetch error:', e);
    }
  }

  _renderBgTaskDetail(task, outputTail) {
    const el = document.getElementById('bg-tasks-detail');
    const statusColor = { running: '#44aa44', completed: '#666', failed: '#ff4444', stopped: '#aa4444' };

    let metaHtml = `<span style="color:${statusColor[task.status] || '#666'}">\u2588 ${task.status.toUpperCase()}</span>`;
    if (task.port) {
      const addr = task.address || `http://localhost:${task.port}`;
      metaHtml += `<span style="color:#aa44ff">\u25B6 <a href="${addr}" target="_blank" style="color:#aa44ff;">${addr}</a></span>`;
    }
    if (task.pid) metaHtml += `<span style="color:#555">PID ${task.pid}</span>`;
    if (task.started_by) metaHtml += `<span style="color:#555">by ${this._escHtml(task.started_by)}</span>`;
    const dur = this._bgTaskDuration(task);
    if (dur) metaHtml += `<span style="color:#555">${dur}</span>`;

    el.innerHTML = `
      <div class="bg-tasks-detail-header">
        <div class="bg-tasks-detail-cmd">${this._escHtml(task.command)}</div>
        <div class="bg-tasks-detail-desc">${this._escHtml(task.description)}</div>
        <div class="bg-tasks-detail-meta">${metaHtml}</div>
      </div>
      <div class="bg-tasks-detail-output" id="bg-tasks-output"></div>
      ${task.status === 'running' ? '<div class="bg-tasks-detail-actions"><button class="bg-tasks-stop-btn" id="bg-tasks-stop-btn">\u25A0 STOP</button></div>' : ''}
    `;

    // Render output in xterm
    const outputEl = document.getElementById('bg-tasks-output');
    if (this._bgTaskXterm) { this._bgTaskXterm.dispose(); }
    this._bgTaskXterm = new XTermLog(outputEl, { fontSize: 11 });
    if (outputTail) {
      for (const line of outputTail.split('\n')) {
        this._bgTaskXterm.writeln(line);
      }
    } else {
      this._bgTaskXterm.writeln(`${ANSI.gray}No output yet${ANSI.reset}`);
    }

    // Stop button
    const stopBtn = document.getElementById('bg-tasks-stop-btn');
    if (stopBtn) {
      stopBtn.addEventListener('click', async () => {
        stopBtn.disabled = true;
        stopBtn.textContent = 'STOPPING...';
        try {
          await fetch(`/api/background-tasks/${task.id}/stop`, { method: 'POST' });
          this._fetchBackgroundTasks();
        } catch (e) {
          console.error('[bg-tasks] stop error:', e);
        }
      });
    }
  }

  _bgTaskDuration(task) {
    if (!task.started_at) return '';
    const start = new Date(task.started_at);
    const end = task.ended_at ? new Date(task.ended_at) : new Date();
    const s = Math.floor((end - start) / 1000);
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m${s % 60}s`;
    return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`;
  }
```

- [ ] **Step 4: Commit**

```bash
git add frontend/app.js
git commit -m "feat: background tasks modal JS — list, detail, XTermLog output, stop"
```

---

### Task 7: Integration — Startup/Shutdown Hooks + Full Test

**Files:**
- Modify: `src/onemancompany/api/routes.py` (or main app entry point)

- [ ] **Step 1: Wire BackgroundTaskManager into app lifecycle**

Find where the app starts (where `system_cron_manager.start_all()` is called) and add:

```python
from onemancompany.core.background_tasks import background_task_manager
background_task_manager.start()
```

Find the shutdown handler and add:

```python
await background_task_manager.stop_all()
```

- [ ] **Step 2: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v --timeout=30`
Expected: All tests PASS (existing + new)

- [ ] **Step 3: Commit**

```bash
git add src/onemancompany/api/routes.py  # or wherever lifecycle hooks are
git commit -m "feat: wire background task manager into app startup/shutdown"
```

> **Note:** MCP bridge registration for self-hosted employees is deferred to a follow-up PR.

- [ ] **Step 4: Create PR**

```bash
git push -u origin feat/background-tasks
gh pr create --title "feat: background task system — agent tools + management UI" --body "..."
```
