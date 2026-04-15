"""Coverage tests for agents/base.py — missing lines."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _extract_text (lines 73-81)
# ---------------------------------------------------------------------------

class TestExtractText:
    def test_str_content(self):
        from onemancompany.agents.base import _extract_text
        assert _extract_text("hello") == "hello"

    def test_list_of_blocks(self):
        from onemancompany.agents.base import _extract_text
        content = [
            {"type": "text", "text": "Hello"},
            {"type": "tool_use", "name": "bash"},
            "raw string",
        ]
        result = _extract_text(content)
        assert "Hello" in result
        assert "raw string" in result

    def test_none_content(self):
        from onemancompany.agents.base import _extract_text
        assert _extract_text(None) == ""

    def test_other_type(self):
        from onemancompany.agents.base import _extract_text
        assert _extract_text(42) == "42"


# ---------------------------------------------------------------------------
# extract_final_content (lines 97, 102-104, 112-133)
# ---------------------------------------------------------------------------

class TestExtractFinalContent:
    def test_empty_messages(self):
        from onemancompany.agents.base import extract_final_content
        assert extract_final_content({"messages": []}) == ""
        assert extract_final_content({}) == ""

    def test_ai_message_with_text(self):
        from onemancompany.agents.base import extract_final_content
        from langchain_core.messages import AIMessage
        result = extract_final_content({
            "messages": [AIMessage(content="Hello world")]
        })
        assert result == "Hello world"

    def test_tool_messages_fallback(self):
        from onemancompany.agents.base import extract_final_content
        from langchain_core.messages import AIMessage, ToolMessage
        result = extract_final_content({
            "messages": [
                AIMessage(content="", tool_calls=[{"name": "bash", "args": {}, "id": "t1"}]),
                ToolMessage(content="file.txt", tool_call_id="t1"),
            ]
        })
        assert "bash" in result

    def test_last_resort_all_tool_calls(self):
        from onemancompany.agents.base import extract_final_content
        from langchain_core.messages import AIMessage, ToolMessage
        # Multiple AI messages with tool_calls but no text
        result = extract_final_content({
            "messages": [
                AIMessage(content="", tool_calls=[{"name": "read_file", "args": {}, "id": "t1"}]),
                ToolMessage(content="data", tool_call_id="t1"),
                AIMessage(content="", tool_calls=[{"name": "write_file", "args": {}, "id": "t2"}]),
                ToolMessage(content="ok", tool_call_id="t2"),
            ]
        })
        assert "read_file" in result or "write_file" in result


# ---------------------------------------------------------------------------
# _resolve_provider_key (line 144)
# ---------------------------------------------------------------------------

class TestResolveProviderKey:
    def test_employee_key_takes_precedence(self):
        from onemancompany.agents.base import _resolve_provider_key
        assert _resolve_provider_key("openai", "sk-emp-key") == "sk-emp-key"


# ---------------------------------------------------------------------------
# make_llm — various branches (lines 172, 179, 189, 219, 237)
# ---------------------------------------------------------------------------

class TestMakeLlm:
    def test_make_llm_with_temperature_override(self, monkeypatch):
        from onemancompany.agents.base import make_llm
        import onemancompany.core.config as config_mod

        mock_settings = MagicMock()
        mock_settings.default_llm_model = "gpt-4"
        mock_settings.default_api_provider = "openrouter"
        mock_settings.openrouter_api_key = "sk-test"
        mock_settings.openrouter_base_url = "https://openrouter.ai/api/v1"
        mock_settings.default_api_base_url = ""
        mock_settings.custom_chat_class = ""
        monkeypatch.setattr("onemancompany.agents.base._cfg.settings", mock_settings)

        llm = make_llm(temperature=0.0)
        assert llm is not None


# ---------------------------------------------------------------------------
# _parse_skill_frontmatter (lines 407, 410, 414-415)
# ---------------------------------------------------------------------------

class TestParseSkillFrontmatter:
    def test_no_frontmatter(self):
        from onemancompany.agents.base import _parse_skill_frontmatter
        meta, body = _parse_skill_frontmatter("# Regular content")
        assert meta == {}
        assert body == "# Regular content"

    def test_valid_frontmatter(self):
        from onemancompany.agents.base import _parse_skill_frontmatter
        raw = "---\nname: Test\ndescription: A test skill\n---\n# Body"
        meta, body = _parse_skill_frontmatter(raw)
        assert meta["name"] == "Test"
        assert "# Body" in body

    def test_unclosed_frontmatter(self):
        from onemancompany.agents.base import _parse_skill_frontmatter
        raw = "---\nname: Test\nno closing"
        meta, body = _parse_skill_frontmatter(raw)
        assert meta == {}

    def test_invalid_yaml_frontmatter(self):
        from onemancompany.agents.base import _parse_skill_frontmatter
        raw = "---\n: : : invalid\n---\nBody"
        meta, body = _parse_skill_frontmatter(raw)
        # Should return empty meta on parse error
        assert isinstance(meta, dict)


# ---------------------------------------------------------------------------
# get_employee_skills_prompt (lines 455, 464-465)
# ---------------------------------------------------------------------------

class TestEmployeeSkillsPrompt:
    def test_no_skills(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        from onemancompany.agents.base import get_employee_skills_prompt
        assert get_employee_skills_prompt("00010") == ""

    def test_with_autoload_skill(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        skills_dir = tmp_path / "00010" / "skills" / "test_skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("---\nname: TestSkill\nautoload: true\n---\n# Auto content")

        from onemancompany.agents.base import get_employee_skills_prompt
        result = get_employee_skills_prompt("00010")
        assert "Auto content" in result
        assert "Active Skills" in result

    def test_with_catalog_skill(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        skills_dir = tmp_path / "00010" / "skills" / "manual_skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("---\nname: ManualSkill\ndescription: Do manual things\n---\n# Manual")

        from onemancompany.agents.base import get_employee_skills_prompt
        result = get_employee_skills_prompt("00010")
        assert "ManualSkill" in result
        assert "Available Skills" in result
