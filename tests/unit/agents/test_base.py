"""Unit tests for agents/base.py — BaseAgentRunner, EmployeeAgent, make_llm, tracked_ainvoke."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.state import CompanyState, Employee, OfficeTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cs() -> CompanyState:
    cs = CompanyState()
    cs._next_employee_number = 100
    return cs


def _make_emp(emp_id: str, **kwargs) -> Employee:
    defaults = dict(
        id=emp_id, name=f"Emp {emp_id}", role="Engineer",
        skills=["python"], employee_number=emp_id, nickname="测试",
    )
    defaults.update(kwargs)
    return Employee(**defaults)


def _emp_to_dict(emp: Employee) -> dict:
    """Convert an Employee object to a dict mirroring store.load_employee() output."""
    return {
        "id": emp.id, "name": emp.name, "role": emp.role,
        "skills": emp.skills, "employee_number": emp.employee_number,
        "nickname": emp.nickname, "level": getattr(emp, "level", 1),
        "department": getattr(emp, "department", ""),
        "tool_permissions": getattr(emp, "tool_permissions", []) or [],
        "guidance_notes": getattr(emp, "guidance_notes", []) or [],
        "runtime": {
            "status": getattr(emp, "status", "idle"),
            "api_online": getattr(emp, "api_online", False),
        },
    }


def _mock_store_for_employees(monkeypatch, employees: dict[str, "Employee"]):
    """Patch store.load_employee and store.load_all_employees to return dicts from Employee objects."""
    from onemancompany.core import store as store_mod
    emp_dicts = {eid: _emp_to_dict(e) for eid, e in employees.items()}
    monkeypatch.setattr(store_mod, "load_employee",
                        lambda eid: emp_dicts.get(eid))
    monkeypatch.setattr(store_mod, "load_all_employees",
                        lambda: dict(emp_dicts))
    # Also mock guidance/culture/direction with defaults
    monkeypatch.setattr(store_mod, "load_employee_guidance",
                        lambda eid: (emp_dicts.get(eid) or {}).get("guidance_notes", []))
    monkeypatch.setattr(store_mod, "load_culture", lambda: [])
    monkeypatch.setattr(store_mod, "load_direction", lambda: "")


# ---------------------------------------------------------------------------
# make_llm
# ---------------------------------------------------------------------------

class TestMakeLlm:
    def test_default_openrouter(self, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.core import config as config_mod

        mock_settings = MagicMock()
        mock_settings.default_llm_model = "gpt-4"
        mock_settings.openrouter_api_key = "test-key"
        mock_settings.openrouter_base_url = "https://openrouter.ai/api/v1"
        monkeypatch.setattr(config_mod, "settings", mock_settings)
        monkeypatch.setattr(base_mod, "employee_configs", {})

        llm = base_mod.make_llm()
        assert llm is not None
        # Should be a ChatOpenAI instance
        assert llm.model_name == "gpt-4"

    def test_employee_specific_model(self, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.core import config as config_mod

        mock_settings = MagicMock()
        mock_settings.default_llm_model = "gpt-4"
        mock_settings.openrouter_api_key = "test-key"
        mock_settings.openrouter_base_url = "https://openrouter.ai/api/v1"
        monkeypatch.setattr(config_mod, "settings", mock_settings)

        cfg = MagicMock()
        cfg.llm_model = "custom-model"
        cfg.temperature = 0.5
        cfg.api_provider = "openrouter"
        cfg.api_key = ""
        monkeypatch.setattr(base_mod, "employee_configs", {"00010": cfg})

        llm = base_mod.make_llm("00010")
        assert llm.model_name == "custom-model"

    def test_non_openrouter_without_key_falls_back(self, monkeypatch):
        """Non-openrouter provider without API key falls back to default model."""
        from onemancompany.agents import base as base_mod
        from onemancompany.core import config as config_mod

        mock_settings = MagicMock()
        mock_settings.default_llm_model = "default-model"
        mock_settings.openrouter_api_key = "test-key"
        mock_settings.openrouter_base_url = "https://openrouter.ai/api/v1"
        mock_settings.anthropic_api_key = ""  # no company-level key either
        monkeypatch.setattr(config_mod, "settings", mock_settings)

        cfg = MagicMock()
        cfg.llm_model = "claude-3"
        cfg.temperature = 0.7
        cfg.api_provider = "anthropic"
        cfg.api_key = ""  # no key
        monkeypatch.setattr(base_mod, "employee_configs", {"00010": cfg})

        llm = base_mod.make_llm("00010")
        # Should fall back to default model via OpenRouter
        assert llm.model_name == "default-model"

    def test_deepseek_provider(self, monkeypatch):
        """DeepSeek provider should use ChatOpenAI with DeepSeek base URL."""
        from onemancompany.agents import base as base_mod
        from onemancompany.core import config as config_mod

        mock_settings = MagicMock()
        mock_settings.default_llm_model = "gpt-4"
        mock_settings.deepseek_api_key = "sk-ds-test"
        monkeypatch.setattr(config_mod, "settings", mock_settings)

        cfg = MagicMock()
        cfg.llm_model = "deepseek-chat"
        cfg.temperature = 0.5
        cfg.api_provider = "deepseek"
        cfg.api_key = ""  # use company-level key
        monkeypatch.setattr(base_mod, "employee_configs", {"00010": cfg})

        llm = base_mod.make_llm("00010")
        assert llm.model_name == "deepseek-chat"
        assert "deepseek" in llm.openai_api_base

    def test_kimi_provider(self, monkeypatch):
        """Kimi provider should use ChatOpenAI with Moonshot base URL."""
        from onemancompany.agents import base as base_mod
        from onemancompany.core import config as config_mod

        mock_settings = MagicMock()
        mock_settings.default_llm_model = "gpt-4"
        mock_settings.kimi_api_key = "sk-kimi-test"
        monkeypatch.setattr(config_mod, "settings", mock_settings)

        cfg = MagicMock()
        cfg.llm_model = "moonshot-v1-8k"
        cfg.temperature = 0.7
        cfg.api_provider = "kimi"
        cfg.api_key = ""
        monkeypatch.setattr(base_mod, "employee_configs", {"00010": cfg})

        llm = base_mod.make_llm("00010")
        assert llm.model_name == "moonshot-v1-8k"
        assert "moonshot" in llm.openai_api_base

    def test_openai_direct_provider(self, monkeypatch):
        """OpenAI direct provider (not via OpenRouter)."""
        from onemancompany.agents import base as base_mod
        from onemancompany.core import config as config_mod

        mock_settings = MagicMock()
        mock_settings.default_llm_model = "gpt-4"
        mock_settings.openai_api_key = "sk-openai-test"
        monkeypatch.setattr(config_mod, "settings", mock_settings)

        cfg = MagicMock()
        cfg.llm_model = "gpt-4o"
        cfg.temperature = 0.3
        cfg.api_provider = "openai"
        cfg.api_key = ""
        monkeypatch.setattr(base_mod, "employee_configs", {"00010": cfg})

        llm = base_mod.make_llm("00010")
        assert llm.model_name == "gpt-4o"
        assert "openai.com" in llm.openai_api_base

    def test_employee_specific_key_overrides_company(self, monkeypatch):
        """Employee's own API key takes priority over company-level key."""
        from onemancompany.agents import base as base_mod
        from onemancompany.core import config as config_mod

        mock_settings = MagicMock()
        mock_settings.default_llm_model = "gpt-4"
        mock_settings.deepseek_api_key = "company-key"
        monkeypatch.setattr(config_mod, "settings", mock_settings)

        cfg = MagicMock()
        cfg.llm_model = "deepseek-chat"
        cfg.temperature = 0.7
        cfg.api_provider = "deepseek"
        cfg.api_key = "employee-key"
        monkeypatch.setattr(base_mod, "employee_configs", {"00010": cfg})

        llm = base_mod.make_llm("00010")
        assert llm.openai_api_key.get_secret_value() == "employee-key"

    def test_unknown_provider_falls_back_to_openrouter(self, monkeypatch):
        """Unknown provider name falls back to openrouter with default model."""
        from onemancompany.agents import base as base_mod
        from onemancompany.core import config as config_mod

        mock_settings = MagicMock()
        mock_settings.default_llm_model = "default-model"
        mock_settings.openrouter_api_key = "or-key"
        mock_settings.openrouter_base_url = "https://openrouter.ai/api/v1"
        monkeypatch.setattr(config_mod, "settings", mock_settings)

        cfg = MagicMock()
        cfg.llm_model = "some-model"
        cfg.temperature = 0.7
        cfg.api_provider = "nonexistent_provider"
        cfg.api_key = ""
        monkeypatch.setattr(base_mod, "employee_configs", {"00010": cfg})

        llm = base_mod.make_llm("00010")
        assert llm.model_name == "default-model"


