"""Tests for unified CEO communication system."""
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


from onemancompany.core.conversation import Conversation, Message

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
