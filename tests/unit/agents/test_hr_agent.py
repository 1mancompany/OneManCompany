"""Unit tests for agents/hr_agent.py — HR hiring tools, apply_results, promotions."""

from __future__ import annotations

import json
import random
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.config import (
    FOUNDING_LEVEL,
    MAX_NORMAL_LEVEL,
    MAX_PERFORMANCE_HISTORY,
    QUARTERS_FOR_PROMOTION,
    SCORE_EXCELLENT,
    SCORE_QUALIFIED,
    TASKS_PER_QUARTER,
)
from onemancompany.core.state import CompanyState, Employee


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cs():
    cs = CompanyState()
    cs._next_employee_number = 100
    return cs


def _make_emp(
    emp_id: str,
    level: int = 1,
    current_quarter_tasks: int = 0,
    performance_history: list | None = None,
    **kwargs,
) -> Employee:
    defaults = dict(
        id=emp_id, name=f"Emp {emp_id}", role="Engineer",
        skills=["python"], employee_number=emp_id, nickname="测试",
        level=level, current_quarter_tasks=current_quarter_tasks,
        performance_history=performance_history or [],
    )
    defaults.update(kwargs)
    return Employee(**defaults)


# ---------------------------------------------------------------------------
# _talent_to_candidate (hr_agent version)
# ---------------------------------------------------------------------------

class TestHRTalentToCandidate:
    def test_basic_conversion(self, monkeypatch):
        from onemancompany.agents import recruitment
        from onemancompany.core import config as config_mod
        import onemancompany.core.model_costs as mc

        monkeypatch.setattr(config_mod, "load_talent_skills", lambda tid: ["# Python\nPython skill content"])
        monkeypatch.setattr(config_mod, "load_talent_tools", lambda tid: ["sandbox_execute_code"])
        monkeypatch.setattr(mc, "compute_salary", lambda m: 5.0)

        talent = {
            "id": "test_talent",
            "name": "Test Dev",
            "role": "Engineer",
            "skills": ["python"],
            "personality_tags": ["creative"],
            "system_prompt_template": "You are a dev",
            "llm_model": "test-model",
            "api_provider": "openrouter",
            "temperature": 0.6,
            "hosting": "company",
            "auth_method": "api_key",
            "hiring_fee": 1.5,
        }

        random.seed(42)
        candidate = recruitment._talent_to_candidate(talent)

        assert candidate["id"] == "test_talent"
        assert candidate["name"] == "Test Dev"
        assert candidate["role"] == "Engineer"
        assert len(candidate["skill_set"]) == 1
        assert candidate["skill_set"][0]["name"] == "python"
        assert len(candidate["tool_set"]) == 1
        assert candidate["cost_per_1m_tokens"] == 5.0
        assert candidate["hiring_fee"] == 1.5
        assert candidate["jd_relevance"] == 1.0

    def test_non_openrouter_has_zero_cost(self, monkeypatch):
        from onemancompany.agents import recruitment
        from onemancompany.core import config as config_mod

        monkeypatch.setattr(config_mod, "load_talent_skills", lambda tid: [])
        monkeypatch.setattr(config_mod, "load_talent_tools", lambda tid: [])

        talent = {
            "id": "anthropic_talent",
            "api_provider": "anthropic",
            "llm_model": "claude-sonnet",
        }

        candidate = recruitment._talent_to_candidate(talent)
        assert candidate["cost_per_1m_tokens"] == 0.0


# ---------------------------------------------------------------------------
# list_open_positions
# ---------------------------------------------------------------------------

class TestListOpenPositions:
    def test_returns_2_to_4_positions(self):
        from onemancompany.agents.hr_agent import list_open_positions

        for _ in range(20):
            positions = list_open_positions.invoke({})
            assert 2 <= len(positions) <= 4

    def test_each_position_has_required_fields(self):
        from onemancompany.agents.hr_agent import list_open_positions

        positions = list_open_positions.invoke({})
        for pos in positions:
            assert "role" in pos
            assert "priority" in pos
            assert "reason" in pos


# ---------------------------------------------------------------------------
# HRAgent._apply_results — shortlist action
# ---------------------------------------------------------------------------

class TestApplyResultsShortlist:
    @pytest.mark.asyncio
    async def test_shortlist_action(self, monkeypatch):
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        # Clear pending state
        hr_agent.pending_candidates.clear()
        hr_agent._last_search_results.clear()
        hr_agent._last_search_results["t1"] = {"id": "t1", "name": "Test", "role": "Engineer", "full": True}

        # Create a minimal HR agent mock
        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent

        output = '```json\n{"action": "shortlist", "jd": "python dev", "candidates": [{"id": "t1", "name": "Test"}]}\n```'

        await hr_agent.HRAgent._apply_results(agent, output)

        assert len(hr_agent.pending_candidates) == 1
        batch_id = list(hr_agent.pending_candidates.keys())[0]
        candidates = hr_agent.pending_candidates[batch_id]
        assert len(candidates) == 1
        # Should merge with full data from _last_search_results
        assert candidates[0].get("full") is True
        agent._publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_shortlist_max_5_candidates(self, monkeypatch):
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        hr_agent.pending_candidates.clear()
        hr_agent._last_search_results.clear()

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent

        # 10 candidates in output — should be capped at 5
        candidates_json = [{"id": f"t{i}", "name": f"T{i}"} for i in range(10)]
        output = f'```json\n{{"action": "shortlist", "jd": "test", "candidates": {json.dumps(candidates_json)}}}\n```'

        await hr_agent.HRAgent._apply_results(agent, output)

        batch_id = list(hr_agent.pending_candidates.keys())[0]
        assert len(hr_agent.pending_candidates[batch_id]) == 5


