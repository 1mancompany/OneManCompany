"""Tests for /api/auth/* endpoints."""
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport


def _make_test_app():
    """Create a minimal FastAPI app with auth routes."""
    from fastapi import FastAPI
    from onemancompany.api.routes import router

    app = FastAPI()
    app.include_router(router)
    return app


class TestGetProviders:
    async def test_returns_all_groups(self):
        app = _make_test_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/auth/providers")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 12  # 12 provider groups

        # Check structure
        first = data[0]
        assert "group_id" in first
        assert "label" in first
        assert "choices" in first
        assert isinstance(first["choices"], list)

    async def test_choices_have_available_field(self):
        app = _make_test_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/auth/providers")

        data = resp.json()
        for group in data:
            for choice in group["choices"]:
                assert "available" in choice
                assert "provider" in choice
                assert "auth_method" in choice


class TestVerifyEndpoint:
    async def test_verify_success(self):
        app = _make_test_app()
        transport = ASGITransport(app=app)

        with patch("onemancompany.core.auth_verify.probe_chat", new_callable=AsyncMock) as mock_probe:
            mock_probe.return_value = (True, "")
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/auth/verify", json={
                    "provider": "deepseek",
                    "auth_method": "api_key",
                    "api_key": "sk-test",
                    "model": "deepseek-chat",
                })

        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_verify_failure(self):
        app = _make_test_app()
        transport = ASGITransport(app=app)

        with patch("onemancompany.core.auth_verify.probe_chat", new_callable=AsyncMock) as mock_probe:
            mock_probe.return_value = (False, "Invalid API key")
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/auth/verify", json={
                    "provider": "deepseek",
                    "auth_method": "api_key",
                    "api_key": "bad-key",
                    "model": "deepseek-chat",
                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "Invalid API key" in data["error"]


class TestApplyEndpoint:
    async def test_apply_company(self):
        app = _make_test_app()
        transport = ASGITransport(app=app)

        with patch("onemancompany.core.auth_apply.apply_auth_choice", new_callable=AsyncMock) as mock_apply:
            mock_apply.return_value = {"status": "applied", "scope": "company"}
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/auth/apply", json={
                    "scope": "company",
                    "choice": "deepseek-api-key",
                    "api_key": "sk-test",
                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "applied"

    async def test_apply_employee(self):
        app = _make_test_app()
        transport = ASGITransport(app=app)

        with patch("onemancompany.core.auth_apply.apply_auth_choice", new_callable=AsyncMock) as mock_apply:
            mock_apply.return_value = {"status": "applied", "scope": "employee"}
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/auth/apply", json={
                    "scope": "employee",
                    "employee_id": "00010",
                    "choice": "deepseek-api-key",
                    "api_key": "sk-test",
                    "model": "deepseek-chat",
                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "applied"
