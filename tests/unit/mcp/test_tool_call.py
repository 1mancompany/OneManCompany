"""Tests for internal MCP tool-call API endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from onemancompany.api.routes import router
from onemancompany.core.tool_registry import ToolMeta, tool_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_test_tools():
    """Remove test tools after each test."""
    yield
    for name in list(tool_registry._tools.keys()):
        if name.startswith("test_") or name == "bad_tool":
            tool_registry._tools.pop(name, None)
            tool_registry._meta.pop(name, None)


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
        # execute_tool returns error dict, not HTTP 404
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"

    @pytest.mark.asyncio
    async def test_tool_call_with_context(self):
        """Tool call sets context vars and invokes the tool."""
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.ainvoke = AsyncMock(return_value={"status": "ok", "data": "test"})

        tool_registry.register(mock_tool, ToolMeta(name="test_tool", category="base"))

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
        # employee_id auto-filled by execute_tool for MCP path
        mock_tool.ainvoke.assert_called_once_with({"param1": "value1", "employee_id": "00002"})

    @pytest.mark.asyncio
    async def test_tool_error_returns_error_dict(self):
        """Tool that raises an exception returns error dict, not 500."""
        mock_tool = MagicMock()
        mock_tool.name = "bad_tool"
        mock_tool.ainvoke = AsyncMock(side_effect=ValueError("something broke"))

        tool_registry.register(mock_tool, ToolMeta(name="bad_tool", category="base"))

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
