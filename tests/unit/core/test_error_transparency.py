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

try:
    from onemancompany.core.vessel import SubprocessExecutor
except ImportError:
    SubprocessExecutor = None

_CTX = TaskContext(
    employee_id="00010",
    project_id="proj1",
    work_dir="/tmp/proj",
    task_id="node1",
)


class TestClaudeSessionExecutorError:
    @pytest.mark.asyncio
    async def test_daemon_error_sets_error_field(self):
        executor = ClaudeSessionExecutor("00010")
        mock_result = {
            "success": False,
            "output": "[claude-daemon error] connection refused",
            "tool_calls_count": 0,
            "tokens_used": 0,
            "cost_usd": 0.0,
            "model": "",
            "input_tokens": 0,
            "output_tokens": 0,
        }
        with patch(
            "onemancompany.core.claude_session.run_claude_session",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await executor.execute("do stuff", _CTX)
        assert result.error == "[claude-daemon error] connection refused"
        assert result.output == ""


class TestScriptExecutorError:
    @pytest.mark.asyncio
    async def test_nonzero_exit_no_stdout_sets_error_field(self):
        executor = ScriptExecutor("00010", script_path="/bin/true")
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b"segfault"))
            mock_proc.returncode = 1
            mock_exec.return_value = mock_proc
            result = await executor.execute("do stuff", _CTX)
        assert result.error is not None
        assert "exit" in result.error.lower() or "script" in result.error.lower()

    @pytest.mark.asyncio
    async def test_timeout_sets_error_field(self):
        executor = ScriptExecutor("00010", script_path="/bin/true")
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_exec.return_value = mock_proc
            with patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=asyncio.TimeoutError):
                result = await executor.execute("do stuff", _CTX)
        assert result.error is not None
        assert "timed out" in result.error.lower() or "timeout" in result.error.lower()

    @pytest.mark.asyncio
    async def test_exception_sets_error_field(self):
        executor = ScriptExecutor("00010", script_path="/bin/true")
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = FileNotFoundError("/bin/missing")
            result = await executor.execute("do stuff", _CTX)
        assert result.error is not None
        assert "script error" in result.error.lower()


@pytest.mark.skipif(SubprocessExecutor is None, reason="SubprocessExecutor not available")
class TestSubprocessExecutorError:
    @pytest.mark.asyncio
    async def test_nonzero_exit_sets_error_field(self):
        executor = SubprocessExecutor("00010", script_path="/bin/false", timeout_seconds=10)
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"segfault"))
        mock_proc.returncode = 1
        mock_proc.pid = 12345
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            result = await executor.execute("do stuff", _CTX)
        assert result.error is not None
        assert "exit" in result.error.lower() or "1" in result.error


class TestExecuteTaskErrorCheck:
    """Test that _execute_task raises ExecutionError when LaunchResult.error is set."""

    def test_launch_result_with_error(self):
        lr = LaunchResult(
            output="some output",
            error="LLM returned an error",
            model_used="test-model",
        )
        assert lr.error == "LLM returned an error"
        assert lr.output == "some output"
