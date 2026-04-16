"""Coverage tests for agents/base.py — missing lines."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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

class TestExtractFinalContentLastResort:
    """Cover lines 130-133: last-resort tool call collection across ALL messages."""
    def test_all_tool_calls_collected(self):
        from onemancompany.agents.base import extract_final_content
        from langchain_core.messages import AIMessage, HumanMessage
        # Path 2 walks backward: hits AIMessage(no tool_calls) → no tool_names
        # Path 3 scans ALL messages → finds tool_calls on first AIMessage
        result = extract_final_content({
            "messages": [
                AIMessage(content="", tool_calls=[{"name": "tool_a", "args": {}, "id": "t1"}]),
                HumanMessage(content="continue"),
                AIMessage(content=""),  # no tool_calls, no text → backward scan stops here
            ]
        })
        assert "tool_a" in result

    def test_fallback_to_no_output(self):
        """Cover line 134: no tool calls at all, last message content is empty."""
        from onemancompany.agents.base import extract_final_content
        from langchain_core.messages import HumanMessage
        result = extract_final_content({
            "messages": [HumanMessage(content="")]
        })
        assert result  # Should return _NO_OUTPUT sentinel


class TestResolveProviderKeyFull:
    """Cover line 144: provider registry fallback."""
    def test_no_employee_key_uses_provider(self, monkeypatch):
        from onemancompany.agents.base import _resolve_provider_key
        mock_prov = MagicMock()
        mock_prov.env_key = "openrouter_api_key"
        mock_settings = MagicMock()
        mock_settings.openrouter_api_key = "sk-company"
        with patch("onemancompany.agents.base.get_provider", return_value=mock_prov), \
             patch("onemancompany.agents.base._cfg") as mock_cfg:
            mock_cfg.settings = mock_settings
            result = _resolve_provider_key("openrouter", "")
        assert result == "sk-company"


class TestMakeLlmBranches:
    """Cover lines 179, 189, 219, 237 of make_llm."""
    def test_custom_chat_class_override(self, monkeypatch):
        """Cover line 179: custom provider with custom_chat_class."""
        from onemancompany.agents.base import make_llm, CHAT_CLASS_OPENAI
        mock_settings = MagicMock()
        mock_settings.default_llm_model = "gpt-4"
        mock_settings.default_api_provider = "custom"
        mock_settings.custom_chat_class = CHAT_CLASS_OPENAI
        mock_settings.default_api_base_url = "http://localhost:8080"
        mock_settings.openrouter_api_key = "sk-test"
        mock_settings.openrouter_base_url = "https://openrouter.ai/api/v1"

        mock_prov = MagicMock()
        mock_prov.chat_class = "other"
        mock_prov.base_url = "http://default"
        mock_prov.env_key = ""

        monkeypatch.setattr("onemancompany.agents.base._cfg.settings", mock_settings)
        with patch("onemancompany.agents.base.get_provider", return_value=mock_prov):
            llm = make_llm()
        assert llm is not None

    def test_anthropic_oauth_auth_method(self, monkeypatch):
        """Cover line 189: anthropic_auth_method fallback."""
        from onemancompany.agents.base import make_llm, CHAT_CLASS_ANTHROPIC, AuthMethod
        mock_settings = MagicMock()
        mock_settings.default_llm_model = "claude-3"
        mock_settings.default_api_provider = "anthropic"
        mock_settings.anthropic_auth_method = AuthMethod.OAUTH
        mock_settings.anthropic_oauth_token = "sk-ant-oat-xxx"
        mock_settings.anthropic_api_key = ""
        mock_settings.custom_chat_class = ""
        mock_settings.openrouter_api_key = ""

        mock_prov = MagicMock()
        mock_prov.chat_class = CHAT_CLASS_ANTHROPIC
        mock_prov.env_key = "anthropic_api_key"
        mock_prov.base_url = ""

        monkeypatch.setattr("onemancompany.agents.base._cfg.settings", mock_settings)
        with patch("onemancompany.agents.base.get_provider", return_value=mock_prov), \
             patch("onemancompany.agents.base.employee_configs", {}):
            llm = make_llm()
        assert llm is not None

    def test_custom_base_url_override(self, monkeypatch):
        """Cover line 219: custom base_url for default_api_provider."""
        from onemancompany.agents.base import make_llm, CHAT_CLASS_OPENAI
        mock_settings = MagicMock()
        mock_settings.default_llm_model = "gpt-4"
        mock_settings.default_api_provider = "custom"
        mock_settings.default_api_base_url = "http://custom-endpoint"
        mock_settings.custom_chat_class = CHAT_CLASS_OPENAI
        mock_settings.openrouter_api_key = "sk-test"
        mock_settings.openrouter_base_url = "https://openrouter.ai/api/v1"

        mock_prov = MagicMock()
        mock_prov.chat_class = CHAT_CLASS_OPENAI
        mock_prov.base_url = "http://default"
        mock_prov.env_key = "custom_api_key"

        monkeypatch.setattr("onemancompany.agents.base._cfg.settings", mock_settings)
        monkeypatch.setattr("onemancompany.agents.base.employee_configs", {})
        with patch("onemancompany.agents.base.get_provider", return_value=mock_prov):
            setattr(mock_settings, "custom_api_key", "sk-custom")
            llm = make_llm()
        assert llm is not None

    def test_fallback_no_key_warning(self, monkeypatch):
        """Cover line 237: no API key warning."""
        from onemancompany.agents.base import make_llm
        mock_settings = MagicMock()
        mock_settings.default_llm_model = "gpt-4"
        mock_settings.default_api_provider = "openrouter"
        mock_settings.openrouter_api_key = ""
        mock_settings.openrouter_base_url = "https://openrouter.ai/api/v1"
        mock_settings.custom_chat_class = ""

        mock_prov = MagicMock()
        mock_prov.chat_class = "openai"
        mock_prov.env_key = "openrouter_api_key"

        monkeypatch.setattr("onemancompany.agents.base._cfg.settings", mock_settings)
        monkeypatch.setattr("onemancompany.agents.base.employee_configs", {})
        with patch("onemancompany.agents.base.get_provider", return_value=mock_prov):
            llm = make_llm()
        assert llm is not None


class TestTrackedAinvokeBranches:
    """Cover lines 305-306, 318, 368-383 of tracked_ainvoke."""
    @pytest.mark.asyncio
    async def test_usage_metadata_fallback(self):
        """Cover lines 305-306: usage_metadata dict fallback."""
        from onemancompany.agents.base import tracked_ainvoke
        mock_result = MagicMock()
        mock_result.response_metadata = {}
        mock_result.usage_metadata = {"input_tokens": 10, "output_tokens": 5}
        mock_result.content = "Hello"

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_result)

        with patch("onemancompany.agents.base._record_overhead"):
            result = await tracked_ainvoke(mock_llm, "test prompt", category="test")
        assert result is mock_result

    @pytest.mark.asyncio
    async def test_provider_cost_used(self):
        """Cover line 318: provider-reported cost."""
        from onemancompany.agents.base import tracked_ainvoke
        mock_result = MagicMock()
        mock_result.response_metadata = {
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001}
        }
        mock_result.content = "Hello"

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_result)

        with patch("onemancompany.agents.base._record_overhead"):
            result = await tracked_ainvoke(mock_llm, "test", category="test")
        assert result is mock_result

    @pytest.mark.asyncio
    async def test_project_id_writes_trace(self):
        """Cover lines 334-383: project_id trace writing including debug trace."""
        from onemancompany.agents.base import tracked_ainvoke
        mock_result = MagicMock()
        mock_result.response_metadata = {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        mock_result.content = "Hello"

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_result)

        with patch("onemancompany.agents.base._record_overhead"), \
             patch("onemancompany.core.project_archive.record_project_cost"), \
             patch("onemancompany.core.claude_session.write_llm_trace"), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value="/tmp/proj"), \
             patch("onemancompany.core.llm_trace.write_debug_trace_async") as mock_debug, \
             patch("onemancompany.core.agent_loop._current_task_id") as mock_tid:
            mock_tid.get.return_value = "node_1"
            result = await tracked_ainvoke(
                mock_llm, "test", category="test",
                employee_id="00010", project_id="proj_1"
            )
        assert result is mock_result
        mock_debug.assert_called_once()

    @pytest.mark.asyncio
    async def test_project_id_debug_trace_node_id_error(self):
        """Cover lines 368-369: exception resolving node_id."""
        from onemancompany.agents.base import tracked_ainvoke
        mock_result = MagicMock()
        mock_result.response_metadata = {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        mock_result.content = "Hello"

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_result)

        with patch("onemancompany.agents.base._record_overhead"), \
             patch("onemancompany.core.project_archive.record_project_cost"), \
             patch("onemancompany.core.claude_session.write_llm_trace"), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value="/tmp/proj"), \
             patch("onemancompany.core.llm_trace.write_debug_trace_async"), \
             patch("onemancompany.core.agent_loop._current_task_id") as mock_tid:
            mock_tid.get.side_effect = LookupError("no context")
            result = await tracked_ainvoke(
                mock_llm, "test", category="test",
                employee_id="00010", project_id="proj_1"
            )
        assert result is mock_result

    @pytest.mark.asyncio
    async def test_project_id_debug_trace_outer_exception(self):
        """Cover lines 382-383: outer exception in debug trace."""
        from onemancompany.agents.base import tracked_ainvoke
        mock_result = MagicMock()
        mock_result.response_metadata = {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        mock_result.content = "Hello"

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_result)

        with patch("onemancompany.agents.base._record_overhead"), \
             patch("onemancompany.core.project_archive.record_project_cost"), \
             patch("onemancompany.core.claude_session.write_llm_trace"), \
             patch("onemancompany.core.project_archive.get_project_dir", side_effect=RuntimeError("boom")):
            result = await tracked_ainvoke(
                mock_llm, "test", category="test",
                employee_id="00010", project_id="proj_1"
            )
        assert result is mock_result


class TestGetEmployeeSkillsIndex:
    """Cover lines 425-433: get_employee_skills_index."""
    def test_skills_index(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        skills_dir = tmp_path / "00010" / "skills" / "my_skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("---\nname: MySkill\ndescription: Does things\n---\nBody")

        from onemancompany.agents.base import get_employee_skills_index
        index = get_employee_skills_index("00010")
        assert "my_skill" in index
        assert index["my_skill"]["name"] == "MySkill"
        assert index["my_skill"]["description"] == "Does things"


class TestBaseAgentRunnerUsage:
    """Cover lines 657-712, 770-808: usage extraction from streaming and ainvoke results."""
    def test_extract_usage_from_streaming_metadata(self):
        """Cover lines 657-663, 701, 705-712: streaming usage_metadata path."""
        from onemancompany.agents.base import BaseAgentRunner
        from langchain_core.messages import AIMessage

        agent = BaseAgentRunner.__new__(BaseAgentRunner)
        agent.employee_id = "00010"

        # Create AIMessage with usage_metadata
        msg = AIMessage(content="test")
        msg.response_metadata = {}
        msg.usage_metadata = {"input_tokens": 100, "output_tokens": 50}

        result = {"messages": [msg]}
        with patch("onemancompany.agents.base._record_overhead"):
            usage = agent._extract_and_record_usage(result)
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 50

    def test_extract_usage_provider_cost(self):
        """Cover lines 770-792: provider-reported cost path."""
        from onemancompany.agents.base import BaseAgentRunner
        from langchain_core.messages import AIMessage

        agent = BaseAgentRunner.__new__(BaseAgentRunner)
        agent.employee_id = "00010"

        msg = AIMessage(content="test")
        msg.response_metadata = {
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.005},
            "model_name": "gpt-4",
        }

        result = {"messages": [msg]}
        with patch("onemancompany.agents.base._record_overhead"):
            usage = agent._extract_and_record_usage(result)
        assert usage["cost_usd"] == 0.005

    def test_extract_usage_records_overhead(self):
        """Cover line 808: records overhead when tokens > 0."""
        from onemancompany.agents.base import BaseAgentRunner
        from langchain_core.messages import AIMessage

        agent = BaseAgentRunner.__new__(BaseAgentRunner)
        agent.employee_id = "00010"

        msg = AIMessage(content="test")
        msg.response_metadata = {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}

        result = {"messages": [msg]}
        with patch("onemancompany.agents.base._record_overhead") as mock_record:
            agent._extract_and_record_usage(result)
        mock_record.assert_called_once()


class TestWriteDebugTrace:
    """Cover lines 831-871: _write_debug_trace."""
    def test_write_debug_trace_success(self):
        from onemancompany.agents.base import BaseAgentRunner

        agent = BaseAgentRunner.__new__(BaseAgentRunner)
        agent.employee_id = "00010"
        agent._debug_tool_cache = {}

        mock_vessel = MagicMock()
        mock_entry = MagicMock()
        mock_entry.tree_path = "/tmp/tree.yaml"
        mock_entry.node_id = "node_1"
        mock_vessel.manager._current_entries = {"00010": mock_entry}

        mock_tree = MagicMock()
        mock_node = MagicMock()
        mock_node.project_dir = "/tmp/project"
        mock_tree.get_node.return_value = mock_node

        result = {"messages": []}

        with patch("onemancompany.core.agent_loop._current_vessel") as mock_cv, \
             patch("onemancompany.core.task_tree.get_tree", return_value=mock_tree), \
             patch("onemancompany.core.llm_trace.write_debug_trace_async"), \
             patch("onemancompany.core.tool_registry.tool_registry") as mock_reg:
            mock_cv.get.return_value = mock_vessel
            mock_reg.get_proxied_tools_for.return_value = []
            agent._write_debug_trace(result, {"model": "gpt-4", "input_tokens": 10, "output_tokens": 5})

    def test_write_debug_trace_no_vessel(self):
        """Cover line 829: no vessel returns early."""
        from onemancompany.agents.base import BaseAgentRunner

        agent = BaseAgentRunner.__new__(BaseAgentRunner)
        agent.employee_id = "00010"

        with patch("onemancompany.core.agent_loop._current_vessel") as mock_cv:
            mock_cv.get.return_value = None
            agent._write_debug_trace({}, {})

    def test_write_debug_trace_exception(self):
        """Cover line 871: exception caught."""
        from onemancompany.agents.base import BaseAgentRunner

        agent = BaseAgentRunner.__new__(BaseAgentRunner)
        agent.employee_id = "00010"

        with patch("onemancompany.core.agent_loop._current_vessel") as mock_cv:
            mock_cv.get.side_effect = RuntimeError("boom")
            # Should not raise
            agent._write_debug_trace({}, {})


class TestCustomSettings:
    """Cover lines 984-985: custom settings in dynamic context."""
    def test_custom_settings_in_context(self, tmp_path, monkeypatch):
        from onemancompany.agents.base import BaseAgentRunner

        agent = BaseAgentRunner.__new__(BaseAgentRunner)
        agent.employee_id = "00010"

        with patch("onemancompany.core.state.get_active_tasks", return_value=[]), \
             patch("onemancompany.core.config.load_custom_settings", return_value={"target_email": "test@test.com"}), \
             patch("onemancompany.core.state.company_state") as mock_cs:
            mock_cs.employees = {}
            result = agent._get_dynamic_context_section()
        assert "target_email" in result


class TestSoulSection:
    """Cover lines 1020-1031: _get_soul_section."""
    def test_soul_exists(self, tmp_path, monkeypatch):
        import onemancompany.agents.base as base_mod
        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path)
        ws_dir = tmp_path / "00010" / "workspace"
        ws_dir.mkdir(parents=True)
        (ws_dir / "SOUL.md").write_text("I learned that X is important.")

        agent = base_mod.BaseAgentRunner.__new__(base_mod.BaseAgentRunner)
        agent.employee_id = "00010"
        result = agent._get_soul_section()
        assert "I learned that X is important" in result

    def test_soul_read_error(self, tmp_path, monkeypatch):
        """Cover lines 1029-1030: exception reading SOUL.md."""
        import onemancompany.agents.base as base_mod
        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path)
        ws_dir = tmp_path / "00010" / "workspace"
        ws_dir.mkdir(parents=True)
        (ws_dir / "SOUL.md").write_text("content")

        agent = base_mod.BaseAgentRunner.__new__(base_mod.BaseAgentRunner)
        agent.employee_id = "00010"

        with patch("onemancompany.agents.base.read_text_utf", side_effect=OSError("perm")):
            result = agent._get_soul_section()
        assert result == ""


class TestLoadAgentPromptSections:
    """Cover lines 1070, 1078, 1082-1083: _load_agent_prompt_sections."""
    def test_prompt_sections_loaded(self, tmp_path, monkeypatch):
        import onemancompany.agents.base as base_mod
        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path)

        emp_dir = tmp_path / "00010"
        vessel_dir = emp_dir / "vessel"
        vessel_dir.mkdir(parents=True)
        (vessel_dir / "custom.md").write_text("Custom section content")

        agent = base_mod.BaseAgentRunner.__new__(base_mod.BaseAgentRunner)
        agent.employee_id = "00010"

        mock_ps = MagicMock()
        mock_ps.name = "custom"
        mock_ps.file = "custom.md"
        mock_ps.priority = 50

        mock_config = MagicMock()
        mock_config.context.prompt_sections = [mock_ps]

        from onemancompany.agents.base import PromptBuilder
        pb = PromptBuilder()

        with patch("onemancompany.core.vessel_config.load_vessel_config", return_value=mock_config):
            agent._load_agent_prompt_sections(pb)

        assert "custom" in pb._sections

    def test_prompt_sections_missing_file(self, tmp_path, monkeypatch):
        """Cover line 1078: file not found in either directory."""
        import onemancompany.agents.base as base_mod
        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path)

        emp_dir = tmp_path / "00010"
        emp_dir.mkdir(parents=True)

        agent = base_mod.BaseAgentRunner.__new__(base_mod.BaseAgentRunner)
        agent.employee_id = "00010"

        mock_ps = MagicMock()
        mock_ps.name = "missing"
        mock_ps.file = "nonexistent.md"
        mock_ps.priority = 50

        mock_config = MagicMock()
        mock_config.context.prompt_sections = [mock_ps]

        from onemancompany.agents.base import PromptBuilder
        pb = PromptBuilder()

        with patch("onemancompany.core.vessel_config.load_vessel_config", return_value=mock_config):
            agent._load_agent_prompt_sections(pb)

        assert "missing" not in pb._sections

    def test_prompt_sections_skip_empty_name(self, tmp_path, monkeypatch):
        """Cover line 1070: skip sections with empty name."""
        import onemancompany.agents.base as base_mod
        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path)

        emp_dir = tmp_path / "00010"
        emp_dir.mkdir(parents=True)

        agent = base_mod.BaseAgentRunner.__new__(base_mod.BaseAgentRunner)
        agent.employee_id = "00010"

        mock_ps = MagicMock()
        mock_ps.name = ""
        mock_ps.file = "test.md"

        mock_config = MagicMock()
        mock_config.context.prompt_sections = [mock_ps]

        from onemancompany.agents.base import PromptBuilder
        pb = PromptBuilder()

        with patch("onemancompany.core.vessel_config.load_vessel_config", return_value=mock_config):
            agent._load_agent_prompt_sections(pb)

    def test_prompt_sections_read_error(self, tmp_path, monkeypatch):
        """Cover lines 1082-1083: error reading section file."""
        import onemancompany.agents.base as base_mod
        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path)

        emp_dir = tmp_path / "00010"
        vessel_dir = emp_dir / "vessel"
        vessel_dir.mkdir(parents=True)
        (vessel_dir / "broken.md").write_text("content")

        agent = base_mod.BaseAgentRunner.__new__(base_mod.BaseAgentRunner)
        agent.employee_id = "00010"

        mock_ps = MagicMock()
        mock_ps.name = "broken"
        mock_ps.file = "broken.md"
        mock_ps.priority = 50

        mock_config = MagicMock()
        mock_config.context.prompt_sections = [mock_ps]

        from onemancompany.agents.base import PromptBuilder
        pb = PromptBuilder()

        with patch("onemancompany.core.vessel_config.load_vessel_config", return_value=mock_config), \
             patch("onemancompany.agents.base.read_text_utf", side_effect=OSError("read error")):
            agent._load_agent_prompt_sections(pb)


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
