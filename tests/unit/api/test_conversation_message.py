"""Tests for /api/conversation/{conv_id}/message — must resolve pending interactions."""
from __future__ import annotations

from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from onemancompany.core.conversation import Message


def _mock_service():
    svc = MagicMock()
    svc.send_message = AsyncMock(
        return_value=Message(sender="ceo", role="CEO", text="ok", timestamp="t")
    )
    svc.resolve_interaction = AsyncMock(return_value={"type": "followup", "text": ""})
    return svc


class TestConversationMessageResolvesInteraction:
    """Bug regression: CEO confirm message must resolve pending interaction,
    not dispatch to adapter (which causes EA to self-execute)."""

    @pytest.mark.asyncio
    async def test_resolves_pending_interaction_instead_of_dispatching(self):
        from onemancompany.api.routes import send_conversation_message

        svc = _mock_service()
        svc.resolve_interaction.return_value = {"type": "resolved", "node_id": "node_abc"}

        with patch("onemancompany.api.routes._get_conv_svc", return_value=svc), \
             patch("onemancompany.api.routes._dispatch_conversation_to_adapter", new_callable=AsyncMock) as mock_dispatch:
            result = await send_conversation_message("conv_123", {"text": "Yes confirmed"})

            svc.resolve_interaction.assert_awaited_once_with("conv_123", "Yes confirmed")
            mock_dispatch.assert_not_awaited()
            assert result["status"] == "resolved"

    @pytest.mark.asyncio
    async def test_dispatches_to_adapter_when_no_pending(self):
        from onemancompany.api.routes import send_conversation_message

        svc = _mock_service()
        svc.resolve_interaction.return_value = {"type": "followup", "text": "hello"}

        with patch("onemancompany.api.routes._get_conv_svc", return_value=svc), \
             patch("onemancompany.api.routes._dispatch_conversation_to_adapter", new_callable=AsyncMock) as mock_dispatch, \
             patch("onemancompany.api.routes._active_adapter_tasks", set()), \
             patch("onemancompany.api.routes._active_adapter_by_conv", {}):
            result = await send_conversation_message("conv_123", {"text": "hello"})

            svc.resolve_interaction.assert_awaited_once()
            assert result["status"] == "sent"
