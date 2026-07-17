"""Unit tests for extended ACP capabilities:
fork_session, set_mode, set_model, set_config, AvailableCommandsUpdate, and agent modes.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers (mirrors test_client.py helpers)
# ---------------------------------------------------------------------------


def _make_mock_conn(session_id: str = "emp-001") -> MagicMock:
    """Create a mock ACP ClientSideConnection with all extended methods."""
    conn = MagicMock()

    fork_resp = MagicMock()
    fork_resp.session_id = f"{session_id}__fork"
    conn.fork_session = AsyncMock(return_value=fork_resp)

    conn.set_session_mode = AsyncMock(return_value=MagicMock())
    conn.set_session_model = AsyncMock(return_value=MagicMock())
    conn.set_config_option = AsyncMock(return_value=MagicMock())

    return conn


def _make_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required env vars for OMCAgent construction."""
    monkeypatch.setenv("OMC_EMPLOYEE_ID", "test-emp-ext")
    monkeypatch.setenv("OMC_EXECUTOR_TYPE", "langchain")
    monkeypatch.setenv("OMC_SERVER_URL", "http://localhost:8000")
    monkeypatch.setenv("OMC_EMPLOYEE_DIR", "/tmp/test_emp_ext")


# ---------------------------------------------------------------------------
# Test 1: fork_session
# ---------------------------------------------------------------------------


class TestForkSession:
    """test_fork_session"""

    @pytest.mark.asyncio
    async def test_fork_session(self) -> None:
        """AcpConnectionManager.fork_session() delegates to conn.fork_session() and returns forked session id."""
        from onemancompany.acp.client import AcpConnectionManager

        manager = AcpConnectionManager()
        mock_conn = _make_mock_conn(session_id="emp-fork")

        # Inject state directly
        manager._connections["emp-fork"] = mock_conn
        manager._sessions["emp-fork"] = "emp-fork"

        forked_id = await manager.fork_session("emp-fork")

        # Verify delegation occurred with correct session_id
        mock_conn.fork_session.assert_awaited_once()
        call_kwargs = mock_conn.fork_session.call_args
        assert call_kwargs[1].get("session_id") == "emp-fork"

        # Verify the returned id matches the forked session_id from the response
        assert forked_id == "emp-fork__fork"


# ---------------------------------------------------------------------------
# Test 2: set_mode
# ---------------------------------------------------------------------------


class TestSetMode:
    """test_set_mode"""

    @pytest.mark.asyncio
    async def test_set_mode(self) -> None:
        """AcpConnectionManager.set_mode() calls conn.set_session_mode() with correct args."""
        from onemancompany.acp.client import AcpConnectionManager

        manager = AcpConnectionManager()
        mock_conn = _make_mock_conn(session_id="emp-mode")

        manager._connections["emp-mode"] = mock_conn
        manager._sessions["emp-mode"] = "emp-mode"

        await manager.set_mode("emp-mode", "plan")

        mock_conn.set_session_mode.assert_awaited_once()
        call_kwargs = mock_conn.set_session_mode.call_args
        assert call_kwargs[1].get("mode_id") == "plan"
        assert call_kwargs[1].get("session_id") == "emp-mode"


# ---------------------------------------------------------------------------
# Test 3: set_model
# ---------------------------------------------------------------------------


class TestSetModel:
    """test_set_model"""

    @pytest.mark.asyncio
    async def test_set_model(self) -> None:
        """AcpConnectionManager.set_model() calls conn.set_session_model() with correct args."""
        from onemancompany.acp.client import AcpConnectionManager

        manager = AcpConnectionManager()
        mock_conn = _make_mock_conn(session_id="emp-model")

        manager._connections["emp-model"] = mock_conn
        manager._sessions["emp-model"] = "emp-model"

        await manager.set_model("emp-model", "claude-3-5-sonnet")

        mock_conn.set_session_model.assert_awaited_once()
        call_kwargs = mock_conn.set_session_model.call_args
        assert call_kwargs[1].get("model_id") == "claude-3-5-sonnet"
        assert call_kwargs[1].get("session_id") == "emp-model"


# ---------------------------------------------------------------------------
# Test 4: set_config
# ---------------------------------------------------------------------------


class TestSetConfig:
    """test_set_config"""

    @pytest.mark.asyncio
    async def test_set_config(self) -> None:
        """AcpConnectionManager.set_config() calls conn.set_config_option() with correct args."""
        from onemancompany.acp.client import AcpConnectionManager

        manager = AcpConnectionManager()
        mock_conn = _make_mock_conn(session_id="emp-cfg")

        manager._connections["emp-cfg"] = mock_conn
        manager._sessions["emp-cfg"] = "emp-cfg"

        await manager.set_config("emp-cfg", "verbose", True)

        mock_conn.set_config_option.assert_awaited_once()
        call_kwargs = mock_conn.set_config_option.call_args
        assert call_kwargs[1].get("config_id") == "verbose"
        assert call_kwargs[1].get("value") is True
        assert call_kwargs[1].get("session_id") == "emp-cfg"


# ---------------------------------------------------------------------------
# Test 5: agent modes
# ---------------------------------------------------------------------------


class TestAgentModes:
    """test_agent_modes"""

    def test_agent_modes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OMCAgent module-level _MODES list has exactly 3 modes: execute, plan, review."""
        _make_env(monkeypatch)

        from onemancompany.acp import agent_process

        modes = agent_process._MODES

        assert len(modes) == 3, f"Expected 3 modes, got {len(modes)}: {modes}"

        mode_ids = {m["id"] for m in modes}
        assert mode_ids == {"execute", "plan", "review"}


# ---------------------------------------------------------------------------
# Test 6: available commands / _modes structure
# ---------------------------------------------------------------------------


class TestAvailableCommands:
    """test_available_commands"""

    def test_available_commands(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OMCAgent._MODES list entries have id, name, and description keys."""
        _make_env(monkeypatch)

        from onemancompany.acp import agent_process

        modes = agent_process._MODES

        for mode in modes:
            assert "id" in mode, f"Mode missing 'id': {mode}"
            assert "name" in mode, f"Mode missing 'name': {mode}"
            assert "description" in mode, f"Mode missing 'description': {mode}"

            assert isinstance(mode["id"], str) and mode["id"]
            assert isinstance(mode["name"], str) and mode["name"]
            assert isinstance(mode["description"], str) and mode["description"]