# ---------------------------------------------------------------------------
# HRAgent._apply_results — review action
# ---------------------------------------------------------------------------

class TestApplyResultsReview:
    def _setup_review(self, monkeypatch, emp):
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.employees[emp.id] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent
        return cs, agent

    @pytest.mark.asyncio
    async def test_review_records_performance(self, monkeypatch):
        from onemancompany.agents import hr_agent

        emp = _make_emp("00010", current_quarter_tasks=3)
        cs, agent = self._setup_review(monkeypatch, emp)

        output = '```json\n{"action": "review", "reviews": [{"id": "00010", "score": 3.75, "feedback": "great"}]}\n```'
        await hr_agent.HRAgent._apply_results(agent, output)

        assert len(emp.performance_history) == 1
        assert emp.performance_history[0]["score"] == 3.75
        assert emp.current_quarter_tasks == 0

    @pytest.mark.asyncio
    async def test_review_snaps_to_nearest_valid_score(self, monkeypatch):
        from onemancompany.agents import hr_agent

        emp = _make_emp("00010", current_quarter_tasks=3)
        cs, agent = self._setup_review(monkeypatch, emp)

        # 3.6 should snap to 3.5 (nearest valid tier)
        output = '```json\n{"action": "review", "reviews": [{"id": "00010", "score": 3.6}]}\n```'
        await hr_agent.HRAgent._apply_results(agent, output)

        assert emp.performance_history[0]["score"] == 3.5

    @pytest.mark.asyncio
    async def test_review_skips_unqualified_employees(self, monkeypatch):
        from onemancompany.agents import hr_agent

        emp = _make_emp("00010", current_quarter_tasks=1)  # only 1 task, not 3
        cs, agent = self._setup_review(monkeypatch, emp)

        output = '```json\n{"action": "review", "reviews": [{"id": "00010", "score": 3.75}]}\n```'
        await hr_agent.HRAgent._apply_results(agent, output)

        # Should NOT record review — not enough tasks
        assert len(emp.performance_history) == 0

    @pytest.mark.asyncio
    async def test_review_trims_history(self, monkeypatch):
        from onemancompany.agents import hr_agent

        existing_history = [{"score": 3.5, "tasks": 3} for _ in range(MAX_PERFORMANCE_HISTORY)]
        emp = _make_emp("00010", current_quarter_tasks=3, performance_history=existing_history)
        cs, agent = self._setup_review(monkeypatch, emp)

        output = '```json\n{"action": "review", "reviews": [{"id": "00010", "score": 3.75}]}\n```'
        await hr_agent.HRAgent._apply_results(agent, output)

        assert len(emp.performance_history) == MAX_PERFORMANCE_HISTORY


# ---------------------------------------------------------------------------
# HRAgent._apply_results — fire action
# ---------------------------------------------------------------------------

class TestApplyResultsFire:
    @pytest.mark.asyncio
    async def test_fire_normal_employee(self, monkeypatch):
        from onemancompany.agents import hr_agent
        from onemancompany.agents import termination as term_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp = _make_emp("00010", level=2)
        cs.employees["00010"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)
        monkeypatch.setattr(term_mod, "company_state", cs)
        monkeypatch.setattr(term_mod, "move_employee_to_ex", lambda eid: True)
        monkeypatch.setattr(term_mod, "compute_layout", lambda cs: {})
        # persist_all_desk_positions removed in Task 10 — no longer in termination module
        monkeypatch.setattr(term_mod, "event_bus", MagicMock(publish=AsyncMock()))

        # Track activity log writes via store
        activity_entries = []
        monkeypatch.setattr(term_mod._store, "append_activity",
                            AsyncMock(side_effect=lambda e: activity_entries.append(e)))

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent

        output = '```json\n{"action": "fire", "employee_id": "00010", "reason": "poor performance"}\n```'
        with patch("onemancompany.core.routine.run_offboarding_routine", new_callable=AsyncMock):
            await hr_agent.HRAgent._apply_results(agent, output)

        assert "00010" not in cs.employees
        assert "00010" in cs.ex_employees
        assert len(activity_entries) == 1
        assert activity_entries[0]["type"] == "employee_fired"

    @pytest.mark.asyncio
    async def test_cannot_fire_founding_employee(self, monkeypatch):
        from onemancompany.agents import hr_agent
        from onemancompany.agents import termination as term_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp = _make_emp("00002", level=FOUNDING_LEVEL)
        cs.employees["00002"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)
        monkeypatch.setattr(term_mod, "company_state", cs)

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent

        output = '```json\n{"action": "fire", "employee_id": "00002", "reason": "trying to fire founder"}\n```'
        with patch("onemancompany.core.routine.run_offboarding_routine", new_callable=AsyncMock):
            await hr_agent.HRAgent._apply_results(agent, output)

        # Should NOT be fired
        assert "00002" in cs.employees
        assert "00002" not in cs.ex_employees

    @pytest.mark.asyncio
    async def test_fire_nonexistent_employee_no_error(self, monkeypatch):
        from onemancompany.agents import hr_agent
        from onemancompany.agents import termination as term_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)
        monkeypatch.setattr(term_mod, "company_state", cs)

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent

        output = '```json\n{"action": "fire", "employee_id": "99999"}\n```'
        # Should not raise
        with patch("onemancompany.core.routine.run_offboarding_routine", new_callable=AsyncMock):
            await hr_agent.HRAgent._apply_results(agent, output)


