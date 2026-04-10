"""Tests for unified CEO communication system."""
import asyncio

import pytest

from onemancompany.core.models import ConversationType, ConversationPhase


class TestConversationEnums:
    def test_project_type_exists(self):
        assert ConversationType.PROJECT == "project"

    def test_archived_phase_exists(self):
        assert ConversationPhase.ARCHIVED == "archived"

    def test_backward_compat_types(self):
        assert ConversationType.ONE_ON_ONE == "oneonone"
        assert ConversationType.EA_CHAT == "ea_chat"
        assert ConversationType.CEO_INBOX == "ceo_inbox"

    def test_backward_compat_phases(self):
        assert ConversationPhase.ACTIVE == "active"
        assert ConversationPhase.CLOSING == "closing"
        assert ConversationPhase.CLOSED == "closed"


from onemancompany.core.conversation import Conversation, Message, Interaction


class TestConversationModel:
    def test_participants_field(self):
        conv = Conversation(
            id="test", type="project", phase="active",
            employee_id="00004", tools_enabled=False,
            participants=["00002", "00004", "00005"],
        )
        assert conv.participants == ["00002", "00004", "00005"]

    def test_participants_default_empty(self):
        conv = Conversation(
            id="test", type="oneonone", phase="active",
            employee_id="00004", tools_enabled=False,
        )
        assert conv.participants == []

    def test_project_id_field(self):
        conv = Conversation(
            id="test", type="project", phase="active",
            employee_id="00004", tools_enabled=False,
            project_id="proj_abc",
        )
        assert conv.project_id == "proj_abc"

    def test_project_id_default_none(self):
        conv = Conversation(
            id="test", type="oneonone", phase="active",
            employee_id="00004", tools_enabled=False,
        )
        assert conv.project_id is None

    def test_message_mentions_field(self):
        msg = Message(sender="ceo", role="CEO", text="@Sam fix this",
                      mentions=["00002"])
        assert msg.mentions == ["00002"]

    def test_message_mentions_default_empty(self):
        msg = Message(sender="ceo", role="CEO", text="hello")
        assert msg.mentions == []


# ---------------------------------------------------------------------------
# Pending queue tests
# ---------------------------------------------------------------------------


class TestInteractionDataclass:
    def test_interaction_fields(self):
        loop = asyncio.new_event_loop()
        future = loop.create_future()
        interaction = Interaction(
            node_id="n1", tree_path="/path", project_id="proj",
            source_employee="00004", interaction_type="ceo_request",
            message="Need approval", future=future,
        )
        assert interaction.node_id == "n1"
        assert interaction.interaction_type == "ceo_request"
        assert interaction.message == "Need approval"
        assert not interaction.future.done()
        loop.close()

    def test_interaction_created_at_auto_set(self):
        loop = asyncio.new_event_loop()
        future = loop.create_future()
        interaction = Interaction(
            node_id="n1", tree_path="/p", project_id="proj",
            source_employee="00004", interaction_type="ceo_request",
            message="msg", future=future,
        )
        # __post_init__ sets created_at if empty
        assert interaction.created_at != ""
        loop.close()

    def test_interaction_created_at_explicit(self):
        loop = asyncio.new_event_loop()
        future = loop.create_future()
        interaction = Interaction(
            node_id="n1", tree_path="/p", project_id="proj",
            source_employee="00004", interaction_type="ceo_request",
            message="msg", future=future, created_at="2026-01-01T00:00:00",
        )
        assert interaction.created_at == "2026-01-01T00:00:00"
        loop.close()


from onemancompany.core.conversation import ConversationService


