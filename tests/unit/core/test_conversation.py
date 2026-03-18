"""Tests for conversation data models and disk persistence."""

import pytest
import yaml

from onemancompany.core.conversation import Conversation, Message


# ---------------------------------------------------------------------------
# Data model tests
# ---------------------------------------------------------------------------

def test_conversation_to_dict():
    conv = Conversation(
        id="test-uuid",
        type="ceo_inbox",
        phase="created",
        employee_id="00100",
        tools_enabled=False,
        metadata={"node_id": "node-1"},
        created_at="2026-03-18T10:00:00",
        closed_at=None,
    )
    d = conv.to_dict()
    assert d["id"] == "test-uuid"
    assert d["type"] == "ceo_inbox"
    assert d["phase"] == "created"
    assert d["tools_enabled"] is False


def test_conversation_from_dict():
    d = {
        "id": "test-uuid", "type": "oneonone", "phase": "active",
        "employee_id": "00100", "tools_enabled": True,
        "metadata": {}, "created_at": "2026-03-18T10:00:00", "closed_at": None,
    }
    conv = Conversation.from_dict(d)
    assert conv.type == "oneonone"
    assert conv.tools_enabled is True


def test_message_to_dict():
    msg = Message(sender="ceo", role="CEO", text="hello", timestamp="2026-03-18T10:00:00", attachments=[])
    d = msg.to_dict()
    assert d["sender"] == "ceo"
    assert d["text"] == "hello"


def test_message_from_dict():
    d = {"sender": "00100", "role": "Alice", "text": "hi", "timestamp": "2026-03-18T10:00:00", "attachments": ["/tmp/f.txt"]}
    msg = Message.from_dict(d)
    assert msg.attachments == ["/tmp/f.txt"]


# ---------------------------------------------------------------------------
# Disk persistence tests
# ---------------------------------------------------------------------------

from onemancompany.core.conversation import (
    save_conversation_meta, load_conversation_meta,
    append_message, load_messages,
    _resolve_conv_dir,
)


def test_save_and_load_meta(tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")

    conv = Conversation(
        id="conv-001", type="ceo_inbox", phase="active",
        employee_id="00100", tools_enabled=False,
        metadata={"node_id": "n1", "project_dir": str(tmp_path / "projects" / "proj1")},
        created_at="2026-03-18T10:00:00",
    )
    save_conversation_meta(conv)
    loaded = load_conversation_meta("conv-001", conv_dir=_resolve_conv_dir(conv))
    assert loaded.id == "conv-001"
    assert loaded.phase == "active"


@pytest.mark.asyncio
async def test_append_and_load_messages(tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")

    conv = Conversation(
        id="conv-002", type="oneonone", phase="active",
        employee_id="00100", tools_enabled=True,
        metadata={},
        created_at="2026-03-18T10:00:00",
    )
    conv_dir = tmp_path / "employees" / "00100" / "conversations" / "conv-002"
    save_conversation_meta(conv)

    msg1 = Message(sender="ceo", role="CEO", text="hello", timestamp="2026-03-18T10:00:01")
    msg2 = Message(sender="00100", role="Alice", text="hi back", timestamp="2026-03-18T10:00:02")
    await append_message(conv_dir, msg1)
    await append_message(conv_dir, msg2)

    messages = load_messages(conv_dir)
    assert len(messages) == 2
    assert messages[0].sender == "ceo"
    assert messages[1].text == "hi back"


# ---------------------------------------------------------------------------
# ConversationService tests
# ---------------------------------------------------------------------------

from onemancompany.core.conversation import ConversationService


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    return ConversationService()


@pytest.mark.asyncio
async def test_create_conversation(svc, tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    conv = await svc.create(
        type="oneonone", employee_id="00100", tools_enabled=True,
    )
    assert conv.phase == "active"
    assert conv.type == "oneonone"
    assert conv.employee_id == "00100"
    # Verify persisted to disk
    loaded = svc.get(conv.id)
    assert loaded.id == conv.id


@pytest.mark.asyncio
async def test_close_conversation(svc, tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    conv = await svc.create(type="oneonone", employee_id="00100", tools_enabled=True)
    result = await svc.close(conv.id, wait_hooks=False)
    closed = svc.get(conv.id)
    assert closed.phase == "closed"


@pytest.mark.asyncio
async def test_list_active(svc, tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    c1 = await svc.create(type="oneonone", employee_id="00100", tools_enabled=True)
    c2 = await svc.create(type="oneonone", employee_id="00101", tools_enabled=True)
    await svc.close(c2.id)
    active = svc.list_active()
    assert len(active) == 1
    assert active[0].id == c1.id


@pytest.mark.asyncio
async def test_list_active_filter_by_type(svc, tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    c1 = await svc.create(type="oneonone", employee_id="00100", tools_enabled=True)
    c2 = await svc.create(
        type="ceo_inbox", employee_id="00100", tools_enabled=False,
        project_dir=str(tmp_path / "projects" / "p1"), node_id="n1",
    )
    active = svc.list_active(type="ceo_inbox")
    assert len(active) == 1
    assert active[0].type == "ceo_inbox"


@pytest.mark.asyncio
async def test_send_message_publishes_event(svc, tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    conv = await svc.create(type="oneonone", employee_id="00100", tools_enabled=True)

    published_events = []
    async def mock_publish(event):
        published_events.append(event)

    monkeypatch.setattr("onemancompany.core.conversation.event_bus.publish", mock_publish)

    msg = await svc.send_message(conv.id, sender="ceo", role="CEO", text="hi")
    assert len(published_events) == 1
    assert published_events[0].type == "conversation_message"
    assert published_events[0].payload["conv_id"] == conv.id
    assert published_events[0].payload["text"] == "hi"


@pytest.mark.asyncio
async def test_close_publishes_phase_event(svc, tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    conv = await svc.create(type="oneonone", employee_id="00100", tools_enabled=True)

    published_events = []
    async def mock_publish(event):
        published_events.append(event)

    monkeypatch.setattr("onemancompany.core.conversation.event_bus.publish", mock_publish)

    await svc.close(conv.id)
    phase_events = [e for e in published_events if e.type == "conversation_phase"]
    assert len(phase_events) >= 1
    assert phase_events[-1].payload["phase"] == "closed"


@pytest.mark.asyncio
async def test_create_publishes_phase_event(svc, tmp_path, monkeypatch):
    published_events = []
    async def mock_publish(event):
        published_events.append(event)

    monkeypatch.setattr("onemancompany.core.conversation.event_bus.publish", mock_publish)
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")

    conv = await svc.create(type="oneonone", employee_id="00100", tools_enabled=True)
    phase_events = [e for e in published_events if e.type == "conversation_phase"]
    assert len(phase_events) == 1
    assert phase_events[0].payload["phase"] == "active"
    assert phase_events[0].payload["conv_id"] == conv.id