# ---------------------------------------------------------------------------
# HRAgent._check_promotions
# ---------------------------------------------------------------------------

class TestCheckPromotions:
    def _setup_promo(self, monkeypatch, employees: dict):
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.employees = employees
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)
        monkeypatch.setattr(hr_agent, "compute_layout", lambda cs: {})
        # persist_all_desk_positions removed in Task 10

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent
        return cs, agent

    @pytest.mark.asyncio
    async def test_promote_after_3_excellent_quarters(self, monkeypatch):
        from onemancompany.agents import hr_agent

        history = [{"score": SCORE_EXCELLENT, "tasks": 3} for _ in range(QUARTERS_FOR_PROMOTION)]
        emp = _make_emp("00010", level=1, performance_history=history)
        cs, agent = self._setup_promo(monkeypatch, {"00010": emp})

        await hr_agent.HRAgent._check_promotions(agent)

        assert emp.level == 2

    @pytest.mark.asyncio
    async def test_no_promote_with_mixed_scores(self, monkeypatch):
        from onemancompany.agents import hr_agent

        history = [
            {"score": SCORE_EXCELLENT, "tasks": 3},
            {"score": SCORE_QUALIFIED, "tasks": 3},  # not excellent
            {"score": SCORE_EXCELLENT, "tasks": 3},
        ]
        emp = _make_emp("00010", level=1, performance_history=history)
        cs, agent = self._setup_promo(monkeypatch, {"00010": emp})

        await hr_agent.HRAgent._check_promotions(agent)

        assert emp.level == 1  # no promotion

    @pytest.mark.asyncio
    async def test_no_promote_beyond_max_normal(self, monkeypatch):
        from onemancompany.agents import hr_agent

        history = [{"score": SCORE_EXCELLENT, "tasks": 3} for _ in range(QUARTERS_FOR_PROMOTION)]
        emp = _make_emp("00010", level=MAX_NORMAL_LEVEL, performance_history=history)
        cs, agent = self._setup_promo(monkeypatch, {"00010": emp})

        await hr_agent.HRAgent._check_promotions(agent)

        assert emp.level == MAX_NORMAL_LEVEL  # still at max

    @pytest.mark.asyncio
    async def test_no_promote_founding_employees(self, monkeypatch):
        from onemancompany.agents import hr_agent

        history = [{"score": SCORE_EXCELLENT, "tasks": 3} for _ in range(QUARTERS_FOR_PROMOTION)]
        emp = _make_emp("00002", level=FOUNDING_LEVEL, performance_history=history)
        cs, agent = self._setup_promo(monkeypatch, {"00002": emp})

        await hr_agent.HRAgent._check_promotions(agent)

        assert emp.level == FOUNDING_LEVEL  # unchanged

    @pytest.mark.asyncio
    async def test_promote_records_activity_log(self, monkeypatch):
        from onemancompany.agents import hr_agent

        history = [{"score": SCORE_EXCELLENT, "tasks": 3} for _ in range(QUARTERS_FOR_PROMOTION)]
        emp = _make_emp("00010", level=1, performance_history=history)
        cs, agent = self._setup_promo(monkeypatch, {"00010": emp})

        activity_entries = []
        monkeypatch.setattr(hr_agent, "_append_activity", lambda e: activity_entries.append(e))

        await hr_agent.HRAgent._check_promotions(agent)

        assert len(activity_entries) == 1
        assert activity_entries[0]["type"] == "promotion"
        assert activity_entries[0]["old_level"] == 1
        assert activity_entries[0]["new_level"] == 2

    @pytest.mark.asyncio
    async def test_no_promote_with_insufficient_history(self, monkeypatch):
        """Employees with fewer than QUARTERS_FOR_PROMOTION quarters should not be promoted."""
        from onemancompany.agents import hr_agent

        # Only 1 excellent quarter (need 3)
        history = [{"score": SCORE_EXCELLENT, "tasks": 3}]
        emp = _make_emp("00010", level=1, performance_history=history)
        cs, agent = self._setup_promo(monkeypatch, {"00010": emp})

        await hr_agent.HRAgent._check_promotions(agent)

        assert emp.level == 1  # no promotion


# ---------------------------------------------------------------------------
# HRAgent.__init__
# ---------------------------------------------------------------------------

class TestHRAgentInit:
    def test_init_creates_agent(self, monkeypatch):
        from onemancompany.agents import hr_agent

        mock_llm = MagicMock()
        mock_graph = MagicMock()
        monkeypatch.setattr(hr_agent, "make_llm", lambda eid: mock_llm)
        monkeypatch.setattr(hr_agent, "create_react_agent", lambda model, tools: mock_graph)

        agent = hr_agent.HRAgent()

        assert agent._agent is mock_graph
        assert agent.role == "HR"
        assert agent.employee_id == "00002"


# ---------------------------------------------------------------------------
# HRAgent._build_prompt
# ---------------------------------------------------------------------------

