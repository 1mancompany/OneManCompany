"""Tests for CEO conversation session and message persistence."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestConversationSession:
    """Test the async conversation loop."""

    @pytest.mark.asyncio
    async def test_session_processes_message_and_responds(self, tmp_path):
        from onemancompany.core.ceo_conversation import (
            ConversationSession, load_messages, COMPLETE_SIGNAL,
        )

        mock_broadcast = AsyncMock()
        mock_ainvoke = AsyncMock(return_value="I'll look into that.")

        session = ConversationSession(
            node_id="n1",
            employee_id="emp001",
            project_dir=str(tmp_path),
            broadcast_fn=mock_broadcast,
        )

        with patch("onemancompany.core.ceo_conversation._build_agent_and_invoke",
                    mock_ainvoke):
            loop_task = asyncio.create_task(session.run())
            await session.send("What's the status?")
            await asyncio.sleep(0.1)
            await session.complete()
            await asyncio.wait_for(loop_task, timeout=2.0)

        msgs = load_messages(tmp_path / "conversations", "n1")
        assert len(msgs) >= 2
        assert msgs[0]["sender"] == "ceo"
        assert msgs[0]["text"] == "What's the status?"
        mock_broadcast.assert_called()

    @pytest.mark.asyncio
    async def test_session_complete_signal_terminates_loop(self, tmp_path):
        from onemancompany.core.ceo_conversation import ConversationSession

        mock_broadcast = AsyncMock()
        session = ConversationSession(
            node_id="n2",
            employee_id="emp001",
            project_dir=str(tmp_path),
            broadcast_fn=mock_broadcast,
        )

        mock_ainvoke = AsyncMock(return_value="Summary: all done.")

        with patch("onemancompany.core.ceo_conversation._build_agent_and_invoke",
                    mock_ainvoke):
            loop_task = asyncio.create_task(session.run())
            await session.complete()
            result = await asyncio.wait_for(loop_task, timeout=2.0)

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_session_registry(self, tmp_path):
        from onemancompany.core.ceo_conversation import (
            ConversationSession, get_session, register_session, unregister_session,
        )

        session = ConversationSession(
            node_id="n3",
            employee_id="emp001",
            project_dir=str(tmp_path),
            broadcast_fn=AsyncMock(),
        )

        assert get_session("n3") is None
        register_session(session)
        assert get_session("n3") is session
        unregister_session("n3")
        assert get_session("n3") is None
