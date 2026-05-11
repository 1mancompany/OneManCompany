"""Coverage tests for agents/recruitment.py — missing lines."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_recruitment_state():
    import onemancompany.agents.recruitment as rec_mod

    rec_mod.pending_candidates.clear()
    rec_mod._pending_project_ctx.clear()
    rec_mod._last_search_results.clear()
    rec_mod._last_session_id = ""
    rec_mod.talent_market._session = None
    rec_mod.talent_market._stack = None
    rec_mod.talent_market._api_key = ""
    rec_mod.talent_market._url = ""
    yield
    rec_mod.pending_candidates.clear()
    rec_mod._pending_project_ctx.clear()
    rec_mod._last_search_results.clear()
    rec_mod._last_session_id = ""
    rec_mod.talent_market._session = None
    rec_mod.talent_market._stack = None
    rec_mod.talent_market._api_key = ""
    rec_mod.talent_market._url = ""


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

    def test_compute_salary_failure_defaults_cost_to_zero(self):
        from onemancompany.agents.recruitment import _normalize_market_candidate

        candidate = {
            "talent": {
                "profile": {
                    "id": "t1",
                    "name": "Worker",
                    "role": "Engineer",
                    "llm_model": "broken-model",
                    "api_provider": "openrouter",
                },
                "skills_detail": [],
                "tools_detail": [],
            }
        }
        with patch("onemancompany.core.model_costs.compute_salary", side_effect=RuntimeError("bad model")):
            result = _normalize_market_candidate(candidate)
        assert result["cost_per_1m_tokens"] == 0.0


# ---------------------------------------------------------------------------
# TalentMarketClient reconnect / retry branches
# ---------------------------------------------------------------------------

class TestTalentMarketClientCoverage:
    @pytest.mark.asyncio
    async def test_call_auto_reconnects_before_first_request(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        client._url = "https://talent.test/sse"
        client._api_key = "secret"

        payload = MagicMock()
        payload.text = json.dumps({"status": "ok"})
        result_obj = MagicMock()
        result_obj.content = [payload]

        session = AsyncMock()
        session.call_tool = AsyncMock(return_value=result_obj)

        async def _fake_reconnect():
            client._session = session

        client._reconnect = AsyncMock(side_effect=_fake_reconnect)

        result = await client._call("ping")

        assert result == {"status": "ok"}
        client._reconnect.assert_awaited_once()
        session.call_tool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_call_retries_once_after_tool_error(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        client._api_key = "secret"

        payload = MagicMock()
        payload.text = json.dumps({"status": "retried"})
        result_obj = MagicMock()
        result_obj.content = [payload]

        session = AsyncMock()
        session.call_tool = AsyncMock(side_effect=[RuntimeError("boom"), result_obj])
        client._session = session
        client._reconnect = AsyncMock()

        result = await client._call("ping")

        assert result == {"status": "retried"}
        client._reconnect.assert_awaited_once()
        assert session.call_tool.await_count == 2

    @pytest.mark.asyncio
    async def test_call_does_not_retry_when_retry_disabled(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        client._api_key = "secret"
        session = AsyncMock()
        session.call_tool = AsyncMock(side_effect=RuntimeError("boom"))
        client._session = session

        with pytest.raises(RuntimeError, match="boom"):
            await client._call("ping", _retry=False)

    @pytest.mark.asyncio
    async def test_reconnect_recovers_from_disconnect_error(self):
        from onemancompany.agents.recruitment import TalentMarketClient

        client = TalentMarketClient()
        client._session = AsyncMock()
        client._stack = AsyncMock()
        client._url = "https://talent.test/sse"
        client._api_key = "secret"
        client.disconnect = AsyncMock(side_effect=RuntimeError("stale socket"))
        client.connect = AsyncMock()

        await client._reconnect()

        assert client._session is None
        assert client._stack is None
        client.connect.assert_awaited_once_with("https://talent.test/sse", "secret")


# ---------------------------------------------------------------------------
# start_talent_market
# ---------------------------------------------------------------------------

class TestStartTalentMarketCoverage:
    @pytest.mark.asyncio
    async def test_start_connects_when_api_key_exists(self, monkeypatch):
        import onemancompany.agents.recruitment as rec_mod

        connect = AsyncMock()
        monkeypatch.setattr(rec_mod, "load_app_config", lambda: {
            "talent_market": {"url": "https://talent.test/sse", "api_key": "abc123"}
        })
        monkeypatch.setattr(rec_mod.talent_market, "connect", connect)

        await rec_mod.start_talent_market()

        connect.assert_awaited_once_with("https://talent.test/sse", "abc123")


# ---------------------------------------------------------------------------
# Response normalization helpers
# ---------------------------------------------------------------------------

class TestResponseHelpers:
    def test_normalize_api_response_wraps_flat_results(self):
        from onemancompany.agents.recruitment import _normalize_api_response

        result = _normalize_api_response({"results": [{"id": "c1"}]})
        assert result["roles"] == [{"role": "General", "candidates": [{"id": "c1"}]}]

    def test_normalize_api_response_preserves_grouped_results(self):
        from onemancompany.agents.recruitment import _normalize_api_response

        grouped = {"results": [{"role": "Engineer", "candidates": [{"id": "c1"}]}]}
        result = _normalize_api_response(grouped)
        assert result["roles"] == grouped["results"]

    def test_is_error_response_handles_error_payload_shapes(self):
        from onemancompany.agents.recruitment import _is_error_response

        assert _is_error_response({"error": {"message": "no credits"}}) == "no credits"
        assert _is_error_response({"status": "error", "message": "bad request"}) == "bad request"


# ---------------------------------------------------------------------------
# search_candidates and shortlist branches
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


class TestSearchCandidatesCoverage:
    @pytest.mark.asyncio
    async def test_ai_error_retries_without_ai_and_returns_warning(self, monkeypatch):
        import onemancompany.agents.recruitment as rec_mod

        monkeypatch.setattr(rec_mod, "load_app_config", lambda: {
            "talent_market": {"mode": "remote", "use_ai_search": True}
        })
        monkeypatch.setattr(rec_mod, "_persist_candidates", lambda: None)
        monkeypatch.setattr(rec_mod, "_auto_submit_shortlist", AsyncMock(return_value="auto-submitted"))
        rec_mod.talent_market._session = object()
        rec_mod.talent_market.search = AsyncMock(side_effect=[
            {"error": {"message": "AI credits exhausted"}},
            {"results": [{"id": "c1", "talent_id": "c1", "name": "Ada"}], "session_id": "sess-1"},
        ])

        result = await rec_mod.search_candidates.ainvoke({"job_description": "Need a backend engineer"})

        assert result["auto_shortlisted"] is True
        assert "AI search unavailable" in result["warning"]
        assert result["roles"][0]["candidates"][0]["id"] == "c1"

    @pytest.mark.asyncio
    async def test_ai_error_falls_back_to_local_when_standard_search_empty(self, monkeypatch):
        import onemancompany.agents.recruitment as rec_mod

        local = {
            "type": "individual",
            "summary": "Local fallback",
            "roles": [{"role": "Local", "description": "jd", "candidates": [{"id": "local-1"}]}],
        }
        monkeypatch.setattr(rec_mod, "load_app_config", lambda: {
            "talent_market": {"mode": "remote", "use_ai_search": True}
        })
        monkeypatch.setattr(rec_mod, "_persist_candidates", lambda: None)
        monkeypatch.setattr(rec_mod, "_local_fallback_search", lambda jd: local)
        rec_mod.talent_market._session = object()
        rec_mod.talent_market.search = AsyncMock(side_effect=[
            {"error": {"message": "AI credits exhausted"}},
            {"results": []},
        ])

        result = await rec_mod.search_candidates.ainvoke({"job_description": "Need a backend engineer"})

        assert result["roles"][0]["candidates"][0]["id"] == "local-1"
        assert "Standard search returned no results" in result["warning"]

    @pytest.mark.asyncio
    async def test_ai_error_then_standard_search_error_falls_back_to_local(self, monkeypatch):
        import onemancompany.agents.recruitment as rec_mod

        local = {
            "type": "individual",
            "summary": "Local fallback",
            "roles": [{"role": "Local", "description": "jd", "candidates": [{"id": "local-1"}]}],
        }
        monkeypatch.setattr(rec_mod, "load_app_config", lambda: {
            "talent_market": {"mode": "remote", "use_ai_search": True}
        })
        monkeypatch.setattr(rec_mod, "_persist_candidates", lambda: None)
        monkeypatch.setattr(rec_mod, "_local_fallback_search", lambda jd: local)
        rec_mod.talent_market._session = object()
        rec_mod.talent_market.search = AsyncMock(side_effect=[
            {"error": {"message": "AI credits exhausted"}},
            {"status": "error", "message": "standard search unavailable"},
        ])

        result = await rec_mod.search_candidates.ainvoke({"job_description": "Need a backend engineer"})

        assert result["warning"] == "Cloud search failed (AI credits exhausted). Using local talent pool instead."
        assert result["roles"][0]["candidates"][0]["id"] == "local-1"

    @pytest.mark.asyncio
    async def test_ai_error_then_standard_roles_return_market_results(self, monkeypatch):
        import onemancompany.agents.recruitment as rec_mod

        monkeypatch.setattr(rec_mod, "load_app_config", lambda: {
            "talent_market": {"mode": "remote", "use_ai_search": True}
        })
        monkeypatch.setattr(rec_mod, "_persist_candidates", lambda: None)
        monkeypatch.setattr(rec_mod, "_auto_submit_shortlist", AsyncMock(return_value="auto-submitted"))
        rec_mod.talent_market._session = object()
        rec_mod.talent_market.search = AsyncMock(side_effect=[
            {"error": {"message": "AI credits exhausted"}},
            {"roles": [{"role": "Engineer", "description": "jd", "candidates": [{"id": "c1", "talent_id": "c1"}]}]},
        ])

        result = await rec_mod.search_candidates.ainvoke({"job_description": "Need a backend engineer"})

        assert result["auto_shortlisted"] is True
        assert result["warning"] == "AI search unavailable (AI credits exhausted). Showing standard search results."

    @pytest.mark.asyncio
    async def test_non_ai_error_falls_back_to_local(self, monkeypatch):
        import onemancompany.agents.recruitment as rec_mod

        local = {
            "type": "individual",
            "summary": "Local fallback",
            "roles": [{"role": "Local", "description": "jd", "candidates": [{"id": "local-1"}]}],
        }
        monkeypatch.setattr(rec_mod, "load_app_config", lambda: {
            "talent_market": {"mode": "remote", "use_ai_search": False}
        })
        monkeypatch.setattr(rec_mod, "_persist_candidates", lambda: None)
        monkeypatch.setattr(rec_mod, "_local_fallback_search", lambda jd: local)
        rec_mod.talent_market._session = object()
        rec_mod.talent_market.search = AsyncMock(return_value={"status": "error", "message": "quota"})

        result = await rec_mod.search_candidates.ainvoke({"job_description": "Need a backend engineer"})

        assert result["roles"][0]["candidates"][0]["id"] == "local-1"
        assert result["warning"] == "Cloud search failed (quota). Using local talent pool instead."

    @pytest.mark.asyncio
    async def test_remote_search_exception_uses_local_warning(self, monkeypatch):
        import onemancompany.agents.recruitment as rec_mod

        local = {
            "type": "individual",
            "summary": "Local fallback",
            "roles": [{"role": "Local", "description": "jd", "candidates": [{"id": "local-1"}]}],
        }
        monkeypatch.setattr(rec_mod, "load_app_config", lambda: {
            "talent_market": {"mode": "remote"}
        })
        monkeypatch.setattr(rec_mod, "_persist_candidates", lambda: None)
        monkeypatch.setattr(rec_mod, "_local_fallback_search", lambda jd: local)
        rec_mod.talent_market._session = object()
        rec_mod.talent_market.search = AsyncMock(side_effect=RuntimeError("socket closed"))

        result = await rec_mod.search_candidates.ainvoke({"job_description": "Need a backend engineer"})

        assert result["warning"] == "Cloud connection error. Using local talent pool instead."

    @pytest.mark.asyncio
    async def test_remote_mode_without_connection_returns_local_warning(self, monkeypatch):
        import onemancompany.agents.recruitment as rec_mod

        local = {
            "type": "individual",
            "summary": "Local fallback",
            "roles": [{"role": "Local", "description": "jd", "candidates": [{"id": "local-1"}]}],
        }
        monkeypatch.setattr(rec_mod, "load_app_config", lambda: {
            "talent_market": {"mode": "remote"}
        })
        monkeypatch.setattr(rec_mod, "_persist_candidates", lambda: None)
        monkeypatch.setattr(rec_mod, "_local_fallback_search", lambda jd: local)

        result = await rec_mod.search_candidates.ainvoke({"job_description": "Need a backend engineer"})

        assert result["warning"] == "Cloud Talent Market not connected. Using local talent pool."

    @pytest.mark.asyncio
    async def test_search_normalizes_nested_market_candidates_and_skips_missing_ids(self, monkeypatch):
        import onemancompany.agents.recruitment as rec_mod

        nested_candidate = {
            "talent": {
                "id": "tal-1",
                "profile": {
                    "id": "tal-1",
                    "name": "Ada",
                    "role": "Engineer",
                    "llm_model": "gpt-4.1-mini",
                    "api_provider": "openrouter",
                },
                "skills_detail": [{"name": "Python", "description": "ships code"}],
                "tools_detail": [{"name": "bash", "description": "shell"}],
            },
            "score": 0.95,
        }
        monkeypatch.setattr(rec_mod, "load_app_config", lambda: {
            "talent_market": {"mode": "remote"}
        })
        monkeypatch.setattr(rec_mod, "_persist_candidates", lambda: None)
        monkeypatch.setattr(rec_mod, "_auto_submit_shortlist", AsyncMock(return_value="auto-submitted"))
        rec_mod.talent_market._session = object()
        rec_mod.talent_market.search = AsyncMock(return_value={
            "roles": [{
                "role": "Engineer",
                "description": "Need a backend engineer",
                "candidates": [nested_candidate, {"name": "No ID candidate"}],
            }]
        })

        with patch("onemancompany.core.model_costs.compute_salary", return_value=5.5):
            result = await rec_mod.search_candidates.ainvoke({"job_description": "Need a backend engineer"})

        assert result["roles"][0]["candidates"][0]["id"] == "tal-1"
        assert "tal-1" in rec_mod._last_search_results
        assert len(rec_mod._last_search_results) == 1


class TestShortlistCoverage:
    @pytest.mark.asyncio
    async def test_create_batch_rejects_when_pending_already_exists(self):
        import onemancompany.agents.recruitment as rec_mod

        rec_mod.pending_candidates["batch-1"] = [{"id": "c0"}]
        result = await rec_mod._create_and_publish_batch("jd", [{"id": "c1"}], [])

        assert result == "A shortlist is already pending (batch_id=batch-1)."

    @pytest.mark.asyncio
    async def test_create_batch_requires_candidates(self):
        import onemancompany.agents.recruitment as rec_mod

        result = await rec_mod._create_and_publish_batch("jd", [], [])

        assert result == "ERROR: No valid candidates found."

    @pytest.mark.asyncio
    async def test_submit_shortlist_requires_known_candidate_ids(self):
        import onemancompany.agents.recruitment as rec_mod

        result = await rec_mod.submit_shortlist.ainvoke({
            "jd": "Need a backend engineer",
            "candidate_ids": ["missing"],
        })

        assert result == "ERROR: No valid candidates found. Call search_candidates() first."

    @pytest.mark.asyncio
    async def test_submit_shortlist_hydrates_roles_from_cached_candidates(self, monkeypatch):
        import onemancompany.agents.recruitment as rec_mod

        captured = {}
        rec_mod._last_search_results["c1"] = {"id": "c1", "name": "Ada"}

        async def _fake_create(jd, candidates, roles):
            captured["jd"] = jd
            captured["candidates"] = candidates
            captured["roles"] = roles
            return "ok"

        monkeypatch.setattr(rec_mod, "_create_and_publish_batch", _fake_create)

        result = await rec_mod.submit_shortlist.ainvoke({
            "jd": "Need a backend engineer",
            "candidate_ids": ["c1"],
            "roles": [{
                "role": "Engineer",
                "description": "Backend work",
                "candidates": [{"id": "c1"}, {"id": "missing"}],
            }],
        })

        assert result == "ok"
        assert captured["candidates"] == [{"id": "c1", "name": "Ada"}]
        assert captured["roles"] == [{
            "role": "Engineer",
            "description": "Backend work",
            "candidates": [{"id": "c1", "name": "Ada"}],
        }]
