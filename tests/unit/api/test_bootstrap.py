"""Unit tests for GET /api/bootstrap — single-call frontend init endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from onemancompany.core.state import CompanyState, MeetingRoom


def _make_test_app() -> FastAPI:
    from onemancompany.api.routes import router
    app = FastAPI()
    app.include_router(router)
    return app


def _emp_dict(emp_id: str = "00010", name: str = "Alice", role: str = "Engineer") -> dict:
    return {
        "id": emp_id,
        "name": name,
        "role": role,
        "runtime": {"status": "idle", "is_listening": False, "current_task_summary": ""},
        "work_principles": "",
        "guidance_notes": [],
    }


class TestBootstrapEndpoint:
    @pytest.mark.asyncio
    async def test_returns_all_sections(self):
        state = CompanyState()
        room = MeetingRoom(id="room1", name="Alpha", description="Test room")
        state.meeting_rooms["room1"] = room
        state.office_layout = {"width": 40, "height": 30}

        fake_employees = {"00010": _emp_dict("00010")}
        fake_tools = [{"name": "Hammer", "slug": "hammer"}]
        fake_activity = [{"type": "test", "desc": "hello"}]
        fake_overhead = {"company_tokens": 42}
        fake_projects = [
            {"project_id": "p1", "task": "Do stuff", "status": "in_progress",
             "routed_to": "", "current_owner": "", "created_at": "", "completed_at": ""},
        ]

        em_mock = MagicMock()
        em_mock._running_tasks = {}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes._store.aload_all_employees", new_callable=AsyncMock, return_value=fake_employees), \
             patch("onemancompany.api.routes._store.aload_tools", new_callable=AsyncMock, return_value=fake_tools), \
             patch("onemancompany.api.routes._store.aload_activity_log", new_callable=AsyncMock, return_value=fake_activity), \
             patch("onemancompany.api.routes._store.aload_overhead", new_callable=AsyncMock, return_value=fake_overhead), \
             patch("onemancompany.core.project_archive.list_projects", return_value=fake_projects), \
             patch("onemancompany.api.routes.event_bus", MagicMock()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/bootstrap")

        assert resp.status_code == 200
        data = resp.json()
        # All sections present
        assert "employees" in data
        assert "tasks" in data
        assert "rooms" in data
        assert "tools" in data
        assert "activity_log" in data
        assert "version" in data
        assert "office_layout" in data

        # Employee data correct (CEO filtered out already by fake data)
        assert len(data["employees"]) == 1
        assert data["employees"][0]["id"] == "00010"

        # Lightweight tasks — tree is None
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["project_id"] == "p1"
        assert data["tasks"][0]["tree"] is None

        # Rooms from company_state
        assert len(data["rooms"]) == 1

        # Tools, activity log
        assert len(data["tools"]) == 1
        assert len(data["activity_log"]) == 1

    @pytest.mark.asyncio
    async def test_ceo_filtered_from_employees(self):
        """CEO (00001) should not appear in employee list."""
        state = CompanyState()

        fake_employees = {
            "00001": _emp_dict("00001", "CEO", "CEO"),
            "00010": _emp_dict("00010", "Dev", "Engineer"),
        }

        em_mock = MagicMock()
        em_mock._running_tasks = {}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes._store.aload_all_employees", new_callable=AsyncMock, return_value=fake_employees), \
             patch("onemancompany.api.routes._store.aload_tools", new_callable=AsyncMock, return_value=[]), \
             patch("onemancompany.api.routes._store.aload_activity_log", new_callable=AsyncMock, return_value=[]), \
             patch("onemancompany.api.routes._store.aload_overhead", new_callable=AsyncMock, return_value={}), \
             patch("onemancompany.core.project_archive.list_projects", return_value=[]), \
             patch("onemancompany.api.routes.event_bus", MagicMock()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/bootstrap")

        data = resp.json()
        emp_ids = [e["id"] for e in data["employees"]]
        assert "00001" not in emp_ids
        assert "00010" in emp_ids

    @pytest.mark.asyncio
    async def test_named_projects_excluded_from_tasks(self):
        """Named projects should be excluded from task queue."""
        state = CompanyState()

        fake_projects = [
            {"project_id": "p1", "task": "T1", "status": "active", "is_named": True,
             "routed_to": "", "current_owner": "", "created_at": "", "completed_at": ""},
            {"project_id": "p2", "task": "T2", "status": "in_progress",
             "routed_to": "", "current_owner": "", "created_at": "", "completed_at": ""},
        ]

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes._store.aload_all_employees", new_callable=AsyncMock, return_value={}), \
             patch("onemancompany.api.routes._store.aload_tools", new_callable=AsyncMock, return_value=[]), \
             patch("onemancompany.api.routes._store.aload_activity_log", new_callable=AsyncMock, return_value=[]), \
             patch("onemancompany.api.routes._store.aload_overhead", new_callable=AsyncMock, return_value={}), \
             patch("onemancompany.core.project_archive.list_projects", return_value=fake_projects), \
             patch("onemancompany.api.routes.event_bus", MagicMock()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/bootstrap")

        data = resp.json()
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["project_id"] == "p2"
