"""Tests for unified CEO session API endpoints."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from onemancompany.core.ceo_broker import CeoBroker, CeoInteraction


class TestListSessions:
    @pytest.mark.asyncio
    async def test_returns_empty_list(self):
        from onemancompany.api.routes import list_ceo_sessions

        with patch("onemancompany.core.ceo_broker.get_ceo_broker") as mock_get:
            broker = CeoBroker()
            mock_get.return_value = broker
            result = await list_ceo_sessions()
            assert result == {"sessions": []}

    @pytest.mark.asyncio
    async def test_returns_sessions_sorted(self):
        from onemancompany.api.routes import list_ceo_sessions

        broker = CeoBroker()
        s1 = broker.get_or_create_session("proj_a")
        s2 = broker.get_or_create_session("proj_b")
        s2.enqueue(
            CeoInteraction(
                node_id="x",
                tree_path="",
                project_id="proj_b",
                source_employee="00003",
                interaction_type="ceo_request",
                message="Help",
                future=asyncio.get_event_loop().create_future(),
            )
        )
        with patch("onemancompany.core.ceo_broker.get_ceo_broker", return_value=broker):
            result = await list_ceo_sessions()
            assert result["sessions"][0]["project_id"] == "proj_b"


class TestGetSession:
    @pytest.mark.asyncio
    async def test_returns_session_history(self):
        from onemancompany.api.routes import get_ceo_session

        broker = CeoBroker()
        session = broker.get_or_create_session("proj_001")
        session.push_system_message("Hello", source="00003")
        with patch("onemancompany.core.ceo_broker.get_ceo_broker", return_value=broker):
            result = await get_ceo_session("proj_001")
            assert result["project_id"] == "proj_001"
            assert len(result["history"]) == 1

    @pytest.mark.asyncio
    async def test_404_for_unknown_session(self):
        from onemancompany.api.routes import get_ceo_session
        from fastapi import HTTPException

        broker = CeoBroker()
        with patch("onemancompany.core.ceo_broker.get_ceo_broker", return_value=broker):
            with pytest.raises(HTTPException) as exc_info:
                await get_ceo_session("nonexistent")
            assert exc_info.value.status_code == 404


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_resolves_pending_interaction(self):
        from onemancompany.api.routes import send_ceo_session_message

        broker = CeoBroker()
        session = broker.get_or_create_session("proj_001")
        future = asyncio.get_event_loop().create_future()
        session.enqueue(
            CeoInteraction(
                node_id="abc",
                tree_path="",
                project_id="proj_001",
                source_employee="00003",
                interaction_type="ceo_request",
                message="Approve?",
                future=future,
            )
        )
        with patch("onemancompany.core.ceo_broker.get_ceo_broker", return_value=broker):
            result = await send_ceo_session_message("proj_001", {"text": "Yes"})
            assert result["type"] == "resolved"
            assert result["node_id"] == "abc"
            assert future.result() == "Yes"

    @pytest.mark.asyncio
    async def test_empty_message_returns_400(self):
        from onemancompany.api.routes import send_ceo_session_message
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await send_ceo_session_message("proj_001", {"text": ""})
        assert exc_info.value.status_code == 400