# ---------------------------------------------------------------------------
# _record_overhead
# ---------------------------------------------------------------------------

class TestRecordOverhead:
    def test_accumulates_cost_record(self, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)

        base_mod._record_overhead(
            "agent_task", "gpt-4", 100, 50, 0.01,
            employee_id="00010", task_id="t1",
        )

        assert cs.overhead_costs.total_cost_usd > 0


# ---------------------------------------------------------------------------
# tracked_ainvoke
# ---------------------------------------------------------------------------

class TestTrackedAinvoke:
    @pytest.mark.asyncio
    async def test_records_token_usage(self, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)

        mock_result = MagicMock()
        mock_result.response_metadata = {
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "model_name": "gpt-4",
        }

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_result)

        monkeypatch.setattr(base_mod, "employee_configs", {})
        mock_settings = MagicMock()
        mock_settings.default_llm_model = "gpt-4"
        monkeypatch.setattr(config_mod, "settings", mock_settings)

        result = await base_mod.tracked_ainvoke(
            mock_llm, "hello", category="test", employee_id="00010",
        )

        assert result is mock_result
        mock_llm.ainvoke.assert_awaited_once_with("hello")
        assert cs.overhead_costs.total_cost_usd >= 0

    @pytest.mark.asyncio
    async def test_records_project_cost(self, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)

        mock_result = MagicMock()
        mock_result.response_metadata = {
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "model_name": "gpt-4",
        }

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_result)

        monkeypatch.setattr(base_mod, "employee_configs", {})
        mock_settings = MagicMock()
        mock_settings.default_llm_model = "gpt-4"
        monkeypatch.setattr(config_mod, "settings", mock_settings)

        mock_record_project_cost = MagicMock()
        monkeypatch.setattr(
            "onemancompany.core.project_archive.record_project_cost",
            mock_record_project_cost,
        )

        await base_mod.tracked_ainvoke(
            mock_llm, "hello", category="test",
            employee_id="00010", project_id="proj1",
        )

        mock_record_project_cost.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_no_usage_metadata(self, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)

        mock_result = MagicMock()
        mock_result.response_metadata = {}

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_result)

        monkeypatch.setattr(base_mod, "employee_configs", {})
        mock_settings = MagicMock()
        mock_settings.default_llm_model = "gpt-4"
        monkeypatch.setattr(config_mod, "settings", mock_settings)

        result = await base_mod.tracked_ainvoke(mock_llm, "hello")
        assert result is mock_result


# ---------------------------------------------------------------------------
# get_employee_skills_prompt
# ---------------------------------------------------------------------------

class TestGetEmployeeSkillsPrompt:
    def test_returns_empty_when_no_skills(self, monkeypatch):
        from onemancompany.agents import base as base_mod

        monkeypatch.setattr(base_mod, "load_employee_skills", lambda eid: {})
        result = base_mod.get_employee_skills_prompt("00010")
        assert result == ""

    def test_builds_skills_index(self, monkeypatch):
        from onemancompany.agents import base as base_mod

        monkeypatch.setattr(
            base_mod, "load_employee_skills",
            lambda eid: {
                "python": "---\nname: python\ndescription: Python expertise\n---\nFull content",
                "js": "---\nname: js\ndescription: JavaScript skill\n---\nJS content",
            },
        )
        result = base_mod.get_employee_skills_prompt("00010")
        assert "Available Skills" in result
        assert "load_skill" in result
        assert "python" in result
        assert "JavaScript skill" in result
        # Full content should NOT be in the index prompt
        assert "Full content" not in result
        assert "JS content" not in result


# ---------------------------------------------------------------------------
# get_employee_tools_prompt
# ---------------------------------------------------------------------------

