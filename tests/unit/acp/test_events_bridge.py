"""Tests for ACP events bridge — TDD first pass."""

from onemancompany.acp.events_bridge import acp_update_to_event
from onemancompany.core.models import EventType


def test_acp_update_event_type_exists():
    """EventType.ACP_UPDATE exists with value 'acp_update'."""
    assert EventType.ACP_UPDATE == "acp_update"
    assert EventType.ACP_UPDATE.value == "acp_update"


def test_bridge_message_chunk():
    """Creates a CompanyEvent for a message chunk ACP update."""
    event = acp_update_to_event(
        employee_id="00010",
        kind="message",
        data={"content": "Hello world", "index": 0},
    )
    assert event.type == EventType.ACP_UPDATE
    assert event.agent == "00010"
    assert event.payload["kind"] == "message"
    assert event.payload["employee_id"] == "00010"
    assert event.payload["data"] == {"content": "Hello world", "index": 0}


def test_bridge_tool_call_start():
    """Creates a CompanyEvent for a tool_call_start ACP update."""
    event = acp_update_to_event(
        employee_id="00011",
        kind="tool_call_start",
        data={"tool_name": "read_file", "tool_call_id": "tc_abc"},
    )
    assert event.type == EventType.ACP_UPDATE
    assert event.agent == "00011"
    assert event.payload["kind"] == "tool_call_start"
    assert event.payload["employee_id"] == "00011"
    assert event.payload["data"]["tool_name"] == "read_file"


def test_bridge_plan_update():
    """Creates a CompanyEvent for a plan ACP update."""
    event = acp_update_to_event(
        employee_id="00012",
        kind="plan",
        data={"steps": ["step1", "step2"], "current": 0},
    )
    assert event.type == EventType.ACP_UPDATE
    assert event.agent == "00012"
    assert event.payload["kind"] == "plan"
    assert event.payload["employee_id"] == "00012"
    assert event.payload["data"]["steps"] == ["step1", "step2"]
