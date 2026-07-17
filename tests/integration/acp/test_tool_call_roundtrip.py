"""Integration test: tool call proxy via HTTP.

Tests the OMCAcpClient callbacks — specifically ext_notification for heartbeat
and usage_final, and request_permission via policy engine — as a higher-level
integration scenario that exercises the client callbacks end-to-end.

The "HTTP roundtrip" is simulated: we call the OMCAcpClient methods directly
(as the ACP connection would) and verify the manager state + EventBus are
updated correctly.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_manager_with_employee(emp_id: str = "tc-emp-001") -> Any:
    """Create an AcpConnectionManager with a pre-registered mock employee."""
    from onemancompany.acp.client import AcpConnectionManager, PendingResult

    manager = AcpConnectionManager()

    proc = MagicMock()
    proc.returncode = None
    manager._processes[emp_id] = proc
    manager._sessions[emp_id] = emp_id
    manager._executor_types[emp_id] = "langchain"
    manager._extra_envs[emp_id] = {}

    # Pre-inject a pending result so usage_final has something to signal
    pending = PendingResult()
    manager._pending_results[emp_id] = pending

    return manager, pending


# ---------------------------------------------------------------------------
# Test 1: heartbeat ext_notification updates manager timestamp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_heartbeat_ext_notification_updates_manager() -> None:
    """ext_notification('heartbeat') → manager.record_heartbeat called with correct emp_id."""
    from onemancompany.acp.client import OMCAcpClient, AcpConnectionManager

    emp_id = "tc-emp-001"
    manager = AcpConnectionManager()

    client = OMCAcpClient(employee_id=emp_id, manager=manager)

    import time
    before = time.monotonic()
    await client.ext_notification("heartbeat", {})
    after = time.monotonic()

    ts = manager._heartbeat_timestamps.get(emp_id)
    assert ts is not None, "Heartbeat timestamp should be recorded"
    assert before <= ts <= after


# ---------------------------------------------------------------------------
# Test 2: usage_final ext_notification signals PendingResult
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_usage_final_ext_notification_signals_pending_result() -> None:
    """ext_notification('usage_final') → PendingResult.usage_final_event set with token counts."""
    emp_id = "tc-emp-002"
    manager, pending = _make_manager_with_employee(emp_id)

    from onemancompany.acp.client import OMCAcpClient

    client = OMCAcpClient(employee_id=emp_id, manager=manager)

    assert not pending.usage_final_event.is_set()

    await client.ext_notification("usage_final", {
        "input_tokens": 250,
        "output_tokens": 125,
        "cost_usd": 0.005,
        "model": "claude-opus",
    })

    assert pending.usage_final_event.is_set()
    assert pending.input_tokens == 250
    assert pending.output_tokens == 125
    assert pending.cost_usd == 0.005
    assert pending.model_used == "claude-opus"


# ---------------------------------------------------------------------------
# Test 3: ide_endpoint ext_notification stores port
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_ide_endpoint_ext_notification_stores_port() -> None:
    """ext_notification('http_endpoint') → manager records IDE endpoint port."""
    from onemancompany.acp.client import OMCAcpClient, AcpConnectionManager

    emp_id = "tc-emp-003"
    manager = AcpConnectionManager()
    client = OMCAcpClient(employee_id=emp_id, manager=manager)

    await client.ext_notification("http_endpoint", {"port": 9876})

    assert manager.get_ide_endpoint(emp_id) == 9876


# ---------------------------------------------------------------------------
# Test 4: session_update publishes to EventBus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_session_update_publishes_to_event_bus() -> None:
    """session_update with AgentMessageChunk → EventBus.publish called once."""
    from onemancompany.acp.client import OMCAcpClient, AcpConnectionManager
    from onemancompany.acp.adapter import AgentMessageChunk

    emp_id = "tc-emp-004"
    manager = AcpConnectionManager()
    manager._pending_results[emp_id] = __import__(
        "onemancompany.acp.client", fromlist=["PendingResult"]
    ).PendingResult()

    client = OMCAcpClient(employee_id=emp_id, manager=manager)

    # Build a minimal AgentMessageChunk using the real schema types
    from acp.schema import TextContentBlock
    text_content = TextContentBlock(type="text", text="Hello from agent")
    chunk = AgentMessageChunk(
        message_id="msg-001",
        content=text_content,
        session_update="agent_message_chunk",
    )

    published_events: list[Any] = []

    async def _capture_publish(event: Any) -> None:
        published_events.append(event)

    from onemancompany.core.events import event_bus
    with patch.object(event_bus, "publish", side_effect=_capture_publish):
        await client.session_update(session_id=emp_id, update=chunk)

    assert len(published_events) == 1, "EventBus.publish should have been called once"