class TestGetEmployeeToolsPrompt:
    def test_returns_empty_when_no_tools(self, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.tools = {}
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)

        result = base_mod.get_employee_tools_prompt("00010")
        assert result == ""

    def _register_asset_tool(self, monkeypatch, cs, name, description,
                             allowed_users=None, source="asset"):
        """Helper to register an asset tool in both tool_registry and company_state."""
        from onemancompany.core import tool_registry as tr_mod
        from onemancompany.core.tool_registry import ToolMeta
        tool_registry = tr_mod.tool_registry
        from langchain_core.tools import tool as lc_tool

        @lc_tool
        def _dummy(**kwargs) -> str:
            """Dummy."""
            return ""
        _dummy.name = name
        _dummy.description = description

        meta = ToolMeta(name=name, category="asset", allowed_users=allowed_users, source=source)
        tool_registry.register(_dummy, meta)

    def _mock_store_employee(self, monkeypatch, emp_id="00010"):
        """Mock store.load_employee to return a dict for the given employee."""
        from onemancompany.core import store as store_mod
        emp_dict = {"id": emp_id, "name": f"Emp {emp_id}", "role": "Engineer",
                    "skills": ["python"], "tool_permissions": []}
        monkeypatch.setattr(store_mod, "load_employee",
                            lambda eid: emp_dict if eid == emp_id else None)

    def test_includes_open_access_tools(self, monkeypatch):
        from onemancompany.agents import base as base_mod

        self._mock_store_employee(monkeypatch, "00010")
        self._register_asset_tool(monkeypatch, None, "MyTool", "A tool", allowed_users=None)

        result = base_mod.get_employee_tools_prompt("00010")
        assert "MyTool" in result
        assert "A tool" in result

    def test_excludes_talent_tools_for_unauthorized(self, monkeypatch):
        """Talent-brought tools are restricted to allowed_users only."""
        from onemancompany.agents import base as base_mod

        self._mock_store_employee(monkeypatch, "00010")
        self._register_asset_tool(monkeypatch, None, "SecretTool", "Restricted",
                                  allowed_users=["00099"], source="talent")

        result = base_mod.get_employee_tools_prompt("00010")
        assert "SecretTool" not in result

    def test_includes_talent_tools_for_authorized(self, monkeypatch):
        """Talent-brought tools are visible to the bringing employee."""
        from onemancompany.agents import base as base_mod

        self._mock_store_employee(monkeypatch, "00010")
        self._register_asset_tool(monkeypatch, None, "SpecialTool", "Only for 00010",
                                  allowed_users=["00010"], source="talent")

        result = base_mod.get_employee_tools_prompt("00010")
        assert "SpecialTool" in result

    def test_company_asset_tools_available_to_all(self, monkeypatch):
        """Company-provided asset tools (source=asset) are available to everyone."""
        from onemancompany.agents import base as base_mod

        self._mock_store_employee(monkeypatch, "00010")
        self._register_asset_tool(monkeypatch, None, "CompanyTool", "Company provided",
                                  allowed_users=["00099"], source="asset")

        result = base_mod.get_employee_tools_prompt("00010")
        assert "CompanyTool" in result


# ---------------------------------------------------------------------------
# BaseAgentRunner
# ---------------------------------------------------------------------------

