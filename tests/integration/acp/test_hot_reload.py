"""Integration test: hot_reload_employee flow.

Verifies the full hot-reload sequence:
  close_session → kill process → spawn new subprocess → initialize → load_session

This is end-to-end through AcpConnectionManager.hot_reload_employee, using
mock subprocess + connection.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

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
# Test: hot_reload_employee performs close → kill → respawn → load_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_hot_reload_close_kill_spawn_load() -> None:
    """hot_reload_employee: close old session, kill old process, spawn new, load_session."""
    from onemancompany.acp.client import AcpConnectionManager

    emp_id = "hr-emp-001"
    manager = AcpConnectionManager()

    old_proc = _make_mock_process(returncode=None)
    old_conn = _make_mock_conn(emp_id)

    # Pre-register state
    manager._processes[emp_id] = old_proc
    manager._connections[emp_id] = old_conn
    manager._sessions[emp_id] = emp_id
    manager._executor_types[emp_id] = "langchain"
    manager._extra_envs[emp_id] = {"KEY": "val"}

    new_proc = _make_mock_process(returncode=None)
    new_conn = _make_mock_conn(emp_id)

    async def _fake_spawn(employee_id: str, executor_type: str, extra_env: dict) -> Any:
        assert employee_id == emp_id
        assert executor_type == "langchain"
        assert extra_env == {"KEY": "val"}
        return new_proc, new_conn

    with patch.object(manager, "_spawn_subprocess", side_effect=_fake_spawn):
        await manager.hot_reload_employee(emp_id)

    # Old session should have been closed
    old_conn.close_session.assert_awaited_once_with(session_id=emp_id)

    # Old process should have been killed
    old_proc.kill.assert_called_once()

    # New process and connection should be stored
    assert manager._processes[emp_id] is new_proc
    assert manager._connections[emp_id] is new_conn

    # New connection: initialize then load_session (NOT resume_session or new_session)
    new_conn.initialize.assert_awaited_once()
    new_conn.load_session.assert_awaited_once()
    new_conn.resume_session.assert_not_called()
    new_conn.new_session.assert_not_called()

    # Session ID preserved
    assert manager._sessions[emp_id] == emp_id

    # Heartbeat timestamp refreshed
    import time
    assert manager._heartbeat_timestamps[emp_id] > 0


# ---------------------------------------------------------------------------
# Test: hot_reload tolerates close_session errors (conn may already be dead)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_hot_reload_tolerates_close_session_error() -> None:
    """hot_reload_employee: close_session raising an exception should not abort reload."""
    from onemancompany.acp.client import AcpConnectionManager

    emp_id = "hr-emp-002"
    manager = AcpConnectionManager()

    old_proc = _make_mock_process(returncode=None)
    old_conn = _make_mock_conn(emp_id)
    old_conn.close_session = AsyncMock(side_effect=ConnectionError("pipe broken"))

    manager._processes[emp_id] = old_proc
    manager._connections[emp_id] = old_conn
    manager._sessions[emp_id] = emp_id
    manager._executor_types[emp_id] = "langchain"
    manager._extra_envs[emp_id] = {}

    new_proc = _make_mock_process(returncode=None)
    new_conn = _make_mock_conn(emp_id)

    async def _fake_spawn(employee_id: str, executor_type: str, extra_env: dict) -> Any:
        return new_proc, new_conn

    # Should NOT raise even though close_session threw
    with patch.object(manager, "_spawn_subprocess", side_effect=_fake_spawn):
        await manager.hot_reload_employee(emp_id)  # must not raise

    # Reload should have completed: new process stored
    assert manager._processes[emp_id] is new_proc
    assert manager._connections[emp_id] is new_conn
    new_conn.initialize.assert_awaited_once()
    new_conn.load_session.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test: hot_reload preserves executor_type and extra_env for new spawn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_hot_reload_preserves_executor_type_and_extra_env() -> None:
    """hot_reload_employee: new spawn uses the same executor_type and extra_env."""
    from onemancompany.acp.client import AcpConnectionManager

    emp_id = "hr-emp-003"
    manager = AcpConnectionManager()

    old_proc = _make_mock_process()
    old_conn = _make_mock_conn(emp_id)

    manager._processes[emp_id] = old_proc
    manager._connections[emp_id] = old_conn
    manager._sessions[emp_id] = emp_id
    manager._executor_types[emp_id] = "claude_cli"
    manager._extra_envs[emp_id] = {"OMC_MODEL": "claude-opus-4", "DEBUG": "1"}

    spawn_calls: list[tuple] = []

    new_proc = _make_mock_process()
    new_conn = _make_mock_conn(emp_id)

    async def _fake_spawn(employee_id: str, executor_type: str, extra_env: dict) -> Any:
        spawn_calls.append((employee_id, executor_type, dict(extra_env)))
        return new_proc, new_conn

    with patch.object(manager, "_spawn_subprocess", side_effect=_fake_spawn):
        await manager.hot_reload_employee(emp_id)

    assert len(spawn_calls) == 1
    called_emp_id, called_executor, called_env = spawn_calls[0]
    assert called_emp_id == emp_id
    assert called_executor == "claude_cli"
    assert called_env == {"OMC_MODEL": "claude-opus-4", "DEBUG": "1"}
