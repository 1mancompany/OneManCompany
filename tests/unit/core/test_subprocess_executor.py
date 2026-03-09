"""Tests for SubprocessExecutor — subprocess-based task execution with two-stage kill."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.vessel import LaunchResult, TaskContext


class TestSubprocessExecutor:
    @pytest.mark.asyncio
    async def test_execute_happy_path(self):
        """Execute runs launch.sh and captures JSON output."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (
            b'{"output":"done","model":"test","input_tokens":10,"output_tokens":5}',
            b"",
        )
        mock_proc.returncode = 0
        mock_proc.pid = 12345

        ctx = TaskContext(project_id="p1", work_dir="/tmp", employee_id="00010", task_id="t1")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await exe.execute("do work", ctx)

        assert result.output == "done"
        assert result.model_used == "test"
        assert result.input_tokens == 10
        assert result.output_tokens == 5

    @pytest.mark.asyncio
    async def test_execute_plain_text_output(self):
        """Non-JSON stdout is returned as plain text."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"plain text result", b"")
        mock_proc.returncode = 0
        mock_proc.pid = 12345

        ctx = TaskContext(project_id="p1", work_dir="/tmp", employee_id="00010", task_id="t1")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await exe.execute("do work", ctx)

        assert result.output == "plain text result"

    @pytest.mark.asyncio
    async def test_execute_nonzero_exit(self):
        """Non-zero exit returns error in output."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"something failed")
        mock_proc.returncode = 1
        mock_proc.pid = 12345

        ctx = TaskContext(project_id="p1", work_dir="/tmp", employee_id="00010", task_id="t1")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await exe.execute("do work", ctx)

        assert "Error" in result.output
        assert "exit 1" in result.output

    @pytest.mark.asyncio
    async def test_execute_timeout_raises(self):
        """Timeout triggers cancel and raises TimeoutError."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh", timeout_seconds=1)

        mock_proc = AsyncMock()
        mock_proc.communicate.side_effect = asyncio.TimeoutError()
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.pid = 12345
        mock_proc.returncode = None

        ctx = TaskContext(project_id="p1", work_dir="/tmp", employee_id="00010", task_id="t1")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(TimeoutError, match="Timeout"):
                await exe.execute("do work", ctx)

        mock_proc.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_graceful(self):
        """cancel() exits after SIGTERM if process dies within grace period."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")

        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.pid = 999
        # First wait succeeds (process exits)
        mock_proc.wait = AsyncMock(return_value=0)
        exe._process = mock_proc

        await exe.cancel()

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_force_kill(self):
        """cancel() sends SIGKILL after grace period if process won't die."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor, _KILL_POLL_INTERVAL

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")

        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.pid = 999
        # wait always times out until kill
        call_count = 0

        async def mock_wait():
            nonlocal call_count
            call_count += 1
            if call_count <= 6:  # 6 * 5s = 30s grace period
                raise asyncio.TimeoutError()
            return -9

        mock_proc.wait = mock_wait
        exe._process = mock_proc

        await exe.cancel()

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_already_exited(self):
        """cancel() is a no-op if process already exited."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")

        mock_proc = MagicMock()
        mock_proc.returncode = 0  # already exited
        mock_proc.terminate = MagicMock()
        exe._process = mock_proc

        await exe.cancel()

        mock_proc.terminate.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_no_process(self):
        """cancel() is a no-op if no process was started."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")
        await exe.cancel()  # should not raise