class TestBaseAgentRunner:
    def _make_runner(self, monkeypatch, cs=None, emp=None):
        from onemancompany.agents.base import BaseAgentRunner
        from onemancompany.core import state as state_mod
        from onemancompany.agents import base as base_mod

        if cs is None:
            cs = _make_cs()
        if emp is not None:
            _mock_store_for_employees(monkeypatch, {emp.id: emp})
        else:
            _mock_store_for_employees(monkeypatch, {})
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)

        runner = BaseAgentRunner()
        runner.employee_id = emp.id if emp else ""
        runner.role = "TestAgent"
        return runner, cs

    def test_set_status(self, monkeypatch):
        emp = _make_emp("00010")
        runner, cs = self._make_runner(monkeypatch, emp=emp)

        # _set_status is a no-op now (runtime persisted via store)
        runner._set_status("working")  # should not raise

    def test_set_status_no_employee(self, monkeypatch):
        runner, cs = self._make_runner(monkeypatch)
        runner.employee_id = "nonexistent"
        # Should not raise
        runner._set_status("working")

    @pytest.mark.asyncio
    async def test_publish(self, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.core import events as events_mod

        mock_publish = AsyncMock()
        monkeypatch.setattr(events_mod, "event_bus", MagicMock(publish=mock_publish))
        monkeypatch.setattr(base_mod, "event_bus", MagicMock(publish=mock_publish))

        emp = _make_emp("00010")
        runner, _ = self._make_runner(monkeypatch, emp=emp)

        await runner._publish("agent_thinking", {"message": "test"})
        mock_publish.assert_awaited_once()
        event = mock_publish.call_args[0][0]
        assert event.type == "agent_thinking"
        assert event.agent == "TestAgent"

    # NOTE: guidance, culture, and talent_persona tests removed — these sections
    # are now injected via _build_company_context_block() in vessel.py.
    # See tests/unit/core/test_company_context_injection.py for coverage.

    def test_get_dynamic_context_section(self, monkeypatch):
        cs = _make_cs()
        emp1 = _make_emp("00010")
        emp2 = _make_emp("00020", name="Alice", nickname="A", role="Designer", level=2)
        _mock_store_for_employees(monkeypatch, {"00010": emp1, "00020": emp2})

        from onemancompany.core import state as state_mod
        from onemancompany.agents import base as base_mod
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)

        from onemancompany.agents.base import BaseAgentRunner
        runner = BaseAgentRunner()
        runner.employee_id = "00010"

        result = runner._get_dynamic_context_section()
        assert "Current Context" in result
        assert "Alice" in result  # should show colleague
        assert "00010" not in result or "Team" in result  # self not in team list

    def test_get_company_direction_section_empty(self, monkeypatch):
        runner, cs = self._make_runner(monkeypatch, emp=_make_emp("00010"))
        # Default mock returns "" for direction

        result = runner._get_company_direction_section()
        assert result == ""

    def test_get_company_direction_section(self, monkeypatch):
        runner, cs = self._make_runner(monkeypatch, emp=_make_emp("00010"))
        from onemancompany.core import store as store_mod
        monkeypatch.setattr(store_mod, "load_direction",
                            lambda: "Build the best AI company")

        result = runner._get_company_direction_section()
        assert "Company Direction" in result
        assert "Build the best AI company" in result

    def test_build_prompt_builder(self, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.core import config as config_mod

        emp = _make_emp("00010")
        runner, cs = self._make_runner(monkeypatch, emp=emp)

        # Mock file loading to avoid filesystem access
        monkeypatch.setattr(base_mod, "load_employee_skills", lambda eid: {})
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", Path("/nonexistent"))
        monkeypatch.setattr(config_mod, "SHARED_PROMPTS_DIR", Path("/nonexistent"))
        monkeypatch.setattr(runner, "_load_prompt_file", lambda f: None)
        monkeypatch.setattr(runner.__class__, "_load_shared_prompt", staticmethod(lambda f: None))

        pb = runner._build_prompt_builder()
        # Should have context and efficiency sections at minimum
        names = pb.section_names()
        assert "context" in names
        assert "efficiency" in names

    def test_load_agent_prompt_sections(self, tmp_path, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.agents.base import BaseAgentRunner

        # Create vessel config with prompt sections
        vessel_dir = tmp_path / "emp" / "00010" / "vessel"
        vessel_dir.mkdir(parents=True)

        vessel_cfg = {"context": {"prompt_sections": [
            {"name": "custom", "file": "custom.md", "priority": 25},
        ]}}
        import yaml
        (vessel_dir / "vessel.yaml").write_text(yaml.dump(vessel_cfg))
        (vessel_dir / "custom.md").write_text("Custom section content")

        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path / "emp")

        runner = BaseAgentRunner()
        runner.employee_id = "00010"

        from onemancompany.agents.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        runner._load_agent_prompt_sections(pb)

        assert pb.has("custom")
        assert "Custom section content" in pb.get("custom")

    def test_load_agent_prompt_sections_no_manifest(self, tmp_path, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.agents.base import BaseAgentRunner

        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path / "emp")

        runner = BaseAgentRunner()
        runner.employee_id = "00010"

        from onemancompany.agents.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        runner._load_agent_prompt_sections(pb)
        # No sections added
        assert pb.section_names() == []

    def test_get_efficiency_guidelines_section_default(self, monkeypatch):
        from onemancompany.agents import base as base_mod

        emp = _make_emp("00010")
        runner, _ = self._make_runner(monkeypatch, emp=emp)
        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", Path("/nonexistent"))
        monkeypatch.setattr(base_mod, "SHARED_PROMPTS_DIR", Path("/nonexistent"))

        result = runner._get_efficiency_guidelines_section()
        assert "Efficiency Rules" in result

    def test_get_efficiency_guidelines_from_file(self, tmp_path, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.agents.base import BaseAgentRunner

        # Create employee prompts dir with efficiency.md
        emp_dir = tmp_path / "emp" / "00010" / "prompts"
        emp_dir.mkdir(parents=True)
        (emp_dir / "efficiency.md").write_text("Custom efficiency rules")

        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path / "emp")

        runner = BaseAgentRunner()
        runner.employee_id = "00010"

        result = runner._get_efficiency_guidelines_section()
        assert "Custom efficiency rules" in result

    def test_load_prompt_file(self, tmp_path, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.agents.base import BaseAgentRunner

        emp_dir = tmp_path / "emp" / "00010" / "prompts"
        emp_dir.mkdir(parents=True)
        (emp_dir / "role.md").write_text("You are a great engineer")

        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path / "emp")

        runner = BaseAgentRunner()
        runner.employee_id = "00010"

        result = runner._load_prompt_file("role.md")
        assert result == "You are a great engineer"

    def test_load_prompt_file_not_found(self, tmp_path, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.agents.base import BaseAgentRunner

        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path / "emp")

        runner = BaseAgentRunner()
        runner.employee_id = "00010"

        result = runner._load_prompt_file("nonexistent.md")
        assert result is None

    def test_load_shared_prompt(self, tmp_path, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.agents.base import BaseAgentRunner

        shared_dir = tmp_path / "shared"
        shared_dir.mkdir()
        (shared_dir / "work_approach.md").write_text("Shared approach")

        monkeypatch.setattr(base_mod, "SHARED_PROMPTS_DIR", shared_dir)

        result = BaseAgentRunner._load_shared_prompt("work_approach.md")
        assert result == "Shared approach"

    def test_get_model_name(self, monkeypatch):
        from onemancompany.agents.base import BaseAgentRunner
        from onemancompany.agents import base as base_mod

        cfg = MagicMock()
        cfg.llm_model = "custom-model"
        monkeypatch.setattr(base_mod, "employee_configs", {"00010": cfg})

        runner = BaseAgentRunner()
        runner.employee_id = "00010"

        assert runner._get_model_name() == "custom-model"

    def test_get_model_name_fallback(self, monkeypatch):
        from onemancompany.agents.base import BaseAgentRunner
        from onemancompany.agents import base as base_mod
        from onemancompany.core import config as config_mod

        monkeypatch.setattr(base_mod, "employee_configs", {})
        mock_settings = MagicMock()
        mock_settings.default_llm_model = "default-model"
        monkeypatch.setattr(config_mod, "settings", mock_settings)

        runner = BaseAgentRunner()
        runner.employee_id = "nonexistent"

        assert runner._get_model_name() == "default-model"


# ---------------------------------------------------------------------------
# EmployeeAgent
# ---------------------------------------------------------------------------

class TestEmployeeAgent:
    def _setup(self, monkeypatch, emp_id="00010", **emp_kwargs):
        from onemancompany.core import state as state_mod
        from onemancompany.agents import base as base_mod, common_tools as ct_mod
        from onemancompany.core import config as config_mod
        from onemancompany.core import tool_registry as tr_mod

        cs = _make_cs()
        emp = _make_emp(emp_id, **emp_kwargs)
        _mock_store_for_employees(monkeypatch, {emp_id: emp})
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        monkeypatch.setattr(base_mod, "load_employee_skills", lambda eid: {})
        # Mock tool_registry to return empty tools
        mock_registry = MagicMock()
        mock_registry.get_proxied_tools_for = MagicMock(return_value=[])
        mock_registry.all_tool_names = MagicMock(return_value=[])
        mock_registry.get_meta = MagicMock(return_value=None)
        monkeypatch.setattr(tr_mod, "tool_registry", mock_registry)
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", Path("/nonexistent"))
        monkeypatch.setattr(config_mod, "SHARED_PROMPTS_DIR", Path("/nonexistent"))

        # Mock create_react_agent and make_llm to avoid LLM calls
        monkeypatch.setattr(base_mod, "make_llm", lambda eid: MagicMock())
        mock_agent = MagicMock()
        monkeypatch.setattr(
            "onemancompany.agents.base.create_react_agent",
            lambda model, tools: mock_agent,
        )
        return cs, emp, mock_agent

    def test_init_sets_role(self, monkeypatch):
        self._setup(monkeypatch, emp_id="00010", role="Designer")

        from onemancompany.agents.base import EmployeeAgent
        agent = EmployeeAgent("00010")

        assert agent.role == "Designer"
        assert agent.employee_id == "00010"

    def test_init_fallback_role(self, monkeypatch):
        """When employee not in state, role defaults to 'Employee'."""
        from onemancompany.core import state as state_mod
        from onemancompany.agents import base as base_mod
        from onemancompany.core import tool_registry as tr_mod

        cs = _make_cs()
        _mock_store_for_employees(monkeypatch, {})
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)
        mock_registry = MagicMock()
        mock_registry.get_proxied_tools_for = MagicMock(return_value=[])
        mock_registry.all_tool_names = MagicMock(return_value=[])
        mock_registry.get_meta = MagicMock(return_value=None)
        monkeypatch.setattr(tr_mod, "tool_registry", mock_registry)
        monkeypatch.setattr(base_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(
            "onemancompany.agents.base.create_react_agent",
            lambda model, tools: MagicMock(),
        )

        from onemancompany.agents.base import EmployeeAgent
        agent = EmployeeAgent("nonexistent")
        assert agent.role == "Employee"

    def test_build_prompt_no_employee(self, monkeypatch):
        from onemancompany.core import state as state_mod
        from onemancompany.agents import base as base_mod
        from onemancompany.core import tool_registry as tr_mod

        cs = _make_cs()
        _mock_store_for_employees(monkeypatch, {})
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)
        mock_registry = MagicMock()
        mock_registry.get_proxied_tools_for = MagicMock(return_value=[])
        mock_registry.all_tool_names = MagicMock(return_value=[])
        mock_registry.get_meta = MagicMock(return_value=None)
        monkeypatch.setattr(tr_mod, "tool_registry", mock_registry)
        monkeypatch.setattr(base_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(
            "onemancompany.agents.base.create_react_agent",
            lambda model, tools: MagicMock(),
        )

        from onemancompany.agents.base import EmployeeAgent
        agent = EmployeeAgent("nonexistent")
        prompt = agent._build_prompt()
        assert prompt == "You are a company employee."

    def test_build_prompt_with_employee(self, monkeypatch):
        self._setup(monkeypatch, emp_id="00010", name="Alice", nickname="凌霄",
                     role="Engineer", department="Engineering", level=2)

        from onemancompany.agents.base import EmployeeAgent
        agent = EmployeeAgent("00010")
        prompt = agent._build_prompt()

        assert "Alice" in prompt
        assert "凌霄" in prompt
        assert "Engineer" in prompt

    def test_unauthorized_tools_section_always_empty(self, monkeypatch):
        """All company tools are now available to all employees — no gated concept."""
        self._setup(monkeypatch, emp_id="00010", tool_permissions=[])

        from onemancompany.agents.base import EmployeeAgent
        agent = EmployeeAgent("00010")

        section = agent._get_unauthorized_tools_section()
        assert section == ""

    def test_all_tools_included_for_employee(self, monkeypatch):
        """All tools (formerly gated or not) are available to every employee."""
        self._setup(monkeypatch, emp_id="00010", tool_permissions=[])

        from onemancompany.core import tool_registry as tr_mod
        from onemancompany.core.tool_registry import ToolMeta

        mock_tool = MagicMock()
        mock_tool.name = "bash"
        mock_registry = MagicMock()
        mock_registry.get_proxied_tools_for = MagicMock(return_value=[mock_tool])
        monkeypatch.setattr(tr_mod, "tool_registry", mock_registry)

        from onemancompany.agents.base import EmployeeAgent
        agent = EmployeeAgent("00010")

        assert "bash" in agent._authorized_tool_names

    @pytest.mark.asyncio
    async def test_run(self, monkeypatch):
        from onemancompany.core import state as state_mod, events as events_mod
        from onemancompany.agents import base as base_mod

        cs, emp, mock_agent = self._setup(monkeypatch)

        # Mock agent loop context
        monkeypatch.setattr(
            "onemancompany.core.agent_loop._current_vessel",
            MagicMock(get=lambda x=None: None),
        )

        final_msg = MagicMock()
        final_msg.content = "Task completed successfully"
        mock_agent.ainvoke = AsyncMock(return_value={"messages": [final_msg]})

        mock_publish = AsyncMock()
        monkeypatch.setattr(events_mod, "event_bus", MagicMock(publish=mock_publish))
        monkeypatch.setattr(base_mod, "event_bus", MagicMock(publish=mock_publish))

        from onemancompany.agents.base import EmployeeAgent
        agent = EmployeeAgent("00010")

        result = await agent.run("Do something")
        assert result == "Task completed successfully"
        # _set_status is a no-op now; status persisted via store

    @pytest.mark.asyncio
    async def test_run_streamed_fallback(self, monkeypatch):
        """When on_log is None, run_streamed falls back to run()."""
        from onemancompany.core import state as state_mod, events as events_mod
        from onemancompany.agents import base as base_mod

        cs, emp, mock_agent = self._setup(monkeypatch)

        monkeypatch.setattr(
            "onemancompany.core.agent_loop._current_vessel",
            MagicMock(get=lambda x=None: None),
        )

        final_msg = MagicMock()
        final_msg.content = "Done"
        mock_agent.ainvoke = AsyncMock(return_value={"messages": [final_msg]})

        mock_publish = AsyncMock()
        monkeypatch.setattr(events_mod, "event_bus", MagicMock(publish=mock_publish))
        monkeypatch.setattr(base_mod, "event_bus", MagicMock(publish=mock_publish))

        from onemancompany.agents.base import EmployeeAgent
        agent = EmployeeAgent("00010")

        result = await agent.run_streamed("Do something", on_log=None)
        assert result == "Done"

    def test_build_prompt_with_role_file(self, monkeypatch, tmp_path):
        """When employee has a role.md prompt file, uses that as header."""
        from onemancompany.agents import base as base_mod
        from onemancompany.core import config as config_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import tool_registry as tr_mod

        cs = _make_cs()
        emp = _make_emp(
            "00010", name="Alice", nickname="凌霄",
            role="Engineer", department="Engineering", level=2,
        )
        _mock_store_for_employees(monkeypatch, {"00010": emp})
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)

        # Set up employees dir with role.md
        emp_dir = tmp_path / "emp" / "00010" / "prompts"
        emp_dir.mkdir(parents=True)
        (emp_dir / "role.md").write_text("You are {name} (nickname: {nickname}), Lv.{level}")

        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path / "emp")
        monkeypatch.setattr(base_mod, "SHARED_PROMPTS_DIR", Path("/nonexistent"))
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "emp")
        monkeypatch.setattr(config_mod, "SHARED_PROMPTS_DIR", Path("/nonexistent"))
        monkeypatch.setattr(base_mod, "load_employee_skills", lambda eid: {})
        mock_registry = MagicMock()
        mock_registry.get_proxied_tools_for = MagicMock(return_value=[])
        mock_registry.all_tool_names = MagicMock(return_value=[])
        mock_registry.get_meta = MagicMock(return_value=None)
        monkeypatch.setattr(tr_mod, "tool_registry", mock_registry)
        monkeypatch.setattr(base_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(
            "onemancompany.agents.base.create_react_agent",
            lambda model, tools: MagicMock(),
        )

        from onemancompany.agents.base import EmployeeAgent
        agent = EmployeeAgent("00010")
        prompt = agent._build_prompt()
        assert "Alice" in prompt
        assert "凌霄" in prompt
        assert "Lv.2" in prompt


# ---------------------------------------------------------------------------
# get_employee_talent_persona
# ---------------------------------------------------------------------------

class TestGetEmployeeTalentPersona:
    def test_returns_empty_when_no_file(self, monkeypatch, tmp_path):
        from onemancompany.agents import base as base_mod

        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path / "emp")
        result = base_mod.get_employee_talent_persona("00010")
        assert result == ""

    def test_returns_content_when_file_exists(self, monkeypatch, tmp_path):
        from onemancompany.agents import base as base_mod

        prompts_dir = tmp_path / "emp" / "00010" / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "talent_persona.md").write_text("You are a senior PM.")

        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path / "emp")
        result = base_mod.get_employee_talent_persona("00010")
        assert "You are a senior PM." in result
        assert result.startswith("\n")

    def test_returns_empty_when_file_is_blank(self, monkeypatch, tmp_path):
        from onemancompany.agents import base as base_mod

        prompts_dir = tmp_path / "emp" / "00010" / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "talent_persona.md").write_text("   ")

        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path / "emp")
        result = base_mod.get_employee_talent_persona("00010")
        assert result == ""


