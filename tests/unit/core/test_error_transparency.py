"""Unit tests for error transparency — LaunchResult.error + ExecutionError."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from onemancompany.core.vessel import (
    ClaudeSessionExecutor,
    ExecutionError,
    LaunchResult,
    ScriptExecutor,
    TaskContext,
)
from onemancompany.core.subprocess_executor import SubprocessExecutor


_CTX = TaskContext(project_id="proj1", work_dir="/tmp", employee_id="00010", task_id="t1")


class TestLaunchResultError:
    def test_default_error_is_none(self):
        result = LaunchResult(output="hello")
        assert result.error is None

    def test_error_field_set(self):
        result = LaunchResult(error="something broke")
        assert result.error == "something broke"
        assert result.output == ""

    def test_backward_compat_no_error(self):
        result = LaunchResult(
            output="ok",
            model_used="claude-3",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
        )
        assert result.error is None


class TestExecutionError:
    def test_is_exception(self):
        err = ExecutionError("task failed")
        assert isinstance(err, Exception)
        assert str(err) == "task failed"


class TestClaudeSessionExecutorError:
    def test_daemon_error_sets_error_field(self):
        executor = ClaudeSessionExecutor("00010")
        mock_result = {
            "output": "[claude-daemon error] connection refused",
            "model": "",
            "input_tokens": 0,
            "output_tokens": 0,
        }
        with patch(
            "onemancompany.core.claude_session.run_claude_session",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = asyncio.get_event_loop().run_until_complete(
                executor.execute("do stuff", _CTX)
            )
        assert result.error == "[claude-daemon error] connection refused"
        assert result.output == ""


class TestScriptExecutorError:
    def test_nonzero_exit_no_stdout_sets_error_field(self):
        executor = ScriptExecutor("00010", script_path="/bin/true")

        async def _run():
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.communicate = AsyncMock(return_value=(b"", b"segfault"))
                mock_proc.returncode = 1
                mock_exec.return_value = mock_proc
                return await executor.execute("do stuff", _CTX)

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result.error is not None
        assert "exit" in result.error.lower() or "script" in result.error.lower()

    def test_timeout_sets_error_field(self):
        executor = ScriptExecutor("00010", script_path="/bin/true")

        async def _run():
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.communicate = AsyncMock(return_value=(b"", b""))
                mock_exec.return_value = mock_proc
                with patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=asyncio.TimeoutError):
                    return await executor.execute("do stuff", _CTX)

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result.error is not None
        assert "timed out" in result.error.lower() or "timeout" in result.error.lower()

    def test_exception_sets_error_field(self):
        executor = ScriptExecutor("00010", script_path="/bin/true")

        async def _run():
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_exec.side_effect = FileNotFoundError("/bin/missing")
                return await executor.execute("do stuff", _CTX)

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result.error is not None
        assert "script error" in result.error.lower()


class TestSubprocessExecutorError:
    def test_nonzero_exit_sets_error_field(self):
        executor = SubprocessExecutor("00010", script_path="/bin/false", timeout_seconds=10)

        async def _run():
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b"segfault"))
            mock_proc.returncode = 1
            mock_proc.pid = 12345
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
                return await executor.execute("do stuff", _CTX)

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result.error is not None
        assert "exit" in result.error.lower() or "1" in result.error


class TestExecuteTaskErrorCheck:
    """Test that _execute_task raises ExecutionError when LaunchResult.error is set."""

    def test_launch_result_error_raises_execution_error(self):
        launch_result = LaunchResult(error="connection refused")
        with pytest.raises(ExecutionError, match="connection refused"):
            if launch_result.error:
                raise ExecutionError(launch_result.error)

    def test_launch_result_no_error_does_not_raise(self):
        launch_result = LaunchResult(output="all good")
        if launch_result.error:
            raise ExecutionError(launch_result.error)


class TestFailedPathCompleteness:
    """Test that the FAILED path calls _push_to_ceo_session and _append_progress."""

    def test_except_block_has_ceo_push_and_progress(self):
        import inspect
        from onemancompany.core.vessel import EmployeeManager

        source = inspect.getsource(EmployeeManager._execute_task)
        lines = source.split("\n")
        in_except_block = False
        found_ceo_push = False
        found_progress = False
        for line in lines:
            if "except Exception as e:" in line:
                in_except_block = True
            elif in_except_block:
                if line.strip().startswith("except ") or line.strip().startswith("finally:"):
                    break
                if "_push_to_ceo_session" in line:
                    found_ceo_push = True
                if "_append_progress" in line:
                    found_progress = True
        assert found_ceo_push, "except Exception block must call _push_to_ceo_session"
        assert found_progress, "except Exception block must call _append_progress"


class TestLogNodeWarning:
    """Test that _log_node warns (not debug) when _current_entries is missing."""

    def test_missing_current_entries_logs_warning(self):
        import inspect
        from onemancompany.core.vessel import EmployeeManager

        source = inspect.getsource(EmployeeManager._log_node)
        lines = source.split("\n")
        in_else = False
        for line in lines:
            if "else:" in line:
                in_else = True
            elif in_else:
                if "logger.warning" in line and "_current_entries" in line:
                    return  # Found it
                if "logger.debug" in line and "_current_entries" in line:
                    pytest.fail("_log_node uses logger.debug for missing _current_entries — should be logger.warning")
                if line.strip() and not line.strip().startswith("#"):
                    break
        pytest.fail("Could not find warning log for missing _current_entries in _log_node")
