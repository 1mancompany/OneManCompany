"""Tests for tool reliability Batch 1 — dedicated tools + prompt guide."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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


class TestUpdateGuidance:
    @pytest.mark.asyncio
    async def test_appends_note_and_returns_ok(self):
        with patch("onemancompany.agents.common_tools._store") as mock_store:
            mock_store.load_employee = lambda eid: {"id": eid} if eid == "00005" else None
            mock_store.load_employee_guidance = lambda eid: ["Be proactive"]
            mock_store.save_guidance = AsyncMock()

            from onemancompany.agents.common_tools import update_guidance
            result = await update_guidance.coroutine(
                target_employee_id="00005",
                note="Focus on client communication",
                employee_id="00004",
            )
        assert result["status"] == "ok"
        assert result["notes_count"] == 2
        mock_store.save_guidance.assert_awaited_once_with("00005", ["Be proactive", "Focus on client communication"])

    @pytest.mark.asyncio
    async def test_invalid_employee_returns_error(self):
        with patch("onemancompany.agents.common_tools._store") as mock_store:
            mock_store.load_employee = lambda eid: None

            from onemancompany.agents.common_tools import update_guidance
            result = await update_guidance.coroutine(
                target_employee_id="99999",
                note="anything",
                employee_id="00004",
            )
        assert result["status"] == "error"
        assert "99999" in result["message"]

    @pytest.mark.asyncio
    async def test_empty_note_returns_error(self):
        from onemancompany.agents.common_tools import update_guidance
        result = await update_guidance.coroutine(
            target_employee_id="00005",
            note="   ",
            employee_id="00004",
        )
        assert result["status"] == "error"
        assert "empty" in result["message"].lower()


class TestToolSelectionGuide:
    def test_prompt_contains_tool_selection_guide(self):
        from onemancompany.agents.base import get_employee_tools_prompt
        from onemancompany.core.tool_registry import tool_registry

        with patch.object(tool_registry, "get_tools_for", return_value=[
            MagicMock(name="read", description="Read a file"),
        ]), patch.object(tool_registry, "get_meta", return_value=None):
            prompt = get_employee_tools_prompt("00010")

        assert "Tool Selection Guide" in prompt
        assert "update_work_principles" in prompt
        assert "update_guidance" in prompt
        assert "IMPORTANT" in prompt
        assert "verify the change" in prompt.lower()
        assert "Internal vs External" in prompt