# ---------------------------------------------------------------------------
# make_llm anthropic provider
# ---------------------------------------------------------------------------

class TestMakeLlmAnthropic:
    def test_anthropic_with_api_key(self, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.core import config as config_mod

        mock_settings = MagicMock()
        mock_settings.default_llm_model = "gpt-4"
        mock_settings.openrouter_api_key = "test-key"
        mock_settings.openrouter_base_url = "https://openrouter.ai/api/v1"
        monkeypatch.setattr(config_mod, "settings", mock_settings)

        cfg = MagicMock()
        cfg.llm_model = "claude-sonnet-4-20250514"
        cfg.temperature = 0.5
        cfg.api_provider = "anthropic"
        cfg.api_key = "sk-ant-test-key-123"
        cfg.auth_method = "api_key"
        monkeypatch.setattr(base_mod, "employee_configs", {"00010": cfg})

        # Mock ChatAnthropic
        mock_chat_anthropic = MagicMock()
        monkeypatch.setattr(
            "onemancompany.agents.base.ChatAnthropic",
            mock_chat_anthropic,
            raising=False,
        )
        # We need to mock the import inside make_llm
        import importlib
        mock_module = MagicMock()
        mock_module.ChatAnthropic = mock_chat_anthropic
        monkeypatch.setitem(__import__('sys').modules, 'langchain_anthropic', mock_module)

        llm = base_mod.make_llm("00010")
        mock_chat_anthropic.assert_called_once()
        call_kwargs = mock_chat_anthropic.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert call_kwargs["api_key"] == "sk-ant-test-key-123"

    def test_anthropic_oauth_sets_beta_header(self, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.core import config as config_mod

        mock_settings = MagicMock()
        mock_settings.default_llm_model = "gpt-4"
        monkeypatch.setattr(config_mod, "settings", mock_settings)

        cfg = MagicMock()
        cfg.llm_model = "claude-sonnet-4-20250514"
        cfg.temperature = 0.5
        cfg.api_provider = "anthropic"
        cfg.api_key = "sk-ant-oat-token-123"
        cfg.auth_method = "oauth"
        monkeypatch.setattr(base_mod, "employee_configs", {"00010": cfg})

        mock_chat_anthropic = MagicMock()
        mock_module = MagicMock()
        mock_module.ChatAnthropic = mock_chat_anthropic
        monkeypatch.setitem(__import__('sys').modules, 'langchain_anthropic', mock_module)

        base_mod.make_llm("00010")
        call_kwargs = mock_chat_anthropic.call_args[1]
        assert call_kwargs["default_headers"] is not None
        assert "anthropic-beta" in call_kwargs["default_headers"]


# NOTE: TestGetTalentPersonaSection removed — talent persona is now loaded
# via _build_company_context_block() in vessel.py.
# See tests/unit/core/test_company_context_injection.py::TestTalentPersonaLoading.

# ---------------------------------------------------------------------------
# BaseAgentRunner._build_full_prompt with agent loop context
# ---------------------------------------------------------------------------

class TestBuildFullPrompt:
    def test_appends_history_context(self, monkeypatch):
        from onemancompany.agents.base import BaseAgentRunner
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)

        runner = BaseAgentRunner()
        runner.employee_id = ""
        runner._build_prompt = lambda: "base prompt"

        mock_loop = MagicMock()
        mock_loop.get_history_context.return_value = "\n## History\nPrevious tasks..."
        monkeypatch.setattr(
            "onemancompany.core.agent_loop._current_vessel",
            MagicMock(get=lambda x=None: mock_loop),
        )

        result = runner._build_full_prompt()
        assert "base prompt" in result
        assert "History" in result

    def test_no_loop_returns_plain_prompt(self, monkeypatch):
        from onemancompany.agents.base import BaseAgentRunner
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)

        runner = BaseAgentRunner()
        runner.employee_id = ""
        runner._build_prompt = lambda: "simple prompt"

        monkeypatch.setattr(
            "onemancompany.core.agent_loop._current_vessel",
            MagicMock(get=lambda x=None: None),
        )

        result = runner._build_full_prompt()
        assert result == "simple prompt"


