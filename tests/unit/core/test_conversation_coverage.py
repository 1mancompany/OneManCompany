"""Coverage tests for core/conversation.py — missing lines."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# Avoid heavy imports — patch at module level where needed


# ---------------------------------------------------------------------------
# resolve_conv_dir — CEO_INBOX and PRODUCT types (lines 122-125)
# ---------------------------------------------------------------------------

class TestResolveConvDir:
    def test_ceo_inbox_type(self, tmp_path, monkeypatch):
        import onemancompany.core.conversation as conv_mod
        from onemancompany.core.conversation import Conversation, ConversationType, resolve_conv_dir

        conv = Conversation(
            id="conv123", type=ConversationType.CEO_INBOX,
            employee_id="00001", phase="active", tools_enabled=False,
            metadata={"project_dir": str(tmp_path / "proj")},
        )
        result = resolve_conv_dir(conv)
        assert "conversations" in str(result)
        assert "conv123" in str(result)

    def test_product_type_with_slug(self, tmp_path, monkeypatch):
        import onemancompany.core.conversation as conv_mod
        from onemancompany.core.conversation import Conversation, ConversationType, resolve_conv_dir

        monkeypatch.setattr(conv_mod, "PRODUCTS_DIR", tmp_path / "products")
        conv = Conversation(
            id="conv123", type=ConversationType.PRODUCT,
            employee_id="00001", phase="active", tools_enabled=False,
            metadata={"product_slug": "my-product"},
        )
        result = resolve_conv_dir(conv)
        assert "my-product" in str(result)

    def test_product_type_without_slug(self, tmp_path, monkeypatch):
        import onemancompany.core.conversation as conv_mod
        from onemancompany.core.conversation import Conversation, ConversationType, resolve_conv_dir

        monkeypatch.setattr(conv_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        conv = Conversation(
            id="conv123", type=ConversationType.PRODUCT,
            employee_id="00010", phase="active", tools_enabled=False,
            metadata={},
        )
        result = resolve_conv_dir(conv)
        assert "00010" in str(result)


# ---------------------------------------------------------------------------
# load_messages — missing file (line 167)
# ---------------------------------------------------------------------------

class TestLoadMessages:
    def test_missing_messages_file(self, tmp_path):
        from onemancompany.core.conversation import load_messages
        result = load_messages(tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# ConversationService.get — not found (line 221)
# ---------------------------------------------------------------------------

class TestConversationServiceGet:
    def test_get_not_found_raises(self):
        from onemancompany.core.conversation import ConversationService
        svc = ConversationService()
        with pytest.raises(ValueError, match="not found"):
            svc.get("nonexistent")

    def test_get_messages_not_found_raises(self):
        from onemancompany.core.conversation import ConversationService
        svc = ConversationService()
        with pytest.raises(ValueError, match="not found"):
            svc.get_messages("nonexistent")


# ---------------------------------------------------------------------------
# list_by_phase — load failure, phase filtering (lines 238-245)
# ---------------------------------------------------------------------------

class TestListByPhase:
    def test_skips_failed_meta(self, tmp_path, monkeypatch):
        from onemancompany.core.conversation import ConversationService
        svc = ConversationService()
        svc._index["bad"] = tmp_path / "nonexistent"
        result = svc.list_by_phase()
        assert len(result) == 0


# ---------------------------------------------------------------------------
# close — hooks import/failure (lines 267-270)
# ---------------------------------------------------------------------------

class TestClose:
    @pytest.mark.asyncio
    async def test_close_with_import_error(self, tmp_path, monkeypatch):
        from onemancompany.core.conversation import (
            Conversation, ConversationPhase, ConversationService,
            save_conversation_meta, resolve_conv_dir,
        )
        import onemancompany.core.conversation as conv_mod

        monkeypatch.setattr(conv_mod, "EMPLOYEES_DIR", tmp_path / "employees")

        svc = ConversationService()
        # Create a conversation manually
        conv = Conversation(
            id="c1", type="oneonone", employee_id="00010",
            phase=ConversationPhase.ACTIVE.value, tools_enabled=False,
        )
        conv_dir = resolve_conv_dir(conv)
        conv_dir.mkdir(parents=True, exist_ok=True)
        save_conversation_meta(conv)
        svc._index["c1"] = conv_dir

        with patch("onemancompany.core.conversation.event_bus", MagicMock(publish=AsyncMock())):
            with patch.dict("sys.modules", {"onemancompany.core.conversation_hooks": None}):
                closed_conv, hook_result = await svc.close("c1")
        assert closed_conv.phase == ConversationPhase.CLOSED.value
        assert "c1" not in svc._index


# ---------------------------------------------------------------------------
# send_message — not found (line 301)
# ---------------------------------------------------------------------------

class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_message_not_found(self):
        from onemancompany.core.conversation import ConversationService
        svc = ConversationService()
        with pytest.raises(ValueError, match="not found"):
            await svc.send_message("bad_id", "ceo", "user", "hello")


# ---------------------------------------------------------------------------
# _schedule_auto_reply / _ea_auto_reply (lines 408-441, 450-498)
# ---------------------------------------------------------------------------

class TestAutoReply:
    def test_schedule_auto_reply_credential_request_skips(self):
        from onemancompany.core.conversation import ConversationService, Interaction
        svc = ConversationService()
        interaction = Interaction(
            node_id="n1", tree_path="/tmp/tree.yaml", project_id="proj1",
            source_employee="00010", interaction_type="credential_request",
            message="need creds", future=asyncio.Future(),
        )
        # Should not schedule anything
        svc._start_auto_reply_timer("c1", interaction)
        assert len(svc._auto_reply_tasks) == 0

    def test_schedule_auto_reply_no_event_loop(self):
        """When there's no running event loop (RuntimeError), skip gracefully."""
        from onemancompany.core.conversation import ConversationService, Interaction
        svc = ConversationService()
        interaction = Interaction(
            node_id="n1", tree_path="/tmp/tree.yaml", project_id="proj1",
            source_employee="00010", interaction_type="approval",
            message="test", future=asyncio.Future(),
        )
        # This test runs without an event loop context, so create_task raises RuntimeError
        # The function should handle this gracefully (lines 438-441)
        svc._start_auto_reply_timer("c1", interaction)
        assert len(svc._auto_reply_tasks) == 0