@pytest.fixture
def pending_svc(tmp_path, monkeypatch):
    monkeypatch.setattr("onemancompany.core.conversation.PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
    return ConversationService()


class TestPendingQueue:
    @pytest.mark.asyncio
    async def test_enqueue_and_resolve(self, pending_svc, tmp_path, monkeypatch):
        monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
        conv = await pending_svc.create(type="oneonone", employee_id="00100", tools_enabled=False)

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        interaction = Interaction(
            node_id="n1", tree_path="/p", project_id="proj",
            source_employee="00100", interaction_type="ceo_request",
            message="Approve this?", future=future,
        )
        await pending_svc.enqueue_interaction(conv.id, interaction)
        assert pending_svc.get_pending_count(conv.id) == 1

        result = await pending_svc.resolve_interaction(conv.id, "Yes, approved")
        assert result["type"] == "resolved"
        assert result["node_id"] == "n1"
        assert future.done()
        assert future.result() == "Yes, approved"
        assert pending_svc.get_pending_count(conv.id) == 0

    @pytest.mark.asyncio
    async def test_resolve_no_pending_returns_followup(self, pending_svc, tmp_path, monkeypatch):
        monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
        conv = await pending_svc.create(type="oneonone", employee_id="00100", tools_enabled=False)

        result = await pending_svc.resolve_interaction(conv.id, "Hello")
        assert result["type"] == "followup"
        assert result["text"] == "Hello"

    @pytest.mark.asyncio
    async def test_fifo_order(self, pending_svc, tmp_path, monkeypatch):
        monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
        conv = await pending_svc.create(type="oneonone", employee_id="00100", tools_enabled=False)

        loop = asyncio.get_running_loop()
        f1 = loop.create_future()
        f2 = loop.create_future()
        i1 = Interaction(
            node_id="n1", tree_path="/p", project_id="proj",
            source_employee="00100", interaction_type="ceo_request",
            message="First", future=f1,
        )
        i2 = Interaction(
            node_id="n2", tree_path="/p", project_id="proj",
            source_employee="00100", interaction_type="ceo_request",
            message="Second", future=f2,
        )
        await pending_svc.enqueue_interaction(conv.id, i1)
        await pending_svc.enqueue_interaction(conv.id, i2)
        assert pending_svc.get_pending_count(conv.id) == 2

        result1 = await pending_svc.resolve_interaction(conv.id, "Reply 1")
        assert result1["node_id"] == "n1"
        assert f1.result() == "Reply 1"

        result2 = await pending_svc.resolve_interaction(conv.id, "Reply 2")
        assert result2["node_id"] == "n2"
        assert f2.result() == "Reply 2"

    @pytest.mark.asyncio
    async def test_get_pending_count_excludes_done_futures(self, pending_svc, tmp_path, monkeypatch):
        monkeypatch.setattr("onemancompany.core.conversation.EMPLOYEES_DIR", tmp_path / "employees")
        conv = await pending_svc.create(type="oneonone", employee_id="00100", tools_enabled=False)

        loop = asyncio.get_running_loop()
        f1 = loop.create_future()
        f2 = loop.create_future()
        i1 = Interaction(
            node_id="n1", tree_path="/p", project_id="proj",
            source_employee="00100", interaction_type="ceo_request",
            message="msg1", future=f1,
        )
        i2 = Interaction(
            node_id="n2", tree_path="/p", project_id="proj",
            source_employee="00100", interaction_type="ceo_request",
            message="msg2", future=f2,
        )
        await pending_svc.enqueue_interaction(conv.id, i1)
        await pending_svc.enqueue_interaction(conv.id, i2)

        # Manually resolve one future without going through resolve_interaction
        f1.set_result("done externally")
        assert pending_svc.get_pending_count(conv.id) == 1

    @pytest.mark.asyncio
    async def test_resolve_unknown_conv_returns_followup(self, pending_svc):
        result = await pending_svc.resolve_interaction("nonexistent", "Hello")
        assert result["type"] == "followup"


# ---------------------------------------------------------------------------
# Project conversation helpers
# ---------------------------------------------------------------------------


class TestProjectConversation:
    @pytest.mark.asyncio
    async def test_create_project_conversation(self, pending_svc):
        conv = await pending_svc.get_or_create_project_conversation(
            "proj_001", participants=["00002", "00004"],
        )
        assert conv.type == ConversationType.PROJECT.value
        assert conv.project_id == "proj_001"
        assert conv.participants == ["00002", "00004"]
        assert conv.employee_id == "00002"

    @pytest.mark.asyncio
    async def test_reuse_existing_project_conversation(self, pending_svc):
        conv1 = await pending_svc.get_or_create_project_conversation(
            "proj_001", participants=["00002"],
        )
        conv2 = await pending_svc.get_or_create_project_conversation(
            "proj_001", participants=["00002"],
        )
        assert conv1.id == conv2.id

    @pytest.mark.asyncio
    async def test_add_participants_to_existing(self, pending_svc):
        conv1 = await pending_svc.get_or_create_project_conversation(
            "proj_001", participants=["00002"],
        )
        conv2 = await pending_svc.get_or_create_project_conversation(
            "proj_001", participants=["00002", "00005"],
        )
        assert conv1.id == conv2.id
        # Re-read from disk to confirm persistence
        reloaded = pending_svc.get(conv1.id)
        assert "00005" in reloaded.participants

    @pytest.mark.asyncio
    async def test_different_projects_different_convs(self, pending_svc):
        conv1 = await pending_svc.get_or_create_project_conversation(
            "proj_001", participants=["00002"],
        )
        conv2 = await pending_svc.get_or_create_project_conversation(
            "proj_002", participants=["00002"],
        )
        assert conv1.id != conv2.id

    @pytest.mark.asyncio
    async def test_create_project_no_participants(self, pending_svc):
        conv = await pending_svc.get_or_create_project_conversation("proj_001")
        assert conv.participants == []
        assert conv.employee_id == ""


# ---------------------------------------------------------------------------
# 1-on-1 auto-create
# ---------------------------------------------------------------------------


class TestOneononeAutoCreate:
    @pytest.mark.asyncio
    async def test_create_oneonone(self, pending_svc):
        conv = await pending_svc.get_or_create_oneonone("00100")
        assert conv.type == ConversationType.ONE_ON_ONE.value
        assert conv.employee_id == "00100"
        assert conv.participants == ["00100"]

    @pytest.mark.asyncio
    async def test_reuse_existing_oneonone(self, pending_svc):
        conv1 = await pending_svc.get_or_create_oneonone("00100")
        conv2 = await pending_svc.get_or_create_oneonone("00100")
        assert conv1.id == conv2.id

    @pytest.mark.asyncio
    async def test_different_employees_different_convs(self, pending_svc):
        conv1 = await pending_svc.get_or_create_oneonone("00100")
        conv2 = await pending_svc.get_or_create_oneonone("00200")
        assert conv1.id != conv2.id


# ---------------------------------------------------------------------------
# Push system message
# ---------------------------------------------------------------------------


class TestPushSystemMessage:
    @pytest.mark.asyncio
    async def test_push_system_message(self, pending_svc):
        conv = await pending_svc.create(
            type="oneonone", employee_id="00100", participants=["00100"],
        )
        msg = await pending_svc.push_system_message(conv.id, "Task assigned", source_employee="00002")
        assert msg.sender == "system"
        assert msg.text == "Task assigned"
        # Verify message persisted
        messages = pending_svc.get_messages(conv.id)
        assert any(m.text == "Task assigned" for m in messages)

    @pytest.mark.asyncio
    async def test_push_system_message_default_source(self, pending_svc):
        conv = await pending_svc.create(
            type="oneonone", employee_id="00100", participants=["00100"],
        )
        msg = await pending_svc.push_system_message(conv.id, "Auto notification")
        assert msg.sender == "system"
        assert msg.role == "system"


# ---------------------------------------------------------------------------
# Reactivation
# ---------------------------------------------------------------------------


class TestReactivation:
    @pytest.mark.asyncio
    async def test_reactivate_archived(self, pending_svc):
        from onemancompany.core.conversation import save_conversation_meta
        conv = await pending_svc.create(
            type="oneonone", employee_id="00100", participants=["00100"],
        )
        # Manually archive
        conv.phase = ConversationPhase.ARCHIVED.value
        conv.closed_at = "2026-01-01T00:00:00"
        save_conversation_meta(conv)

        reactivated = await pending_svc.reactivate(conv.id)
        assert reactivated.phase == ConversationPhase.ACTIVE.value
        assert reactivated.closed_at is None
        # Verify persisted to disk
        reloaded = pending_svc.get(conv.id)
        assert reloaded.phase == ConversationPhase.ACTIVE.value

    @pytest.mark.asyncio
    async def test_reactivate_already_active_noop(self, pending_svc):
        conv = await pending_svc.create(
            type="oneonone", employee_id="00100", participants=["00100"],
        )
        reactivated = await pending_svc.reactivate(conv.id)
        assert reactivated.phase == ConversationPhase.ACTIVE.value
