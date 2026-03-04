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

def _make_cs() -> CompanyState:
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
        from onemancompany.agents import hr_agent
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
        candidate = hr_agent._talent_to_candidate(talent)

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
        from onemancompany.agents import hr_agent
        from onemancompany.core import config as config_mod

        monkeypatch.setattr(config_mod, "load_talent_skills", lambda tid: [])
        monkeypatch.setattr(config_mod, "load_talent_tools", lambda tid: [])

        talent = {
            "id": "anthropic_talent",
            "api_provider": "anthropic",
            "llm_model": "claude-sonnet",
        }

        candidate = hr_agent._talent_to_candidate(talent)
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
        monkeypatch.setattr(hr_agent, "update_employee_performance", lambda eid, tasks, hist: None)

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
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp = _make_emp("00010", level=2)
        cs.employees["00010"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)
        monkeypatch.setattr(hr_agent, "move_employee_to_ex", lambda eid: True)
        monkeypatch.setattr(hr_agent, "compute_layout", lambda cs: {})
        monkeypatch.setattr(hr_agent, "persist_all_desk_positions", lambda cs: None)

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent

        output = '```json\n{"action": "fire", "employee_id": "00010", "reason": "poor performance"}\n```'
        await hr_agent.HRAgent._apply_results(agent, output)

        assert "00010" not in cs.employees
        assert "00010" in cs.ex_employees
        assert len(cs.activity_log) == 1
        assert cs.activity_log[0]["type"] == "employee_fired"

    @pytest.mark.asyncio
    async def test_cannot_fire_founding_employee(self, monkeypatch):
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        emp = _make_emp("00002", level=FOUNDING_LEVEL)
        cs.employees["00002"] = emp
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent

        output = '```json\n{"action": "fire", "employee_id": "00002", "reason": "trying to fire founder"}\n```'
        await hr_agent.HRAgent._apply_results(agent, output)

        # Should NOT be fired
        assert "00002" in cs.employees
        assert "00002" not in cs.ex_employees

    @pytest.mark.asyncio
    async def test_fire_nonexistent_employee_no_error(self, monkeypatch):
        from onemancompany.agents import hr_agent
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(hr_agent, "company_state", cs)

        agent = MagicMock()
        agent._publish = AsyncMock()
        agent.__class__ = hr_agent.HRAgent

        output = '```json\n{"action": "fire", "employee_id": "99999"}\n```'
        # Should not raise
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
        monkeypatch.setattr(hr_agent, "update_employee_level", lambda eid, lvl, title: None)
        monkeypatch.setattr(hr_agent, "compute_layout", lambda cs: {})
        monkeypatch.setattr(hr_agent, "persist_all_desk_positions", lambda cs: None)

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

        await hr_agent.HRAgent._check_promotions(agent)

        assert len(cs.activity_log) == 1
        assert cs.activity_log[0]["type"] == "promotion"
        assert cs.activity_log[0]["old_level"] == 1
        assert cs.activity_log[0]["new_level"] == 2
