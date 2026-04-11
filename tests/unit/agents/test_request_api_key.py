"""Tests for request_api_key tool."""
from __future__ import annotations

from unittest.mock import patch, AsyncMock, MagicMock

import pytest


class TestRequestApiKey:
    """request_api_key tool — agent requests API key from CEO via chat."""

    @pytest.mark.asyncio
    async def test_dnd_mode_returns_error(self):
        """When CEO DND is on, tool should refuse and tell agent to try alternatives."""
        from onemancompany.agents.common_tools import request_api_key

        with patch("onemancompany.core.config.get_ceo_dnd", return_value=True):
            result = await request_api_key.ainvoke({
                "service_name": "stripe",
                "reason": "Need payment processing",
                "employee_id": "00004",
            })

        assert result["status"] == "dnd_active"
        assert "alternative" in result["message"].lower() or "not available" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_enqueues_interaction_when_available(self):
        """When CEO is available, should enqueue a credential interaction."""
        from onemancompany.agents.common_tools import request_api_key
        from onemancompany.core.conversation import Interaction

        mock_service = MagicMock()
        mock_conv = MagicMock()
        mock_conv.id = "conv_123"
        mock_service.get_or_create_oneonone = AsyncMock(return_value=mock_conv)

        # Auto-resolve the future when enqueue_interaction is called
        async def _auto_resolve(conv_id, interaction):
            interaction.future.set_result("sk-test-key-123")
        mock_service.enqueue_interaction = AsyncMock(side_effect=_auto_resolve)

        with patch("onemancompany.core.config.get_ceo_dnd", return_value=False), \
             patch("onemancompany.core.conversation.get_conversation_service", return_value=mock_service), \
             patch("onemancompany.core.conversation.event_bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            result = await request_api_key.ainvoke({
                "service_name": "stripe",
                "reason": "Need payment processing",
                "employee_id": "00004",
            })

        assert result["status"] == "ok"
        assert result["env_key"] == "STRIPE_API_KEY"
        mock_service.enqueue_interaction.assert_awaited_once()
        # Verify interaction type is credential_request
        call_args = mock_service.enqueue_interaction.call_args
        interaction = call_args[0][1]
        assert interaction.interaction_type == "credential_request"
        assert interaction.credential_env_key == "STRIPE_API_KEY"
        assert "stripe" in interaction.message.lower()

    @pytest.mark.asyncio
    async def test_env_var_key_name(self):
        """Service name should be converted to env var format."""
        from onemancompany.agents.common_tools import _credential_env_key
        assert _credential_env_key("stripe") == "STRIPE_API_KEY"
        assert _credential_env_key("my_service") == "MY_SERVICE_API_KEY"
        assert _credential_env_key("OpenAI") == "OPENAI_API_KEY"


class TestResolveCredentialInteraction:
    """resolve_interaction with credential_request type — mask and store."""

    @pytest.mark.asyncio
    async def test_credential_resolve_stores_env_var(self):
        """Resolving a credential interaction should store the key as env var."""
        import asyncio
        from onemancompany.core.conversation import ConversationService, Interaction

        service = ConversationService.__new__(ConversationService)
        service._pending = {}
        service._auto_reply_tasks = {}

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        interaction = Interaction(
            node_id="node_abc",
            tree_path="",
            project_id="proj_1",
            source_employee="00004",
            interaction_type="credential_request",
            message="🔑 Need Stripe API key",
            future=future,
            credential_env_key="STRIPE_API_KEY",
        )
        from collections import deque
        service._pending["conv_123"] = deque([interaction])

        with patch("onemancompany.core.config.update_env_var") as mock_update:
            result = await service.resolve_interaction("conv_123", "sk-live-abc123")

        assert result["type"] == "resolved"
        assert result.get("display_text") == "••• (saved as STRIPE_API_KEY)"
        mock_update.assert_called_once_with("STRIPE_API_KEY", "sk-live-abc123")
        assert future.result() == "sk-live-abc123"

    @pytest.mark.asyncio
    async def test_normal_resolve_no_masking(self):
        """Non-credential interactions should not mask or store env vars."""
        import asyncio
        from onemancompany.core.conversation import ConversationService, Interaction

        service = ConversationService.__new__(ConversationService)
        service._pending = {}
        service._auto_reply_tasks = {}

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        interaction = Interaction(
            node_id="node_abc",
            tree_path="",
            project_id="proj_1",
            source_employee="00004",
            interaction_type="ceo_request",
            message="Need approval",
            future=future,
        )
        from collections import deque
        service._pending["conv_123"] = deque([interaction])

        with patch("onemancompany.core.config.update_env_var") as mock_update:
            result = await service.resolve_interaction("conv_123", "approved")

        assert result["type"] == "resolved"
        assert "display_text" not in result
        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_credential_resolve_rejects_empty_key(self):
        """Empty or whitespace-only key should not be stored."""
        import asyncio
        from onemancompany.core.conversation import ConversationService, Interaction

        service = ConversationService.__new__(ConversationService)
        service._pending = {}
        service._auto_reply_tasks = {}

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        interaction = Interaction(
            node_id="node_abc",
            tree_path="",
            project_id="proj_1",
            source_employee="00004",
            interaction_type="credential_request",
            message="Need key",
            future=future,
            credential_env_key="EMPTY_API_KEY",
        )
        from collections import deque
        service._pending["conv_123"] = deque([interaction])

        with patch("onemancompany.core.config.update_env_var") as mock_update:
            result = await service.resolve_interaction("conv_123", "   ")

        assert result["display_text"] == "(empty or invalid key — not saved)"
        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_credential_resolve_rejects_newlines(self):
        """Keys with newlines should not be stored (would corrupt .env)."""
        import asyncio
        from onemancompany.core.conversation import ConversationService, Interaction

        service = ConversationService.__new__(ConversationService)
        service._pending = {}
        service._auto_reply_tasks = {}

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        interaction = Interaction(
            node_id="node_abc",
            tree_path="",
            project_id="proj_1",
            source_employee="00004",
            interaction_type="credential_request",
            message="Need key",
            future=future,
            credential_env_key="BAD_API_KEY",
        )
        from collections import deque
        service._pending["conv_123"] = deque([interaction])

        with patch("onemancompany.core.config.update_env_var") as mock_update:
            result = await service.resolve_interaction("conv_123", "sk-abc\ninjection")

        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_credential_no_auto_reply_timer(self):
        """Credential requests must NOT start auto-reply timer."""
        import asyncio
        from onemancompany.core.conversation import ConversationService, Interaction
        from collections import deque

        service = ConversationService.__new__(ConversationService)
        service._pending = {}
        service._auto_reply_tasks = {}
        service._conversations = {}
        service.send_message = AsyncMock()

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        interaction = Interaction(
            node_id="node_abc",
            tree_path="",
            project_id="proj_1",
            source_employee="00004",
            interaction_type="credential_request",
            message="Need key",
            future=future,
            credential_env_key="TEST_API_KEY",
        )

        with patch("onemancompany.core.conversation.event_bus") as mock_bus:
            mock_bus.publish = AsyncMock()
            await service.enqueue_interaction("conv_123", interaction)

        # No timer should have been started
        assert len(service._auto_reply_tasks) == 0
