"""Unit tests for agents/ea_agent.py — EAAgent init, prompt, and run."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from onemancompany.core.state import CompanyState, Employee



def _make_cs():
    cs = CompanyState()
    cs._next_employee_number = 100
    return cs


def _make_emp(emp_id: str, **kwargs) -> Employee:
    defaults = dict(
        id=emp_id, name=f"Emp {emp_id}", role="EA",
        skills=["management"], employee_number=emp_id, nickname="小秘",
    )
    defaults.update(kwargs)
    return Employee(**defaults)


class TestEAAgentInit:
    def test_creates_agent_with_common_tools(self, monkeypatch):
        from onemancompany.agents import ea_agent as ea_mod
        from onemancompany.agents import base as base_mod
        from onemancompany.core import config as config_mod
        from onemancompany.core import tool_registry as tr_mod

        monkeypatch.setattr(base_mod, "make_llm", lambda eid: MagicMock())

        fake_tool = MagicMock()
        monkeypatch.setattr(
            tr_mod.tool_registry, "get_proxied_tools_for",
            lambda eid: [fake_tool],
        )

        created_tools = []

        def mock_create_react_agent(model, tools):
            created_tools.extend(tools)
            return MagicMock()

        monkeypatch.setattr(ea_mod, "create_react_agent", mock_create_react_agent)

        from onemancompany.agents.ea_agent import EAAgent
        agent = EAAgent()

        assert agent.role == "EA"
        assert agent.employee_id == config_mod.EA_ID
        assert len(created_tools) > 0

    def test_employee_id_is_ea_id(self, monkeypatch):
        from onemancompany.agents import ea_agent as ea_mod
        from onemancompany.agents import base as base_mod
        from onemancompany.core.config import EA_ID

        monkeypatch.setattr(base_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(ea_mod, "create_react_agent", lambda model, tools: MagicMock())

        from onemancompany.agents.ea_agent import EAAgent
        agent = EAAgent()
        assert agent.employee_id == EA_ID


class TestEAAgentBuildPrompt:
    def _make_agent(self, monkeypatch, cs=None):
        from onemancompany.agents import ea_agent as ea_mod
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        if cs is None:
            cs = _make_cs()
            emp = _make_emp(config_mod.EA_ID)
            cs.employees[config_mod.EA_ID] = emp

        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(base_mod, "load_employee_skills", lambda eid: {})
        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", Path("/nonexistent"))
        monkeypatch.setattr(base_mod, "SHARED_PROMPTS_DIR", Path("/nonexistent"))
        monkeypatch.setattr(ea_mod, "create_react_agent", lambda model, tools: MagicMock())

        from onemancompany.agents.ea_agent import EAAgent
        return EAAgent()

    def test_contains_ea_system_prompt(self, monkeypatch):
        agent = self._make_agent(monkeypatch)
        prompt = agent._build_prompt()
        assert "EA" in prompt
        assert "Dispatch Authority" in prompt

    def test_contains_routing_references(self, monkeypatch):
        agent = self._make_agent(monkeypatch)
        prompt = agent._build_prompt()
        assert "O-level" in prompt

    def test_contains_efficiency_rules(self, monkeypatch):
        agent = self._make_agent(monkeypatch)
        prompt = agent._build_prompt()
        assert "Efficiency" in prompt

    def test_includes_dynamic_context(self, monkeypatch):
        agent = self._make_agent(monkeypatch)
        prompt = agent._build_prompt()
        assert "Current Context" in prompt

    def test_guidance_not_in_system_prompt(self, monkeypatch):
        """Guidance is injected via _build_company_context_block in task prompt, not system prompt."""
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        emp = _make_emp(config_mod.EA_ID, guidance_notes=["Be quick", "No fluff"])
        cs.employees[config_mod.EA_ID] = emp

        agent = self._make_agent(monkeypatch, cs=cs)
        prompt = agent._build_prompt()
        assert "Be quick" not in prompt

    def test_culture_not_in_system_prompt(self, monkeypatch):
        """Culture is injected via _build_company_context_block in task prompt, not system prompt."""
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        emp = _make_emp(config_mod.EA_ID)
        cs.employees[config_mod.EA_ID] = emp
        cs.company_culture = [{"content": "Move fast"}]

        agent = self._make_agent(monkeypatch, cs=cs)
        prompt = agent._build_prompt()
        assert "Move fast" not in prompt


class TestEAPromptContents:
    def test_ea_role_guide_references_sop(self):
        """EA role_guide.md references SOP for progressive disclosure."""
        from onemancompany.core.config import EMPLOYEES_DIR, EA_ID
        guide = (EMPLOYEES_DIR / EA_ID / "role_guide.md").read_text()
        assert "ea_dispatch_authority_sop" in guide
        assert "O-level" in guide


class TestEAAgentRun:
    @pytest.mark.asyncio
    async def test_run_returns_final_content(self, monkeypatch):
        from onemancompany.agents import ea_agent as ea_mod
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod, events as events_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        emp = _make_emp(config_mod.EA_ID)
        cs.employees[config_mod.EA_ID] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(base_mod, "load_employee_skills", lambda eid: {})
        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", Path("/nonexistent"))
        monkeypatch.setattr(base_mod, "SHARED_PROMPTS_DIR", Path("/nonexistent"))

        mock_publish = AsyncMock()
        monkeypatch.setattr(events_mod, "event_bus", MagicMock(publish=mock_publish))
        monkeypatch.setattr(base_mod, "event_bus", MagicMock(publish=mock_publish))

        # Mock agent loop context
        monkeypatch.setattr(
            "onemancompany.core.agent_loop._current_loop",
            MagicMock(get=lambda x=None: None),
        )

        final_msg = MagicMock()
        final_msg.content = "Routed task to COO"
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": [final_msg]})
        monkeypatch.setattr(ea_mod, "create_react_agent", lambda model, tools: mock_agent)

        from onemancompany.agents.ea_agent import EAAgent
        agent = EAAgent()

        result = await agent.run("Build a new feature")
        assert result == "Routed task to COO"
        assert cs.employees[config_mod.EA_ID].status == "idle"

    @pytest.mark.asyncio
    async def test_run_completes_and_invokes_agent(self, monkeypatch):
        """Verify that run() invokes the LLM agent and completes successfully.

        Note: _set_status is a no-op after Task 10 (runtime status persisted
        to disk via save_employee_runtime, not in-memory).
        """
        from onemancompany.agents import ea_agent as ea_mod
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod, events as events_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        emp = _make_emp(config_mod.EA_ID)
        cs.employees[config_mod.EA_ID] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(base_mod, "load_employee_skills", lambda eid: {})
        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", Path("/nonexistent"))
        monkeypatch.setattr(base_mod, "SHARED_PROMPTS_DIR", Path("/nonexistent"))
        monkeypatch.setattr(events_mod, "event_bus", MagicMock(publish=AsyncMock()))
        monkeypatch.setattr(base_mod, "event_bus", MagicMock(publish=AsyncMock()))
        monkeypatch.setattr(
            "onemancompany.core.agent_loop._current_loop",
            MagicMock(get=lambda x=None: None),
        )

        agent_invoked = False

        async def spy_ainvoke(messages):
            nonlocal agent_invoked
            agent_invoked = True
            return {"messages": [MagicMock(content="done")]}

        mock_agent = MagicMock()
        mock_agent.ainvoke = spy_ainvoke
        monkeypatch.setattr(ea_mod, "create_react_agent", lambda model, tools: mock_agent)

        from onemancompany.agents.ea_agent import EAAgent
        agent = EAAgent()
        result = await agent.run("test")

        assert agent_invoked, "LLM agent should have been invoked"
        assert result == "done"
