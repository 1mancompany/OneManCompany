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

    def test_detect_port_from_command_no_separator(self):
        from onemancompany.core.background_tasks import BackgroundTaskManager
        assert BackgroundTaskManager._detect_port_from_command("node server.js -p3000") == 3000


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


import asyncio


class TestLaunchTask:
    """BackgroundTaskManager.launch() — integration tests using real subprocesses."""

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
    """BackgroundTaskManager.terminate() — integration tests using real subprocesses."""

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