# ---------------------------------------------------------------------------
# _ea_auto_reply — LLM call (lines 450-498)
# ---------------------------------------------------------------------------

class TestEaAutoReply:
    @pytest.mark.asyncio
    async def test_ea_auto_reply_accept(self):
        from onemancompany.core.conversation import ConversationService, Interaction

        interaction = Interaction(
            node_id="n1", tree_path="/tmp/tree.yaml", project_id="proj1",
            source_employee="00010", interaction_type="approval",
            message="Please approve budget", future=asyncio.Future(),
        )

        mock_llm = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = '{"decision": "accept", "reason": "Looks good"}'

        with patch("onemancompany.agents.base.make_llm", return_value=mock_llm), \
             patch("onemancompany.agents.base.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_resp), \
             patch("onemancompany.agents.base._extract_text", return_value='{"decision": "accept", "reason": "Looks good"}'):
            result = await ConversationService._ea_auto_reply("c1", interaction)
        assert "ACCEPT" in result

    @pytest.mark.asyncio
    async def test_ea_auto_reply_timeout(self):
        from onemancompany.core.conversation import ConversationService, Interaction

        interaction = Interaction(
            node_id="n1", tree_path="/tmp/tree.yaml", project_id="proj1",
            source_employee="00010", interaction_type="approval",
            message="test", future=asyncio.Future(),
        )

        with patch("onemancompany.agents.base.make_llm"), \
             patch("onemancompany.agents.base.tracked_ainvoke", new_callable=AsyncMock,
                   side_effect=asyncio.TimeoutError):
            with patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=asyncio.TimeoutError):
                result = await ConversationService._ea_auto_reply("c1", interaction)
        assert "timed out" in result.lower() or "ACCEPT" in result


# ---------------------------------------------------------------------------
# rebuild_index — projects + products (lines 591-602)
# ---------------------------------------------------------------------------

