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


class TestInputValidation:
    def test_validate_employee_id_valid(self):
        from onemancompany.agents.common_tools import _validate_employee_id
        assert _validate_employee_id("00004") is None
        assert _validate_employee_id("00010") is None

    def test_validate_employee_id_non_empty_passes(self):
        from onemancompany.agents.common_tools import _validate_employee_id
        assert _validate_employee_id("abc") is None  # format not enforced, existence check handles it

    def test_validate_employee_id_empty(self):
        from onemancompany.agents.common_tools import _validate_employee_id
        result = _validate_employee_id("")
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_work_principles_empty_id(self):
        from onemancompany.agents.common_tools import update_work_principles
        result = await update_work_principles.coroutine(
            target_employee_id="",
            content="anything",
            employee_id="00004",
        )
        assert result["status"] == "error"
        assert "required" in result["message"].lower()


class TestNextStepHints:
    @pytest.mark.asyncio
    async def test_write_returns_next_step(self, tmp_path):
        from onemancompany.agents.common_tools import write
        target = tmp_path / "test.md"

        with patch("onemancompany.agents.common_tools._resolve_employee_path", return_value=target):
            result = await write.coroutine(file_path=str(target), content="hello", employee_id="00010")
        assert result["status"] == "ok"
        assert "next_step" in result
        assert "read" in result["next_step"].lower()


class TestToolError:
    def test_basic_error(self):
        from onemancompany.agents.common_tools import _tool_error
        result = _tool_error("File not found.")
        assert result["status"] == "error"
        assert result["is_error"] is True
        assert result["message"].startswith("ERROR:")
        assert "File not found" in result["message"]

    def test_error_with_hint(self):
        from onemancompany.agents.common_tools import _tool_error
        result = _tool_error("Employee not found.", hint="Use list_colleagues().")
        assert "Use list_colleagues()" in result["message"]

    def test_error_with_retry(self):
        from onemancompany.agents.common_tools import _tool_error
        result = _tool_error("Invalid path.", retry_with="write('correct/path', content)")
        assert "Retry:" in result["message"]
        assert "write('correct/path'" in result["message"]

    def test_validate_employee_id_uses_tool_error(self):
        from onemancompany.agents.common_tools import _validate_employee_id
        result = _validate_employee_id("")
        assert result["is_error"] is True
        assert result["message"].startswith("ERROR:")


class TestNextStepHints:
    @pytest.mark.asyncio
    async def test_update_work_principles_returns_next_step(self):
        with patch("onemancompany.agents.common_tools._store") as mock_store:
            mock_store.save_work_principles = AsyncMock()
            mock_store.load_employee = lambda eid: {"id": eid} if eid == "00004" else None

            from onemancompany.agents.common_tools import update_work_principles
            result = await update_work_principles.coroutine(
                target_employee_id="00004",
                content="# Principles",
                employee_id="00004",
            )
        assert result["status"] == "ok"
        assert "next_step" in result