# ---------------------------------------------------------------------------
# BaseAgentRunner.run_streamed (full streaming path)
# ---------------------------------------------------------------------------

class TestRunStreamed:
    @pytest.mark.asyncio
    async def test_run_streamed_full_path(self, monkeypatch):
        from onemancompany.agents.base import BaseAgentRunner
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod, events as events_mod

        cs = _make_cs()
        emp = _make_emp("00010")
        _mock_store_for_employees(monkeypatch, {"00010": emp})
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)

        mock_publish = AsyncMock()
        monkeypatch.setattr(base_mod, "event_bus", MagicMock(publish=mock_publish))

        monkeypatch.setattr(
            "onemancompany.core.agent_loop._current_vessel",
            MagicMock(get=lambda x=None: None),
        )

        # Build mock events for astream_events
        mock_output = MagicMock()
        mock_output.content = "Final answer"
        mock_output.tool_calls = None
        mock_output.response_metadata = {
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model_name": "test-model",
        }

        events = [
            {"event": "on_chat_model_start", "data": {"input": [MagicMock(content="Hello")]}},
            {"event": "on_chat_model_end", "data": {"output": mock_output}},
            {"event": "on_tool_end", "data": {"output": "tool result"}, "name": "my_tool"},
        ]

        mock_agent = MagicMock()

        async def fake_astream_events(messages_input, version, config):
            for e in events:
                yield e

        mock_agent.astream_events = fake_astream_events

        runner = BaseAgentRunner()
        runner.employee_id = "00010"
        runner.role = "TestAgent"
        runner._agent = mock_agent
        runner._build_prompt = lambda: "test prompt"

        log_calls = []
        def on_log(kind, content):
            log_calls.append((kind, content))

        result = await runner.run_streamed("Do task", on_log=on_log)
        assert result == "Final answer"
        # Verify logs were called
        assert any(k == "llm_output" for k, _ in log_calls)
        assert any(k == "tool_result" for k, _ in log_calls)
        # _set_status is a no-op now; status persisted via store
        # Should have recorded usage
        assert runner._last_usage["input_tokens"] == 10
        assert runner._last_usage["output_tokens"] == 5

    @pytest.mark.asyncio
    async def test_run_streamed_with_tool_calls(self, monkeypatch):
        from onemancompany.agents.base import BaseAgentRunner
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp = _make_emp("00010")
        _mock_store_for_employees(monkeypatch, {"00010": emp})
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "event_bus", MagicMock(publish=AsyncMock()))
        monkeypatch.setattr(
            "onemancompany.core.agent_loop._current_vessel",
            MagicMock(get=lambda x=None: None),
        )

        mock_output = MagicMock()
        mock_output.content = "Using tool"
        mock_output.tool_calls = [{"name": "search", "args": {"q": "test"}}]
        mock_output.response_metadata = {"usage": {"prompt_tokens": 5, "completion_tokens": 3}}

        events = [
            {"event": "on_chat_model_end", "data": {"output": mock_output}},
        ]

        mock_agent = MagicMock()
        async def fake_astream(msg, version, config):
            for e in events:
                yield e
        mock_agent.astream_events = fake_astream

        runner = BaseAgentRunner()
        runner.employee_id = "00010"
        runner.role = "Agent"
        runner._agent = mock_agent
        runner._build_prompt = lambda: ""

        logs = []
        await runner.run_streamed("task", on_log=lambda k, c: logs.append((k, c)))
        assert any(k == "tool_call" for k, _ in logs)


