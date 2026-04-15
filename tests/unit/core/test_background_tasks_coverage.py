"""Coverage tests for core/background_tasks.py — missing lines."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from onemancompany.core.background_tasks import BackgroundTask, BackgroundTaskManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(task_id: str, status: str = "running", pid: int | None = 1234,
               started: str = "2024-01-01T00:00:00+00:00") -> BackgroundTask:
    return BackgroundTask(
        id=task_id, command="echo test", description="test",
        working_dir="/tmp", started_by="test", status=status,
        pid=pid, started_at=started,
    )


# ---------------------------------------------------------------------------
# get_task / get_all (lines 97, 100)
# ---------------------------------------------------------------------------

class TestBackgroundTaskManagerQueries:
    def test_get_task_found(self, tmp_path):
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        task = _make_task("abc123")
        mgr._tasks["abc123"] = task
        assert mgr.get_task("abc123") is task

    def test_get_task_not_found(self, tmp_path):
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        assert mgr.get_task("nonexistent") is None

    def test_get_all_sorted(self, tmp_path):
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        mgr._tasks["a"] = _make_task("a", started="2024-01-01T00:00:00+00:00")
        mgr._tasks["b"] = _make_task("b", started="2024-01-02T00:00:00+00:00")
        result = mgr.get_all()
        assert result[0].id == "b"  # most recent first


# ---------------------------------------------------------------------------
# _cleanup_old_tasks (lines 113-125)
# ---------------------------------------------------------------------------

class TestCleanupOldTasks:
    def test_cleanup_removes_oldest_non_running(self, tmp_path, monkeypatch):
        import onemancompany.core.background_tasks as bt_mod
        monkeypatch.setattr(bt_mod, "_MAX_RETAINED", 2)
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        mgr._tasks["a"] = _make_task("a", status="completed", started="2024-01-01T00:00:00+00:00")
        mgr._tasks["b"] = _make_task("b", status="completed", started="2024-01-02T00:00:00+00:00")
        mgr._tasks["c"] = _make_task("c", status="running", started="2024-01-03T00:00:00+00:00")
        # Create log dir for task a
        log_dir = mgr.output_log_path("a").parent
        log_dir.mkdir(parents=True)
        (log_dir / "output.log").write_text("log data")

        mgr._cleanup_old_tasks()
        assert "a" not in mgr._tasks  # oldest non-running removed
        assert not log_dir.exists()  # log dir cleaned up


# ---------------------------------------------------------------------------
# _save / _load (lines 139-154, 148, 152-154)
# ---------------------------------------------------------------------------

class TestSaveLoad:
    def test_save_and_load(self, tmp_path, monkeypatch):
        import onemancompany.core.background_tasks as bt_mod
        monkeypatch.setattr(bt_mod, "_MAX_RETAINED", 100)
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        task = _make_task("t1", status="completed")
        mgr._tasks["t1"] = task
        mgr._save()

        mgr2 = BackgroundTaskManager(data_dir=tmp_path)
        with patch.object(mgr2, "_is_pid_alive", return_value=False):
            mgr2._load()
        assert "t1" in mgr2._tasks
        assert mgr2._tasks["t1"].status == "completed"

    def test_load_missing_file(self, tmp_path):
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        mgr._load()  # should not raise
        assert len(mgr._tasks) == 0

    def test_load_corrupt_file(self, tmp_path):
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        yaml_path = mgr._yaml_path()
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        # Write truly invalid YAML that raises an exception during safe_load
        yaml_path.write_bytes(b"\x00\x01\x02\xff")
        mgr._load()  # should not raise
        assert len(mgr._tasks) == 0

    def test_load_marks_stale_running_as_stopped(self, tmp_path, monkeypatch):
        import onemancompany.core.background_tasks as bt_mod
        monkeypatch.setattr(bt_mod, "_MAX_RETAINED", 100)
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        task = _make_task("t1", status="running", pid=99999)
        mgr._tasks["t1"] = task
        mgr._save()

        mgr2 = BackgroundTaskManager(data_dir=tmp_path)
        with patch.object(mgr2, "_is_pid_alive", return_value=False):
            mgr2._load()
        assert mgr2._tasks["t1"].status == "stopped"


# ---------------------------------------------------------------------------
# _is_pid_alive (lines 169, 172)
# ---------------------------------------------------------------------------

class TestIsPidAlive:
    def test_none_pid(self):
        assert BackgroundTaskManager._is_pid_alive(None) is False

    def test_alive_pid(self):
        # Current process is alive
        assert BackgroundTaskManager._is_pid_alive(os.getpid()) is True

    def test_dead_pid(self):
        # Very high PID unlikely to exist
        assert BackgroundTaskManager._is_pid_alive(9999999) is False


# ---------------------------------------------------------------------------
# read_output_tail (lines 184-194)
# ---------------------------------------------------------------------------

class TestReadOutputTail:
    def test_missing_log(self, tmp_path):
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        assert mgr.read_output_tail("nonexistent") == ""

    def test_reads_tail(self, tmp_path):
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        log_path = mgr.output_log_path("t1")
        log_path.parent.mkdir(parents=True)
        log_path.write_text("line1\nline2\nline3\n")
        result = mgr.read_output_tail("t1", lines=2)
        assert "line2" in result
        assert "line3" in result


# ---------------------------------------------------------------------------
# _broadcast_update (lines 207-208)
# ---------------------------------------------------------------------------

class TestBroadcastUpdate:
    def test_broadcast_no_event_loop(self, tmp_path):
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        task = _make_task("t1")
        # Should not raise even without running loop
        mgr._broadcast_update(task)


# ---------------------------------------------------------------------------
# launch — port conflict check (lines 255-257)
# ---------------------------------------------------------------------------

class TestLaunch:
    @pytest.mark.asyncio
    async def test_launch_at_limit_raises(self, tmp_path, monkeypatch):
        import onemancompany.core.background_tasks as bt_mod
        monkeypatch.setattr(bt_mod, "MAX_CONCURRENT", 0)
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        with pytest.raises(RuntimeError, match="limit reached"):
            await mgr.launch("echo hi", "test", "/tmp", "tester")


# ---------------------------------------------------------------------------
# _detect_port_from_command (line 280)
# ---------------------------------------------------------------------------

class TestDetectPort:
    def test_detect_port(self):
        assert BackgroundTaskManager._detect_port_from_command("--port 3000") == 3000

    def test_no_port(self):
        assert BackgroundTaskManager._detect_port_from_command("echo hello") is None


# ---------------------------------------------------------------------------
# _detect_port_from_output (lines 313-330)
# ---------------------------------------------------------------------------

class TestDetectPortFromOutput:
    @pytest.mark.asyncio
    async def test_detects_port_from_log(self, tmp_path, monkeypatch):
        import onemancompany.core.background_tasks as bt_mod
        monkeypatch.setattr(bt_mod, "_MAX_RETAINED", 100)
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        task = _make_task("t1", status="running")
        mgr._tasks["t1"] = task

        log_path = mgr.output_log_path("t1")
        log_path.parent.mkdir(parents=True)
        log_path.write_text("Server running at http://localhost:3000\n")

        # Patch asyncio.sleep to return immediately
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await mgr._detect_port_from_output("t1")
        assert task.port == 3000
        assert task.address == "http://localhost:3000"

    @pytest.mark.asyncio
    async def test_stops_when_task_not_running(self, tmp_path):
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        task = _make_task("t1", status="completed")
        mgr._tasks["t1"] = task
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await mgr._detect_port_from_output("t1")
        assert task.port is None


# ---------------------------------------------------------------------------
# terminate (lines 344-348)
# ---------------------------------------------------------------------------

class TestTerminate:
    @pytest.mark.asyncio
    async def test_terminate_not_found(self, tmp_path):
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        assert await mgr.terminate("nonexistent") is False

    @pytest.mark.asyncio
    async def test_terminate_not_running(self, tmp_path):
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        mgr._tasks["t1"] = _make_task("t1", status="completed")
        assert await mgr.terminate("t1") is False

    @pytest.mark.asyncio
    async def test_terminate_process_lookup_error(self, tmp_path, monkeypatch):
        import onemancompany.core.background_tasks as bt_mod
        monkeypatch.setattr(bt_mod, "_MAX_RETAINED", 100)
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        task = _make_task("t1", status="running")
        mgr._tasks["t1"] = task

        mock_proc = MagicMock()
        mock_proc.terminate.side_effect = ProcessLookupError
        mock_proc.returncode = -15
        mgr._processes["t1"] = mock_proc

        result = await mgr.terminate("t1")
        assert result is True
        assert task.status == "stopped"


# ---------------------------------------------------------------------------
# stop_all / start (lines 366-367, 371-372)
# ---------------------------------------------------------------------------

class TestStopAllAndStart:
    @pytest.mark.asyncio
    async def test_stop_all(self, tmp_path):
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        task = _make_task("t1", status="running")
        mgr._tasks["t1"] = task
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mgr._processes["t1"] = mock_proc
        await mgr.stop_all()
        assert task.status == "stopped"

    def test_start(self, tmp_path, monkeypatch):
        import onemancompany.core.background_tasks as bt_mod
        monkeypatch.setattr(bt_mod, "_MAX_RETAINED", 100)
        mgr = BackgroundTaskManager(data_dir=tmp_path)
        mgr.start()  # should not raise
