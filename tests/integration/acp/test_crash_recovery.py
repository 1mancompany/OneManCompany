"""Integration test: watchdog crash recovery.

Verifies that when a registered subprocess terminates (returncode set),
the watchdog detects it and calls _respawn_employee.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_process(returncode: int | None = None) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


def _make_mock_conn(session_id: str) -> MagicMock:
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


# ---------------------------------------------------------------------------
# Test: watchdog detects crashed process and calls _respawn_employee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_watchdog_detects_crash_and_respawns() -> None:
    """Watchdog: process.returncode != None → _respawn_employee called."""
    from onemancompany.acp.client import AcpConnectionManager

    emp_id = "cr-emp-001"
    manager = AcpConnectionManager()
    manager.WATCHDOG_INTERVAL = 0.05  # fast ticks for testing

    # Register a "crashed" process (returncode already set)
    crashed_proc = _make_mock_process(returncode=1)
    mock_conn = _make_mock_conn(emp_id)

    manager._processes[emp_id] = crashed_proc
    manager._connections[emp_id] = mock_conn
    manager._sessions[emp_id] = emp_id
    manager._executor_types[emp_id] = "langchain"
    manager._extra_envs[emp_id] = {}
    manager._heartbeat_timestamps[emp_id] = time.monotonic()

    respawn_calls: list[str] = []

    async def _fake_respawn(employee_id: str) -> None:
        respawn_calls.append(employee_id)
        # Update to a healthy process so watchdog stops respawning
        healthy = _make_mock_process(returncode=None)
        manager._processes[employee_id] = healthy
        manager._heartbeat_timestamps[employee_id] = time.monotonic()

    with patch.object(manager, "_respawn_employee", side_effect=_fake_respawn):
        # Manually run one watchdog tick
        manager._watchdog_task = None
        manager._ensure_watchdog()

        # Let the watchdog fire at least once
        await asyncio.sleep(0.2)

        # Cancel the watchdog
        if manager._watchdog_task and not manager._watchdog_task.done():
            manager._watchdog_task.cancel()
            try:
                await manager._watchdog_task
            except asyncio.CancelledError:
                pass

    assert emp_id in respawn_calls, (
        f"_respawn_employee should have been called for {emp_id!r} "
        f"when process has returncode=1, got calls={respawn_calls}"
    )


# ---------------------------------------------------------------------------
# Test: _respawn_employee re-establishes connection state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_respawn_employee_restores_connection() -> None:
    """_respawn_employee: spawn new process + initialize + resume_session."""
    from onemancompany.acp.client import AcpConnectionManager

    emp_id = "cr-emp-002"
    manager = AcpConnectionManager()

    # Pre-register state
    manager._executor_types[emp_id] = "langchain"
    manager._extra_envs[emp_id] = {}
    manager._sessions[emp_id] = emp_id

    new_proc = _make_mock_process(returncode=None)
    new_conn = _make_mock_conn(emp_id)

    async def _fake_spawn(employee_id: str, executor_type: str, extra_env: dict) -> Any:
        return new_proc, new_conn

    with patch.object(manager, "_spawn_subprocess", side_effect=_fake_spawn):
        await manager._respawn_employee(emp_id)

    # New process and connection stored
    assert manager._processes[emp_id] is new_proc
    assert manager._connections[emp_id] is new_conn

    # ACP initialization and session resume called
    new_conn.initialize.assert_awaited_once()
    new_conn.resume_session.assert_awaited_once()

    # Heartbeat timestamp refreshed
    assert emp_id in manager._heartbeat_timestamps