# ---------------------------------------------------------------------------
# get_employee_tools_prompt — file content and binary file handling
# ---------------------------------------------------------------------------

class TestGetEmployeeToolsPromptWithFiles:
    def _setup_asset_tool(self, monkeypatch, cs, name, description,
                          folder_name, files=None, allowed_users=None):
        from onemancompany.core import tool_registry as tr_mod
        from onemancompany.core.tool_registry import ToolMeta
        tool_registry = tr_mod.tool_registry
        from onemancompany.core.state import OfficeTool
        from langchain_core.tools import tool as lc_tool

        @lc_tool
        def _dummy(**kwargs) -> str:
            """Dummy."""
            return ""
        _dummy.name = name
        _dummy.description = description

        meta = ToolMeta(name=name, category="asset", allowed_users=allowed_users, source="asset")
        tool_registry.register(_dummy, meta)

        cs.tools[name] = OfficeTool(
            id=name, name=name, description=description,
            added_by="COO", allowed_users=allowed_users,
            folder_name=folder_name, files=files or [],
        )

    def test_includes_file_contents(self, monkeypatch, tmp_path):
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod, config as config_mod

        cs = _make_cs()
        _mock_store_for_employees(monkeypatch, {"00010": _make_emp("00010")})
        tool_folder = tmp_path / "tools" / "my_tool"
        tool_folder.mkdir(parents=True)
        (tool_folder / "readme.md").write_text("Tool readme content")

        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tmp_path / "tools")
        self._setup_asset_tool(monkeypatch, cs, "MyTool", "A tool with files",
                               folder_name="my_tool", files=["readme.md"])

        result = base_mod.get_employee_tools_prompt("00010")
        assert "Tool readme content" in result

    def test_handles_binary_files(self, monkeypatch, tmp_path):
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod, config as config_mod

        cs = _make_cs()
        _mock_store_for_employees(monkeypatch, {"00010": _make_emp("00010")})
        tool_folder = tmp_path / "tools" / "my_tool"
        tool_folder.mkdir(parents=True)
        (tool_folder / "data.bin").write_bytes(b"\x80\x81\x82\x83")

        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tmp_path / "tools")
        self._setup_asset_tool(monkeypatch, cs, "BinTool", "Tool with binary",
                               folder_name="my_tool", files=["data.bin"])

        result = base_mod.get_employee_tools_prompt("00010")
        assert "[binary" in result


# ---------------------------------------------------------------------------
# _load_agent_prompt_sections edge cases
# ---------------------------------------------------------------------------

class TestLoadAgentPromptSectionsEdgeCases:
    def test_skips_section_with_missing_name_or_file(self, tmp_path, monkeypatch):
        import yaml as yaml_mod
        from onemancompany.agents import base as base_mod
        from onemancompany.agents.base import BaseAgentRunner
        from onemancompany.agents.prompt_builder import PromptBuilder

        agent_dir = tmp_path / "emp" / "00010" / "agent"
        agent_dir.mkdir(parents=True)

        manifest = {"prompt_sections": [
            {"name": "", "file": "custom.md", "priority": 25},
            {"name": "valid", "file": "", "priority": 25},
        ]}
        (agent_dir / "manifest.yaml").write_text(yaml_mod.dump(manifest))

        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path / "emp")

        runner = BaseAgentRunner()
        runner.employee_id = "00010"
        pb = PromptBuilder()
        runner._load_agent_prompt_sections(pb)
        assert pb.section_names() == []

    def test_skips_section_with_missing_content_file(self, tmp_path, monkeypatch):
        import yaml as yaml_mod
        from onemancompany.agents import base as base_mod
        from onemancompany.agents.base import BaseAgentRunner
        from onemancompany.agents.prompt_builder import PromptBuilder

        agent_dir = tmp_path / "emp" / "00010" / "agent"
        agent_dir.mkdir(parents=True)

        manifest = {"prompt_sections": [
            {"name": "missing_file", "file": "nonexistent.md", "priority": 25},
        ]}
        (agent_dir / "manifest.yaml").write_text(yaml_mod.dump(manifest))

        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path / "emp")

        runner = BaseAgentRunner()
        runner.employee_id = "00010"
        pb = PromptBuilder()
        runner._load_agent_prompt_sections(pb)
        assert not pb.has("missing_file")

    def test_handles_broken_manifest_yaml(self, tmp_path, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.agents.base import BaseAgentRunner
        from onemancompany.agents.prompt_builder import PromptBuilder

        agent_dir = tmp_path / "emp" / "00010" / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "manifest.yaml").write_text(": bad: yaml: {{}")

        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path / "emp")

        runner = BaseAgentRunner()
        runner.employee_id = "00010"
        pb = PromptBuilder()
        runner._load_agent_prompt_sections(pb)
        assert pb.section_names() == []


# ---------------------------------------------------------------------------
# EmployeeAgent._get_unauthorized_tools_section edge case
# ---------------------------------------------------------------------------

