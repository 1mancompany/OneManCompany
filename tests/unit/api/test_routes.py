"""Unit tests for api/routes.py — FastAPI REST endpoints.

Uses httpx.AsyncClient with a minimal FastAPI app (router only, no lifespan).
All singletons (company_state, event_bus, agent loops, etc.) are mocked.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from onemancompany.core.events import CompanyEvent, EventBus
from onemancompany.core.state import (
    CompanyState,
    Employee,
    MeetingRoom,
    SalesTask,
    TaskEntry,
)


# ---------------------------------------------------------------------------
# Helpers — build a fresh test app + state for each test
# ---------------------------------------------------------------------------


def _make_test_app() -> FastAPI:
    """Create a minimal FastAPI app with just the router, no lifespan."""
    from onemancompany.api.routes import router

    app = FastAPI()
    app.include_router(router)
    return app


def _make_employee(
    id: str = "00010",
    name: str = "Test Dev",
    nickname: str = "测试",
    role: str = "Engineer",
    department: str = "技术研发部",
    level: int = 1,
    skills: list[str] | None = None,
) -> Employee:
    return Employee(
        id=id,
        name=name,
        nickname=nickname,
        role=role,
        department=department,
        level=level,
        skills=skills or ["python"],
    )


def _make_state(**overrides) -> CompanyState:
    """Build a CompanyState with sensible defaults."""
    state = CompanyState()
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


@pytest.fixture
def fresh_event_bus():
    return EventBus()


# ---------------------------------------------------------------------------
# GET /api/state
# ---------------------------------------------------------------------------


class TestGetState:
    async def test_returns_state_json(self):
        state = _make_state()
        emp = _make_employee()
        state.employees[emp.id] = emp

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/state")

        assert resp.status_code == 200
        data = resp.json()
        assert "employees" in data
        assert len(data["employees"]) == 1
        assert data["employees"][0]["id"] == "00010"


# ---------------------------------------------------------------------------
# GET /api/company/direction  +  PUT /api/company/direction
# ---------------------------------------------------------------------------


class TestCompanyDirection:
    async def test_get_direction(self):
        state = _make_state(company_direction="Build AI products")

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/company/direction")

        assert resp.status_code == 200
        assert resp.json()["direction"] == "Build AI products"

    async def test_put_direction(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes.save_company_direction", create=True):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/company/direction", json={"direction": "New direction"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert state.company_direction == "New direction"


# ---------------------------------------------------------------------------
# POST /api/admin/clear-tasks
# ---------------------------------------------------------------------------


class TestAdminClearTasks:
    async def test_clears_tasks_and_resets_status(self):
        emp = _make_employee()
        emp.status = "working"
        state = _make_state(
            employees={emp.id: emp},
            active_tasks=[TaskEntry(project_id="p1", task="t1", routed_to="COO")],
        )
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/admin/clear-tasks")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cleared"
        assert data["tasks_removed"] == 1
        assert len(state.active_tasks) == 0
        assert emp.status == "idle"


# ---------------------------------------------------------------------------
# POST /api/ceo/task
# ---------------------------------------------------------------------------


class TestCeoSubmitTask:
    async def test_empty_task_returns_error(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ceo/task", json={"task": ""})

        assert resp.status_code == 200
        assert resp.json().get("error") == "Empty task"

    async def test_routes_to_ea(self):
        state = _make_state()
        bus = EventBus()
        mock_loop = MagicMock()
        mock_loop.push_task = MagicMock()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes.get_agent_loop", return_value=mock_loop, create=True), \
             patch("onemancompany.core.project_archive.create_project", return_value="proj_123"), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value="/tmp/proj"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ceo/task", json={"task": "Build a website"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["routed_to"] == "EA"
        assert data["status"] == "processing"


# ---------------------------------------------------------------------------
# POST /api/employee/{employee_id}/fire
# ---------------------------------------------------------------------------


class TestFireEmployee:
    async def test_fire_employee_success(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})

        fire_result = {"status": "fired", "employee_id": "00010"}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.agents.termination.execute_fire", new_callable=AsyncMock, return_value=fire_result):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/fire", json={"reason": "test"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "fired"
        # state is injected
        assert "state" in data

    async def test_fire_employee_error(self):
        state = _make_state()

        fire_result = {"error": "Cannot fire founding employees"}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.agents.termination.execute_fire", new_callable=AsyncMock, return_value=fire_result):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00002/fire", json={"reason": "test"})

        assert resp.status_code == 200
        assert resp.json()["error"] == "Cannot fire founding employees"


# ---------------------------------------------------------------------------
# GET /api/employee/{employee_id}
# ---------------------------------------------------------------------------


class TestGetEmployeeDetail:
    async def test_employee_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/99999")

        assert resp.status_code == 200
        assert resp.json()["error"] == "Employee not found"

    async def test_employee_found(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})

        mock_cfg = MagicMock()
        mock_cfg.llm_model = "claude-sonnet-4-6"
        mock_cfg.api_provider = "anthropic"
        mock_cfg.api_key = "sk-ant-1234"
        mock_cfg.hosting = "company"
        mock_cfg.auth_method = "api_key"
        mock_cfg.tool_permissions = ["web_search"]

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.core.config.load_manifest", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "00010"
        assert data["llm_model"] == "claude-sonnet-4-6"
        assert data["api_key_set"] is True
        assert data["hosting"] == "company"


# ---------------------------------------------------------------------------
# GET /api/meeting_rooms
# ---------------------------------------------------------------------------


class TestMeetingRooms:
    async def test_get_meeting_rooms(self):
        room = MeetingRoom(id="room1", name="Alpha Room", description="Main meeting room")
        state = _make_state(meeting_rooms={"room1": room})

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/meeting_rooms")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["meeting_rooms"]) == 1
        assert data["meeting_rooms"][0]["name"] == "Alpha Room"


# ---------------------------------------------------------------------------
# POST /api/meeting/release
# ---------------------------------------------------------------------------


class TestMeetingRelease:
    async def test_release_booked_room(self):
        room = MeetingRoom(
            id="room1", name="Alpha", description="Room",
            is_booked=True, booked_by="00001", participants=["00001", "00002"],
        )
        state = _make_state(meeting_rooms={"room1": room})
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/meeting/release", json={"room_id": "room1"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "released"
        assert room.is_booked is False
        assert room.booked_by == ""
        assert room.participants == []

    async def test_release_missing_room_id(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/meeting/release", json={})

        assert resp.json()["error"] == "Missing room_id"

    async def test_release_nonexistent_room(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/meeting/release", json={"room_id": "nonexistent"})

        assert "not found" in resp.json()["error"]

    async def test_release_unbooked_room(self):
        room = MeetingRoom(id="room1", name="Alpha", description="Room", is_booked=False)
        state = _make_state(meeting_rooms={"room1": room})

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/meeting/release", json={"room_id": "room1"})

        assert "not booked" in resp.json()["error"]


# ---------------------------------------------------------------------------
# Company Culture endpoints
# ---------------------------------------------------------------------------


class TestCompanyCulture:
    async def test_get_culture(self):
        state = _make_state(company_culture=[{"content": "Move fast"}])

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/company-culture")

        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

    async def test_add_culture_item(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.save_company_culture"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/company-culture", json={"content": "Move fast"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "added"
        assert len(state.company_culture) == 1

    async def test_add_culture_empty_content(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/company-culture", json={"content": ""})

        assert resp.json()["error"] == "Missing content"

    async def test_remove_culture_item(self):
        state = _make_state(company_culture=[{"content": "A"}, {"content": "B"}])
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.save_company_culture"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.delete("/api/company-culture/0")

        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"
        assert len(state.company_culture) == 1
        assert state.company_culture[0]["content"] == "B"

    async def test_remove_invalid_index(self):
        state = _make_state(company_culture=[{"content": "A"}])

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.delete("/api/company-culture/5")

        assert resp.json()["error"] == "Invalid index"


# ---------------------------------------------------------------------------
# Remote Worker Endpoints
# ---------------------------------------------------------------------------


class TestRemoteRegister:
    async def test_register_worker(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._remote_workers", {}), \
             patch("onemancompany.api.routes._remote_task_queues", {}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/remote/register", json={
                    "employee_id": "00010",
                    "worker_url": "http://worker:9000",
                    "capabilities": ["coding"],
                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "registered"


class TestRemoteGetTasks:
    async def test_no_tasks_returns_none(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.api.routes._remote_task_queues", {"00010": []}), \
             patch("onemancompany.api.routes._remote_workers", {}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/remote/tasks/00010")

        assert resp.status_code == 200
        assert resp.json()["task"] is None

    async def test_returns_pending_task(self):
        state = _make_state()
        task_data = {"task_id": "t1", "project_id": "p1", "task_description": "Do X"}
        workers = {"00010": {"status": "idle", "current_task_id": None}}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.api.routes._remote_task_queues", {"00010": [task_data]}), \
             patch("onemancompany.api.routes._remote_workers", workers):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/remote/tasks/00010")

        assert resp.status_code == 200
        data = resp.json()
        assert data["task"]["task_id"] == "t1"


class TestRemoteHeartbeat:
    async def test_heartbeat_updates_status(self):
        state = _make_state()
        workers = {"00010": {"status": "idle", "current_task_id": None}}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.api.routes._remote_workers", workers):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/remote/heartbeat", json={
                    "employee_id": "00010",
                    "status": "busy",
                    "current_task_id": "t1",
                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert workers["00010"]["status"] == "busy"


class TestRemoteSubmitResults:
    async def test_submit_results(self):
        state = _make_state()
        bus = EventBus()
        workers = {"00010": {"status": "busy", "current_task_id": "t1"}}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._remote_workers", workers):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/remote/results", json={
                    "task_id": "t1",
                    "employee_id": "00010",
                    "status": "completed",
                    "output": "Done",
                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "received"
        assert workers["00010"]["status"] == "idle"


# ---------------------------------------------------------------------------
# Sales Endpoints
# ---------------------------------------------------------------------------


class TestSalesSubmit:
    async def test_submit_task(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes.get_agent_loop", return_value=None, create=True):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/sales/submit", json={
                    "client_name": "Acme Corp",
                    "description": "Build a website",
                    "budget_tokens": 10000,
                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "submitted"
        assert "task_id" in data
        assert len(state.sales_tasks) == 1

    async def test_submit_missing_fields(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/sales/submit", json={"client_name": ""})

        assert resp.json()["error"] == "Missing client_name or description"


class TestSalesListTasks:
    async def test_list_tasks(self):
        st = SalesTask(id="s1", client_name="Acme", description="Build X")
        state = _make_state(sales_tasks={"s1": st})

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/sales/tasks")

        assert resp.status_code == 200
        assert len(resp.json()["tasks"]) == 1


class TestSalesGetTask:
    async def test_get_existing_task(self):
        st = SalesTask(id="s1", client_name="Acme", description="Build X")
        state = _make_state(sales_tasks={"s1": st})

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/sales/tasks/s1")

        assert resp.status_code == 200
        assert resp.json()["client_name"] == "Acme"

    async def test_get_nonexistent_task(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/sales/tasks/nonexistent")

        assert "not found" in resp.json()["error"]


class TestSalesDeliver:
    async def test_deliver_in_production_task(self):
        st = SalesTask(id="s1", client_name="Acme", description="Build X", status="in_production")
        state = _make_state(sales_tasks={"s1": st})

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/sales/tasks/s1/deliver", json={"delivery_summary": "All done"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "delivered"
        assert st.status == "delivered"
        assert st.delivery == "All done"

    async def test_deliver_wrong_status(self):
        st = SalesTask(id="s1", client_name="Acme", description="Build X", status="pending")
        state = _make_state(sales_tasks={"s1": st})

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/sales/tasks/s1/deliver", json={})

        assert "pending" in resp.json()["error"]


class TestSalesSettle:
    async def test_settle_delivered_task(self):
        st = SalesTask(id="s1", client_name="Acme", description="X", status="delivered", budget_tokens=500)
        state = _make_state(sales_tasks={"s1": st}, company_tokens=1000)

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/sales/tasks/s1/settle")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "settled"
        assert data["tokens_earned"] == 500
        assert data["company_total_tokens"] == 1500
        assert st.status == "settled"

    async def test_settle_wrong_status(self):
        st = SalesTask(id="s1", client_name="Acme", description="X", status="pending")
        state = _make_state(sales_tasks={"s1": st})

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/sales/tasks/s1/settle")

        assert "pending" in resp.json()["error"]


# ---------------------------------------------------------------------------
# GET /api/sales/protocol
# ---------------------------------------------------------------------------


class TestSalesProtocol:
    async def test_protocol_returns_docs(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/sales/protocol")

        assert resp.status_code == 200
        data = resp.json()
        assert data["protocol_version"] == "1.0"
        assert "endpoints" in data
        assert "submit_task" in data["endpoints"]


# ---------------------------------------------------------------------------
# GET /api/ex-employees
# ---------------------------------------------------------------------------


class TestExEmployees:
    async def test_list_ex_employees(self):
        ex = _make_employee(id="00099", name="Fired Dev")
        state = _make_state(ex_employees={"00099": ex})

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/ex-employees")

        assert resp.status_code == 200
        assert len(resp.json()["ex_employees"]) == 1

    async def test_empty_ex_employees(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/ex-employees")

        assert resp.json()["ex_employees"] == []


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


class TestWorkflows:
    async def test_list_workflows(self):
        state = _make_state()
        mock_workflows = {"onboarding": "# Onboarding\nStep 1...", "review": "# Review\nStep 1..."}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.load_workflows", return_value=mock_workflows):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/workflows")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["workflows"]) == 2

    async def test_get_workflow(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.load_workflows", return_value={"onboarding": "# Onboarding"}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/workflows/onboarding")

        assert resp.status_code == 200
        assert resp.json()["content"] == "# Onboarding"

    async def test_get_workflow_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.load_workflows", return_value={}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/workflows/nonexistent")

        assert "not found" in resp.json()["error"]

    async def test_update_workflow(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.save_workflow"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/workflows/onboarding", json={"content": "# Updated"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "saved"

    async def test_update_workflow_empty_content(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/workflows/onboarding", json={"content": ""})

        assert resp.json()["error"] == "Missing content"


# ---------------------------------------------------------------------------
# GET /api/employee/{employee_id}/taskboard
# ---------------------------------------------------------------------------


class TestEmployeeTaskboard:
    async def test_taskboard_no_agent_loop(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/taskboard")

        assert resp.status_code == 200
        assert resp.json()["tasks"] == []


# ---------------------------------------------------------------------------
# GET /api/employee/{employee_id}/logs
# ---------------------------------------------------------------------------


class TestEmployeeLogs:
    async def test_logs_no_agent_loop(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/logs")

        assert resp.status_code == 200
        assert resp.json()["logs"] == []


# ---------------------------------------------------------------------------
# POST /api/admin/reload
# ---------------------------------------------------------------------------


class TestAdminReload:
    async def test_reload(self):
        state = _make_state()
        mock_changes = {"employees_updated": ["00002"], "employees_added": []}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.state.reload_all_from_disk", return_value=mock_changes):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/admin/reload")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "reloaded"


# ---------------------------------------------------------------------------
# Inquiry endpoints
# ---------------------------------------------------------------------------


class TestInquiryEnd:
    async def test_end_missing_session_id(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/inquiry/end", json={})

        assert resp.json()["error"] == "Missing session_id"

    async def test_end_nonexistent_session(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.api.routes._inquiry_sessions", {}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/inquiry/end", json={"session_id": "nonexistent"})

        assert resp.json()["error"] == "Inquiry session not found"


class TestInquiryChat:
    async def test_chat_missing_fields(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/inquiry/chat", json={"session_id": "", "message": ""})

        assert resp.json()["error"] == "Missing session_id or message"


# ---------------------------------------------------------------------------
# POST /api/ceo/qa
# ---------------------------------------------------------------------------


class TestCeoQA:
    async def test_empty_question(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ceo/qa", json={"question": ""})

        assert resp.json()["error"] == "Empty question"


# ---------------------------------------------------------------------------
# 1-on-1 endpoints
# ---------------------------------------------------------------------------


class TestOneOnOneChat:
    async def test_missing_employee_id(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={"message": "hi"})

        assert resp.json()["error"] == "Missing employee_id or message"

    async def test_employee_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "99999",
                    "message": "Hello",
                })

        assert "not found" in resp.json()["error"]


class TestOneOnOneEnd:
    async def test_end_missing_employee_id(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/end", json={})

        assert resp.json()["error"] == "Missing employee_id"

    async def test_end_employee_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/end", json={"employee_id": "99999"})

        assert "not found" in resp.json()["error"]


# ---------------------------------------------------------------------------
# Projects endpoints
# ---------------------------------------------------------------------------


class TestProjects:
    async def test_create_project(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.create_named_project", return_value="proj_abc"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/projects", json={"name": "Test Project"})

        assert resp.status_code == 200
        assert resp.json()["project_id"] == "proj_abc"

    async def test_create_project_missing_name(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/projects", json={"name": ""})

        assert resp.json()["error"] == "Missing project name"


# ---------------------------------------------------------------------------
# Employee manifest
# ---------------------------------------------------------------------------


class TestEmployeeManifest:
    async def test_no_manifest(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.load_manifest", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/manifest")

        assert resp.json()["error"] == "No manifest found"

    async def test_has_manifest(self):
        state = _make_state()
        manifest_data = {"id": "test", "name": "Test", "settings": []}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.load_manifest", return_value=manifest_data):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/manifest")

        assert resp.status_code == 200
        assert resp.json()["id"] == "test"
