"""Tests for CEO conversation session and message persistence."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pathlib import Path

from onemancompany.core.ceo_conversation import (
    CEO_SENDER,
    EA_SENDER,
    EA_AUTO_REPLY_DELAY_SECONDS,
    ConversationSession,
    append_message,
    load_messages,
    COMPLETE_SIGNAL,
    get_session,
    register_session,
    unregister_session,
)


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


class TestEaAutoReply:
    """Test EA auto-reply timer lifecycle."""

    def _make_session(self, tmp_path, node_id="ea_test"):
        return ConversationSession(
            node_id=node_id,
            employee_id="emp001",
            project_dir=str(tmp_path),
            broadcast_fn=AsyncMock(),
        )

    @pytest.mark.asyncio
    async def test_ceo_reply_cancels_timer(self, tmp_path):
        """When CEO replies, the EA timer is cancelled."""
        session = self._make_session(tmp_path)
        session.set_ea_auto_reply(True, "test request")

        assert session._ea_timer_task is not None
        assert not session._ea_timer_task.done()

        await session.send("I approve this")

        assert session._ceo_replied is True
        # Timer should be cancelled
        assert session._ea_timer_task is None or session._ea_timer_task.cancelled()

    @pytest.mark.asyncio
    async def test_disable_ea_cancels_timer(self, tmp_path):
        """Disabling EA auto-reply cancels the timer."""
        session = self._make_session(tmp_path)
        session.set_ea_auto_reply(True, "test request")

        assert session._ea_timer_task is not None

        session.set_ea_auto_reply(False)

        assert session._ea_timer_task is None or session._ea_timer_task.cancelled()

    @pytest.mark.asyncio
    async def test_complete_cancels_timer(self, tmp_path):
        """Completing the session cancels the EA timer."""
        session = self._make_session(tmp_path)
        session.set_ea_auto_reply(True, "test request")

        assert session._ea_timer_task is not None

        await session.complete()

        assert session._ea_timer_task is None or session._ea_timer_task.cancelled()

    @pytest.mark.asyncio
    async def test_ea_timer_fires_and_broadcasts(self, tmp_path):
        """When the timer fires, EA reply is appended and broadcast."""
        session = self._make_session(tmp_path, "ea_fire_test")

        mock_ea_reply = AsyncMock(return_value="[EA Auto-Reply] Decision: ACCEPT\nLooks good")
        mock_ainvoke = AsyncMock(return_value="Summary done.")

        with patch("onemancompany.core.ceo_conversation._ea_auto_reply", mock_ea_reply), \
             patch("onemancompany.core.ceo_conversation._build_agent_and_invoke", mock_ainvoke), \
             patch("onemancompany.core.ceo_conversation.EA_AUTO_REPLY_DELAY_SECONDS", 0.05):
            session._ea_auto_reply_enabled = True
            session._description = "test request"
            session._ceo_replied = False
            session._start_ea_timer()

            loop_task = asyncio.create_task(session.run())
            # Wait for timer to fire + auto-complete
            await asyncio.sleep(0.3)
            # Ensure session completes
            if not loop_task.done():
                await session.complete()
            await asyncio.wait_for(loop_task, timeout=2.0)

        # EA reply should be persisted to disk with sender=ceo (EA acts on behalf)
        msgs = load_messages(tmp_path / "conversations", "ea_fire_test")
        ea_msgs = [m for m in msgs if "EA Auto-Reply" in m.get("text", "")]
        assert len(ea_msgs) >= 1
        assert ea_msgs[0]["sender"] == CEO_SENDER

        # Broadcast should have been called with origin=ea
        broadcast_calls = session._broadcast.call_args_list
        ea_broadcasts = [c for c in broadcast_calls
                         if c.args and c.args[0].get("origin") == EA_SENDER]
        assert len(ea_broadcasts) >= 1

    @pytest.mark.asyncio
    async def test_ea_reply_skipped_if_ceo_replied(self, tmp_path):
        """If CEO replies before timer fires, EA auto-reply is skipped."""
        session = self._make_session(tmp_path, "ea_skip_test")

        with patch("onemancompany.core.ceo_conversation.EA_AUTO_REPLY_DELAY_SECONDS", 0.1):
            session._ea_auto_reply_enabled = True
            session._description = "test request"
            session._ceo_replied = False
            session._start_ea_timer()

            # CEO replies before timer
            session._ceo_replied = True
            session._cancel_ea_timer()

            await asyncio.sleep(0.2)

        # No EA message should be on disk
        msgs = load_messages(tmp_path / "conversations", "ea_skip_test")
        ea_msgs = [m for m in msgs if "EA Auto-Reply" in m.get("text", "")]
        assert len(ea_msgs) == 0