class TestUnauthorizedToolsSectionEmpty:
    def test_returns_empty_when_no_unauthorized_tools(self, monkeypatch):
        from onemancompany.agents.base import EmployeeAgent
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import tool_registry as tr_mod
        from onemancompany.core.tool_registry import ToolMeta

        cs = _make_cs()
        emp = _make_emp("00010", tool_permissions=["some_tool"])
        _mock_store_for_employees(monkeypatch, {"00010": emp})
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "load_employee_skills", lambda eid: {})

        # Mock tool_registry: all tools are returned (none unauthorized)
        mock_tool = MagicMock()
        mock_tool.name = "some_tool"
        mock_registry = MagicMock()
        mock_registry.get_proxied_tools_for = MagicMock(return_value=[mock_tool])
        mock_registry.all_tool_names = MagicMock(return_value=["some_tool"])
        mock_registry.get_meta = MagicMock(return_value=ToolMeta(name="some_tool", category="base"))
        monkeypatch.setattr(tr_mod, "tool_registry", mock_registry)
        monkeypatch.setattr(base_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(
            "onemancompany.agents.base.create_react_agent",
            lambda model, tools: MagicMock(),
        )

        agent = EmployeeAgent("00010")
        section = agent._get_unauthorized_tools_section()
        assert section == ""


# ---------------------------------------------------------------------------
# BaseAgentRunner._build_prompt default (line 335)
# ---------------------------------------------------------------------------

class TestBuildPromptDefault:
    def test_base_build_prompt_uses_builder(self, monkeypatch):
        """BaseAgentRunner._build_prompt() uses PromptBuilder with standard sections."""
        from onemancompany.agents.base import BaseAgentRunner
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)

        runner = BaseAgentRunner()
        runner.employee_id = ""
        result = runner._build_prompt()
        # PromptBuilder includes standard sections (context, efficiency, etc.)
        assert isinstance(result, str)
        assert "Current Context" in result or "Efficiency" in result


# ---------------------------------------------------------------------------
# BaseAgentRunner._get_dynamic_context_section with active_tasks (lines 424-427)
# ---------------------------------------------------------------------------

class TestDynamicContextWithActiveTasks:
    def test_active_tasks_included_in_context(self, monkeypatch):
        """Lines 424-427: active_tasks list appears in dynamic context section."""
        from onemancompany.agents.base import BaseAgentRunner
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core.state import TaskEntry

        cs = _make_cs()
        emp = _make_emp("00010")
        _mock_store_for_employees(monkeypatch, {"00010": emp})
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)

        # Mock get_active_tasks (disk-based) to return test tasks
        mock_tasks = [
            TaskEntry(project_id="proj1", task="Build the login page feature", routed_to="COO"),
            TaskEntry(project_id="proj2", task="Design the dashboard UI", routed_to="HR"),
        ]
        monkeypatch.setattr(state_mod, "get_active_tasks", lambda: mock_tasks)

        runner = BaseAgentRunner()
        runner.employee_id = "00010"

        result = runner._get_dynamic_context_section()
        assert "Active tasks" in result
        assert "COO" in result
        assert "Build the login page" in result
        assert "HR" in result
        assert "Design the dashboard" in result


# ---------------------------------------------------------------------------
# BaseAgentRunner._load_manifest_prompt_sections exception (lines 507-508)
# ---------------------------------------------------------------------------

class TestLoadManifestPromptSectionsException:
    def test_read_exception_silently_ignored(self, tmp_path, monkeypatch):
        """Lines 507-508: Exception reading content file is silently ignored."""
        import yaml as yaml_mod
        from onemancompany.agents import base as base_mod
        from onemancompany.agents.base import BaseAgentRunner
        from onemancompany.agents.prompt_builder import PromptBuilder

        agent_dir = tmp_path / "emp" / "00010" / "agent"
        agent_dir.mkdir(parents=True)

        manifest = {"prompt_sections": [
            {"name": "broken", "file": "broken.md", "priority": 25},
        ]}
        (agent_dir / "manifest.yaml").write_text(yaml_mod.dump(manifest))
        # Create the file as a directory so read_text() will raise
        (agent_dir / "broken.md").mkdir()

        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", tmp_path / "emp")

        runner = BaseAgentRunner()
        runner.employee_id = "00010"
        pb = PromptBuilder()
        runner._load_agent_prompt_sections(pb)
        # The broken section should not be added
        assert not pb.has("broken")


# ---------------------------------------------------------------------------
# on_log structured tool call output
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_streamed_sends_structured_tool_call(monkeypatch):
    """run_streamed should send dict (not string) for tool_call and tool_result."""
    from onemancompany.agents.base import BaseAgentRunner
    from langchain_core.messages import AIMessage, ToolMessage

    # Create a minimal executor
    executor = BaseAgentRunner.__new__(BaseAgentRunner)
    executor.employee_id = "test_emp"
    executor._tools = []
    executor._agent = MagicMock()
    executor._status = "idle"

    # Build fake streaming events
    tool_call_msg = AIMessage(
        content="",
        tool_calls=[{"name": "list_colleagues", "args": {"department": "eng"}, "id": "tc_1"}],
    )
    tool_result_output = "Found 3 colleagues"

    events = [
        {"event": "on_chat_model_end", "data": {"output": tool_call_msg}},
        {"event": "on_tool_end", "data": {"output": tool_result_output}, "name": "list_colleagues"},
        # Final AI message
        {"event": "on_chat_model_end", "data": {"output": AIMessage(content="Done checking.")}},
    ]

    async def fake_astream_events(*args, **kwargs):
        for e in events:
            yield e

    executor._agent.astream_events = fake_astream_events

    # Patch _build_full_prompt, _set_status, _publish, _get_model_name
    executor._build_full_prompt = lambda: "You are a test agent."
    executor._set_status = lambda s: None
    executor._publish = AsyncMock()
    executor._get_model_name = lambda: "test-model"

    logged = []
    def on_log(log_type, content):
        logged.append((log_type, content))

    await executor.run_streamed("do something", on_log=on_log)

    # Find tool_call entry
    tool_calls = [(t, c) for t, c in logged if t == "tool_call"]
    assert len(tool_calls) == 1
    tc_type, tc_content = tool_calls[0]
    assert isinstance(tc_content, dict), f"Expected dict, got {type(tc_content)}"
    assert tc_content["tool_name"] == "list_colleagues"
    assert tc_content["tool_args"] == {"department": "eng"}
    assert "content" in tc_content  # backward compat string

    # Find tool_result entry
    tool_results = [(t, c) for t, c in logged if t == "tool_result"]
    assert len(tool_results) == 1
    tr_type, tr_content = tool_results[0]
    assert isinstance(tr_content, dict), f"Expected dict, got {type(tr_content)}"
    assert tr_content["tool_name"] == "list_colleagues"
    assert "tool_result" in tr_content
