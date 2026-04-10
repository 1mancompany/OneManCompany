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
