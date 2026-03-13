"""Tests for CEO conversation session and message persistence."""
import pytest
from pathlib import Path


class TestMessagePersistence:
    """Test YAML-based conversation message storage."""

    def test_append_and_load_messages(self, tmp_path):
        from onemancompany.core.ceo_conversation import append_message, load_messages

        conv_dir = tmp_path / "conversations"
        node_id = "abc123"

        append_message(conv_dir, node_id, sender="ceo", text="Hello")
        append_message(conv_dir, node_id, sender="emp001", text="Hi CEO")

        msgs = load_messages(conv_dir, node_id)
        assert len(msgs) == 2
        assert msgs[0]["sender"] == "ceo"
        assert msgs[0]["text"] == "Hello"
        assert msgs[1]["sender"] == "emp001"
        assert "timestamp" in msgs[0]

    def test_load_empty_returns_empty_list(self, tmp_path):
        from onemancompany.core.ceo_conversation import load_messages

        msgs = load_messages(tmp_path / "conversations", "nonexistent")
        assert msgs == []

    def test_append_with_attachments(self, tmp_path):
        from onemancompany.core.ceo_conversation import append_message, load_messages

        conv_dir = tmp_path / "conversations"
        append_message(conv_dir, "n1", sender="ceo", text="See attached",
                       attachments=[{"filename": "doc.pdf", "path": "/workspace/doc.pdf"}])

        msgs = load_messages(conv_dir, "n1")
        assert msgs[0]["attachments"][0]["filename"] == "doc.pdf"
