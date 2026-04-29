"""Unit tests for ACP Connection Manager and OMCAcpClient."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_process(returncode: int | None = None) -> MagicMock:
    """Create a mock asyncio subprocess.Process."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


def _make_mock_conn(session_id: str = "emp-001") -> MagicMock:
    """Create a mock ACP ClientSideConnection."""
    conn = MagicMock()

    init_resp = MagicMock()
    conn.initialize = AsyncMock(return_value=init_resp)

    new_sess_resp = MagicMock()
    new_sess_resp.session_id = session_id
    conn.new_session = AsyncMock(return_value=new_sess_resp)

    conn.close_session = AsyncMock(return_value=MagicMock())
    conn.resume_session = AsyncMock(return_value=MagicMock())
    conn.load_session = AsyncMock(return_value=MagicMock())
    conn.fork_session = AsyncMock(return_value=MagicMock(session_id=f"{session_id}__fork"))
    conn.prompt = AsyncMock(return_value=MagicMock())
    conn.cancel = AsyncMock()
    conn.set_session_mode = AsyncMock(return_value=MagicMock())
    conn.set_session_model = AsyncMock(return_value=MagicMock())
    conn.set_config_option = AsyncMock(return_value=MagicMock())

    return conn


# ---------------------------------------------------------------------------
# Test 1: spawn and handshake
# ---------------------------------------------------------------------------


class TestConnectionManagerSpawnAndHandshake:
    """test_connection_manager_spawn_and_handshake"""

    @pytest.mark.asyncio
    async def test_connection_manager_spawn_and_handshake(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """register_employee should spawn subprocess, do ACP handshake, and store connection."""
        from onemancompany.acp.client import AcpConnectionManager

        manager = AcpConnectionManager()

        mock_proc = _make_mock_process()
        mock_conn = _make_mock_conn(session_id="emp-001")

        async def _fake_spawn(employee_id, executor_type, extra_env):
            return mock_proc, mock_conn

        with patch.object(manager, "_spawn_subprocess", side_effect=_fake_spawn):
            with patch.object(manager, "_ensure_watchdog"):
                await manager.register_employee(
                    employee_id="emp-001",
                    executor_type="langchain",
                    extra_env={"EXTRA": "val"},
                )

        # Subprocess stored
        assert manager._processes["emp-001"] is mock_proc
        # Connection stored
        assert manager._connections["emp-001"] is mock_conn
        # Session stored
        assert manager._sessions["emp-001"] == "emp-001"
        # ACP handshake calls made
        mock_conn.initialize.assert_awaited_once()
        mock_conn.new_session.assert_awaited_once()
        # Executor type stored
        assert manager._executor_types["emp-001"] == "langchain"
        # Extra env stored
        assert manager._extra_envs["emp-001"] == {"EXTRA": "val"}


# ---------------------------------------------------------------------------
# Test 2: unregister
# ---------------------------------------------------------------------------


class TestConnectionManagerUnregister:
    """test_connection_manager_unregister"""

    @pytest.mark.asyncio
    async def test_connection_manager_unregister(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """unregister_employee should close session, terminate subprocess, clean up state."""
        from onemancompany.acp.client import AcpConnectionManager

        manager = AcpConnectionManager()

        mock_proc = _make_mock_process()
        mock_conn = _make_mock_conn(session_id="emp-002")

        # Inject state directly (skip spawn)
        manager._processes["emp-002"] = mock_proc
        manager._connections["emp-002"] = mock_conn
        manager._sessions["emp-002"] = "emp-002"
        manager._executor_types["emp-002"] = "langchain"
        manager._extra_envs["emp-002"] = {}
        manager._heartbeat_timestamps["emp-002"] = time.monotonic()
        manager._ide_endpoints["emp-002"] = 9000

        await manager.unregister_employee("emp-002")

        # close_session should be called
        mock_conn.close_session.assert_awaited_once_with(session_id="emp-002")
        # terminate should be called
        mock_proc.terminate.assert_called_once()

        # All state cleaned up
        assert "emp-002" not in manager._processes
        assert "emp-002" not in manager._connections
        assert "emp-002" not in manager._sessions
        assert "emp-002" not in manager._heartbeat_timestamps
        assert "emp-002" not in manager._ide_endpoints


# ---------------------------------------------------------------------------
# Test 3: heartbeat tracking
# ---------------------------------------------------------------------------


class TestHeartbeatTracking:
    """test_heartbeat_tracking"""

    def test_heartbeat_tracking(self) -> None:
        """record_heartbeat should store a recent monotonic timestamp."""
        from onemancompany.acp.client import AcpConnectionManager

        manager = AcpConnectionManager()

        before = time.monotonic()
        manager.record_heartbeat("emp-003")
        after = time.monotonic()

        ts = manager._heartbeat_timestamps.get("emp-003")
        assert ts is not None
        assert before <= ts <= after

    def test_heartbeat_updates_timestamp(self) -> None:
        """Subsequent record_heartbeat calls should update the timestamp."""
        from onemancompany.acp.client import AcpConnectionManager

        manager = AcpConnectionManager()
        manager._heartbeat_timestamps["emp-003"] = 0.0

        manager.record_heartbeat("emp-003")

        ts = manager._heartbeat_timestamps["emp-003"]
        assert ts > 0.0


# ---------------------------------------------------------------------------
# Test 4: send_prompt delegates to connection
# ---------------------------------------------------------------------------


class TestPromptSendsToConnection:
    """test_prompt_sends_to_connection"""

    @pytest.mark.asyncio
    async def test_prompt_sends_to_connection(self) -> None:
        """send_prompt should call conn.prompt with a text block and create PendingResult."""
        from onemancompany.acp.client import AcpConnectionManager, PendingResult

        manager = AcpConnectionManager()

        mock_conn = _make_mock_conn(session_id="emp-004")

        manager._connections["emp-004"] = mock_conn
        manager._sessions["emp-004"] = "emp-004"

        await manager.send_prompt("emp-004", "Do the thing")

        # conn.prompt should have been called once
        mock_conn.prompt.assert_awaited_once()
        call_kwargs = mock_conn.prompt.call_args

        # prompt blocks should be non-empty
        prompt_blocks = call_kwargs[1].get("prompt") or call_kwargs[0][0]
        assert len(prompt_blocks) > 0

        # session_id should be forwarded
        session_id_arg = call_kwargs[1].get("session_id", "")
        assert session_id_arg == "emp-004"

        # PendingResult created
        pending = manager._pending_results.get("emp-004")
        assert isinstance(pending, PendingResult)
        assert not pending.usage_final_event.is_set()
