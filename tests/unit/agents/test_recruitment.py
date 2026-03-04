"""Unit tests for agents/recruitment.py — candidate search and shortlist."""

from __future__ import annotations

import random
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.state import CompanyState


# ---------------------------------------------------------------------------
# _talent_to_candidate (migrated from test_hr_agent.py)
# ---------------------------------------------------------------------------

class TestTalentToCandidate:
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
# search_candidates
# ---------------------------------------------------------------------------

class TestSearchCandidates:
    @pytest.mark.asyncio
    async def test_returns_candidates(self, monkeypatch):
        from onemancompany.agents import recruitment

        # Mock _call_boss_online to return fake candidates
        fake_candidates = [
            {"id": "c1", "name": "Candidate 1", "talent_id": "c1"},
            {"id": "c2", "name": "Candidate 2", "talent_id": "c2"},
        ]
        monkeypatch.setattr(recruitment, "_call_boss_online", AsyncMock(return_value=fake_candidates))

        result = await recruitment.search_candidates.ainvoke({"job_description": "python dev"})

        assert len(result) == 2
        assert result[0]["name"] == "Candidate 1"
        # Should stash results
        assert "c1" in recruitment._last_search_results

    @pytest.mark.asyncio
    async def test_fallback_to_local_talents(self, monkeypatch):
        from onemancompany.agents import recruitment
        from onemancompany.core import config as config_mod

        # Make boss online fail
        monkeypatch.setattr(
            recruitment, "_call_boss_online",
            AsyncMock(side_effect=RuntimeError("offline")),
        )
        # Provide local talents as fallback
        monkeypatch.setattr(config_mod, "list_available_talents", lambda: [{"id": "local1"}])
        monkeypatch.setattr(
            config_mod, "load_talent_profile",
            lambda tid: {"id": "local1", "name": "Local Dev", "skills": [], "api_provider": "openrouter"},
        )
        monkeypatch.setattr(config_mod, "load_talent_skills", lambda tid: [])
        monkeypatch.setattr(config_mod, "load_talent_tools", lambda tid: [])

        result = await recruitment.search_candidates.ainvoke({"job_description": "any dev"})

        assert len(result) >= 1
        assert result[0]["id"] == "local1"


# ---------------------------------------------------------------------------
# pending_candidates
# ---------------------------------------------------------------------------

class TestPendingCandidates:
    def test_store_and_retrieve(self):
        from onemancompany.agents.recruitment import pending_candidates

        pending_candidates.clear()
        batch_id = "test_batch"
        candidates = [{"id": "c1", "name": "Test"}]
        pending_candidates[batch_id] = candidates

        assert batch_id in pending_candidates
        assert pending_candidates[batch_id] == candidates

        # Cleanup
        pending_candidates.clear()
