"""Unit tests for core/claude_session.py — Claude CLI session management."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.claude_session import (
    _get_session_lock,
    _load_sessions,
    _mark_session_used,
    _save_sessions,
    _session_locks,
    _sessions_file,
    cleanup_session,
    get_or_create_session,
    list_sessions,
    run_claude_session,
)


@pytest.fixture(autouse=True)
def _clear_locks():
    """Clear session locks between tests."""
    _session_locks.clear()
    yield
    _session_locks.clear()


# ---------------------------------------------------------------------------
# _sessions_file / _load_sessions / _save_sessions
# ---------------------------------------------------------------------------

class TestSessionIO:
    def test_sessions_file_path(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            path = _sessions_file("00010")
            assert path == tmp_path / "00010" / "sessions.json"

    def test_load_sessions_missing_file(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            result = _load_sessions("00010")
            assert result == {}

    def test_save_and_load_sessions(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            data = {"project1": {"session_id": "abc", "used": False}}
            _save_sessions("00010", data)
            loaded = _load_sessions("00010")
            assert loaded == data

    def test_load_corrupt_json(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            sess_dir = tmp_path / "00010"
            sess_dir.mkdir(parents=True)
            (sess_dir / "sessions.json").write_text("not json", encoding="utf-8")
            result = _load_sessions("00010")
            assert result == {}

    def test_save_creates_parent_dirs(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            _save_sessions("00010", {"p": {"session_id": "x"}})
            assert (tmp_path / "00010" / "sessions.json").exists()


# ---------------------------------------------------------------------------
# _get_session_lock
# ---------------------------------------------------------------------------

class TestSessionLock:
    def test_returns_lock(self):
        lock = _get_session_lock("emp1", "proj1")
        assert isinstance(lock, asyncio.Lock)

    def test_same_key_returns_same_lock(self):
        lock1 = _get_session_lock("emp1", "proj1")
        lock2 = _get_session_lock("emp1", "proj1")
        assert lock1 is lock2

    def test_different_key_returns_different_lock(self):
        lock1 = _get_session_lock("emp1", "proj1")
        lock2 = _get_session_lock("emp1", "proj2")
        assert lock1 is not lock2


# ---------------------------------------------------------------------------
# get_or_create_session
# ---------------------------------------------------------------------------

class TestGetOrCreateSession:
    def test_creates_new_session(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            session_id, is_new = get_or_create_session("00010", "proj1")
            assert is_new is True
            assert len(session_id) == 36  # UUID format

    def test_returns_existing_used_session(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            data = {"proj1": {"session_id": "existing-uuid", "used": True, "work_dir": "", "created": ""}}
            _save_sessions("00010", data)
            session_id, is_new = get_or_create_session("00010", "proj1")
            assert session_id == "existing-uuid"
            assert is_new is False  # should resume

    def test_returns_existing_unused_session_as_new(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            data = {"proj1": {"session_id": "unused-uuid", "used": False, "work_dir": "", "created": ""}}
            _save_sessions("00010", data)
            session_id, is_new = get_or_create_session("00010", "proj1")
            assert session_id == "unused-uuid"
            assert is_new is True  # not yet used, treat as new

    def test_saves_new_session_to_disk(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            session_id, _ = get_or_create_session("00010", "proj1", work_dir="/tmp/work")
            loaded = _load_sessions("00010")
            assert "proj1" in loaded
            assert loaded["proj1"]["session_id"] == session_id
            assert loaded["proj1"]["work_dir"] == "/tmp/work"
            assert loaded["proj1"]["used"] is False

    def test_different_projects_get_different_sessions(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            s1, _ = get_or_create_session("00010", "proj1")
            s2, _ = get_or_create_session("00010", "proj2")
            assert s1 != s2


# ---------------------------------------------------------------------------
# _mark_session_used
# ---------------------------------------------------------------------------

class TestMarkSessionUsed:
    def test_marks_session_used(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            get_or_create_session("00010", "proj1")
            loaded = _load_sessions("00010")
            assert loaded["proj1"]["used"] is False

            _mark_session_used("00010", "proj1")
            loaded = _load_sessions("00010")
            assert loaded["proj1"]["used"] is True

    def test_noop_if_already_used(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            data = {"proj1": {"session_id": "s1", "used": True}}
            _save_sessions("00010", data)
            _mark_session_used("00010", "proj1")  # should not error
            loaded = _load_sessions("00010")
            assert loaded["proj1"]["used"] is True

    def test_noop_if_no_session(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            _mark_session_used("00010", "nonexistent")  # should not error


# ---------------------------------------------------------------------------
# run_claude_session
# ---------------------------------------------------------------------------

class TestRunClaudeSession:
    async def test_successful_new_session(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            mock_proc = AsyncMock()
            mock_proc.pid = 12345
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"output text", b""))

            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await run_claude_session("00010", "proj1", "do something")
                assert result == "output text"

                # Session should be marked as used
                loaded = _load_sessions("00010")
                assert loaded["proj1"]["used"] is True

    async def test_nonzero_exit_with_output(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            mock_proc = AsyncMock()
            mock_proc.pid = 12345
            mock_proc.returncode = 1
            mock_proc.communicate = AsyncMock(return_value=(b"partial output", b"some error"))

            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await run_claude_session("00010", "proj1", "do something")
                # When there IS output, even with non-zero exit, output is returned
                assert result == "partial output"

    async def test_nonzero_exit_without_output(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            mock_proc = AsyncMock()
            mock_proc.pid = 12345
            mock_proc.returncode = 1
            mock_proc.communicate = AsyncMock(return_value=(b"", b"error details"))

            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await run_claude_session("00010", "proj1", "do something")
                assert "[claude-session error]" in result
                assert "error details" in result

    async def test_timeout_handling(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            mock_proc = AsyncMock()
            mock_proc.pid = 12345
            mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_proc.terminate = MagicMock()

            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await run_claude_session("00010", "proj1", "slow task", timeout=1)
                assert "[claude-session timeout]" in result

    async def test_timeout_terminate_raises_exception(self, tmp_path):
        """Lines 159-160: proc.terminate() raises — should be swallowed."""
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            mock_proc = AsyncMock()
            mock_proc.pid = 12345
            mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_proc.terminate = MagicMock(side_effect=ProcessLookupError("already dead"))

            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await run_claude_session("00010", "proj1", "slow task", timeout=1)
                assert "[claude-session timeout]" in result
                mock_proc.terminate.assert_called_once()

    async def test_cli_not_found(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()):
                result = await run_claude_session("00010", "proj1", "test")
                assert "CLI not found" in result

    async def test_unexpected_error(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            with patch("asyncio.create_subprocess_exec", side_effect=OSError("broken pipe")):
                result = await run_claude_session("00010", "proj1", "test")
                assert "[claude-session error]" in result
                assert "broken pipe" in result

    async def test_resume_uses_resume_flag(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            # Create a used session first
            data = {"proj1": {"session_id": "old-session", "used": True, "work_dir": "", "created": ""}}
            _save_sessions("00010", data)

            mock_proc = AsyncMock()
            mock_proc.pid = 12345
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"resumed", b""))

            with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
                result = await run_claude_session("00010", "proj1", "continue work")
                assert result == "resumed"
                # Check that --resume was used
                call_args = mock_exec.call_args[0]
                assert "--resume" in call_args


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------

class TestListSessions:
    def test_empty(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            result = list_sessions("00010")
            assert result == []

    def test_with_sessions(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            data = {
                "proj1": {"session_id": "s1", "work_dir": "/w1", "created": "2024-01-01", "used": True},
                "proj2": {"session_id": "s2", "work_dir": "/w2", "created": "2024-01-02", "used": False},
            }
            _save_sessions("00010", data)

            result = list_sessions("00010")
            assert len(result) == 2
            pids = {r["project_id"] for r in result}
            assert pids == {"proj1", "proj2"}


# ---------------------------------------------------------------------------
# cleanup_session
# ---------------------------------------------------------------------------

class TestCleanupSession:
    def test_removes_session(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            data = {"proj1": {"session_id": "s1", "used": True}}
            _save_sessions("00010", data)

            cleanup_session("00010", "proj1")
            loaded = _load_sessions("00010")
            assert "proj1" not in loaded

    def test_noop_if_no_session(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            cleanup_session("00010", "nonexistent")  # should not error

    def test_preserves_other_sessions(self, tmp_path):
        with patch("onemancompany.core.claude_session.EMPLOYEES_DIR", tmp_path):
            data = {
                "proj1": {"session_id": "s1", "used": True},
                "proj2": {"session_id": "s2", "used": False},
            }
            _save_sessions("00010", data)

            cleanup_session("00010", "proj1")
            loaded = _load_sessions("00010")
            assert "proj1" not in loaded
            assert "proj2" in loaded