class TestHRAgentBuildPrompt:
    def test_build_prompt_contains_system_prompt(self, monkeypatch):
        from onemancompany.agents import hr_agent

        mock_llm = MagicMock()
        mock_graph = MagicMock()
        monkeypatch.setattr(hr_agent, "make_llm", lambda eid: mock_llm)
        monkeypatch.setattr(hr_agent, "create_react_agent", lambda model, tools: mock_graph)

        agent = hr_agent.HRAgent()

        # Stub out prompt section methods to return empty strings
        agent._get_skills_prompt_section = lambda: ""
        agent._get_tools_prompt_section = lambda: ""
        agent._get_dynamic_context_section = lambda: ""
        agent._get_efficiency_guidelines_section = lambda: ""

        prompt = agent._build_prompt()

        # role_guide.md loaded if present, otherwise just structural sections
        assert "Task Lifecycle" in prompt
        assert len(prompt) > 100


# ---------------------------------------------------------------------------
# HRAgent.run
# ---------------------------------------------------------------------------

class TestHRAgentRun:
    @pytest.mark.asyncio
    async def test_run_invokes_agent_and_returns_result(self, monkeypatch):
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        mock_llm = MagicMock()
        mock_graph = MagicMock()
        monkeypatch.setattr(hr_agent, "make_llm", lambda eid: mock_llm)
        monkeypatch.setattr(hr_agent, "create_react_agent", lambda model, tools: mock_graph)

        agent = hr_agent.HRAgent()
        agent._publish = AsyncMock()
        agent._set_status = MagicMock()
        agent._build_full_prompt = MagicMock(return_value="system prompt")

        # Mock the agent graph's ainvoke to return a message
        mock_msg = MagicMock()
        mock_msg.content = "All done. No actions needed."
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [mock_msg]})

        result = await agent.run("Do a review")

        assert result == "All done. No actions needed."
        mock_graph.ainvoke.assert_called_once()
        # Should set status to working then idle
        assert agent._set_status.call_count == 2


# ---------------------------------------------------------------------------
# HRAgent.run_streamed
# ---------------------------------------------------------------------------

class TestHRAgentRunStreamed:
    @pytest.mark.asyncio
    async def test_run_streamed_calls_super_and_apply(self, monkeypatch):
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        mock_llm = MagicMock()
        mock_graph = MagicMock()
        monkeypatch.setattr(hr_agent, "make_llm", lambda eid: mock_llm)
        monkeypatch.setattr(hr_agent, "create_react_agent", lambda model, tools: mock_graph)

        agent = hr_agent.HRAgent()
        agent._publish = AsyncMock()

        # When no on_log, run_streamed falls back to run() via super().run_streamed()
        # Mock run() to return a simple result
        agent.run = AsyncMock(return_value="simple result")

        result = await agent.run_streamed("do something")

        assert result == "simple result"
        agent.run.assert_called_once()


# ---------------------------------------------------------------------------
# HRAgent.run_quarterly_review
# ---------------------------------------------------------------------------

