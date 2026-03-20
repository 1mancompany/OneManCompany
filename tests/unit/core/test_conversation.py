"""Tests for conversation data models and disk persistence."""

import pytest

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
    resolve_conv_dir,
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
    loaded = load_conversation_meta("conv-001", conv_dir=resolve_conv_dir(conv))
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
    closed_conv, hook_result = await svc.close(conv.id, wait_hooks=False)
    assert closed_conv.phase == "closed"


@pytest.mark.asyncio
async def test_list_active(svc, tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    c1 = await svc.create(type="oneonone", employee_id="00100", tools_enabled=True)
    c2 = await svc.create(type="oneonone", employee_id="00101", tools_enabled=True)
    await svc.close(c2.id)  # returns (conv, hook_result)
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


@pytest.mark.asyncio
async def test_close_removes_from_index(svc, tmp_path, monkeypatch):
    """Closed conversations should be removed from the in-memory index."""
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    conv = await svc.create(type="oneonone", employee_id="00100", tools_enabled=True)
    assert conv.id in svc._index
    await svc.close(conv.id)
    assert conv.id not in svc._index


@pytest.mark.asyncio
async def test_rebuild_index(tmp_path, monkeypatch):
    """rebuild_index should discover conversations from disk."""
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    svc1 = ConversationService()
    conv = await svc1.create(type="oneonone", employee_id="00100", tools_enabled=True)

    # New service instance — index is empty
    svc2 = ConversationService()
    assert len(svc2._index) == 0

    svc2.rebuild_index()
    assert conv.id in svc2._index
    loaded = svc2.get(conv.id)
    assert loaded.employee_id == "00100"


@pytest.mark.asyncio
async def test_recover_closing_conversations(tmp_path, monkeypatch):
    """recover() should finalize conversations stuck in 'closing' phase."""
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")

    from onemancompany.core.conversation import save_conversation_meta, Conversation, resolve_conv_dir
    # Manually create a conversation stuck in "closing"
    conv = Conversation(
        id="stuck-conv", type="oneonone", phase="closing",
        employee_id="00100", tools_enabled=True,
        metadata={}, created_at="2026-03-18T10:00:00",
    )
    save_conversation_meta(conv)

    svc = ConversationService()
    svc.rebuild_index()
    assert "stuck-conv" in svc._index

    recovered = await svc.recover()
    assert recovered == 1
    assert "stuck-conv" not in svc._index
