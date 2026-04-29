"""Integration test: heartbeat timeout detection.

Verifies that the watchdog detects a stale heartbeat (no heartbeat received
within HEARTBEAT_TIMEOUT seconds) and terminates + respawns the subprocess.
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
# Test: watchdog respawns on heartbeat timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_watchdog_detects_heartbeat_timeout_and_respawns() -> None:
    """Watchdog: stale heartbeat (now - last_hb > HEARTBEAT_TIMEOUT) → terminate + respawn."""
    from onemancompany.acp.client import AcpConnectionManager

    emp_id = "hb-emp-001"
    manager = AcpConnectionManager()
    manager.WATCHDOG_INTERVAL = 0.05  # fast ticks
    manager.HEARTBEAT_TIMEOUT = 0.1   # short timeout

    proc = _make_mock_process(returncode=None)  # alive process
    conn = _make_mock_conn(emp_id)

    manager._processes[emp_id] = proc
    manager._connections[emp_id] = conn
    manager._sessions[emp_id] = emp_id
    manager._executor_types[emp_id] = "langchain"
    manager._extra_envs[emp_id] = {}

    # Set heartbeat to past so it immediately expires
    manager._heartbeat_timestamps[emp_id] = time.monotonic() - 10.0

    respawn_calls: list[str] = []

    async def _fake_respawn(employee_id: str) -> None:
        respawn_calls.append(employee_id)
        # After respawn, refresh heartbeat so watchdog stops looping
        manager._heartbeat_timestamps[employee_id] = time.monotonic()

    with patch.object(manager, "_respawn_employee", side_effect=_fake_respawn):
        manager._ensure_watchdog()
        await asyncio.sleep(0.3)

        if manager._watchdog_task and not manager._watchdog_task.done():
            manager._watchdog_task.cancel()
            try:
                await manager._watchdog_task
            except asyncio.CancelledError:
                pass

    assert emp_id in respawn_calls, (
        f"_respawn_employee not called for {emp_id!r} on heartbeat timeout, "
        f"got calls={respawn_calls}"
    )
    # The alive process should have been terminated before respawn
    proc.terminate.assert_called()


# ---------------------------------------------------------------------------
# Test: fresh heartbeats prevent respawn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_fresh_heartbeats_prevent_respawn() -> None:
    """Watchdog: process alive + recent heartbeat → no respawn called."""
    from onemancompany.acp.client import AcpConnectionManager

    emp_id = "hb-emp-002"
    manager = AcpConnectionManager()
    manager.WATCHDOG_INTERVAL = 0.05
    manager.HEARTBEAT_TIMEOUT = 60.0  # generous timeout

    proc = _make_mock_process(returncode=None)
    conn = _make_mock_conn(emp_id)

    manager._processes[emp_id] = proc
    manager._connections[emp_id] = conn
    manager._sessions[emp_id] = emp_id
    manager._executor_types[emp_id] = "langchain"
    manager._extra_envs[emp_id] = {}
    manager._heartbeat_timestamps[emp_id] = time.monotonic()  # fresh

    respawn_calls: list[str] = []

    async def _fake_respawn(employee_id: str) -> None:
        respawn_calls.append(employee_id)

    with patch.object(manager, "_respawn_employee", side_effect=_fake_respawn):
        manager._ensure_watchdog()
        await asyncio.sleep(0.2)

        if manager._watchdog_task and not manager._watchdog_task.done():
            manager._watchdog_task.cancel()
            try:
                await manager._watchdog_task
            except asyncio.CancelledError:
                pass

    assert respawn_calls == [], (
        f"_respawn_employee should NOT have been called when heartbeat is fresh, "
        f"got calls={respawn_calls}"
    )


# ---------------------------------------------------------------------------
# Test: record_heartbeat resets timer and prevents timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_record_heartbeat_resets_stale_timer() -> None:
    """record_heartbeat updates timestamp so the watchdog sees a recent value.

    We verify this at the data level: record_heartbeat sets a timestamp close
    to now, which is above the (now - HEARTBEAT_TIMEOUT) threshold.
    """
    from onemancompany.acp.client import AcpConnectionManager

    emp_id = "hb-emp-003"
    manager = AcpConnectionManager()

    # Start with a very stale heartbeat
    manager._heartbeat_timestamps[emp_id] = time.monotonic() - 3600.0

    stale_ts = manager._heartbeat_timestamps[emp_id]

    # Call record_heartbeat — this must update the stored timestamp
    before = time.monotonic()
    manager.record_heartbeat(emp_id)
    after = time.monotonic()

    fresh_ts = manager._heartbeat_timestamps[emp_id]

    assert fresh_ts > stale_ts, "record_heartbeat should update the timestamp"
    assert before <= fresh_ts <= after, "Timestamp should be close to now"

    # Verify the refreshed timestamp is within a tight HEARTBEAT_TIMEOUT window
    # Simulate what the watchdog checks: now - last_hb > HEARTBEAT_TIMEOUT
    heartbeat_timeout = 60.0  # default
    simulated_now = time.monotonic()
    assert (simulated_now - fresh_ts) < heartbeat_timeout, (
        "After record_heartbeat, the timestamp should be recent enough "
        "that the default watchdog timeout would NOT trigger"
    )
