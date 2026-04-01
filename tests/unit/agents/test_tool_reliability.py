"""Tests for tool reliability — prompt guide, EA privilege, error helpers."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestToolSelectionGuide:
    def test_prompt_contains_tool_selection_guide(self):
        from onemancompany.agents.base import get_employee_tools_prompt
        from onemancompany.core.tool_registry import tool_registry

        with patch.object(tool_registry, "get_tools_for", return_value=[
            MagicMock(name="read", description="Read a file"),
        ]), patch.object(tool_registry, "get_meta", return_value=None):
            prompt = get_employee_tools_prompt("00010")

        assert "Tool Selection Guide" in prompt
        assert "deposit_company_knowledge" in prompt
        assert "IMPORTANT" in prompt
        assert "verify the change" in prompt.lower()
        assert "Internal vs External" in prompt


class TestInputValidation:
    def test_validate_employee_id_valid(self):
        from onemancompany.agents.common_tools import _validate_employee_id
        assert _validate_employee_id("00004") is None

    def test_validate_employee_id_empty(self):
        from onemancompany.agents.common_tools import _validate_employee_id
        result = _validate_employee_id("")
        assert result["status"] == "error"


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

    def test_error_with_hint(self):
        from onemancompany.agents.common_tools import _tool_error
        result = _tool_error("Employee not found.", hint="Use list_colleagues().")
        assert "Use list_colleagues()" in result["message"]

    def test_error_with_retry(self):
        from onemancompany.agents.common_tools import _tool_error
        result = _tool_error("Invalid path.", retry_with="write('correct/path', content)")
        assert "Retry:" in result["message"]


class TestEAToolPrivilege:
    def test_ea_can_access_all_role_tools(self):
        """EA has full access to all tools regardless of role restrictions."""
        import onemancompany.agents.coo_agent  # noqa: F401
        from onemancompany.core.tool_registry import tool_registry

        with patch("onemancompany.core.store.load_employee", return_value={"role": "EA"}):
            tools = tool_registry.get_tools_for("00004")
        tool_names = [t.name for t in tools]
        assert "deposit_company_knowledge" in tool_names
        assert "register_asset" in tool_names
        assert "request_hiring" in tool_names

    def test_regular_employee_cannot_access_coo_tools(self):
        """Non-EA, non-COO employees should NOT get COO role tools."""
        import onemancompany.agents.coo_agent  # noqa: F401
        from onemancompany.core.tool_registry import tool_registry

        with patch("onemancompany.core.store.load_employee", return_value={"role": "Engineer"}):
            tools = tool_registry.get_tools_for("00010")
        tool_names = [t.name for t in tools]
        assert "deposit_company_knowledge" not in tool_names
        assert "register_asset" not in tool_names
