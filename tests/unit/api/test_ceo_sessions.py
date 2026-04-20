"""Tests for unified CEO session API endpoints (ConversationService-backed)."""
from __future__ import annotations

import asyncio
from collections import deque
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from onemancompany.core.conversation import Conversation, Message, Interaction
from onemancompany.core.models import ConversationType, ConversationPhase


def _mock_service():
    """Create a mock ConversationService."""
    svc = MagicMock()
    svc.list_by_phase = MagicMock(return_value=[])
    svc.get_messages = MagicMock(return_value=[])
    svc.get_pending_count = MagicMock(return_value=0)
    svc.send_message = AsyncMock()
    svc.resolve_interaction = AsyncMock(return_value={"type": "followup", "text": ""})
    svc.reactivate = AsyncMock()
    return svc


def _make_conv(conv_id: str, project_id: str, phase: str = "active") -> Conversation:
    return Conversation(
        id=conv_id, type=ConversationType.PROJECT.value,
        phase=phase, employee_id="00003",
        tools_enabled=False, project_id=project_id,
        participants=["00003"], created_at="2026-01-01T00:00:00Z",
    )


class TestListSessions:
    @pytest.mark.asyncio
    async def test_returns_empty_list(self):
        from onemancompany.api.routes import list_ceo_sessions

        svc = _mock_service()
        with patch("onemancompany.core.conversation.get_conversation_service", return_value=svc):
            result = await list_ceo_sessions()
            assert result == {"sessions": []}

    @pytest.mark.asyncio
    async def test_returns_sessions_sorted(self):
        from onemancompany.api.routes import list_ceo_sessions

        svc = _mock_service()
        conv_a = _make_conv("conv_a", "proj_a")
        conv_b = _make_conv("conv_b", "proj_b")
        svc.list_by_phase.return_value = [conv_a, conv_b]

        def _pending_count(conv_id):
            return 1 if conv_id == "conv_b" else 0
        svc.get_pending_count.side_effect = _pending_count

        def _messages(conv_id):
            return [Message(sender="x", role="system", text="hi", timestamp="t")]
        svc.get_messages.side_effect = _messages

        with patch("onemancompany.core.conversation.get_conversation_service", return_value=svc), \
             patch("onemancompany.core.project_archive.load_named_project",
                   side_effect=lambda pid: {"status": "active"} if pid in ("proj_a", "proj_b") else None):
            result = await list_ceo_sessions()
            assert len(result["sessions"]) == 2
            assert result["sessions"][0]["project_id"] == "proj_b"
            assert result["sessions"][0]["pending_count"] == 1


class TestGetSession:
    @pytest.mark.asyncio
    async def test_returns_session_history(self):
        from onemancompany.api.routes import get_ceo_session

        svc = _mock_service()
        conv = _make_conv("conv_001", "proj_001")
        svc.list_by_phase.return_value = [conv]
        svc.get_messages.return_value = [
            Message(sender="00003", role="system", text="Hello", timestamp="2026-01-01T00:00:01Z"),
        ]

        with patch("onemancompany.core.conversation.get_conversation_service", return_value=svc):
            result = await get_ceo_session("proj_001")
            assert len(result["history"]) == 1
            assert result["history"][0]["text"] == "Hello"
            assert result["conv_id"] == "conv_001"

    @pytest.mark.asyncio
    async def test_returns_empty_for_unknown_session(self):
        from onemancompany.api.routes import get_ceo_session

        svc = _mock_service()
        with patch("onemancompany.core.conversation.get_conversation_service", return_value=svc):
            result = await get_ceo_session("nonexistent")
            assert result == {"history": [], "pending_count": 0}


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_resolves_pending_interaction(self):
        from onemancompany.api.routes import send_ceo_session_message

        svc = _mock_service()
        conv = _make_conv("conv_001", "proj_001")
        svc.list_by_phase.return_value = [conv]
        svc.resolve_interaction.return_value = {"type": "resolved", "node_id": "abc"}

        with patch("onemancompany.core.conversation.get_conversation_service", return_value=svc):
            result = await send_ceo_session_message("proj_001", {"text": "Yes"})
            assert result["type"] == "resolved"
            assert result["node_id"] == "abc"
            svc.send_message.assert_awaited_once()
            # Verify mentions=[] was passed (no @mentions in "Yes")
            call_kwargs = svc.send_message.call_args
            assert call_kwargs.kwargs.get("mentions") == []

    @pytest.mark.asyncio
    async def test_empty_message_returns_400(self):
        from onemancompany.api.routes import send_ceo_session_message
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await send_ceo_session_message("proj_001", {"text": ""})
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_session_not_found_returns_404(self):
        from onemancompany.api.routes import send_ceo_session_message
        from fastapi import HTTPException

        svc = _mock_service()
        svc.list_by_phase.return_value = []  # no matching session
        with patch("onemancompany.core.conversation.get_conversation_service", return_value=svc):
            with pytest.raises(HTTPException) as exc_info:
                await send_ceo_session_message("nonexistent", {"text": "hello"})
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_followup_dispatches_task(self):
        from onemancompany.api.routes import send_ceo_session_message

        svc = _mock_service()
        conv = _make_conv("conv_001", "proj_001")
        svc.list_by_phase.return_value = [conv]
        svc.resolve_interaction.return_value = {"type": "followup", "text": "Do X"}

        with patch("onemancompany.core.conversation.get_conversation_service", return_value=svc), \
             patch("onemancompany.api.routes.task_followup", new_callable=AsyncMock) as mock_followup:
            mock_followup.return_value = {"status": "dispatched"}
            result = await send_ceo_session_message("proj_001", {"text": "Do X"})
            assert result["type"] == "followup"
            assert result["message"] == "Follow-up instruction dispatched"
            mock_followup.assert_awaited_once_with("proj_001", {"instructions": "Do X"})


class TestParseMentions:
    def test_parses_known_participant(self):
        from onemancompany.api.routes import _parse_mentions

        emp = {"name": "Alice", "nickname": "小爱"}
        with patch("onemancompany.core.store.load_employee", return_value=emp):
            result = _parse_mentions("Hey @Alice please check", ["00010"])
            assert result == ["00010"]

    def test_no_match_returns_empty(self):
        from onemancompany.api.routes import _parse_mentions

        emp = {"name": "Bob", "nickname": "小博"}
        with patch("onemancompany.core.store.load_employee", return_value=emp):
            result = _parse_mentions("Hey @unknown please check", ["00010"])
            assert result == []

    def test_nickname_match(self):
        from onemancompany.api.routes import _parse_mentions

        emp = {"name": "Alice", "nickname": "小爱"}
        with patch("onemancompany.core.store.load_employee", return_value=emp):
            result = _parse_mentions("Hey @小爱 请检查", ["00010"])
            assert result == ["00010"]

    def test_no_duplicates(self):
        from onemancompany.api.routes import _parse_mentions

        emp = {"name": "Alice", "nickname": "alice"}
        with patch("onemancompany.core.store.load_employee", return_value=emp):
            result = _parse_mentions("@Alice @alice hi", ["00010"])
            assert result == ["00010"]

    def test_no_mentions_returns_empty(self):
        from onemancompany.api.routes import _parse_mentions

        result = _parse_mentions("No mentions here", ["00010"])
        assert result == []