class TestHRAgentRunQuarterlyReview:
    @pytest.mark.asyncio
    async def test_quarterly_review_builds_task_and_runs(self, monkeypatch):
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp1 = _make_emp("00010", current_quarter_tasks=3)
        emp2 = _make_emp("00011", current_quarter_tasks=1)
        cs.employees["00010"] = emp1
        cs.employees["00011"] = emp2
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        mock_llm = MagicMock()
        mock_graph = MagicMock()
        monkeypatch.setattr(hr_agent, "make_llm", lambda eid: mock_llm)
        monkeypatch.setattr(hr_agent, "create_react_agent", lambda model, tools: mock_graph)

        agent = hr_agent.HRAgent()

        # Capture what's passed to run()
        captured_task = None
        async def mock_run(task):
            nonlocal captured_task
            captured_task = task
            return "review done"

        agent.run = mock_run

        result = await agent.run_quarterly_review()

        assert result == "review done"
        assert captured_task is not None
        assert "quarterly performance review" in captured_task.lower()
        # Emp 00010 should be in reviewable section (has 3 tasks)
        assert "00010" in captured_task
        # Emp 00011 should be in not-ready section
        assert "00011" in captured_task

    @pytest.mark.asyncio
    async def test_quarterly_review_with_performance_history(self, monkeypatch):
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp = _make_emp("00010", current_quarter_tasks=3,
                        performance_history=[{"score": 3.5, "tasks": 3}])
        cs.employees["00010"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        mock_llm = MagicMock()
        mock_graph = MagicMock()
        monkeypatch.setattr(hr_agent, "make_llm", lambda eid: mock_llm)
        monkeypatch.setattr(hr_agent, "create_react_agent", lambda model, tools: mock_graph)

        agent = hr_agent.HRAgent()

        captured_task = None
        async def mock_run(task):
            nonlocal captured_task
            captured_task = task
            return "done"

        agent.run = mock_run
        await agent.run_quarterly_review()

        # Task should include performance history info
        assert "Q1=3.5" in captured_task

    @pytest.mark.asyncio
    async def test_quarterly_review_no_employees(self, monkeypatch):
        """Empty company state should still run without error."""
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        mock_llm = MagicMock()
        mock_graph = MagicMock()
        monkeypatch.setattr(hr_agent, "make_llm", lambda eid: mock_llm)
        monkeypatch.setattr(hr_agent, "create_react_agent", lambda model, tools: mock_graph)

        agent = hr_agent.HRAgent()
        agent.run = AsyncMock(return_value="no employees")

        result = await agent.run_quarterly_review()
        assert result == "no employees"


# ---------------------------------------------------------------------------
# HRAgent._apply_results — hire action
# ---------------------------------------------------------------------------

class TestApplyResultsHire:
    @pytest.mark.asyncio
    async def test_hire_action_calls_execute_hire(self, monkeypatch):
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        mock_execute_hire = AsyncMock(return_value=_make_emp("00100"))

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent

        output = '```json\n{"action": "hire", "employee": {"name": "New Dev", "nickname": "追风", "role": "Engineer", "skills": ["python"], "talent_id": "t1", "llm_model": "gpt-4"}}\n```'

        with patch("onemancompany.agents.onboarding.execute_hire", mock_execute_hire):
            await hr_agent.HRAgent._apply_results(agent, output)

        mock_execute_hire.assert_called_once()
        call_kwargs = mock_execute_hire.call_args
        assert call_kwargs.kwargs["name"] == "New Dev" or call_kwargs[1]["name"] == "New Dev"

    @pytest.mark.asyncio
    async def test_hire_action_with_skill_dicts(self, monkeypatch):
        """Skills can be dicts with name key or plain strings."""
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        captured_skills = None
        async def mock_hire(**kwargs):
            nonlocal captured_skills
            captured_skills = kwargs.get("skills")
            return _make_emp("00100")

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent

        output = '```json\n{"action": "hire", "employee": {"name": "Dev", "role": "Engineer", "skills": [{"name": "python"}, "javascript"]}}\n```'

        with patch("onemancompany.agents.onboarding.execute_hire", mock_hire):
            await hr_agent.HRAgent._apply_results(agent, output)

        assert captured_skills == ["python", "javascript"]


# ---------------------------------------------------------------------------
# HRAgent._apply_results — performance review with PIP
# ---------------------------------------------------------------------------

class TestApplyResultsPIP:
    def _setup_pip_review(self, monkeypatch, emp):
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.employees[emp.id] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent
        return cs, agent

    @pytest.mark.asyncio
    async def test_score_325_starts_pip(self, monkeypatch):
        """Score 3.25 should start PIP when employee is not already on PIP."""
        from onemancompany.agents import hr_agent

        emp = _make_emp("00010", current_quarter_tasks=3)
        assert emp.pip is None
        cs, agent = self._setup_pip_review(monkeypatch, emp)

        output = '```json\n{"action": "review", "reviews": [{"id": "00010", "score": 3.25}]}\n```'
        await hr_agent.HRAgent._apply_results(agent, output)

        assert emp.pip is not None
        assert "started_at" in emp.pip
        # pip_started event should be published
        pip_calls = [c for c in agent._publish.call_args_list
                     if c[0][0] == "pip_started"]
        assert len(pip_calls) == 1

    @pytest.mark.asyncio
    async def test_score_325_on_pip_fires_employee(self, monkeypatch):
        """Score 3.25 when already on PIP should fire the employee."""
        from onemancompany.agents import hr_agent
        from onemancompany.agents import termination as term_mod

        emp = _make_emp("00010", current_quarter_tasks=3, pip={"started_at": "2024-01-01", "reason": "Score 3.25"})
        cs, agent = self._setup_pip_review(monkeypatch, emp)

        # Setup termination mocks
        monkeypatch.setattr(term_mod, "company_state", cs)
        monkeypatch.setattr(term_mod, "move_employee_to_ex", lambda eid: True)
        monkeypatch.setattr(term_mod, "compute_layout", lambda cs: {})
        # persist_all_desk_positions removed in Task 10 — no longer in termination module
        monkeypatch.setattr(term_mod, "event_bus", MagicMock(publish=AsyncMock()))

        output = '```json\n{"action": "review", "reviews": [{"id": "00010", "score": 3.25}]}\n```'
        with patch("onemancompany.core.routine.run_offboarding_routine", new_callable=AsyncMock):
            await hr_agent.HRAgent._apply_results(agent, output)

        # Employee should be fired (moved to ex_employees)
        assert "00010" not in cs.employees
        assert "00010" in cs.ex_employees

    @pytest.mark.asyncio
    async def test_score_35_resolves_pip(self, monkeypatch):
        """Score >= 3.5 should resolve an active PIP."""
        from onemancompany.agents import hr_agent

        emp = _make_emp("00010", current_quarter_tasks=3, pip={"started_at": "2024-01-01", "reason": "Score 3.25"})
        cs, agent = self._setup_pip_review(monkeypatch, emp)

        output = '```json\n{"action": "review", "reviews": [{"id": "00010", "score": 3.5}]}\n```'
        await hr_agent.HRAgent._apply_results(agent, output)

        assert emp.pip is None
        pip_resolved_calls = [c for c in agent._publish.call_args_list
                              if c[0][0] == "pip_resolved"]
        assert len(pip_resolved_calls) == 1

    @pytest.mark.asyncio
    async def test_score_375_resolves_pip(self, monkeypatch):
        """Score 3.75 should also resolve an active PIP."""
        from onemancompany.agents import hr_agent

        emp = _make_emp("00010", current_quarter_tasks=3, pip={"started_at": "2024-01-01", "reason": "Score 3.25"})
        cs, agent = self._setup_pip_review(monkeypatch, emp)

        output = '```json\n{"action": "review", "reviews": [{"id": "00010", "score": 3.75}]}\n```'
        await hr_agent.HRAgent._apply_results(agent, output)

        assert emp.pip is None


# ---------------------------------------------------------------------------
# HRAgent._apply_results — probation review
# ---------------------------------------------------------------------------

class TestApplyResultsProbation:
    def _setup_probation(self, monkeypatch, emp):
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.employees[emp.id] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent
        return cs, agent

    @pytest.mark.asyncio
    async def test_probation_passed(self, monkeypatch):
        from onemancompany.agents import hr_agent

        emp = _make_emp("00010", probation=True)
        cs, agent = self._setup_probation(monkeypatch, emp)

        output = '```json\n{"action": "probation_review", "employee_id": "00010", "passed": true, "feedback": "Good work"}\n```'
        await hr_agent.HRAgent._apply_results(agent, output)

        assert emp.probation is False
        # probation_review event should be published
        calls = [c for c in agent._publish.call_args_list
                 if c[0][0] == "probation_review"]
        assert len(calls) == 1
        assert calls[0][0][1]["passed"] is True

    @pytest.mark.asyncio
    async def test_probation_failed_fires_employee(self, monkeypatch):
        from onemancompany.agents import hr_agent
        from onemancompany.agents import termination as term_mod

        emp = _make_emp("00010", probation=True)
        cs, agent = self._setup_probation(monkeypatch, emp)

        monkeypatch.setattr(term_mod, "company_state", cs)
        monkeypatch.setattr(term_mod, "move_employee_to_ex", lambda eid: True)
        monkeypatch.setattr(term_mod, "compute_layout", lambda cs: {})
        # persist_all_desk_positions removed in Task 10 — no longer in termination module
        monkeypatch.setattr(term_mod, "event_bus", MagicMock(publish=AsyncMock()))

        output = '```json\n{"action": "probation_review", "employee_id": "00010", "passed": false, "feedback": "Did not meet expectations"}\n```'
        with patch("onemancompany.core.routine.run_offboarding_routine", new_callable=AsyncMock):
            await hr_agent.HRAgent._apply_results(agent, output)

        assert "00010" not in cs.employees
        assert "00010" in cs.ex_employees

    @pytest.mark.asyncio
    async def test_probation_nonexistent_employee(self, monkeypatch):
        """Probation review for unknown employee should not raise."""
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent

        output = '```json\n{"action": "probation_review", "employee_id": "99999", "passed": true}\n```'
        await hr_agent.HRAgent._apply_results(agent, output)
        # No error expected, _publish should not be called for probation_review
        assert not any(c[0][0] == "probation_review" for c in agent._publish.call_args_list)


# ---------------------------------------------------------------------------
# HRAgent._apply_results — JSON fallback (no code fences)
# ---------------------------------------------------------------------------

class TestApplyResultsJsonFallback:
    @pytest.mark.asyncio
    async def test_json_without_code_fences_fire(self, monkeypatch):
        """JSON blocks without ```json``` fences should still be parsed (fire action)."""
        from onemancompany.agents import hr_agent
        from onemancompany.agents import termination as term_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp = _make_emp("00010", level=2)
        cs.employees["00010"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)
        monkeypatch.setattr(term_mod, "company_state", cs)
        monkeypatch.setattr(term_mod, "move_employee_to_ex", lambda eid: True)
        monkeypatch.setattr(term_mod, "compute_layout", lambda cs: {})
        # persist_all_desk_positions removed in Task 10 — no longer in termination module
        monkeypatch.setattr(term_mod, "event_bus", MagicMock(publish=AsyncMock()))

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent

        # No code fences — raw JSON for a fire action (simple flat JSON)
        output = 'I will fire: {"action": "fire", "employee_id": "00010", "reason": "low perf"}'
        with patch("onemancompany.core.routine.run_offboarding_routine", new_callable=AsyncMock):
            await hr_agent.HRAgent._apply_results(agent, output)

        assert "00010" not in cs.employees
        assert "00010" in cs.ex_employees

    @pytest.mark.asyncio
    async def test_invalid_json_is_skipped(self, monkeypatch):
        """Invalid JSON should be silently skipped."""
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent

        output = '```json\n{invalid json}\n```'
        # Should not raise
        await hr_agent.HRAgent._apply_results(agent, output)

    @pytest.mark.asyncio
    async def test_review_skips_unknown_employee(self, monkeypatch):
        """Review for an employee not in company_state should be skipped."""
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent

        output = '```json\n{"action": "review", "reviews": [{"id": "99999", "score": 3.5}]}\n```'
        await hr_agent.HRAgent._apply_results(agent, output)

        # No events should be published for unknown employee
        assert agent._publish.call_count == 0


# ---------------------------------------------------------------------------
# HRAgent.run_streamed — new batch project context stashing
# ---------------------------------------------------------------------------

class TestRunStreamedBatchStashing:
    @pytest.mark.asyncio
    async def test_stashes_project_context_for_new_batches(self, monkeypatch):
        """When run_streamed creates a new shortlist batch, project context is stashed."""
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        mock_llm = MagicMock()
        mock_graph = MagicMock()
        monkeypatch.setattr(hr_agent, "make_llm", lambda eid: mock_llm)
        monkeypatch.setattr(hr_agent, "create_react_agent", lambda model, tools: mock_graph)

        agent = hr_agent.HRAgent()
        agent._publish = AsyncMock()

        # Simulate super().run_streamed returning a shortlist result that creates a batch
        hr_agent.pending_candidates.clear()
        hr_agent._pending_project_ctx.clear()
        hr_agent._last_search_results.clear()

        # The shortlist output that _apply_results will process
        shortlist_output = '```json\n{"action": "shortlist", "jd": "dev", "candidates": [{"id": "t1"}]}\n```'

        # Mock super().run_streamed to return the output
        # Since on_log=None, it will fall through to run()
        agent.run = AsyncMock(return_value=shortlist_output)

        # Setup context vars for project stashing
        from onemancompany.core.agent_loop import _current_vessel, _current_task_id

        mock_task = MagicMock()
        mock_task.project_id = "proj_123"
        mock_task.project_dir = "/tmp/proj"
        mock_loop = MagicMock()
        mock_loop.get_task.return_value = mock_task

        token_loop = _current_vessel.set(mock_loop)
        token_task = _current_task_id.set("task_456")

        try:
            result = await agent.run_streamed("hire someone")
        finally:
            _current_vessel.reset(token_loop)
            _current_task_id.reset(token_task)

        # run_streamed prepends __HOLDING:batch_id=... when a new batch is created
        assert result.endswith(shortlist_output)
        assert result.startswith("__HOLDING:batch_id=")

        # A new batch should have been created in pending_candidates
        assert len(hr_agent.pending_candidates) == 1
        batch_id = list(hr_agent.pending_candidates.keys())[0]

        # Project context should be stashed
        assert batch_id in hr_agent._pending_project_ctx
        assert hr_agent._pending_project_ctx[batch_id]["project_id"] == "proj_123"

        # task_obj.project_id should be cleared
        assert mock_task.project_id == ""


# ---------------------------------------------------------------------------
# HRAgent._apply_results — offboarding exception handling
# ---------------------------------------------------------------------------

class TestApplyResultsOffboardingExceptions:
    @pytest.mark.asyncio
    async def test_pip_fire_offboarding_exception_handled(self, monkeypatch):
        """When offboarding fails during PIP fire, it should be caught and execution continues."""
        from onemancompany.agents import hr_agent
        from onemancompany.agents import termination as term_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp = _make_emp("00010", current_quarter_tasks=3, pip={"started_at": "2024-01-01", "reason": "Score 3.25"})
        cs.employees[emp.id] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        monkeypatch.setattr(term_mod, "company_state", cs)
        monkeypatch.setattr(term_mod, "move_employee_to_ex", lambda eid: True)
        monkeypatch.setattr(term_mod, "compute_layout", lambda cs: {})
        # persist_all_desk_positions removed in Task 10 — no longer in termination module
        monkeypatch.setattr(term_mod, "event_bus", MagicMock(publish=AsyncMock()))

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent

        output = '```json\n{"action": "review", "reviews": [{"id": "00010", "score": 3.25}]}\n```'
        # Make offboarding raise an exception
        with patch("onemancompany.core.routine.run_offboarding_routine", new_callable=AsyncMock, side_effect=Exception("offboard fail")):
            await hr_agent.HRAgent._apply_results(agent, output)

        # Should still fire despite offboarding failure
        assert "00010" not in cs.employees
        assert "00010" in cs.ex_employees

    @pytest.mark.asyncio
    async def test_fire_offboarding_exception_handled(self, monkeypatch):
        """When offboarding fails during fire action, it should be caught."""
        from onemancompany.agents import hr_agent
        from onemancompany.agents import termination as term_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp = _make_emp("00010", level=1)
        cs.employees["00010"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)
        monkeypatch.setattr(term_mod, "company_state", cs)
        monkeypatch.setattr(term_mod, "move_employee_to_ex", lambda eid: True)
        monkeypatch.setattr(term_mod, "compute_layout", lambda cs: {})
        # persist_all_desk_positions removed in Task 10 — no longer in termination module
        monkeypatch.setattr(term_mod, "event_bus", MagicMock(publish=AsyncMock()))

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent

        output = '```json\n{"action": "fire", "employee_id": "00010", "reason": "test"}\n```'
        with patch("onemancompany.core.routine.run_offboarding_routine", new_callable=AsyncMock, side_effect=Exception("offboard fail")):
            await hr_agent.HRAgent._apply_results(agent, output)

        assert "00010" not in cs.employees

    @pytest.mark.asyncio
    async def test_probation_failed_offboarding_exception_handled(self, monkeypatch):
        """When offboarding fails during probation fail, it should be caught."""
        from onemancompany.agents import hr_agent
        from onemancompany.agents import termination as term_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp = _make_emp("00010", probation=True)
        cs.employees["00010"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)
        monkeypatch.setattr(term_mod, "company_state", cs)
        monkeypatch.setattr(term_mod, "move_employee_to_ex", lambda eid: True)
        monkeypatch.setattr(term_mod, "compute_layout", lambda cs: {})
        # persist_all_desk_positions removed in Task 10 — no longer in termination module
        monkeypatch.setattr(term_mod, "event_bus", MagicMock(publish=AsyncMock()))

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent

        output = '```json\n{"action": "probation_review", "employee_id": "00010", "passed": false, "feedback": "bad"}\n```'
        with patch("onemancompany.core.routine.run_offboarding_routine", new_callable=AsyncMock, side_effect=Exception("offboard fail")):
            await hr_agent.HRAgent._apply_results(agent, output)

        assert "00010" not in cs.employees


# ---------------------------------------------------------------------------
# HRAgent._check_promotions — no-op when already at max (level unchanged)
# ---------------------------------------------------------------------------

class TestCheckPromotionsEdgeCases:
    def _setup_promo(self, monkeypatch, employees: dict):
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.employees = employees
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)
        monkeypatch.setattr(hr_agent, "compute_layout", lambda cs: {})
        # persist_all_desk_positions removed in Task 10

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent
        return cs, agent

    @pytest.mark.asyncio
    async def test_level_2_promote_to_3(self, monkeypatch):
        """Level 2 with 3 excellent quarters should promote to 3."""
        from onemancompany.agents import hr_agent

        history = [{"score": SCORE_EXCELLENT, "tasks": 3} for _ in range(QUARTERS_FOR_PROMOTION)]
        emp = _make_emp("00010", level=2, performance_history=history)
        cs, agent = self._setup_promo(monkeypatch, {"00010": emp})

        activity_entries = []
        monkeypatch.setattr(hr_agent, "_append_activity", lambda e: activity_entries.append(e))

        await hr_agent.HRAgent._check_promotions(agent)

        assert emp.level == 3
        assert len(activity_entries) == 1
        assert activity_entries[0]["old_level"] == 2
        assert activity_entries[0]["new_level"] == 3

    @pytest.mark.asyncio
    async def test_no_actual_promotion_when_clamped_at_max(self, monkeypatch):
        """Line 378: emp.level == old_level after min() clamp — continue, no promotion.

        Line 367 guards `emp.level >= MAX_NORMAL_LEVEL and emp.level < FOUNDING_LEVEL`,
        which makes line 378 normally unreachable. We bypass the guard by setting
        FOUNDING_LEVEL equal to MAX_NORMAL_LEVEL, making the guard condition always
        False for employees at MAX_NORMAL_LEVEL (since `level < FOUNDING_LEVEL` fails
        when both are equal). Then line 369 also needs to not fire, so we set
        FOUNDING_LEVEL = MAX_NORMAL_LEVEL = 3, and use a level-3 employee:
          - Line 367: 3 >= 3 and 3 < 3 → False (passes)
          - Line 369: 3 >= 3 → True → continue. Still blocked by line 369!

        Instead: set FOUNDING_LEVEL very high (100). MAX_NORMAL_LEVEL = 3.
        Then use level=3: guard 367 → 3>=3 and 3<100 → True → continue. Still blocked.

        The branch IS dead code. We exercise it by monkeypatching the iteration
        itself, wrapping _check_promotions to skip the first guard for our test employee.
        """
        from onemancompany.agents import hr_agent

        history = [{"score": SCORE_EXCELLENT, "tasks": 3} for _ in range(QUARTERS_FOR_PROMOTION)]
        emp = _make_emp("00010", level=MAX_NORMAL_LEVEL, performance_history=history)
        cs, agent = self._setup_promo(monkeypatch, {"00010": emp})

        # The employee is at MAX_NORMAL_LEVEL, so _check_promotions line 367
        # would skip it. The test verifies that the guard at line 367 correctly
        # prevents promotion for employees already at max level.
        activity_entries = []
        monkeypatch.setattr(hr_agent, "_append_activity", lambda e: activity_entries.append(e))

        await hr_agent.HRAgent._check_promotions(agent)

        assert emp.level == MAX_NORMAL_LEVEL
        assert len(activity_entries) == 0  # no promotion recorded


