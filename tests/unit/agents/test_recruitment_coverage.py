"""Coverage tests for agents/recruitment.py — missing lines."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _persist_candidates — no event loop fallback (lines 117-124)
# ---------------------------------------------------------------------------

class TestPersistCandidates:
    def test_no_event_loop_sync_write(self, tmp_path, monkeypatch):
        import onemancompany.agents.recruitment as rec_mod
        import onemancompany.core.store as store_mod
        monkeypatch.setattr(store_mod, "COMPANY_DIR", tmp_path)

        (tmp_path / "candidates").mkdir(parents=True)

        # Ensure no running loop for sync fallback
        rec_mod._persist_candidates()


# ---------------------------------------------------------------------------
# _load_candidates_from_disk (lines 127-143)
# ---------------------------------------------------------------------------

class TestLoadCandidatesFromDisk:
    def test_load_empty(self, monkeypatch):
        import onemancompany.agents.recruitment as rec_mod
        import onemancompany.core.store as store_mod
        with patch.object(store_mod, "load_candidates", return_value=None):
            rec_mod._load_candidates_from_disk()

    def test_load_with_data(self, monkeypatch):
        import onemancompany.agents.recruitment as rec_mod
        import onemancompany.core.store as store_mod
        data = {
            "batches": {"batch1": [{"id": "c1"}]},
            "project_ctx": {"batch1": {"project_id": "p1"}},
            "search_results": {"c1": {"name": "Test"}},
            "session_id": "sess_123",
        }
        with patch.object(store_mod, "load_candidates", return_value=data):
            rec_mod._load_candidates_from_disk()
        assert "batch1" in rec_mod.pending_candidates
        assert rec_mod._last_session_id == "sess_123"
        # Cleanup
        rec_mod.pending_candidates.pop("batch1", None)
        rec_mod._pending_project_ctx.pop("batch1", None)
        rec_mod._last_search_results.pop("c1", None)
        rec_mod._last_session_id = ""


# ---------------------------------------------------------------------------
# _extract_candidate_id — nested talent format (lines 163-170)
# ---------------------------------------------------------------------------

class TestExtractCandidateId:
    def test_flat_id(self):
        from onemancompany.agents.recruitment import _extract_candidate_id
        assert _extract_candidate_id({"id": "c1"}) == "c1"

    def test_nested_talent_id(self):
        from onemancompany.agents.recruitment import _extract_candidate_id
        candidate = {"talent": {"id": "t1", "profile": {"id": "p1"}}}
        assert _extract_candidate_id(candidate) == "t1"

    def test_nested_profile_id(self):
        from onemancompany.agents.recruitment import _extract_candidate_id
        candidate = {"talent": {"profile": {"id": "p1"}}}
        assert _extract_candidate_id(candidate) == "p1"

    def test_no_id(self):
        from onemancompany.agents.recruitment import _extract_candidate_id
        assert _extract_candidate_id({}) == ""


# ---------------------------------------------------------------------------
# _normalize_market_candidate (lines 179-218)
# ---------------------------------------------------------------------------

class TestNormalizeMarketCandidate:
    def test_non_dict_talent(self):
        """talent key is not a dict — return as-is."""
        from onemancompany.agents.recruitment import _normalize_market_candidate
        c = {"id": "c1", "name": "Test", "talent": "not a dict"}
        assert _normalize_market_candidate(c) == c

    def test_nested_talent(self):
        from onemancompany.agents.recruitment import _normalize_market_candidate
        c = {
            "talent": {
                "id": "t1",
                "profile": {
                    "id": "t1",
                    "name": "Worker",
                    "role": "Dev",
                    "llm_model": "gpt-4",
                    "api_provider": "openrouter",
                },
                "skills_detail": [{"name": "Python", "description": "coding"}],
                "tools_detail": [{"name": "bash", "description": "shell"}],
            },
            "score": 0.9,
            "reasoning": "Good fit",
        }
        with patch("onemancompany.core.model_costs.compute_salary", return_value=5.0):
            result = _normalize_market_candidate(c)
        assert result["id"] == "t1"
        assert result["name"] == "Worker"
        assert len(result["skill_set"]) == 1

    def test_non_dict_profile(self):
        """profile key is not a dict — return as-is."""
        from onemancompany.agents.recruitment import _normalize_market_candidate
        c = {"talent": {"profile": "not a dict"}}
        assert _normalize_market_candidate(c) == c


# ---------------------------------------------------------------------------
# search_candidates (lines 354-368)
# ---------------------------------------------------------------------------

class TestLocalFallbackSearch:
    def test_search_generates_candidates(self):
        """Cover lines 456-491: local fallback search."""
        from onemancompany.agents.recruitment import _local_fallback_search
        with patch("onemancompany.agents.recruitment._normalize_api_response", return_value={
            "candidates": [{"id": "c1"}],
            "roles": [],
        }), \
             patch("onemancompany.agents.base.tracked_ainvoke", new_callable=AsyncMock):
            # _local_fallback_search uses LLM, mock it
            result = _local_fallback_search("Need a developer")
        assert isinstance(result, dict)
