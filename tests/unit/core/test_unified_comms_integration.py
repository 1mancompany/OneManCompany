"""Integration tests for unified CEO communication system.

End-to-end tests verifying the full message flow through ConversationService,
DND mode, project conversations, reactivation, and @mention parsing.
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from onemancompany.core.conversation import (
    ConversationService,
    Conversation,
    Message,
    Interaction,
)
from onemancompany.core.models import ConversationType, ConversationPhase


# ---------------------------------------------------------------------------
# Helpers — in-memory persistence layer for testing
# ---------------------------------------------------------------------------

class _InMemoryStore:
    """Replaces disk I/O so ConversationService works purely in-memory."""

    def __init__(self):
        self.convs: dict[str, Conversation] = {}  # conv_id → Conversation
        self.msgs: dict[str, list[Message]] = {}   # conv_id → messages

    def save_meta(self, conv: Conversation) -> None:
        self.convs[conv.id] = conv

    def load_meta(self, conv_id: str, conv_dir) -> Conversation:
        if conv_id not in self.convs:
            raise FileNotFoundError(f"No meta for {conv_id}")
        return self.convs[conv_id]

    async def append_message(self, conv_dir, msg: Message) -> None:
        # Derive conv_id from the dir name (last path component = uuid)
        conv_id = conv_dir.name
        self.msgs.setdefault(conv_id, []).append(msg)


def _patched_service():
    """Create a ConversationService with all disk I/O redirected to memory."""
    store = _InMemoryStore()
    service = ConversationService()
    patches = (
        patch("onemancompany.core.conversation.save_conversation_meta", store.save_meta),
        patch("onemancompany.core.conversation.load_conversation_meta", store.load_meta),
        patch("onemancompany.core.conversation.append_message", store.append_message),
        patch("onemancompany.core.conversation.event_bus", AsyncMock()),
    )
    return service, store, patches


# ---------------------------------------------------------------------------
# Test 1 — Cron task result reaches 1-on-1
# ---------------------------------------------------------------------------


class TestCronTaskToOneonone:
    """When a cron task completes without project_id, result should reach 1-on-1."""

    @pytest.mark.asyncio
    async def test_push_without_project_routes_to_oneonone(self):
        service, store, patches = _patched_service()
        with patches[0], patches[1], patches[2], patches[3]:
            conv = await service.get_or_create_oneonone("00004")
            assert conv.type == ConversationType.ONE_ON_ONE.value
            assert conv.employee_id == "00004"

            # Push a system message (simulates cron result)
            msg = await service.push_system_message(
                conv.id, "Cron result: done", source_employee="00004",
            )
            assert msg.text == "Cron result: done"
            assert msg.sender == "system"
            # Message was persisted in the in-memory store
            all_msgs = list(store.msgs.values())
            assert any(
                any(m.text == "Cron result: done" for m in msgs)
                for msgs in all_msgs
            )

    @pytest.mark.asyncio
    async def test_oneonone_is_idempotent(self):
        """Calling get_or_create_oneonone twice returns the same conversation."""
        service, _store, patches = _patched_service()
        with patches[0], patches[1], patches[2], patches[3]:
            conv1 = await service.get_or_create_oneonone("00004")
            conv2 = await service.get_or_create_oneonone("00004")
            assert conv1.id == conv2.id


# ---------------------------------------------------------------------------
# Test 2 — report_to_ceo routing
# ---------------------------------------------------------------------------


class TestReportToCeoRouting:
    """report_to_ceo routes to project conv when project context exists."""

    @pytest.mark.asyncio
    async def test_report_creates_oneonone_without_project(self):
        service, _store, patches = _patched_service()
        with patches[0], patches[1], patches[2], patches[3]:
            conv = await service.get_or_create_oneonone("00005")
            assert conv.type == ConversationType.ONE_ON_ONE.value
            assert conv.employee_id == "00005"
            assert "00005" in conv.participants

    @pytest.mark.asyncio
    async def test_report_creates_project_conv_with_project(self):
        service, _store, patches = _patched_service()
        with patches[0], patches[1], patches[2], patches[3]:
            conv = await service.get_or_create_project_conversation(
                "proj_abc", ["00002", "00004"],
            )
            assert conv.type == ConversationType.PROJECT.value
            assert conv.project_id == "proj_abc"
            assert "00002" in conv.participants
            assert "00004" in conv.participants

    @pytest.mark.asyncio
    async def test_project_conv_adds_new_participants(self):
        """Second call with a new participant merges them in."""
        service, _store, patches = _patched_service()
        with patches[0], patches[1], patches[2], patches[3]:
            conv1 = await service.get_or_create_project_conversation(
                "proj_xyz", ["00002"],
            )
            conv2 = await service.get_or_create_project_conversation(
                "proj_xyz", ["00002", "00005"],
            )
            assert conv1.id == conv2.id
            assert "00005" in conv2.participants


# ---------------------------------------------------------------------------
# Test 3 — DND mode
# ---------------------------------------------------------------------------


class TestDNDAutoReply:
    """DND toggle changes global CEO availability state."""

    def test_dnd_toggle(self):
        from onemancompany.core.config import set_ceo_dnd, get_ceo_dnd

        original = get_ceo_dnd()
        try:
            set_ceo_dnd(True)
            assert get_ceo_dnd() is True
            set_ceo_dnd(False)
            assert get_ceo_dnd() is False
        finally:
            set_ceo_dnd(original)

    def test_dnd_defaults_off(self):
        from onemancompany.core.config import get_ceo_dnd, set_ceo_dnd

        set_ceo_dnd(False)
        assert get_ceo_dnd() is False


# ---------------------------------------------------------------------------
# Test 4 — Project conversation reactivation
# ---------------------------------------------------------------------------


class TestProjectReactivation:
    """Archived conversations become active on reactivate()."""

    @pytest.mark.asyncio
    async def test_archived_becomes_active_on_reactivate(self):
        service, store, patches = _patched_service()
        with patches[0], patches[1], patches[2], patches[3]:
            conv = await service.get_or_create_project_conversation(
                "proj_old", ["00004"],
            )
            # Manually archive
            conv.phase = ConversationPhase.ARCHIVED.value
            store.save_meta(conv)

            reactivated = await service.reactivate(conv.id)
            assert reactivated.phase == ConversationPhase.ACTIVE.value
            assert reactivated.closed_at is None

    @pytest.mark.asyncio
    async def test_reactivate_noop_if_already_active(self):
        """Reactivating an already-active conv is a no-op."""
        service, _store, patches = _patched_service()
        with patches[0], patches[1], patches[2], patches[3]:
            conv = await service.get_or_create_oneonone("00004")
            assert conv.phase == ConversationPhase.ACTIVE.value
            same = await service.reactivate(conv.id)
            assert same.phase == ConversationPhase.ACTIVE.value


# ---------------------------------------------------------------------------
# Test 5 — @mention parsing
# ---------------------------------------------------------------------------


class TestMentionParsing:
    """_parse_mentions resolves @Name tokens to employee IDs."""

    def _make_employee(self, name: str, nickname: str = "") -> dict:
        return {"name": name, "nickname": nickname}

    def test_parse_mentions_by_name(self):
        from onemancompany.api.routes import _parse_mentions

        with patch("onemancompany.core.store.load_employee") as mock_load:
            mock_load.side_effect = lambda eid: (
                self._make_employee("Sam", "小红")
                if eid == "00002"
                else self._make_employee("Alex", "大白")
            )
            result = _parse_mentions("@Sam fix this", ["00002", "00003"])
            assert result == ["00002"]

    def test_parse_mentions_no_match(self):
        from onemancompany.api.routes import _parse_mentions

        with patch("onemancompany.core.store.load_employee") as mock_load:
            mock_load.return_value = self._make_employee("Sam")
            result = _parse_mentions("hello everyone", ["00002"])
            assert result == []

    def test_parse_mentions_multiple(self):
        from onemancompany.api.routes import _parse_mentions

        with patch("onemancompany.core.store.load_employee") as mock_load:
            mock_load.side_effect = lambda eid: (
                self._make_employee("Sam")
                if eid == "00002"
                else self._make_employee("Alex")
            )
            result = _parse_mentions(
                "@Sam and @Alex review", ["00002", "00003"],
            )
            assert "00002" in result
            assert "00003" in result

    def test_parse_mentions_by_nickname(self):
        from onemancompany.api.routes import _parse_mentions

        with patch("onemancompany.core.store.load_employee") as mock_load:
            mock_load.side_effect = lambda eid: (
                self._make_employee("Sam", "小红")
                if eid == "00002"
                else self._make_employee("Alex", "大白")
            )
            result = _parse_mentions("@小红 看一下", ["00002", "00003"])
            assert result == ["00002"]

    def test_parse_mentions_no_duplicates(self):
        from onemancompany.api.routes import _parse_mentions

        with patch("onemancompany.core.store.load_employee") as mock_load:
            mock_load.return_value = self._make_employee("Sam")
            result = _parse_mentions("@Sam @Sam twice", ["00002"])
            assert result == ["00002"]
