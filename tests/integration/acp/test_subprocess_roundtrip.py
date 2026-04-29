"""Integration test: subprocess register → prompt → collect_result roundtrip.

Uses mock subprocess + connection so no real agent process is needed.
Tests the full manager workflow end-to-end.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers (shared with other integration tests)
# ---------------------------------------------------------------------------


def _make_mock_process(returncode: int | None = None) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


def _make_mock_conn(session_id: str = "emp-rt-001") -> MagicMock:
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
# Test: full roundtrip register → prompt → collect_result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_register_and_prompt_roundtrip() -> None:
    """Full roundtrip: register → send_prompt → simulate usage_final → collect_result."""
    from onemancompany.acp.client import AcpConnectionManager
    from onemancompany.core.vessel import LaunchResult

    emp_id = "rt-emp-001"
    manager = AcpConnectionManager()

    mock_proc = _make_mock_process()
    mock_conn = _make_mock_conn(session_id=emp_id)

    async def _fake_spawn(employee_id: str, executor_type: str, extra_env: dict) -> Any:
        return mock_proc, mock_conn

    with patch.object(manager, "_spawn_subprocess", side_effect=_fake_spawn):
        with patch.object(manager, "_ensure_watchdog"):
            await manager.register_employee(emp_id, executor_type="langchain")

    # Verify registration state
    assert emp_id in manager._connections
    assert emp_id in manager._sessions
    assert manager._sessions[emp_id] == emp_id
    mock_conn.initialize.assert_awaited_once()
    mock_conn.new_session.assert_awaited_once()

    # Send a prompt
    await manager.send_prompt(emp_id, "Write a unit test for the ACP adapter.")

    # PendingResult should be created
    pending = manager._pending_results.get(emp_id)
    assert pending is not None
    assert not pending.usage_final_event.is_set()

    # Verify conn.prompt was called with the correct session_id
    mock_conn.prompt.assert_awaited_once()
    call_kwargs = mock_conn.prompt.call_args
    session_id_arg = call_kwargs[1].get("session_id") or call_kwargs[0]
    assert emp_id in str(session_id_arg)

    # Simulate the agent sending back output chunks and usage_final
    pending.output_chunks.append("Hello from the agent!")
    pending.input_tokens = 100
    pending.output_tokens = 50
    pending.cost_usd = 0.001
    pending.model_used = "claude-sonnet"
    pending.usage_final_event.set()

    # Collect the result
    result = await manager.collect_result(emp_id, timeout=5.0)

    assert isinstance(result, LaunchResult)
    assert result.output == "Hello from the agent!"
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.total_tokens == 150
    assert result.cost_usd == 0.001
    assert result.model_used == "claude-sonnet"

    # PendingResult should be cleaned up after collect
    assert emp_id not in manager._pending_results
