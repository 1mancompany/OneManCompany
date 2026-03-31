"""Tests for tool reliability Batch 1 — dedicated tools + prompt guide."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class TestUpdateWorkPrinciples:
    @pytest.mark.asyncio
    async def test_updates_principles_and_returns_ok(self):
        with patch("onemancompany.agents.common_tools._store") as mock_store:
            mock_store.save_work_principles = AsyncMock()
            mock_store.load_employee = lambda eid: {"id": eid} if eid == "00004" else None

            from onemancompany.agents.common_tools import update_work_principles
            result = await update_work_principles.coroutine(
                target_employee_id="00004",
                content="# New Principles\n1. Be excellent",
                employee_id="00004",
            )
        assert result["status"] == "ok"
        assert result["employee_id"] == "00004"
        mock_store.save_work_principles.assert_awaited_once_with("00004", "# New Principles\n1. Be excellent")

    @pytest.mark.asyncio
    async def test_invalid_employee_returns_error(self):
        with patch("onemancompany.agents.common_tools._store") as mock_store:
            mock_store.load_employee = lambda eid: None

            from onemancompany.agents.common_tools import update_work_principles
            result = await update_work_principles.coroutine(
                target_employee_id="99999",
                content="anything",
                employee_id="00004",
            )
        assert result["status"] == "error"
        assert "99999" in result["message"]
        assert "list_colleagues" in result["message"]

    @pytest.mark.asyncio
    async def test_empty_content_returns_error(self):
        from onemancompany.agents.common_tools import update_work_principles
        result = await update_work_principles.coroutine(
            target_employee_id="00004",
            content="",
            employee_id="00004",
        )
        assert result["status"] == "error"
        assert "empty" in result["message"].lower()
