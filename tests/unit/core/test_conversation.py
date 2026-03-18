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
