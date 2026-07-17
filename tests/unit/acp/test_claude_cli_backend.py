"""Unit tests for acp/backends/claude_cli_backend.py."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


class TestClaudeCliBackendExecute:
    @pytest.mark.asyncio
    async def test_claude_cli_backend_calls_run_claude_session(self, monkeypatch):
        """execute() calls run_claude_session with correct args and returns result dict."""
        mock_result = {
            "output": "done",
            "model": "claude",
            "input_tokens": 10,
            "output_tokens": 5,
        }
        mock_run = AsyncMock(return_value=mock_result)

        monkeypatch.setenv("OMC_PROJECT_ID", "proj-abc")
        monkeypatch.setenv("OMC_WORK_DIR", "/tmp/work")
        monkeypatch.setenv("OMC_TASK_ID", "task-001")

        with patch(
            "onemancompany.acp.backends.claude_cli_backend.run_claude_session",
            mock_run,
        ):
            from onemancompany.acp.backends.claude_cli_backend import ClaudeCliAcpBackend  # noqa: PLC0415

            backend = ClaudeCliAcpBackend(
                employee_id="00010",
                server_url="http://localhost:8000",
            )
            cancel_event = asyncio.Event()
            result = await backend.execute(
                task_description="Write a report",
                client=None,
                session_id="sess-1",
                cancel_event=cancel_event,
            )

        mock_run.assert_called_once_with(
            "00010",
            "proj-abc",
            prompt="Write a report",
            work_dir="/tmp/work",
            task_id="task-001",
        )

        assert isinstance(result, dict)
        assert result["output"] == "done"
        assert result["model"] == "claude"
        assert result["error"] is None
        assert result["tokens"]["input"] == 10
        assert result["tokens"]["output"] == 5
        assert result["tokens"]["cost_usd"] is None

    @pytest.mark.asyncio
    async def test_claude_cli_backend_handles_daemon_error(self, monkeypatch):
        """execute() should surface [claude-daemon error] as result error field."""
        mock_result = {
            "output": "[claude-daemon error] `claude` CLI not found on PATH",
            "model": "",
            "input_tokens": 0,
            "output_tokens": 0,
        }
        mock_run = AsyncMock(return_value=mock_result)

        monkeypatch.setenv("OMC_PROJECT_ID", "proj-abc")
        monkeypatch.setenv("OMC_WORK_DIR", "")
        monkeypatch.setenv("OMC_TASK_ID", "")

        with patch(
            "onemancompany.acp.backends.claude_cli_backend.run_claude_session",
            mock_run,
        ):
            from onemancompany.acp.backends.claude_cli_backend import ClaudeCliAcpBackend  # noqa: PLC0415

            backend = ClaudeCliAcpBackend(employee_id="00010", server_url="http://localhost:8000")
            result = await backend.execute(
                task_description="Do something",
                client=None,
                session_id="sess-2",
                cancel_event=asyncio.Event(),
            )

        assert result["error"] is not None
        assert "claude-daemon error" in result["error"]

    def test_set_model_is_noop(self):
        """set_model() is a no-op for Claude CLI backend (CLI manages own model)."""
        with patch("onemancompany.acp.backends.claude_cli_backend.run_claude_session"):
            from onemancompany.acp.backends.claude_cli_backend import ClaudeCliAcpBackend  # noqa: PLC0415

            backend = ClaudeCliAcpBackend(employee_id="00010", server_url="http://localhost:8000")
            backend.set_model("some-model")  # should not raise

    def test_set_config_is_noop(self):
        """set_config() is a no-op for Claude CLI backend."""
        with patch("onemancompany.acp.backends.claude_cli_backend.run_claude_session"):
            from onemancompany.acp.backends.claude_cli_backend import ClaudeCliAcpBackend  # noqa: PLC0415

            backend = ClaudeCliAcpBackend(employee_id="00010", server_url="http://localhost:8000")
            backend.set_config("temperature", 0.5)  # should not raise
