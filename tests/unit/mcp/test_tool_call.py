"""Tests for internal MCP tool-call API endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from onemancompany.api.routes import router, _mcp_tool_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_registry():
    """Clear the cached tool registry so it's rebuilt on next call."""
    _mcp_tool_registry.clear()


@pytest.fixture(autouse=True)
def clear_registry():
    _clear_registry()
    yield
    _clear_registry()


def _make_app():
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInternalToolCall:
    @pytest.mark.asyncio
    async def test_missing_tool_name(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/internal/tool-call", json={
                "employee_id": "00002",
                "task_id": "t1",
                "tool_name": "",
                "args": {},
            })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/internal/tool-call", json={
                "employee_id": "00002",
                "task_id": "t1",
                "tool_name": "nonexistent_tool",
                "args": {},
            })
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_tool_call_with_context(self):
        """Tool call sets context vars and invokes the tool."""
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.ainvoke = AsyncMock(return_value={"status": "ok", "data": "test"})

        _mcp_tool_registry["test_tool"] = mock_tool

        mock_vessel = MagicMock()
        mock_em = MagicMock()
        mock_em.get_handle.return_value = mock_vessel

        app = _make_app()
        with patch("onemancompany.core.vessel.employee_manager", mock_em):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post("/api/internal/tool-call", json={
                    "employee_id": "00002",
                    "task_id": "task123",
                    "tool_name": "test_tool",
                    "args": {"param1": "value1"},
                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        mock_tool.ainvoke.assert_called_once_with({"param1": "value1"})

    @pytest.mark.asyncio
    async def test_tool_error_returns_error_dict(self):
        """Tool that raises an exception returns error dict, not 500."""
        mock_tool = MagicMock()
        mock_tool.name = "bad_tool"
        mock_tool.ainvoke = AsyncMock(side_effect=ValueError("something broke"))

        _mcp_tool_registry["bad_tool"] = mock_tool

        app = _make_app()
        with patch("onemancompany.core.vessel.employee_manager", MagicMock()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post("/api/internal/tool-call", json={
                    "employee_id": "00002",
                    "task_id": "t1",
                    "tool_name": "bad_tool",
                    "args": {},
                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "something broke" in data["message"]