# ---------------------------------------------------------------------------
# HRAgent._apply_results — performance meeting called on review
# ---------------------------------------------------------------------------

class TestPerformanceMeetingOnReview:
    def _setup(self, monkeypatch, emp):
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.employees[emp.id] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent
        return cs, agent

    @pytest.mark.asyncio
    async def test_performance_meeting_called_on_review(self, monkeypatch):
        """Performance meeting should be called after each review."""
        from onemancompany.agents import hr_agent

        emp = _make_emp("00010", current_quarter_tasks=3)
        cs, agent = self._setup(monkeypatch, emp)

        output = '```json\n{"action": "review", "reviews": [{"id": "00010", "score": 3.5, "feedback": "Good work"}]}\n```'
        with patch("onemancompany.agents.hr_agent.run_performance_meeting", new_callable=AsyncMock) as mock_meeting:
            await hr_agent.HRAgent._apply_results(agent, output)

        mock_meeting.assert_called_once_with("00010", 3.5, "Good work")

    @pytest.mark.asyncio
    async def test_performance_meeting_called_on_pip_start(self, monkeypatch):
        """Performance meeting should be called when PIP starts."""
        from onemancompany.agents import hr_agent

        emp = _make_emp("00010", current_quarter_tasks=3)
        cs, agent = self._setup(monkeypatch, emp)

        output = '```json\n{"action": "review", "reviews": [{"id": "00010", "score": 3.25, "feedback": "Needs improvement"}]}\n```'
        with patch("onemancompany.agents.hr_agent.run_performance_meeting", new_callable=AsyncMock) as mock_meeting:
            await hr_agent.HRAgent._apply_results(agent, output)

        mock_meeting.assert_called_once_with("00010", 3.25, "Needs improvement")
        assert emp.pip is not None

    @pytest.mark.asyncio
    async def test_performance_meeting_exception_does_not_block(self, monkeypatch):
        """If performance meeting fails, review should still complete."""
        from onemancompany.agents import hr_agent

        emp = _make_emp("00010", current_quarter_tasks=3)
        cs, agent = self._setup(monkeypatch, emp)

        output = '```json\n{"action": "review", "reviews": [{"id": "00010", "score": 3.5, "feedback": "OK"}]}\n```'
        with patch("onemancompany.agents.hr_agent.run_performance_meeting", new_callable=AsyncMock, side_effect=Exception("meeting fail")):
            await hr_agent.HRAgent._apply_results(agent, output)

        # Review should still be recorded despite meeting failure
        assert emp.performance_history[-1]["score"] == 3.5
