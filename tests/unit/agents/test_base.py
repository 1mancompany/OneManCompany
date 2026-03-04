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
        monkeypatch.setattr(base_mod, "settings", mock_settings)
        monkeypatch.setattr(base_mod, "employee_configs", {})

        llm = base_mod.make_llm()
        assert llm is not None
        # Should be a ChatOpenAI instance
        assert llm.model_name == "gpt-4"

    def test_employee_specific_model(self, monkeypatch):
        from onemancompany.agents import base as base_mod

        mock_settings = MagicMock()
        mock_settings.default_llm_model = "gpt-4"
        mock_settings.openrouter_api_key = "test-key"
        mock_settings.openrouter_base_url = "https://openrouter.ai/api/v1"
        monkeypatch.setattr(base_mod, "settings", mock_settings)

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

        mock_settings = MagicMock()
        mock_settings.default_llm_model = "default-model"
        mock_settings.openrouter_api_key = "test-key"
        mock_settings.openrouter_base_url = "https://openrouter.ai/api/v1"
        monkeypatch.setattr(base_mod, "settings", mock_settings)

        cfg = MagicMock()
        cfg.llm_model = "claude-3"
        cfg.temperature = 0.7
        cfg.api_provider = "anthropic"
        cfg.api_key = ""  # no key
        monkeypatch.setattr(base_mod, "employee_configs", {"00010": cfg})

        llm = base_mod.make_llm("00010")
        # Should fall back to default model via OpenRouter
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
        monkeypatch.setattr(base_mod, "settings", mock_settings)

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
        monkeypatch.setattr(base_mod, "settings", mock_settings)

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
        monkeypatch.setattr(base_mod, "settings", mock_settings)

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

    def test_builds_skills_section(self, monkeypatch):
        from onemancompany.agents import base as base_mod

        monkeypatch.setattr(
            base_mod, "load_employee_skills",
            lambda eid: {"python": "Python expertise", "js": "JavaScript skill"},
        )
        result = base_mod.get_employee_skills_prompt("00010")
        assert "Skills & Knowledge" in result
        assert "python" in result
        assert "JavaScript skill" in result


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

    def test_includes_open_access_tools(self, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.tools = {
            "t1": OfficeTool(
                id="t1", name="MyTool", description="A tool",
                added_by="COO", allowed_users=[], folder_name="", files=[],
            ),
        }
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)

        result = base_mod.get_employee_tools_prompt("00010")
        assert "MyTool" in result
        assert "A tool" in result

    def test_excludes_restricted_tools_for_unauthorized(self, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.tools = {
            "t1": OfficeTool(
                id="t1", name="SecretTool", description="Restricted",
                added_by="COO", allowed_users=["00099"], folder_name="", files=[],
            ),
        }
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)

        result = base_mod.get_employee_tools_prompt("00010")
        assert result == ""

    def test_includes_restricted_tools_for_authorized(self, monkeypatch):
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.tools = {
            "t1": OfficeTool(
                id="t1", name="SpecialTool", description="Only for 00010",
                added_by="COO", allowed_users=["00010"], folder_name="", files=[],
            ),
        }
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)

        result = base_mod.get_employee_tools_prompt("00010")
        assert "SpecialTool" in result


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
            cs.employees[emp.id] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)

        runner = BaseAgentRunner()
        runner.employee_id = emp.id if emp else ""
        runner.role = "TestAgent"
        return runner, cs

    def test_set_status(self, monkeypatch):
        emp = _make_emp("00010")
        runner, cs = self._make_runner(monkeypatch, emp=emp)

        runner._set_status("working")
        assert cs.employees["00010"].status == "working"

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

    def test_get_guidance_prompt_section_empty(self, monkeypatch):
        emp = _make_emp("00010")
        runner, _ = self._make_runner(monkeypatch, emp=emp)

        result = runner._get_guidance_prompt_section()
        assert result == ""

    def test_get_guidance_prompt_section_with_notes(self, monkeypatch):
        emp = _make_emp("00010", guidance_notes=["Be concise", "Focus on quality"])
        runner, _ = self._make_runner(monkeypatch, emp=emp)

        result = runner._get_guidance_prompt_section()
        assert "CEO Guidance" in result
        assert "Be concise" in result
        assert "Focus on quality" in result

    def test_get_work_principles_prompt_section_empty(self, monkeypatch):
        emp = _make_emp("00010")
        runner, _ = self._make_runner(monkeypatch, emp=emp)

        result = runner._get_work_principles_prompt_section()
        assert result == ""

    def test_get_work_principles_prompt_section(self, monkeypatch):
        emp = _make_emp("00010", work_principles="Always test first")
        runner, _ = self._make_runner(monkeypatch, emp=emp)

        result = runner._get_work_principles_prompt_section()
        assert "Work Principles" in result
        assert "Always test first" in result

    def test_get_company_culture_prompt_section_empty(self, monkeypatch):
        runner, cs = self._make_runner(monkeypatch, emp=_make_emp("00010"))
        cs.company_culture = []

        result = runner._get_company_culture_prompt_section()
        assert result == ""

    def test_get_company_culture_prompt_section(self, monkeypatch):
        runner, cs = self._make_runner(monkeypatch, emp=_make_emp("00010"))
        cs.company_culture = [
            {"content": "Move fast"},
            {"content": "Stay humble"},
        ]

        result = runner._get_company_culture_prompt_section()
        assert "Company Culture" in result
        assert "Move fast" in result
        assert "Stay humble" in result

    def test_get_dynamic_context_section(self, monkeypatch):
        cs = _make_cs()
        emp1 = _make_emp("00010")
        emp2 = _make_emp("00020", name="Alice", nickname="A", role="Designer", level=2)
        cs.employees = {"00010": emp1, "00020": emp2}

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
        cs.company_direction = ""

        result = runner._get_company_direction_section()
        assert result == ""

    def test_get_company_direction_section(self, monkeypatch):
        runner, cs = self._make_runner(monkeypatch, emp=_make_emp("00010"))
        cs.company_direction = "Build the best AI company"

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

        # Create agent manifest
        agent_dir = tmp_path / "emp" / "00010" / "agent"
        agent_dir.mkdir(parents=True)

        manifest = {"prompt_sections": [
            {"name": "custom", "file": "custom.md", "priority": 25},
        ]}
        import yaml
        (agent_dir / "manifest.yaml").write_text(yaml.dump(manifest))
        (agent_dir / "custom.md").write_text("Custom section content")

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

        monkeypatch.setattr(base_mod, "employee_configs", {})
        mock_settings = MagicMock()
        mock_settings.default_llm_model = "default-model"
        monkeypatch.setattr(base_mod, "settings", mock_settings)

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

        cs = _make_cs()
        emp = _make_emp(emp_id, **emp_kwargs)
        cs.employees[emp_id] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)
        monkeypatch.setattr(ct_mod, "company_state", cs)

        monkeypatch.setattr(base_mod, "load_employee_skills", lambda eid: {})
        monkeypatch.setattr(config_mod, "load_employee_custom_tools", lambda eid: [])
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
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)
        monkeypatch.setattr(config_mod, "load_employee_custom_tools", lambda eid: [])
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
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)
        monkeypatch.setattr(config_mod, "load_employee_custom_tools", lambda eid: [])
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

    def test_unauthorized_tools_section(self, monkeypatch):
        self._setup(monkeypatch, emp_id="00010", tool_permissions=[])

        from onemancompany.agents.base import EmployeeAgent
        agent = EmployeeAgent("00010")

        # Should have some unauthorized tools listed
        section = agent._get_unauthorized_tools_section()
        if agent._unauthorized_tool_names:
            assert "Restricted Tools" in section

    def test_gated_tools_included_when_permitted(self, monkeypatch):
        self._setup(monkeypatch, emp_id="00010", tool_permissions=["read_file"])

        from onemancompany.agents.base import EmployeeAgent
        agent = EmployeeAgent("00010")

        assert "read_file" in agent._authorized_tool_names
        assert "read_file" not in agent._unauthorized_tool_names

    @pytest.mark.asyncio
    async def test_run(self, monkeypatch):
        from onemancompany.core import state as state_mod, events as events_mod
        from onemancompany.agents import base as base_mod

        cs, emp, mock_agent = self._setup(monkeypatch)

        # Mock agent loop context
        monkeypatch.setattr(
            "onemancompany.core.agent_loop._current_loop",
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
        assert cs.employees["00010"].status == "idle"

    @pytest.mark.asyncio
    async def test_run_streamed_fallback(self, monkeypatch):
        """When on_log is None, run_streamed falls back to run()."""
        from onemancompany.core import state as state_mod, events as events_mod
        from onemancompany.agents import base as base_mod

        cs, emp, mock_agent = self._setup(monkeypatch)

        monkeypatch.setattr(
            "onemancompany.core.agent_loop._current_loop",
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