class TestRebuildIndex:
    def test_rebuilds_from_projects_and_products(self, tmp_path, monkeypatch):
        import onemancompany.core.conversation as conv_mod

        monkeypatch.setattr(conv_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr(conv_mod, "PROJECTS_DIR", tmp_path / "projects")
        monkeypatch.setattr(conv_mod, "PRODUCTS_DIR", tmp_path / "products")

        # Create project conversation
        proj_conv = tmp_path / "projects" / "proj1" / "conversations" / "c1"
        proj_conv.mkdir(parents=True)
        (proj_conv / "meta.yaml").write_text("id: c1\ntype: oneonone\nphase: active\n")

        # Create product conversation
        prod_conv = tmp_path / "products" / "prod1" / "conversations" / "c2"
        prod_conv.mkdir(parents=True)
        (prod_conv / "meta.yaml").write_text("id: c2\ntype: product\nphase: active\n")

        svc = conv_mod.ConversationService()
        svc.rebuild_index()

        assert "c1" in svc._index
        assert "c2" in svc._index


# ---------------------------------------------------------------------------
# recover (lines 615-626)
# ---------------------------------------------------------------------------

class TestRecover:
    @pytest.mark.asyncio
    async def test_recover_stuck_conversations(self, tmp_path, monkeypatch):
        import onemancompany.core.conversation as conv_mod
        from onemancompany.core.conversation import (
            Conversation, ConversationPhase, ConversationService,
            save_conversation_meta, resolve_conv_dir,
        )

        monkeypatch.setattr(conv_mod, "EMPLOYEES_DIR", tmp_path / "employees")

        svc = ConversationService()
        conv = Conversation(
            id="stuck1", type="oneonone", employee_id="00010",
            phase=ConversationPhase.CLOSING.value, tools_enabled=False,
        )
        conv_dir = resolve_conv_dir(conv)
        conv_dir.mkdir(parents=True, exist_ok=True)
        save_conversation_meta(conv)
        svc._index["stuck1"] = conv_dir

        with patch("onemancompany.core.conversation_hooks.run_close_hook", new_callable=AsyncMock):
            count = await svc.recover()
        assert count == 1
        assert "stuck1" not in svc._index


# ---------------------------------------------------------------------------
# cancel_all_timers / _drain_pending (lines 663-664, 679-687)
# ---------------------------------------------------------------------------

class TestDrainAndCancel:
    def test_cancel_all_timers(self):
        from onemancompany.core.conversation import ConversationService
        svc = ConversationService()
        mock_task = MagicMock()
        mock_task.done.return_value = False
        svc._auto_reply_tasks["c1:n1"] = mock_task
        svc.cancel_all_timers()
        mock_task.cancel.assert_called_once()
        assert len(svc._auto_reply_tasks) == 0

    def test_drain_pending(self):
        from onemancompany.core.conversation import ConversationService, Interaction
        svc = ConversationService()
        future = asyncio.Future()
        interaction = Interaction(
            node_id="n1", tree_path="/tmp/tree.yaml", project_id="proj1",
            source_employee="00010", interaction_type="approval",
            message="test", future=future,
        )
        svc._pending["c1"] = deque([interaction])
        mock_timer = MagicMock()
        mock_timer.done.return_value = False
        svc._auto_reply_tasks["c1:n1"] = mock_timer

        count = svc._drain_pending("c1")
        assert count == 1
        assert future.exception() is not None
        mock_timer.cancel.assert_called_once()

    def test_remove_by_project(self, tmp_path, monkeypatch):
        import onemancompany.core.conversation as conv_mod
        from onemancompany.core.conversation import (
            Conversation, ConversationService, save_conversation_meta, resolve_conv_dir,
        )

        monkeypatch.setattr(conv_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        svc = ConversationService()

        conv = Conversation(
            id="pc1", type="oneonone", employee_id="00010",
            phase="active", tools_enabled=False, project_id="proj1/iter_001",
        )
        conv_dir = resolve_conv_dir(conv)
        conv_dir.mkdir(parents=True, exist_ok=True)
        save_conversation_meta(conv)
        svc._index["pc1"] = conv_dir

        count = svc.remove_by_project("proj1")
        assert count == 1
        assert "pc1" not in svc._index
