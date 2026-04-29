"""Tests for session state persistence and hot-reload (Task 11).

Covers:
1. test_session_state_saved          — _save_session_state writes file with correct data
2. test_session_state_loaded_on_resume — resume_session reads file, populates _current_mode_id
3. test_session_state_cleared_after_prompt — _clear_session_state removes the file
4. test_missing_state_file_no_error  — load_session with nonexistent file does not crash
5. test_hot_reload_uses_load_session — AcpConnectionManager.hot_reload_employee sequence
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _make_env(monkeypatch: pytest.MonkeyPatch, employee_dir: str) -> None:
    """Inject required env vars for OMCAgent construction."""
    monkeypatch.setenv("OMC_EMPLOYEE_ID", "test-emp-persist")
    monkeypatch.setenv("OMC_EXECUTOR_TYPE", "langchain")
    monkeypatch.setenv("OMC_SERVER_URL", "http://localhost:8000")
    monkeypatch.setenv("OMC_EMPLOYEE_DIR", employee_dir)


def _make_mock_conn(session_id: str = "emp-hot") -> MagicMock:
    """Return a mock ACP ClientSideConnection with async methods."""
    conn = MagicMock()
    conn.initialize = AsyncMock(return_value=MagicMock())
    new_sess_resp = MagicMock()
    new_sess_resp.session_id = session_id
    conn.new_session = AsyncMock(return_value=new_sess_resp)
    conn.close_session = AsyncMock(return_value=MagicMock())
    conn.resume_session = AsyncMock(return_value=MagicMock())
    conn.load_session = AsyncMock(return_value=MagicMock())
    conn.prompt = AsyncMock(return_value=MagicMock())
    conn.cancel = AsyncMock()
    return conn


def _make_mock_process() -> MagicMock:
    """Return a mock asyncio subprocess.Process."""
    proc = MagicMock()
    proc.returncode = None
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# Test 1: _save_session_state writes file with correct data
# ---------------------------------------------------------------------------


class TestSessionStateSaved:
    """test_session_state_saved"""

    def test_session_state_saved(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """_save_session_state must create session_state.json with the given payload."""
        _make_env(monkeypatch, str(tmp_path))

        from onemancompany.acp.agent_process import OMCAgent

        agent = OMCAgent()

        payload: dict[str, Any] = {"current_mode_id": "plan", "extra_key": "value"}
        agent._save_session_state(payload)

        state_path = tmp_path / "session_state.json"
        assert state_path.exists(), "session_state.json must exist after _save_session_state"

        written = json.loads(state_path.read_text(encoding="utf-8"))
        assert written == payload


# ---------------------------------------------------------------------------
# Test 2: resume_session populates _current_mode_id from file
# ---------------------------------------------------------------------------


class TestSessionStateLoadedOnResume:
    """test_session_state_loaded_on_resume"""

    @pytest.mark.asyncio
    async def test_session_state_loaded_on_resume(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """resume_session must read session_state.json and restore _current_mode_id."""
        _make_env(monkeypatch, str(tmp_path))

        # Pre-write a state file
        state_path = tmp_path / "session_state.json"
        state_path.write_text(
            json.dumps({"current_mode_id": "review"}), encoding="utf-8"
        )

        from onemancompany.acp.agent_process import OMCAgent

        agent = OMCAgent()

        # Verify the default mode is different so we can detect the change
        assert agent._current_mode_id != "review"

        resp = await agent.resume_session(cwd=str(tmp_path), session_id="test-emp-persist")

        assert agent._current_mode_id == "review", (
            "resume_session must restore _current_mode_id from session_state.json"
        )
        assert resp is not None


# ---------------------------------------------------------------------------
# Test 3: _clear_session_state removes the file
# ---------------------------------------------------------------------------


class TestSessionStateClearedAfterPrompt:
    """test_session_state_cleared_after_prompt"""

    def test_session_state_cleared_after_prompt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """_clear_session_state must delete session_state.json if it exists."""
        _make_env(monkeypatch, str(tmp_path))

        from onemancompany.acp.agent_process import OMCAgent

        agent = OMCAgent()

        # First save some state
        agent._save_session_state({"current_mode_id": "execute"})
        state_path = tmp_path / "session_state.json"
        assert state_path.exists(), "pre-condition: file must exist before clear"

        # Now clear
        agent._clear_session_state()

        assert not state_path.exists(), "session_state.json must be deleted by _clear_session_state"


# ---------------------------------------------------------------------------
# Test 4: load_session with nonexistent file does not crash
# ---------------------------------------------------------------------------


class TestMissingStateFileNoError:
    """test_missing_state_file_no_error"""

    @pytest.mark.asyncio
    async def test_missing_state_file_no_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """load_session must not raise even when session_state.json does not exist."""
        # Use a subdirectory that has no state file
        missing_dir = tmp_path / "no_such_employee"
        _make_env(monkeypatch, str(missing_dir))

        from onemancompany.acp.agent_process import OMCAgent

        agent = OMCAgent()
        default_mode = agent._current_mode_id

        # Must not raise
        resp = await agent.load_session(cwd=str(missing_dir), session_id="test-emp-persist")

        # Mode should remain at default since there was nothing to load
        assert agent._current_mode_id == default_mode
        assert resp is not None


# ---------------------------------------------------------------------------
# Test 5: hot_reload_employee calls close_session → kill → spawn → initialize → load_session
# ---------------------------------------------------------------------------


class TestHotReloadUsesLoadSession:
    """test_hot_reload_uses_load_session"""

    @pytest.mark.asyncio
    async def test_hot_reload_uses_load_session(self) -> None:
        """AcpConnectionManager.hot_reload_employee must follow the full sequence:
        close_session → kill → spawn new process → initialize → load_session.
        """
        from onemancompany.acp.client import AcpConnectionManager

        manager = AcpConnectionManager()

        old_conn = _make_mock_conn(session_id="emp-hot")
        old_proc = _make_mock_process()

        # Pre-populate manager state (as if register_employee already ran)
        manager._connections["emp-hot"] = old_conn
        manager._processes["emp-hot"] = old_proc
        manager._sessions["emp-hot"] = "emp-hot"
        manager._executor_types["emp-hot"] = "langchain"
        manager._extra_envs["emp-hot"] = {}

        new_proc = _make_mock_process()
        new_conn = _make_mock_conn(session_id="emp-hot")

        call_order: list[str] = []

        # Wrap close_session to record invocation order
        original_close = old_conn.close_session

        async def _tracked_close(**kwargs: Any) -> Any:
            call_order.append("close_session")
            return await original_close(**kwargs)

        old_conn.close_session = _tracked_close

        async def _fake_spawn(
            employee_id: str, executor_type: str, extra_env: dict[str, str]
        ) -> tuple[MagicMock, MagicMock]:
            call_order.append("spawn")
            return new_proc, new_conn

        original_init = new_conn.initialize

        async def _tracked_init(**kwargs: Any) -> Any:
            call_order.append("initialize")
            return await original_init(**kwargs)

        new_conn.initialize = _tracked_init

        original_load = new_conn.load_session

        async def _tracked_load(**kwargs: Any) -> Any:
            call_order.append("load_session")
            return await original_load(**kwargs)

        new_conn.load_session = _tracked_load

        with patch.object(manager, "_spawn_subprocess", side_effect=_fake_spawn):
            await manager.hot_reload_employee("emp-hot")

        # --- Assertions ---

        # Old session must be closed
        assert "close_session" in call_order, "close_session must be called on old connection"

        # Old process must be killed
        old_proc.kill.assert_called_once()

        # New process + connection stored
        assert manager._processes["emp-hot"] is new_proc
        assert manager._connections["emp-hot"] is new_conn

        # Sequence: spawn happened
        assert "spawn" in call_order

        # New connection must be initialized
        assert "initialize" in call_order, "initialize must be called on new connection"

        # load_session must be called (not resume_session)
        new_conn.resume_session.assert_not_awaited()
        assert "load_session" in call_order, "load_session must be called on new connection for hot reload"

        # Verify overall order: close before spawn, spawn before initialize, initialize before load
        assert call_order.index("close_session") < call_order.index("spawn")
        assert call_order.index("spawn") < call_order.index("initialize")
        assert call_order.index("initialize") < call_order.index("load_session")
