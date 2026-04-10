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
