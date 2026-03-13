"""Tests for GET /api/talent-pool endpoint."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


class TestTalentPoolLocal:
    @pytest.mark.asyncio
    async def test_local_fallback(self, monkeypatch):
        """Returns local talents when talent_market not connected."""
        from onemancompany.agents import recruitment
        from onemancompany.core import config as config_mod

        monkeypatch.setattr(recruitment.talent_market, "_session", None)
        monkeypatch.setattr(config_mod, "list_available_talents", lambda: [
            {"id": "local1", "name": "Local Dev"},
        ])
        monkeypatch.setattr(config_mod, "load_talent_profile", lambda tid: {
            "id": "local1", "name": "Local Dev", "role": "Engineer", "skills": ["python"],
        })

        from onemancompany.api.routes import get_talent_pool
        result = await get_talent_pool()

        assert result["source"] == "local"
        assert len(result["talents"]) == 1
        assert result["talents"][0]["talent_id"] == "local1"
        assert result["talents"][0]["status"] == "local"


class TestTalentPoolAPI:
    @pytest.mark.asyncio
    async def test_api_source(self, monkeypatch):
        """Returns API talents when talent_market is connected."""
        from onemancompany.agents import recruitment

        monkeypatch.setattr(recruitment.talent_market, "_session", MagicMock())
        recruitment.talent_market.list_my_talents = AsyncMock(return_value={
            "talents": [
                {"talent_id": "api1", "name": "API Dev", "role": "Engineer",
                 "skills": ["react"], "purchased_at": "2026-03-10T12:00:00Z"},
            ]
        })

        from onemancompany.api.routes import get_talent_pool
        result = await get_talent_pool()

        assert result["source"] == "api"
        assert len(result["talents"]) == 1
        assert result["talents"][0]["talent_id"] == "api1"
        assert result["talents"][0]["status"] == "purchased"

        # Cleanup
        recruitment.talent_market._session = None

    @pytest.mark.asyncio
    async def test_api_error_falls_through_to_local(self, monkeypatch):
        """When API call fails, falls through to local."""
        from onemancompany.agents import recruitment
        from onemancompany.core import config as config_mod

        monkeypatch.setattr(recruitment.talent_market, "_session", MagicMock())
        recruitment.talent_market.list_my_talents = AsyncMock(side_effect=RuntimeError("API error"))

        monkeypatch.setattr(config_mod, "list_available_talents", lambda: [])

        from onemancompany.api.routes import get_talent_pool
        result = await get_talent_pool()

        assert result["source"] == "local"
        assert result["talents"] == []

        # Cleanup
        recruitment.talent_market._session = None
