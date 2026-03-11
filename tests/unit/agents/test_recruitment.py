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

        # Mock _call_boss_online to return role-grouped result
        fake_result = {
            "type": "individual",
            "summary": "Test",
            "roles": [
                {
                    "role": "Engineer",
                    "description": "python dev",
                    "candidates": [
                        {"id": "c1", "name": "Candidate 1", "talent_id": "c1"},
                        {"id": "c2", "name": "Candidate 2", "talent_id": "c2"},
                    ],
                }
            ],
        }
        monkeypatch.setattr(recruitment, "_call_boss_online", AsyncMock(return_value=fake_result))

        result = await recruitment.search_candidates.ainvoke({"job_description": "python dev"})

        assert isinstance(result, dict)
        assert len(result["roles"]) == 1
        assert len(result["roles"][0]["candidates"]) == 2
        assert result["roles"][0]["candidates"][0]["name"] == "Candidate 1"
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

        assert isinstance(result, dict)
        assert len(result["roles"]) >= 1
        assert len(result["roles"][0]["candidates"]) >= 1
        assert result["roles"][0]["candidates"][0]["id"] == "local1"


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


# ---------------------------------------------------------------------------
# start_boss_online / stop_boss_online / _call_boss_online
# ---------------------------------------------------------------------------

class TestBossOnlineLifecycle:
    @pytest.mark.asyncio
    async def test_start_boss_online(self, monkeypatch):
        """Lines 108-129: start_boss_online initializes MCP session."""
        from onemancompany.agents import recruitment
        from contextlib import AsyncExitStack

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()

        mock_read = AsyncMock()
        mock_write = AsyncMock()

        call_count = 0

        async def mock_enter(cm):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (mock_read, mock_write)
            return mock_session

        mock_stack = AsyncMock()
        mock_stack.enter_async_context = mock_enter
        mock_stack.aclose = AsyncMock()

        recruitment._boss_session = None

        with patch("contextlib.AsyncExitStack", return_value=mock_stack):
            with patch.object(recruitment, "stdio_client", return_value=AsyncMock()):
                with patch.object(recruitment, "ClientSession", return_value=AsyncMock()):
                    await recruitment.start_boss_online()

        assert recruitment._boss_session is mock_session
        mock_session.initialize.assert_awaited_once()
        # Clean up
        recruitment._boss_session = None

    @pytest.mark.asyncio
    async def test_stop_boss_online_with_session(self, monkeypatch):
        """Lines 135-140: stop_boss_online tears down exit stack."""
        from onemancompany.agents import recruitment

        mock_stack = AsyncMock()
        mock_stack.aclose = AsyncMock()

        mock_session = MagicMock()
        mock_session._exit_stack = mock_stack

        recruitment._boss_session = mock_session

        await recruitment.stop_boss_online()

        assert recruitment._boss_session is None
        mock_stack.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_boss_online_no_session(self, monkeypatch):
        """Lines 135-140: stop_boss_online with no active session is a noop."""
        from onemancompany.agents import recruitment

        recruitment._boss_session = None
        await recruitment.stop_boss_online()
        assert recruitment._boss_session is None

    @pytest.mark.asyncio
    async def test_stop_boss_online_no_exit_stack(self, monkeypatch):
        """Lines 136-138: stop with session but no _exit_stack attr."""
        from onemancompany.agents import recruitment

        mock_session = MagicMock(spec=[])  # No attributes
        recruitment._boss_session = mock_session

        await recruitment.stop_boss_online()
        assert recruitment._boss_session is None


class TestCallBossOnline:
    @pytest.mark.asyncio
    async def test_call_boss_online_no_session(self):
        """Lines 145-146: _call_boss_online raises when no session."""
        from onemancompany.agents import recruitment

        recruitment._boss_session = None
        with pytest.raises(RuntimeError, match="not running"):
            await recruitment._call_boss_online("python dev")

    @pytest.mark.asyncio
    async def test_call_boss_online_success(self):
        """Lines 148-158: _call_boss_online parses results from session."""
        from onemancompany.agents import recruitment
        import json

        mock_item1 = MagicMock()
        mock_item1.text = json.dumps({
            "type": "individual",
            "summary": "Test",
            "roles": [{"role": "Engineer", "description": "", "candidates": [{"id": "c1", "name": "Candidate 1"}]}],
        })
        mock_item2 = MagicMock()
        mock_item2.text = "not valid json"

        mock_result = MagicMock()
        mock_result.content = [mock_item1, mock_item2]

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        recruitment._boss_session = mock_session

        try:
            result = await recruitment._call_boss_online("python dev", count=5)
            assert isinstance(result, dict)
            assert "roles" in result
            assert len(result["roles"]) == 1
            assert result["roles"][0]["candidates"][0]["id"] == "c1"
            mock_session.call_tool.assert_awaited_once_with(
                "search_candidates",
                arguments={"job_description": "python dev", "count": 5},
            )
        finally:
            recruitment._boss_session = None
