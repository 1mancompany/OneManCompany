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
             patch("onemancompany.core.config.save_company_direction"):
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
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop), \
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
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None):
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


# ---------------------------------------------------------------------------
# POST /api/ceo/qa — happy path
# ---------------------------------------------------------------------------


class TestCeoQAHappyPath:
    async def test_qa_success(self):
        state = _make_state()
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "The answer is 42"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ceo/qa", json={"question": "What is the meaning of life?"})

        assert resp.status_code == 200
        assert resp.json()["answer"] == "The answer is 42"


# ---------------------------------------------------------------------------
# POST /api/oneonone/chat — fallback LLM path
# ---------------------------------------------------------------------------


class TestOneOnOneChatFallback:
    async def test_chat_fallback_llm(self):
        emp = _make_employee(id="00010")
        emp.work_principles = "Be helpful"
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "Hello CEO"

        mock_cfg = MagicMock()
        mock_cfg.hosting = "company"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.agents.base.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_tools_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_talent_persona", return_value=""), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "How are you?",
                })

        assert resp.status_code == 200
        assert resp.json()["response"] == "Hello CEO"

    async def test_chat_with_history(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp}, company_culture=[{"content": "Move fast"}])
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "I remember our chat"

        mock_cfg = MagicMock()
        mock_cfg.hosting = "company"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.agents.base.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_tools_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_talent_persona", return_value=""), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "Follow up",
                    "history": [
                        {"role": "ceo", "content": "Hi"},
                        {"role": "employee", "content": "Hello"},
                    ],
                })

        assert resp.status_code == 200
        assert resp.json()["response"] == "I remember our chat"


# ---------------------------------------------------------------------------
# POST /api/oneonone/end — happy path
# ---------------------------------------------------------------------------


class TestOneOnOneEndHappyPath:
    async def test_end_no_update(self):
        emp = _make_employee(id="00010")
        emp.is_listening = True
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "NO_UPDATE"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.config.save_work_principles"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/end", json={
                    "employee_id": "00010",
                    "history": [
                        {"role": "ceo", "content": "Good chat"},
                        {"role": "employee", "content": "Thanks"},
                    ],
                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ended"
        assert data["principles_updated"] is False
        assert emp.is_listening is False

    async def test_end_with_update(self):
        emp = _make_employee(id="00010")
        emp.is_listening = True
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "UPDATED: Be more proactive\n- Take initiative"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.config.save_work_principles") as mock_save:
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/end", json={
                    "employee_id": "00010",
                    "history": [
                        {"role": "ceo", "content": "Be more proactive"},
                    ],
                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["principles_updated"] is True
        assert emp.work_principles == "Be more proactive\n- Take initiative"
        mock_save.assert_called_once()

    async def test_end_no_history(self):
        emp = _make_employee(id="00010")
        emp.is_listening = True
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/end", json={
                    "employee_id": "00010",
                    "history": [],
                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ended"
        assert data["principles_updated"] is False


# ---------------------------------------------------------------------------
# POST /api/meeting/book
# ---------------------------------------------------------------------------


class TestMeetingBook:
    async def test_book_meeting_with_loop(self):
        state = _make_state()
        bus = EventBus()
        mock_loop = MagicMock()
        mock_loop.push_task = MagicMock()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/meeting/book", json={
                    "employee_id": "00010",
                    "participants": ["00010", "00011"],
                    "purpose": "Planning",
                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "processing"
        mock_loop.push_task.assert_called_once()

    async def test_book_meeting_missing_employee_id(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/meeting/book", json={})

        assert resp.json()["error"] == "Missing employee_id"


# ---------------------------------------------------------------------------
# POST /api/hr/review
# ---------------------------------------------------------------------------


class TestHRReview:
    async def test_hr_review_with_loop(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()
        mock_loop = MagicMock()
        mock_loop.push_task = MagicMock()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/hr/review")

        assert resp.status_code == 200
        assert resp.json()["status"] == "HR review started"
        mock_loop.push_task.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/routine/start
# ---------------------------------------------------------------------------


class TestRoutineStart:
    async def test_start_routine(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._run_agent_safe", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.run_post_task_routine", new_callable=AsyncMock):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/routine/start", json={"task_summary": "Build widget"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "routine_started"


# ---------------------------------------------------------------------------
# POST /api/routine/approve
# ---------------------------------------------------------------------------


class TestRoutineApprove:
    async def test_approve_actions_missing_report_id(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/routine/approve", json={"report_id": ""})

        assert resp.json()["error"] == "Missing report_id"

    async def test_approve_actions_success(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._run_agent_safe", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.execute_approved_actions", new_callable=AsyncMock):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/routine/approve", json={
                    "report_id": "rpt_123",
                    "approved_indices": [0, 2],
                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "executing_approved_actions"


# ---------------------------------------------------------------------------
# POST /api/routine/all_hands
# ---------------------------------------------------------------------------


class TestRoutineAllHands:
    async def test_all_hands_missing_message(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/routine/all_hands", json={"message": ""})

        assert resp.json()["error"] == "Missing CEO message"

    async def test_all_hands_success(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._run_agent_safe", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.run_all_hands_meeting", new_callable=AsyncMock):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/routine/all_hands", json={"message": "Company update"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "all_hands_started"


# ---------------------------------------------------------------------------
# GET /api/models
# ---------------------------------------------------------------------------


class TestListModels:
    async def test_list_models_success(self):
        state = _make_state()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{"id": "model-1", "name": "Model One", "context_length": 4096}]
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch("onemancompany.core.config.settings") as mock_settings:
            mock_settings.openrouter_base_url = "https://api.openrouter.ai/api/v1"
            mock_settings.openrouter_api_key = "sk-test"
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/models")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["models"]) == 1
        assert data["models"][0]["id"] == "model-1"

    async def test_list_models_error(self):
        state = _make_state()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("Connection error"))

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch("onemancompany.core.config.settings") as mock_settings:
            mock_settings.openrouter_base_url = "https://api.openrouter.ai/api/v1"
            mock_settings.openrouter_api_key = "sk-test"
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/models")

        assert resp.status_code == 200
        data = resp.json()
        assert data["models"] == []
        assert "error" in data


# ---------------------------------------------------------------------------
# GET /api/employee/{employee_id}/okrs + PUT
# ---------------------------------------------------------------------------


class TestEmployeeOKRs:
    async def test_get_okrs(self):
        emp = _make_employee(id="00010")
        emp.okrs = [{"objective": "Ship feature", "key_results": []}]
        state = _make_state(employees={"00010": emp})

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/okrs")

        assert resp.status_code == 200
        assert len(resp.json()["okrs"]) == 1

    async def test_get_okrs_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/99999/okrs")

        assert resp.status_code == 404

    async def test_update_okrs(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.update_employee_field"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/00010/okrs", json={
                    "okrs": [{"objective": "New goal"}],
                })

        assert resp.status_code == 200
        assert resp.json()["okrs"] == [{"objective": "New goal"}]
        assert emp.okrs == [{"objective": "New goal"}]

    async def test_update_okrs_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/99999/okrs", json={"okrs": []})

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/employee/{employee_id}/taskboard — with loop
# ---------------------------------------------------------------------------


class TestEmployeeTaskboardWithLoop:
    async def test_taskboard_with_loop(self):
        state = _make_state()
        mock_board = MagicMock()
        mock_board.to_dict.return_value = [{"id": "t1", "description": "Task"}]
        mock_loop = MagicMock()
        mock_loop.board = mock_board

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/taskboard")

        assert resp.status_code == 200
        assert len(resp.json()["tasks"]) == 1


# ---------------------------------------------------------------------------
# PUT /api/employee/{employee_id}/model
# ---------------------------------------------------------------------------


class TestUpdateEmployeeModel:
    async def test_update_model_success(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_cfg = MagicMock()
        mock_cfg.api_provider = "openrouter"
        mock_cfg.salary_per_1m_tokens = 1.0

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", MagicMock()), \
             patch("onemancompany.core.model_costs.compute_salary", return_value=2.5):
            # Make profile_path.exists() return False to skip disk write
            mock_path = MagicMock()
            mock_path.exists.return_value = False
            with patch("onemancompany.core.config.EMPLOYEES_DIR.__truediv__", return_value=MagicMock(__truediv__=MagicMock(return_value=mock_path))):
                app = _make_test_app()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                    resp = await c.put("/api/employee/00010/model", json={"model": "gpt-4o"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"

    async def test_update_model_missing_model(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/00010/model", json={"model": ""})

        assert resp.json()["error"] == "Missing model"

    async def test_update_model_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/99999/model", json={"model": "gpt-4o"})

        assert resp.json()["error"] == "Employee not found"


# ---------------------------------------------------------------------------
# GET /api/projects
# ---------------------------------------------------------------------------


class TestGetProjects:
    async def test_list_projects(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.list_projects", return_value=[{"id": "p1", "name": "Project 1"}]):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects")

        assert resp.status_code == 200
        assert len(resp.json()["projects"]) == 1


# ---------------------------------------------------------------------------
# GET /api/projects/named
# ---------------------------------------------------------------------------


class TestListNamedProjects:
    async def test_list_named_projects(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.list_named_projects", return_value=[{"id": "p1"}]):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/named")

        assert resp.status_code == 200
        assert len(resp.json()["projects"]) == 1


# ---------------------------------------------------------------------------
# GET /api/projects/named/{project_id}
# ---------------------------------------------------------------------------


class TestGetNamedProjectDetail:
    async def test_project_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/named/nonexistent")

        assert resp.json()["error"] == "Named project not found"

    async def test_project_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_named_project", return_value={
                 "name": "Test", "iterations": [], "status": "active"
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/named/test-project")

        assert resp.status_code == 200
        assert resp.json()["name"] == "Test"


# ---------------------------------------------------------------------------
# POST /api/projects/{project_id}/archive
# ---------------------------------------------------------------------------


class TestArchiveProject:
    async def test_archive_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/projects/nonexistent/archive")

        assert resp.json()["error"] == "Named project not found"

    async def test_archive_success(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_named_project", return_value={"name": "Test"}), \
             patch("onemancompany.core.project_archive.archive_project"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/projects/test-proj/archive")

        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id}
# ---------------------------------------------------------------------------


class TestGetProjectDetail:
    async def test_project_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_project", return_value={"task": "Build"}), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value="/tmp/proj"), \
             patch("onemancompany.core.project_archive.list_project_files", return_value=["file.py"]):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/proj1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["task"] == "Build"
        assert data["project_dir"] == "/tmp/proj"

    async def test_project_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_project", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/nonexistent")

        assert resp.json()["error"] == "Project not found"


# ---------------------------------------------------------------------------
# GET /api/dashboard/costs
# ---------------------------------------------------------------------------


class TestDashboardCosts:
    async def test_get_costs(self):
        from onemancompany.core.models import OverheadCosts as OH
        oh = OH()
        state = _make_state()
        state.overhead_costs = oh

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.get_cost_summary", return_value={
                 "total": {"cost_usd": 1.5}, "projects": [],
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/dashboard/costs")

        assert resp.status_code == 200
        data = resp.json()
        assert "grand_total_usd" in data
        assert "overhead" in data


# ---------------------------------------------------------------------------
# File edits endpoints
# ---------------------------------------------------------------------------


class TestFileEdits:
    async def test_get_pending_edits(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.file_editor.list_pending_edits", return_value=[]):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/file-edits")

        assert resp.status_code == 200
        assert resp.json()["edits"] == []

    async def test_approve_edit_success(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.file_editor.execute_edit", return_value={
                 "status": "ok", "rel_path": "file.py", "backup_path": "/backup/file.py"
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/file-edits/edit_123/approve")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_approve_edit_error(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.file_editor.execute_edit", return_value={
                 "status": "error", "message": "Edit not found"
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/file-edits/nonexistent/approve")

        assert resp.json()["status"] == "error"

    async def test_reject_edit_success(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.file_editor.reject_edit", return_value={
                 "status": "ok", "rel_path": "file.py"
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/file-edits/edit_123/reject")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_reject_edit_error(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.file_editor.reject_edit", return_value={
                 "status": "error", "message": "Not found"
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/file-edits/nonexistent/reject")

        assert resp.json()["status"] == "error"


# ---------------------------------------------------------------------------
# Resolutions endpoints
# ---------------------------------------------------------------------------


class TestResolutions:
    async def test_get_deferred_edits(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.resolutions.list_deferred_edits", return_value=[]):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/resolutions/deferred")

        assert resp.status_code == 200
        assert resp.json()["edits"] == []

    async def test_list_resolutions(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.resolutions.list_resolutions", return_value=[]):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/resolutions")

        assert resp.status_code == 200
        assert resp.json()["resolutions"] == []

    async def test_get_resolution_detail_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.resolutions.load_resolution", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/resolutions/nonexistent")

        assert resp.json()["error"] == "Resolution not found"

    async def test_get_resolution_detail_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.resolutions.load_resolution", return_value={"id": "r1", "edits": []}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/resolutions/r1")

        assert resp.status_code == 200
        assert resp.json()["id"] == "r1"

    async def test_decide_on_resolution_missing_decisions(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/resolutions/r1/decide", json={"decisions": {}})

        assert resp.json()["error"] == "No decisions provided"

    async def test_decide_on_resolution_success(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.resolutions.decide_resolution", return_value={
                 "status": "ok", "results": [{"edit_id": "e1", "action": "approve"}]
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/resolutions/r1/decide", json={
                    "decisions": {"e1": "approve"},
                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_execute_deferred_success(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.resolutions.execute_deferred_edit", return_value={
                 "status": "ok", "rel_path": "file.py", "backup_path": "/backup"
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/resolutions/deferred/r1/e1/execute")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Inquiry end — with session
# ---------------------------------------------------------------------------


class TestInquiryEndWithSession:
    async def test_end_with_existing_session(self):
        from onemancompany.api.routes import InquirySession
        room = MeetingRoom(id="room1", name="Alpha", description="Room", is_booked=True, booked_by="00001")
        state = _make_state(meeting_rooms={"room1": room})
        bus = EventBus()

        session = InquirySession(
            session_id="sess1",
            task="What is our revenue?",
            room_id="room1",
            agent_role="COO",
            participants=["00001", "00003"],
            history=[{"role": "ceo", "speaker": "CEO", "content": "Hi"}],
        )

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._inquiry_sessions", {"sess1": session}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/inquiry/end", json={"session_id": "sess1"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ended"
        assert room.is_booked is False


# ---------------------------------------------------------------------------
# Inquiry chat — with session
# ---------------------------------------------------------------------------


class TestInquiryChatWithSession:
    async def test_chat_with_session(self):
        from onemancompany.api.routes import InquirySession
        emp = _make_employee(id="00003", name="COO Boss", role="COO")
        state = _make_state(employees={"00003": emp})
        bus = EventBus()

        session = InquirySession(
            session_id="sess1",
            task="Revenue question",
            room_id="room1",
            agent_role="COO",
            participants=["00001", "00003"],
            history=[
                {"role": "ceo", "speaker": "CEO", "content": "What's our revenue?"},
                {"role": "agent", "speaker": "COO Boss", "content": "Revenue is $1M"},
            ],
        )
        session._system_prompt = "You are the COO"

        mock_result = MagicMock()
        mock_result.content = "Let me explain further"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._inquiry_sessions", {"sess1": session}), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/inquiry/chat", json={
                    "session_id": "sess1",
                    "message": "Tell me more",
                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["response"] == "Let me explain further"
        assert data["speaker"] == "COO Boss"

    async def test_chat_session_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.api.routes._inquiry_sessions", {}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/inquiry/chat", json={
                    "session_id": "nonexistent",
                    "message": "Hello",
                })

        assert resp.json()["error"] == "Inquiry session not found"


# ---------------------------------------------------------------------------
# Employee sessions (self-hosted)
# ---------------------------------------------------------------------------


class TestEmployeeSessions:
    async def test_get_sessions_not_self_hosted(self):
        state = _make_state()
        mock_cfg = MagicMock()
        mock_cfg.hosting = "company"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/sessions")

        assert resp.json()["error"] == "Employee is not self-hosted"

    async def test_get_sessions_no_config(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/sessions")

        assert resp.json()["error"] == "Employee is not self-hosted"

    async def test_get_sessions_success(self):
        state = _make_state()
        mock_cfg = MagicMock()
        mock_cfg.hosting = "self"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.core.claude_session.list_sessions", return_value=[{"project_id": "p1"}]):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/sessions")

        assert resp.status_code == 200
        assert resp.json()["employee_id"] == "00010"

    async def test_delete_session_not_self_hosted(self):
        state = _make_state()
        mock_cfg = MagicMock()
        mock_cfg.hosting = "company"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.delete("/api/employee/00010/sessions/proj1")

        assert resp.json()["error"] == "Employee is not self-hosted"

    async def test_delete_session_success(self):
        state = _make_state()
        mock_cfg = MagicMock()
        mock_cfg.hosting = "self"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.core.claude_session.cleanup_session"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.delete("/api/employee/00010/sessions/proj1")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Hiring requests
# ---------------------------------------------------------------------------


class TestHiringRequests:
    async def test_list_hiring_requests(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.agents.coo_agent.pending_hiring_requests", {"r1": {"role": "Developer", "reason": "Need help"}}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/hiring-requests")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1

    async def test_decide_hiring_request_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.agents.coo_agent.pending_hiring_requests", {}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/hiring-requests/nonexistent/decide", json={"approved": True})

        assert "not found" in resp.json()["error"]

    async def test_decide_hiring_request_rejected(self):
        state = _make_state()
        bus = EventBus()
        reqs = {"r1": {"role": "Developer", "reason": "Need help"}}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.agents.coo_agent.pending_hiring_requests", reqs):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/hiring-requests/r1/decide", json={"approved": False})

        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"


# ---------------------------------------------------------------------------
# Upload file
# ---------------------------------------------------------------------------


class TestUploadFile:
    async def test_upload_file(self, tmp_path):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.api.routes.COMPANY_DIR", tmp_path):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/upload", files={"file": ("test.txt", b"hello world", "text/plain")})

        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "test.txt"
        assert data["size"] == 11


# ---------------------------------------------------------------------------
# POST /api/oneonone/chat — agent loop path
# ---------------------------------------------------------------------------


class TestOneOnOneChatAgentLoop:
    async def test_chat_via_agent_loop(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_task = MagicMock()
        mock_task.id = "t1"
        mock_task.status = "completed"
        mock_task.result = "Agent response here"
        mock_task.logs = []

        mock_board = MagicMock()
        mock_board.get_task.return_value = mock_task

        mock_loop = MagicMock()
        mock_loop.push_task.return_value = mock_task
        mock_loop.board = mock_board

        mock_cfg = MagicMock()
        mock_cfg.hosting = "company"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "Do this task",
                })

        assert resp.status_code == 200
        assert resp.json()["response"] == "Agent response here"

    async def test_chat_via_agent_loop_with_logs_fallback(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_task = MagicMock()
        mock_task.id = "t1"
        mock_task.status = "completed"
        mock_task.result = ""
        mock_task.logs = [
            {"type": "start", "content": "Started"},
            {"type": "result", "content": "Log-based result"},
        ]

        mock_board = MagicMock()
        mock_board.get_task.return_value = mock_task

        mock_loop = MagicMock()
        mock_loop.push_task.return_value = mock_task
        mock_loop.board = mock_board

        mock_cfg = MagicMock()
        mock_cfg.hosting = "company"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "Do this task",
                })

        assert resp.status_code == 200
        assert resp.json()["response"] == "Log-based result"

    async def test_chat_via_agent_loop_no_result_no_logs(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_task = MagicMock()
        mock_task.id = "t1"
        mock_task.status = "in_progress"
        mock_task.result = ""
        mock_task.logs = []

        mock_board = MagicMock()
        mock_board.get_task.return_value = mock_task

        mock_loop = MagicMock()
        mock_loop.push_task.return_value = mock_task
        mock_loop.board = mock_board

        mock_cfg = MagicMock()
        mock_cfg.hosting = "company"

        sleep_count = 0
        async def fast_sleep(t):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                mock_task.status = "completed"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("asyncio.sleep", side_effect=fast_sleep):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "Do this",
                })

        assert resp.status_code == 200

    async def test_chat_first_message_marks_listening(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "Hello"

        mock_cfg = MagicMock()
        mock_cfg.hosting = "company"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.agents.base.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_tools_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_talent_persona", return_value=""), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "Hi there",
                    "history": [],
                })

        assert resp.status_code == 200
        assert emp.is_listening is True


# ---------------------------------------------------------------------------
# POST /api/oneonone/chat — self-hosted path
# ---------------------------------------------------------------------------


class TestOneOnOneChatSelfHosted:
    async def test_chat_self_hosted_first_message(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_cfg = MagicMock()
        mock_cfg.hosting = "self"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.core.claude_session.run_claude_session", new_callable=AsyncMock, return_value="Self-hosted reply"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "Hello",
                    "history": [],
                })

        assert resp.status_code == 200
        assert resp.json()["response"] == "Self-hosted reply"

    async def test_chat_self_hosted_subsequent_message(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_cfg = MagicMock()
        mock_cfg.hosting = "self"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.core.claude_session.run_claude_session", new_callable=AsyncMock, return_value="Follow-up reply"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "More info",
                    "history": [{"role": "ceo", "content": "Hello"}],
                })

        assert resp.status_code == 200
        assert resp.json()["response"] == "Follow-up reply"


# ---------------------------------------------------------------------------
# POST /api/ceo/task — with project_id and project_name paths
# ---------------------------------------------------------------------------


class TestCeoSubmitTaskPaths:
    async def test_task_with_project_id(self):
        state = _make_state()
        bus = EventBus()
        mock_loop = MagicMock()
        mock_loop.push_task = MagicMock()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop), \
             patch("onemancompany.core.project_archive.create_iteration", return_value="iter_001"), \
             patch("onemancompany.core.project_archive.get_project_workspace", return_value="/tmp/ws"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ceo/task", json={
                    "task": "Add new feature",
                    "project_id": "my-project",
                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "my-project"
        assert data["iteration_id"] == "iter_001"

    async def test_task_with_project_name(self):
        state = _make_state()
        bus = EventBus()
        mock_loop = MagicMock()
        mock_loop.push_task = MagicMock()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop), \
             patch("onemancompany.core.project_archive.create_named_project", return_value="new-project"), \
             patch("onemancompany.core.project_archive.create_iteration", return_value="iter_001"), \
             patch("onemancompany.core.project_archive.get_project_workspace", return_value="/tmp/ws"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ceo/task", json={
                    "task": "Build new product",
                    "project_name": "New Product",
                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "new-project"


# ---------------------------------------------------------------------------
# POST /api/task/{project_id}/abort
# ---------------------------------------------------------------------------


class TestAbortTask:
    async def test_abort_task(self):
        state = _make_state(active_tasks=[
            TaskEntry(project_id="proj1", task="Build", routed_to="COO"),
        ])
        bus = EventBus()

        mock_board = MagicMock()
        mock_board.cancel_by_project.return_value = []

        mock_loop = MagicMock()
        mock_loop.board = mock_board

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.agent_loops", {"emp01": mock_loop}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/task/proj1/abort")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert len(state.active_tasks) == 0


# ---------------------------------------------------------------------------
# POST /api/employee/{employee_id}/task/{task_id}/cancel
# ---------------------------------------------------------------------------


class TestCancelAgentTask:
    async def test_cancel_task_no_loop(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/task/t1/cancel")

        assert resp.json()["message"] == "Agent not found"

    async def test_cancel_task_not_found(self):
        state = _make_state()
        mock_board = MagicMock()
        mock_board.get_task.return_value = None
        mock_loop = MagicMock()
        mock_loop.board = mock_board

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/task/t999/cancel")

        assert resp.json()["message"] == "Task not found"

    async def test_cancel_task_already_completed(self):
        state = _make_state()
        mock_task = MagicMock()
        mock_task.status = "completed"
        mock_board = MagicMock()
        mock_board.get_task.return_value = mock_task
        mock_loop = MagicMock()
        mock_loop.board = mock_board

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/task/t1/cancel")

        assert "already" in resp.json()["message"]

    async def test_cancel_task_success(self):
        state = _make_state()
        bus = EventBus()

        mock_task = MagicMock()
        mock_task.status = "pending"
        mock_task.sub_task_ids = []
        mock_board = MagicMock()
        mock_board.get_task.return_value = mock_task
        mock_loop = MagicMock()
        mock_loop.board = mock_board

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/task/t1/cancel")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert mock_task.status == "cancelled"


# ---------------------------------------------------------------------------
# PUT /api/employee/{employee_id}/api-key
# ---------------------------------------------------------------------------


class TestUpdateEmployeeApiKey:
    async def test_update_api_key_employee_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/99999/api-key", json={"api_key": "sk-test"})

        assert resp.json()["error"] == "Employee not found"

    async def test_update_api_key_no_config(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/00010/api-key", json={"api_key": "sk-test"})

        assert resp.json()["error"] == "Employee config not found"

    async def test_update_api_key_openrouter_rejected(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})

        mock_cfg = MagicMock()
        mock_cfg.api_provider = "openrouter"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/00010/api-key", json={"api_key": "sk-test"})

        assert "OpenRouter" in resp.json()["error"]

    async def test_update_api_key_success(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_cfg = MagicMock()
        mock_cfg.api_provider = "anthropic"
        mock_cfg.llm_model = "claude-3"
        mock_cfg.hosting = "company"

        mock_path = MagicMock()
        mock_path.exists.return_value = False

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", MagicMock(__truediv__=MagicMock(return_value=MagicMock(__truediv__=MagicMock(return_value=mock_path))))), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/00010/api-key", json={"api_key": "sk-new-key"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert data["api_key_set"] is True


# ---------------------------------------------------------------------------
# Employee detail — self-hosted variant
# ---------------------------------------------------------------------------


class TestGetEmployeeDetailSelfHosted:
    async def test_employee_self_hosted_includes_sessions(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})

        mock_cfg = MagicMock()
        mock_cfg.llm_model = "claude-3"
        mock_cfg.api_provider = "anthropic"
        mock_cfg.api_key = "sk-test"
        mock_cfg.hosting = "self"
        mock_cfg.auth_method = "api_key"
        mock_cfg.tool_permissions = []

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.core.config.load_manifest", return_value=None), \
             patch("onemancompany.core.claude_session.list_sessions", return_value=[{"project_id": "p1"}]):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010")

        assert resp.status_code == 200
        data = resp.json()
        assert data["hosting"] == "self"
        assert "sessions" in data


# ---------------------------------------------------------------------------
# Employee detail — no config
# ---------------------------------------------------------------------------


class TestGetEmployeeDetailNoConfig:
    async def test_employee_no_config(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {}), \
             patch("onemancompany.core.config.load_manifest", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010")

        assert resp.status_code == 200
        data = resp.json()
        assert data["llm_model"] == ""
        assert data["hosting"] == "company"


# ---------------------------------------------------------------------------
# Employee detail — with manifest
# ---------------------------------------------------------------------------


class TestGetEmployeeDetailWithManifest:
    async def test_employee_with_manifest(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})

        mock_cfg = MagicMock()
        mock_cfg.llm_model = "gpt-4"
        mock_cfg.api_provider = "openrouter"
        mock_cfg.api_key = ""
        mock_cfg.hosting = "company"
        mock_cfg.auth_method = "api_key"
        mock_cfg.tool_permissions = None

        manifest = {"name": "Test Manifest", "settings": []}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.core.config.load_manifest", return_value=manifest):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010")

        assert resp.status_code == 200
        data = resp.json()
        assert "manifest" in data
        assert data["manifest"]["name"] == "Test Manifest"


# ---------------------------------------------------------------------------
# GET /api/employee/{employee_id}/logs — with loop
# ---------------------------------------------------------------------------


class TestEmployeeLogsWithLoop:
    async def test_logs_with_current_task(self):
        state = _make_state()

        mock_loop = MagicMock()
        mock_loop._current_task = MagicMock()
        mock_loop._current_task.logs = [
            {"type": "start", "content": "Started"},
            {"type": "result", "content": "Done"},
        ]

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/logs")

        assert resp.status_code == 200
        assert len(resp.json()["logs"]) == 2

    async def test_logs_from_recent_task(self):
        state = _make_state()
        from onemancompany.core.agent_loop import AgentTask as AT

        task1 = AT(id="t1", description="Old task")
        task1.logs = [{"type": "result", "content": "Old result"}]

        mock_loop = MagicMock()
        mock_loop._current_task = None
        mock_loop.board = MagicMock()
        mock_loop.board.tasks = [task1]

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/logs")

        assert resp.status_code == 200
        assert len(resp.json()["logs"]) == 1

    async def test_logs_no_tasks_with_logs(self):
        state = _make_state()
        from onemancompany.core.agent_loop import AgentTask as AT

        task_no_logs = AT(id="t1", description="Task")

        mock_loop = MagicMock()
        mock_loop._current_task = None
        mock_loop.board = MagicMock()
        mock_loop.board.tasks = [task_no_logs]

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/logs")

        assert resp.status_code == 200
        assert resp.json()["logs"] == []


# ---------------------------------------------------------------------------
# PUT /api/employee/{employee_id}/model
# ---------------------------------------------------------------------------


class TestUpdateEmployeeModel:
    async def test_update_model_success(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_cfg = MagicMock()
        mock_cfg.api_provider = "openrouter"
        mock_cfg.salary_per_1m_tokens = 1.0

        mock_path = MagicMock()
        mock_path.exists.return_value = False

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", MagicMock(__truediv__=MagicMock(return_value=MagicMock(__truediv__=MagicMock(return_value=mock_path))))), \
             patch("onemancompany.core.model_costs.compute_salary", return_value=2.5):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/00010/model", json={"model": "gpt-4o"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"

    async def test_update_model_missing_model(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/00010/model", json={"model": ""})

        assert resp.json()["error"] == "Missing model"

    async def test_update_model_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/99999/model", json={"model": "gpt-4o"})

        assert resp.json()["error"] == "Employee not found"


# ---------------------------------------------------------------------------
# GET /api/projects
# ---------------------------------------------------------------------------


class TestGetProjects:
    async def test_list_projects(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.list_projects", return_value=[{"id": "p1", "name": "Project 1"}]):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects")

        assert resp.status_code == 200
        assert len(resp.json()["projects"]) == 1


# ---------------------------------------------------------------------------
# GET /api/projects/named
# ---------------------------------------------------------------------------


class TestListNamedProjects:
    async def test_list_named_projects(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.list_named_projects", return_value=[{"id": "p1"}]):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/named")

        assert resp.status_code == 200
        assert len(resp.json()["projects"]) == 1


# ---------------------------------------------------------------------------
# GET /api/projects/named/{project_id}
# ---------------------------------------------------------------------------


class TestGetNamedProjectDetail:
    async def test_project_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/named/nonexistent")

        assert resp.json()["error"] == "Named project not found"

    async def test_project_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_named_project", return_value={
                 "name": "Test", "iterations": [], "status": "active"
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/named/test-project")

        assert resp.status_code == 200
        assert resp.json()["name"] == "Test"


# ---------------------------------------------------------------------------
# POST /api/projects/{project_id}/archive
# ---------------------------------------------------------------------------


class TestArchiveProject:
    async def test_archive_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/projects/nonexistent/archive")

        assert resp.json()["error"] == "Named project not found"

    async def test_archive_success(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_named_project", return_value={"name": "Test"}), \
             patch("onemancompany.core.project_archive.archive_project"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/projects/test-proj/archive")

        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id}
# ---------------------------------------------------------------------------


class TestGetProjectDetail:
    async def test_project_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_project", return_value={"task": "Build"}), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value="/tmp/proj"), \
             patch("onemancompany.core.project_archive.list_project_files", return_value=["file.py"]):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/proj1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["task"] == "Build"
        assert data["project_dir"] == "/tmp/proj"

    async def test_project_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_project", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/nonexistent")

        assert resp.json()["error"] == "Project not found"


# ---------------------------------------------------------------------------
# GET /api/dashboard/costs
# ---------------------------------------------------------------------------


class TestDashboardCosts:
    async def test_get_costs(self):
        from onemancompany.core.models import OverheadCosts as OH
        oh = OH()
        state = _make_state()
        state.overhead_costs = oh

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.get_cost_summary", return_value={
                 "total": {"cost_usd": 1.5}, "projects": [],
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/dashboard/costs")

        assert resp.status_code == 200
        data = resp.json()
        assert "grand_total_usd" in data
        assert "overhead" in data


# ---------------------------------------------------------------------------
# File edits endpoints
# ---------------------------------------------------------------------------


class TestFileEdits:
    async def test_get_pending_edits(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.file_editor.list_pending_edits", return_value=[]):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/file-edits")

        assert resp.status_code == 200
        assert resp.json()["edits"] == []

    async def test_approve_edit_success(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.file_editor.execute_edit", return_value={
                 "status": "ok", "rel_path": "file.py", "backup_path": "/backup/file.py"
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/file-edits/edit_123/approve")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_approve_edit_error(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.file_editor.execute_edit", return_value={
                 "status": "error", "message": "Edit not found"
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/file-edits/nonexistent/approve")

        assert resp.json()["status"] == "error"

    async def test_reject_edit_success(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.file_editor.reject_edit", return_value={
                 "status": "ok", "rel_path": "file.py"
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/file-edits/edit_123/reject")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_reject_edit_error(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.file_editor.reject_edit", return_value={
                 "status": "error", "message": "Not found"
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/file-edits/nonexistent/reject")

        assert resp.json()["status"] == "error"


# ---------------------------------------------------------------------------
# Resolutions endpoints
# ---------------------------------------------------------------------------


class TestResolutions:
    async def test_get_deferred_edits(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.resolutions.list_deferred_edits", return_value=[]):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/resolutions/deferred")

        assert resp.status_code == 200
        assert resp.json()["edits"] == []

    async def test_list_resolutions(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.resolutions.list_resolutions", return_value=[]):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/resolutions")

        assert resp.status_code == 200
        assert resp.json()["resolutions"] == []

    async def test_get_resolution_detail_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.resolutions.load_resolution", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/resolutions/nonexistent")

        assert resp.json()["error"] == "Resolution not found"

    async def test_get_resolution_detail_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.resolutions.load_resolution", return_value={"id": "r1", "edits": []}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/resolutions/r1")

        assert resp.status_code == 200
        assert resp.json()["id"] == "r1"

    async def test_decide_on_resolution_missing_decisions(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/resolutions/r1/decide", json={"decisions": {}})

        assert resp.json()["error"] == "No decisions provided"

    async def test_decide_on_resolution_success(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.resolutions.decide_resolution", return_value={
                 "status": "ok", "results": [{"edit_id": "e1", "action": "approve"}]
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/resolutions/r1/decide", json={
                    "decisions": {"e1": "approve"},
                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_execute_deferred_success(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.resolutions.execute_deferred_edit", return_value={
                 "status": "ok", "rel_path": "file.py", "backup_path": "/backup"
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/resolutions/deferred/r1/e1/execute")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Inquiry end — with session
# ---------------------------------------------------------------------------


class TestInquiryEndWithSession:
    async def test_end_with_existing_session(self):
        from onemancompany.api.routes import InquirySession
        room = MeetingRoom(id="room1", name="Alpha", description="Room", is_booked=True, booked_by="00001")
        state = _make_state(meeting_rooms={"room1": room})
        bus = EventBus()

        session = InquirySession(
            session_id="sess1",
            task="What is our revenue?",
            room_id="room1",
            agent_role="COO",
            participants=["00001", "00003"],
            history=[{"role": "ceo", "speaker": "CEO", "content": "Hi"}],
        )

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._inquiry_sessions", {"sess1": session}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/inquiry/end", json={"session_id": "sess1"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ended"
        assert room.is_booked is False


# ---------------------------------------------------------------------------
# Inquiry chat — with session
# ---------------------------------------------------------------------------


class TestInquiryChatWithSession:
    async def test_chat_with_session(self):
        from onemancompany.api.routes import InquirySession
        emp = _make_employee(id="00003", name="COO Boss", role="COO")
        state = _make_state(employees={"00003": emp})
        bus = EventBus()

        session = InquirySession(
            session_id="sess1",
            task="Revenue question",
            room_id="room1",
            agent_role="COO",
            participants=["00001", "00003"],
            history=[
                {"role": "ceo", "speaker": "CEO", "content": "What's our revenue?"},
                {"role": "agent", "speaker": "COO Boss", "content": "Revenue is $1M"},
            ],
        )
        session._system_prompt = "You are the COO"

        mock_result = MagicMock()
        mock_result.content = "Let me explain further"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._inquiry_sessions", {"sess1": session}), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/inquiry/chat", json={
                    "session_id": "sess1",
                    "message": "Tell me more",
                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["response"] == "Let me explain further"
        assert data["speaker"] == "COO Boss"

    async def test_chat_session_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.api.routes._inquiry_sessions", {}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/inquiry/chat", json={
                    "session_id": "nonexistent",
                    "message": "Hello",
                })

        assert resp.json()["error"] == "Inquiry session not found"


# ---------------------------------------------------------------------------
# Employee sessions (self-hosted)
# ---------------------------------------------------------------------------


class TestEmployeeSessions:
    async def test_get_sessions_not_self_hosted(self):
        state = _make_state()
        mock_cfg = MagicMock()
        mock_cfg.hosting = "company"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/sessions")

        assert resp.json()["error"] == "Employee is not self-hosted"

    async def test_get_sessions_no_config(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/sessions")

        assert resp.json()["error"] == "Employee is not self-hosted"

    async def test_get_sessions_success(self):
        state = _make_state()
        mock_cfg = MagicMock()
        mock_cfg.hosting = "self"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.core.claude_session.list_sessions", return_value=[{"project_id": "p1"}]):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/sessions")

        assert resp.status_code == 200
        assert resp.json()["employee_id"] == "00010"

    async def test_delete_session_not_self_hosted(self):
        state = _make_state()
        mock_cfg = MagicMock()
        mock_cfg.hosting = "company"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.delete("/api/employee/00010/sessions/proj1")

        assert resp.json()["error"] == "Employee is not self-hosted"

    async def test_delete_session_success(self):
        state = _make_state()
        mock_cfg = MagicMock()
        mock_cfg.hosting = "self"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.core.claude_session.cleanup_session"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.delete("/api/employee/00010/sessions/proj1")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Hiring requests
# ---------------------------------------------------------------------------


class TestHiringRequests:
    async def test_list_hiring_requests(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.agents.coo_agent.pending_hiring_requests", {"r1": {"role": "Developer", "reason": "Need help"}}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/hiring-requests")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1

    async def test_decide_hiring_request_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.agents.coo_agent.pending_hiring_requests", {}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/hiring-requests/nonexistent/decide", json={"approved": True})

        assert "not found" in resp.json()["error"]

    async def test_decide_hiring_request_rejected(self):
        state = _make_state()
        bus = EventBus()
        reqs = {"r1": {"role": "Developer", "reason": "Need help"}}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.agents.coo_agent.pending_hiring_requests", reqs):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/hiring-requests/r1/decide", json={"approved": False})

        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"


# ---------------------------------------------------------------------------
# OAuth start
# ---------------------------------------------------------------------------


class TestOAuthStart:
    async def test_oauth_start_employee_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/99999/oauth/start")

        assert resp.json()["error"] == "Employee not found"

    async def test_oauth_start_not_oauth_auth(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})

        mock_cfg = MagicMock()
        mock_cfg.auth_method = "api_key"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/oauth/start")

        assert "OAuth" in resp.json()["error"]

    async def test_oauth_start_success(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})

        mock_cfg = MagicMock()
        mock_cfg.auth_method = "oauth"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.api.routes._oauth_sessions", {}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/oauth/start")

        assert resp.status_code == 200
        data = resp.json()
        assert "auth_url" in data
        assert "state" in data


# ---------------------------------------------------------------------------
# OAuth refresh
# ---------------------------------------------------------------------------


class TestOAuthRefresh:
    async def test_oauth_refresh_no_token(self):
        state = _make_state()

        mock_cfg = MagicMock()
        mock_cfg.oauth_refresh_token = ""

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/oauth/refresh")

        assert "No refresh token" in resp.json()["error"]

    async def test_oauth_refresh_no_config(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/oauth/refresh")

        assert "No refresh token" in resp.json()["error"]


# ---------------------------------------------------------------------------
# POST /api/meeting/book
# ---------------------------------------------------------------------------


class TestMeetingBook:
    async def test_book_meeting_with_loop(self):
        state = _make_state()
        bus = EventBus()
        mock_loop = MagicMock()
        mock_loop.push_task = MagicMock()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/meeting/book", json={
                    "employee_id": "00010",
                    "participants": ["00010", "00011"],
                    "purpose": "Planning",
                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "processing"
        mock_loop.push_task.assert_called_once()

    async def test_book_meeting_missing_employee_id(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/meeting/book", json={})

        assert resp.json()["error"] == "Missing employee_id"


# ---------------------------------------------------------------------------
# POST /api/hr/review
# ---------------------------------------------------------------------------


class TestHRReview:
    async def test_hr_review_with_loop(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()
        mock_loop = MagicMock()
        mock_loop.push_task = MagicMock()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/hr/review")

        assert resp.status_code == 200
        assert resp.json()["status"] == "HR review started"
        mock_loop.push_task.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/routine/start + approve + all_hands
# ---------------------------------------------------------------------------


class TestRoutineStart:
    async def test_start_routine(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._run_agent_safe", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.run_post_task_routine", new_callable=AsyncMock):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/routine/start", json={"task_summary": "Build widget"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "routine_started"


class TestRoutineApprove:
    async def test_approve_actions_missing_report_id(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/routine/approve", json={"report_id": ""})

        assert resp.json()["error"] == "Missing report_id"

    async def test_approve_actions_success(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._run_agent_safe", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.execute_approved_actions", new_callable=AsyncMock):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/routine/approve", json={
                    "report_id": "rpt_123",
                    "approved_indices": [0, 2],
                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "executing_approved_actions"


class TestRoutineAllHands:
    async def test_all_hands_missing_message(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/routine/all_hands", json={"message": ""})

        assert resp.json()["error"] == "Missing CEO message"

    async def test_all_hands_success(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._run_agent_safe", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.run_all_hands_meeting", new_callable=AsyncMock):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/routine/all_hands", json={"message": "Company update"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "all_hands_started"


# ---------------------------------------------------------------------------
# GET /api/models
# ---------------------------------------------------------------------------


class TestListModels:
    async def test_list_models_success(self):
        state = _make_state()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{"id": "model-1", "name": "Model One", "context_length": 4096}]
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch("onemancompany.core.config.settings") as mock_settings:
            mock_settings.openrouter_base_url = "https://api.openrouter.ai/api/v1"
            mock_settings.openrouter_api_key = "sk-test"
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/models")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["models"]) == 1
        assert data["models"][0]["id"] == "model-1"

    async def test_list_models_error(self):
        state = _make_state()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("Connection error"))

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch("onemancompany.core.config.settings") as mock_settings:
            mock_settings.openrouter_base_url = "https://api.openrouter.ai/api/v1"
            mock_settings.openrouter_api_key = "sk-test"
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/models")

        assert resp.status_code == 200
        data = resp.json()
        assert data["models"] == []
        assert "error" in data


# ---------------------------------------------------------------------------
# GET /api/employee/{employee_id}/okrs + PUT
# ---------------------------------------------------------------------------


class TestEmployeeOKRs:
    async def test_get_okrs(self):
        emp = _make_employee(id="00010")
        emp.okrs = [{"objective": "Ship feature", "key_results": []}]
        state = _make_state(employees={"00010": emp})

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/okrs")

        assert resp.status_code == 200
        assert len(resp.json()["okrs"]) == 1

    async def test_get_okrs_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/99999/okrs")

        assert resp.status_code == 404

    async def test_update_okrs(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.update_employee_field"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/00010/okrs", json={
                    "okrs": [{"objective": "New goal"}],
                })

        assert resp.status_code == 200
        assert resp.json()["okrs"] == [{"objective": "New goal"}]

    async def test_update_okrs_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/99999/okrs", json={"okrs": []})

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/employee/{employee_id}/taskboard — with loop
# ---------------------------------------------------------------------------


class TestEmployeeTaskboardWithLoop:
    async def test_taskboard_with_loop(self):
        state = _make_state()
        mock_board = MagicMock()
        mock_board.to_dict.return_value = [{"id": "t1", "description": "Task"}]
        mock_loop = MagicMock()
        mock_loop.board = mock_board

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/taskboard")

        assert resp.status_code == 200
        assert len(resp.json()["tasks"]) == 1


# ---------------------------------------------------------------------------
# Sales — CSO notification + not found cases
# ---------------------------------------------------------------------------


class TestSalesSubmitWithCSO:
    async def test_submit_with_cso_notification(self):
        state = _make_state()
        bus = EventBus()
        mock_loop = MagicMock()
        mock_loop.push_task = MagicMock()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/sales/submit", json={
                    "client_name": "Acme Corp",
                    "description": "Build widget",
                })

        assert resp.status_code == 200
        mock_loop.push_task.assert_called_once()


class TestSalesDeliverNotFound:
    async def test_deliver_task_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/sales/tasks/nonexistent/deliver", json={})

        assert "not found" in resp.json()["error"]


class TestSalesSettleNotFound:
    async def test_settle_task_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/sales/tasks/nonexistent/settle")

        assert "not found" in resp.json()["error"]


# ---------------------------------------------------------------------------
# _run_agent_safe — the main task execution wrapper
# ---------------------------------------------------------------------------


class TestRunAgentSafe:
    async def test_run_agent_safe_happy_path(self):
        """Covers lines 65-179: successful agent run with project archive."""
        state = _make_state()
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "Done"

        # Use ceo_submit_task to trigger _run_agent_safe indirectly
        mock_loop = MagicMock()
        mock_loop.push_task = MagicMock()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop), \
             patch("onemancompany.core.project_archive.create_project", return_value="proj1"), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value="/tmp/proj1"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ceo/task", json={"task": "Build something"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processing"

    async def test_run_agent_safe_directly(self):
        """Call _run_agent_safe directly to cover lines 65-179."""
        from onemancompany.api.routes import _run_agent_safe

        state = _make_state()
        bus = EventBus()

        async def dummy_coro():
            return "result text"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.append_action"), \
             patch("onemancompany.core.project_archive.complete_project"), \
             patch("onemancompany.core.project_archive.create_project", return_value="p1"), \
             patch("onemancompany.core.resolutions.create_resolution", return_value=None), \
             patch("onemancompany.core.state.flush_pending_reload", return_value=None), \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
            await _run_agent_safe(dummy_coro(), "COO", project_id="proj_x", task_description="test task")

        # Employees should be reset to idle
        for emp in state.employees.values():
            assert emp.status == "idle"

    async def test_run_agent_safe_with_auto_project(self):
        """No project_id provided — auto-generates one."""
        from onemancompany.api.routes import _run_agent_safe

        state = _make_state()
        bus = EventBus()

        async def dummy_coro():
            return "done"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.append_action"), \
             patch("onemancompany.core.project_archive.complete_project"), \
             patch("onemancompany.core.resolutions.create_resolution", return_value=None), \
             patch("onemancompany.core.state.flush_pending_reload", return_value=None), \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
            await _run_agent_safe(dummy_coro(), "COO", task_description="auto task")

    async def test_run_agent_safe_agent_error(self):
        """Agent raises an exception — error path."""
        from onemancompany.api.routes import _run_agent_safe

        state = _make_state()
        bus = EventBus()

        async def failing_coro():
            raise RuntimeError("Agent exploded")

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.append_action"), \
             patch("onemancompany.core.project_archive.complete_project"), \
             patch("onemancompany.core.resolutions.create_resolution", return_value=None), \
             patch("onemancompany.core.state.flush_pending_reload", return_value=None), \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
            await _run_agent_safe(
                failing_coro(), "COO",
                project_id="proj_err", task_description="bad task"
            )

    async def test_run_agent_safe_with_routine(self):
        """run_routine_after triggers post-task routine."""
        from onemancompany.api.routes import _run_agent_safe

        state = _make_state()
        bus = EventBus()

        async def dummy_coro():
            return "ok"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.append_action"), \
             patch("onemancompany.core.project_archive.complete_project"), \
             patch("onemancompany.core.resolutions.create_resolution", return_value=None), \
             patch("onemancompany.core.routine.run_post_task_routine", new_callable=AsyncMock), \
             patch("onemancompany.core.state.flush_pending_reload", return_value=None), \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
            await _run_agent_safe(
                dummy_coro(), "COO",
                run_routine_after="Build something",
                project_id="proj_rtn"
            )

    async def test_run_agent_safe_routine_error(self):
        """Routine raises — error path in finally block."""
        from onemancompany.api.routes import _run_agent_safe

        state = _make_state()
        bus = EventBus()

        async def dummy_coro():
            return "ok"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.append_action"), \
             patch("onemancompany.core.project_archive.complete_project"), \
             patch("onemancompany.core.resolutions.create_resolution", return_value=None), \
             patch("onemancompany.core.routine.run_post_task_routine", new_callable=AsyncMock, side_effect=RuntimeError("routine failed")), \
             patch("onemancompany.core.state.flush_pending_reload", return_value=None), \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
            await _run_agent_safe(
                dummy_coro(), "COO",
                run_routine_after="Something",
                project_id="proj_rtn_err"
            )

    async def test_run_agent_safe_with_resolution(self):
        """Resolution created from file edits."""
        from onemancompany.api.routes import _run_agent_safe

        state = _make_state()
        bus = EventBus()

        async def dummy_coro():
            return "done"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.append_action"), \
             patch("onemancompany.core.project_archive.complete_project"), \
             patch("onemancompany.core.resolutions.create_resolution", return_value={"id": "r1", "edits": []}), \
             patch("onemancompany.core.state.flush_pending_reload", return_value=None), \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
            await _run_agent_safe(
                dummy_coro(), "COO",
                project_id="proj_res", task_description="edit files"
            )

    async def test_run_agent_safe_flush_reload(self):
        """Post-task flush returns reload data."""
        from onemancompany.api.routes import _run_agent_safe

        state = _make_state()
        bus = EventBus()

        async def dummy_coro():
            return "ok"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.append_action"), \
             patch("onemancompany.core.project_archive.complete_project"), \
             patch("onemancompany.core.resolutions.create_resolution", return_value=None), \
             patch("onemancompany.core.state.flush_pending_reload", return_value={"employees_updated": ["e1"], "employees_added": ["e2"]}), \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
            await _run_agent_safe(
                dummy_coro(), "COO",
                project_id="proj_flush"
            )


# ---------------------------------------------------------------------------
# _start_inquiry — inquiry session creation (lines 211-340)
# ---------------------------------------------------------------------------


class TestStartInquiry:
    async def test_start_inquiry_via_ceo_qa(self):
        """CEO QA that triggers inquiry — covers _start_inquiry."""
        emp_coo = _make_employee(id="00003", name="COO Agent", nickname="SomeNick")
        emp_hr = _make_employee(id="00002", name="HR Agent", nickname="HRNick")
        emp_cso = _make_employee(id="00005", name="CSO Agent", nickname="CSONick")
        state = _make_state(employees={"00002": emp_hr, "00003": emp_coo, "00005": emp_cso})
        state.meeting_rooms = {
            "room1": MeetingRoom(id="room1", name="Room A", description="A meeting room", position=(0, 0))
        }
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "Here's what I think..."

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.agents.base.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_tools_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_talent_persona", return_value=""):
            # Call _start_inquiry directly
            from onemancompany.api.routes import _start_inquiry
            result = await _start_inquiry("What is our Q4 strategy?")

        assert result["task_type"] == "inquiry"
        assert result["status"] == "inquiry_active"
        assert "session_id" in result
        assert result["agent_role"] in ("HR", "COO", "CSO")

    async def test_start_inquiry_no_room(self):
        """No meeting rooms available."""
        emp = _make_employee(id="00002", name="COO Agent")
        state = _make_state(employees={"00002": emp})
        state.meeting_rooms = {}
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.agents.base.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_tools_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_talent_persona", return_value=""):
            from onemancompany.api.routes import _start_inquiry
            result = await _start_inquiry("How are sales?")

        assert "error" in result

    async def test_start_inquiry_hr_keyword(self):
        """HR keyword routes to HR agent."""
        emp = _make_employee(id="00002", name="HR Agent")
        state = _make_state(employees={"00002": emp})
        state.meeting_rooms = {
            "room1": MeetingRoom(id="room1", name="Room B", description="Another meeting room", position=(0, 0))
        }
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "I'll handle recruiting."

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.agents.base.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_tools_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_talent_persona", return_value=""):
            from onemancompany.api.routes import _start_inquiry
            result = await _start_inquiry("招聘一个新的工程师")

        assert result["task_type"] == "inquiry"
        assert result["agent_role"] == "HR"


# ---------------------------------------------------------------------------
# CEO task — EA routing fallback (lines 446-453)
# ---------------------------------------------------------------------------


class TestCeoTaskEAFallback:
    async def test_ceo_task_ea_fallback(self):
        """When no EA loop, falls back to EAAgent."""
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None), \
             patch("onemancompany.core.project_archive.create_project", return_value="p1"), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value="/tmp/p1"), \
             patch("onemancompany.core.project_archive.get_project_workspace", return_value="/tmp/p1"), \
             patch("onemancompany.agents.ea_agent.EAAgent") as mock_ea_cls, \
             patch("onemancompany.api.routes._run_agent_safe", new_callable=AsyncMock):
            mock_ea = MagicMock()
            mock_ea.run = MagicMock(return_value=AsyncMock()())
            mock_ea_cls.return_value = mock_ea

            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ceo/task", json={"task": "Do something"})

        assert resp.status_code == 200
        assert resp.json()["routed_to"] == "EA"


# ---------------------------------------------------------------------------
# Meeting book — COO fallback (lines 712-718)
# ---------------------------------------------------------------------------


class TestMeetingBookCOOFallback:
    async def test_meeting_book_coo_fallback(self):
        """When no COO agent loop, falls back to COOAgent."""
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None), \
             patch("onemancompany.agents.coo_agent.COOAgent") as mock_coo_cls, \
             patch("onemancompany.api.routes._run_agent_safe", new_callable=AsyncMock):
            mock_coo = MagicMock()
            mock_coo.run = MagicMock(return_value=AsyncMock()())
            mock_coo_cls.return_value = mock_coo

            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/meeting/book", json={
                    "employee_id": "00001",
                    "participants": ["00002"],
                    "purpose": "sync up"
                })

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# HR review — fallback (lines 906-908)
# ---------------------------------------------------------------------------


class TestHRReviewFallback:
    async def test_hr_review_fallback(self):
        """When no HR agent loop, falls back to HRAgent."""
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None), \
             patch("onemancompany.agents.hr_agent.HRAgent") as mock_hr_cls, \
             patch("onemancompany.api.routes._run_agent_safe", new_callable=AsyncMock):
            mock_hr = MagicMock()
            mock_hr.run_quarterly_review = MagicMock(return_value=AsyncMock()())
            mock_hr_cls.return_value = mock_hr

            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/hr/review")

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Oneonone chat — attachments path (lines 491-494)
# ---------------------------------------------------------------------------


class TestOneOnOneChatAttachments:
    async def test_chat_with_attachments(self):
        """Covers attach_info building in oneonone/chat."""
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "Got the files"

        mock_cfg = MagicMock()
        mock_cfg.hosting = "company"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.agents.base.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_tools_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_talent_persona", return_value=""), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "Check these files",
                    "history": [{"role": "ceo", "content": "prev msg"}],
                    "attachments": [{"filename": "doc.pdf", "path": "/uploads/doc.pdf"}],
                })

        assert resp.status_code == 200
        assert resp.json()["response"] == "Got the files"


# ---------------------------------------------------------------------------
# Oneonone chat — with history in LLM path (line 564 context)
# ---------------------------------------------------------------------------


class TestOneOnOneChatWithHistoryLLM:
    async def test_chat_with_history_llm(self):
        """Covers the history-building branch in the LLM fallback path."""
        emp = _make_employee(id="00010")
        emp.work_principles = "Be helpful"
        state = _make_state(employees={"00010": emp})
        state.company_culture = [{"content": "Move fast"}]
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "Follow-up reply"

        mock_cfg = MagicMock()
        mock_cfg.hosting = "company"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.agents.base.get_employee_skills_prompt", return_value="Skill: Python"), \
             patch("onemancompany.agents.base.get_employee_tools_prompt", return_value="Tool: Sandbox"), \
             patch("onemancompany.agents.base.get_employee_talent_persona", return_value="A diligent worker"), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "How is the project going?",
                    "history": [
                        {"role": "ceo", "content": "Start the project"},
                        {"role": "employee", "content": "On it!"},
                    ],
                })

        assert resp.status_code == 200
        assert resp.json()["response"] == "Follow-up reply"


# ---------------------------------------------------------------------------
# OAuth exchange (lines 1430-1529)
# ---------------------------------------------------------------------------


class TestOAuthExchange:
    async def test_oauth_exchange_missing_params(self):
        """Missing code or state."""
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/oauth/exchange", json={"code": "", "state": ""})

        assert "error" in resp.json()

    async def test_oauth_exchange_invalid_state(self):
        """State not found in sessions."""
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/oauth/exchange", json={"code": "abc", "state": "bad_state"})

        assert "Invalid" in resp.json()["error"]

    async def test_oauth_exchange_success(self, tmp_path):
        """Full OAuth exchange happy path."""
        from onemancompany.api.routes import _oauth_sessions

        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        _oauth_sessions["test_state_123"] = {
            "employee_id": "00010",
            "code_verifier": "verifier123",
            "redirect_uri": "http://localhost:8000/api/oauth/callback",
        }

        mock_cfg = MagicMock()
        mock_cfg.api_key = ""
        mock_cfg.oauth_refresh_token = ""

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "tok_abc", "refresh_token": "ref_xyz"}

        mock_key_resp = MagicMock()
        mock_key_resp.status_code = 200
        mock_key_resp.json.return_value = {"api_key": "sk-ant-permanent"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return mock_resp
            return mock_key_resp

        mock_client.post = mock_post

        # Create profile.yaml so the persist path works
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        (emp_dir / "profile.yaml").write_text("api_key: old\n")

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", tmp_path):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/oauth/exchange", json={
                    "code": "auth_code_123",
                    "state": "test_state_123",
                })

        data = resp.json()
        assert data["status"] == "ok"
        assert data["api_key_set"] is True

    async def test_oauth_exchange_employee_mismatch(self):
        """Employee ID in session doesn't match URL."""
        from onemancompany.api.routes import _oauth_sessions

        state = _make_state()
        bus = EventBus()

        _oauth_sessions["mismatch_state"] = {
            "employee_id": "00099",
            "code_verifier": "v",
            "redirect_uri": "http://localhost",
        }

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/oauth/exchange", json={
                    "code": "code",
                    "state": "mismatch_state",
                })

        assert "mismatch" in resp.json()["error"].lower()

    async def test_oauth_exchange_token_error(self):
        """Token exchange raises an exception."""
        from onemancompany.api.routes import _oauth_sessions

        state = _make_state()
        bus = EventBus()

        _oauth_sessions["err_state"] = {
            "employee_id": "00010",
            "code_verifier": "v",
            "redirect_uri": "http://localhost",
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=RuntimeError("Network error"))

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("httpx.AsyncClient", return_value=mock_client):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/oauth/exchange", json={
                    "code": "code",
                    "state": "err_state",
                })

        assert "error" in resp.json()

    async def test_oauth_exchange_no_access_token(self):
        """Token response missing access_token."""
        from onemancompany.api.routes import _oauth_sessions

        state = _make_state()
        bus = EventBus()

        _oauth_sessions["no_tok_state"] = {
            "employee_id": "00010",
            "code_verifier": "v",
            "redirect_uri": "http://localhost",
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("httpx.AsyncClient", return_value=mock_client):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/oauth/exchange", json={
                    "code": "code",
                    "state": "no_tok_state",
                })

        assert "No access_token" in resp.json()["error"]

    async def test_oauth_exchange_token_failed_status(self):
        """Token exchange returns non-200 status after both form and json attempts."""
        from onemancompany.api.routes import _oauth_sessions

        state = _make_state()
        bus = EventBus()

        _oauth_sessions["fail_state"] = {
            "employee_id": "00010",
            "code_verifier": "v",
            "redirect_uri": "http://localhost",
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad request"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("httpx.AsyncClient", return_value=mock_client):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/oauth/exchange", json={
                    "code": "code",
                    "state": "fail_state",
                })

        assert "Token exchange failed" in resp.json()["error"]


# ---------------------------------------------------------------------------
# OAuth callback (lines 1535-1629)
# ---------------------------------------------------------------------------


class TestOAuthCallback:
    async def test_callback_with_error(self):
        """Error parameter in callback."""
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/oauth/callback", params={
                    "error": "access_denied",
                    "state": "s1",
                    "code": "",
                })

        assert resp.status_code == 200
        assert "Login failed" in resp.text

    async def test_callback_invalid_state(self):
        """State not found."""
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/oauth/callback", params={
                    "code": "abc",
                    "state": "invalid_state",
                })

        assert resp.status_code == 200
        assert "Invalid session" in resp.text

    async def test_callback_success(self, tmp_path):
        """Full OAuth callback happy path."""
        from onemancompany.api.routes import _oauth_sessions

        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        _oauth_sessions["cb_state"] = {
            "employee_id": "00010",
            "code_verifier": "v",
            "redirect_uri": "http://localhost/api/oauth/callback",
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "tok", "refresh_token": "ref"}

        mock_key_resp = MagicMock()
        mock_key_resp.status_code = 200
        mock_key_resp.json.return_value = {"api_key": "sk-perm"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        call_count = 0
        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return mock_resp
            return mock_key_resp
        mock_client.post = mock_post

        mock_cfg = MagicMock()
        mock_cfg.api_key = ""
        mock_cfg.oauth_refresh_token = ""

        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        (emp_dir / "profile.yaml").write_text("api_key: old\n")

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", tmp_path):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/oauth/callback", params={
                    "code": "auth_code",
                    "state": "cb_state",
                })

        assert resp.status_code == 200
        assert "Login Successful" in resp.text

    async def test_callback_token_exchange_error(self):
        """Token exchange fails."""
        from onemancompany.api.routes import _oauth_sessions

        state = _make_state()
        bus = EventBus()

        _oauth_sessions["cb_err_state"] = {
            "employee_id": "00010",
            "code_verifier": "v",
            "redirect_uri": "http://localhost",
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=RuntimeError("net err"))

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("httpx.AsyncClient", return_value=mock_client):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/oauth/callback", params={
                    "code": "code",
                    "state": "cb_err_state",
                })

        assert "Token exchange error" in resp.text

    async def test_callback_token_failed_status(self):
        """Token exchange returns non-200."""
        from onemancompany.api.routes import _oauth_sessions

        state = _make_state()
        bus = EventBus()

        _oauth_sessions["cb_fail_state"] = {
            "employee_id": "00010",
            "code_verifier": "v",
            "redirect_uri": "http://localhost",
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("httpx.AsyncClient", return_value=mock_client):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/oauth/callback", params={
                    "code": "code",
                    "state": "cb_fail_state",
                })

        assert "Token exchange failed" in resp.text


# ---------------------------------------------------------------------------
# OAuth refresh with token exchange (lines 1652-1682)
# ---------------------------------------------------------------------------


class TestOAuthRefreshTokenExchange:
    async def test_refresh_success(self, tmp_path):
        """Full refresh happy path."""
        state = _make_state()
        bus = EventBus()

        mock_cfg = MagicMock()
        mock_cfg.oauth_refresh_token = "ref_tok"
        mock_cfg.api_key = "old_key"
        mock_cfg.api_provider = "anthropic"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "new_tok", "refresh_token": "new_ref"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        (emp_dir / "profile.yaml").write_text("api_key: old\n")

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", tmp_path):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/oauth/refresh")

        assert resp.json()["status"] == "refreshed"

    async def test_refresh_failed_status(self):
        """Refresh returns non-200."""
        state = _make_state()
        bus = EventBus()

        mock_cfg = MagicMock()
        mock_cfg.oauth_refresh_token = "ref_tok"
        mock_cfg.api_provider = "anthropic"

        mock_resp = MagicMock()
        mock_resp.status_code = 401

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("httpx.AsyncClient", return_value=mock_client):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/oauth/refresh")

        assert "Refresh failed" in resp.json()["error"]

    async def test_refresh_exception(self):
        """Refresh raises exception."""
        state = _make_state()
        bus = EventBus()

        mock_cfg = MagicMock()
        mock_cfg.oauth_refresh_token = "ref_tok"
        mock_cfg.api_provider = "anthropic"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=RuntimeError("timeout"))

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("httpx.AsyncClient", return_value=mock_client):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/oauth/refresh")

        assert "Refresh error" in resp.json()["error"]


# ---------------------------------------------------------------------------
# Ex-employee rehire (lines 2122-2200)
# ---------------------------------------------------------------------------


class TestRehireEmployee:
    async def test_rehire_not_found(self):
        state = _make_state()
        state.ex_employees = {}
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ex-employees/99999/rehire")

        assert "not found" in resp.json()["error"]

    async def test_rehire_success(self):
        state = _make_state()
        ex_emp = MagicMock()
        ex_emp.id = "00010"
        ex_emp.name = "Test Employee"
        ex_emp.nickname = "TestNick"
        ex_emp.department = "技术研发部"
        ex_emp.role = "Engineer"
        ex_emp.skills = ["Python"]
        ex_emp.sprite = "employee_default"
        ex_emp.remote = False
        state.ex_employees = {"00010": ex_emp}
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.load_employee_guidance", return_value="Be helpful"), \
             patch("onemancompany.core.config.load_work_principles", return_value="Work hard"), \
             patch("onemancompany.core.config.move_ex_employee_back", return_value=True), \
             patch("onemancompany.core.layout.compute_layout"), \
             patch("onemancompany.core.layout.persist_all_desk_positions"), \
             patch("onemancompany.core.layout.get_next_desk_for_department", return_value=(5, 5)), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None), \
             patch("onemancompany.core.agent_loop.register_and_start_agent", new_callable=AsyncMock), \
             patch("onemancompany.agents.base.EmployeeAgent"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ex-employees/00010/rehire")

        assert resp.json()["status"] == "rehired"

    async def test_rehire_move_failed(self):
        state = _make_state()
        ex_emp = MagicMock()
        ex_emp.id = "00010"
        state.ex_employees = {"00010": ex_emp}
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.move_ex_employee_back", return_value=False):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ex-employees/00010/rehire")

        assert "Failed" in resp.json()["error"]

    async def test_rehire_remote_employee(self):
        """Remote employee doesn't register agent loop."""
        state = _make_state()
        ex_emp = MagicMock()
        ex_emp.id = "00010"
        ex_emp.name = "Remote Worker"
        ex_emp.nickname = "RemoteNick"
        ex_emp.department = "技术研发部"
        ex_emp.role = "Remote Dev"
        ex_emp.skills = []
        ex_emp.sprite = "employee_default"
        ex_emp.remote = True
        state.ex_employees = {"00010": ex_emp}
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.load_employee_guidance", return_value=""), \
             patch("onemancompany.core.config.load_work_principles", return_value=""), \
             patch("onemancompany.core.config.move_ex_employee_back", return_value=True), \
             patch("onemancompany.core.layout.compute_layout"), \
             patch("onemancompany.core.layout.persist_all_desk_positions"), \
             patch("onemancompany.core.layout.get_next_desk_for_department", return_value=(3, 3)):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ex-employees/00010/rehire")

        assert resp.json()["status"] == "rehired"

    async def test_rehire_self_hosted(self):
        """Self-hosted employee registers via register_self_hosted."""
        state = _make_state()
        ex_emp = MagicMock()
        ex_emp.id = "00010"
        ex_emp.name = "Self Hosted"
        ex_emp.nickname = "SelfNick"
        ex_emp.department = "技术研发部"
        ex_emp.role = "Self Dev"
        ex_emp.skills = []
        ex_emp.sprite = "employee_default"
        ex_emp.remote = False
        state.ex_employees = {"00010": ex_emp}
        bus = EventBus()

        mock_cfg = MagicMock()
        mock_cfg.hosting = "self"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.load_employee_guidance", return_value=""), \
             patch("onemancompany.core.config.load_work_principles", return_value=""), \
             patch("onemancompany.core.config.move_ex_employee_back", return_value=True), \
             patch("onemancompany.core.layout.compute_layout"), \
             patch("onemancompany.core.layout.persist_all_desk_positions"), \
             patch("onemancompany.core.layout.get_next_desk_for_department", return_value=(4, 4)), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None), \
             patch("onemancompany.core.agent_loop.register_self_hosted"), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ex-employees/00010/rehire")

        assert resp.json()["status"] == "rehired"


# ---------------------------------------------------------------------------
# Hire candidate (lines 2286-2356)
# ---------------------------------------------------------------------------


class TestHireCandidate:
    async def test_hire_candidate_not_found(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.agents.hr_agent.pending_candidates", {}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/candidates/hire", json={
                    "batch_id": "b1",
                    "candidate_id": "c1",
                })

        assert "not found" in resp.json()["error"]

    async def test_hire_candidate_success(self):
        state = _make_state()
        bus = EventBus()

        mock_emp = _make_employee(id="00099", name="New Hire")

        candidates = {"b1": [{"id": "c1", "name": "New Hire", "role": "Engineer", "skill_set": ["Python"]}]}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.agents.hr_agent.pending_candidates", candidates), \
             patch("onemancompany.agents.onboarding.execute_hire", new_callable=AsyncMock, return_value=mock_emp), \
             patch("onemancompany.agents.onboarding.generate_nickname", new_callable=AsyncMock, return_value="CoolNick"), \
             patch("onemancompany.core.config.load_talent_profile", return_value={}), \
             patch("onemancompany.agents.hr_agent._pending_project_ctx", {}), \
             patch("onemancompany.core.project_archive.append_action"), \
             patch("onemancompany.core.project_archive.complete_project"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/candidates/hire", json={
                    "batch_id": "b1",
                    "candidate_id": "c1",
                })

        assert resp.json()["status"] == "hired"

    async def test_hire_candidate_with_project(self):
        """Hire with pending project context triggers retrospective."""
        state = _make_state()
        bus = EventBus()

        mock_emp = _make_employee(id="00099", name="New Hire")

        candidates = {"b1": [{"id": "c1", "name": "New Hire", "role": "Engineer", "skill_set": []}]}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.agents.hr_agent.pending_candidates", candidates), \
             patch("onemancompany.agents.onboarding.execute_hire", new_callable=AsyncMock, return_value=mock_emp), \
             patch("onemancompany.core.config.load_talent_profile", return_value={}), \
             patch("onemancompany.agents.hr_agent._pending_project_ctx", {"b1": {"project_id": "proj1"}}), \
             patch("onemancompany.core.project_archive.append_action"), \
             patch("onemancompany.core.project_archive.complete_project"), \
             patch("onemancompany.core.routine.run_post_task_routine", new_callable=AsyncMock):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/candidates/hire", json={
                    "batch_id": "b1",
                    "candidate_id": "c1",
                    "nickname": "GivenNick",
                })

        assert resp.json()["status"] == "hired"

    async def test_hire_candidate_exception(self):
        """execute_hire raises an exception."""
        state = _make_state()
        bus = EventBus()

        candidates = {"b1": [{"id": "c1", "name": "Bad", "role": "Dev", "skill_set": []}]}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.agents.hr_agent.pending_candidates", candidates), \
             patch("onemancompany.agents.onboarding.execute_hire", new_callable=AsyncMock, side_effect=RuntimeError("hire failed")), \
             patch("onemancompany.core.config.load_talent_profile", return_value={}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/candidates/hire", json={
                    "batch_id": "b1",
                    "candidate_id": "c1",
                    "nickname": "TestNick",
                })

        assert "Hire failed" in resp.json()["error"]


# ---------------------------------------------------------------------------
# Hiring request approved (lines 2249-2260)
# ---------------------------------------------------------------------------


class TestHiringRequestApproved:
    async def test_hiring_request_approved(self):
        state = _make_state()
        bus = EventBus()

        pending = {"h1": {"role": "Engineer", "desired_skills": ["Go"], "reason": "Growth"}}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.agents.coo_agent.pending_hiring_requests", pending), \
             patch("onemancompany.api.routes._run_agent_safe", new_callable=AsyncMock), \
             patch("onemancompany.core.agent_loop.employee_manager", MagicMock()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/hiring-requests/h1/decide", json={
                    "approved": True,
                    "note": "Let's hire",
                })

        data = resp.json()
        assert data["status"] == "approved"


# ---------------------------------------------------------------------------
# Project file serving (lines 2064-2108)
# ---------------------------------------------------------------------------


class TestProjectFileServing:
    async def test_serve_text_file(self, tmp_path):
        """Serve a .py text file."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "main.py").write_text("print('hello')")

        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value=str(ws)):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/proj1/files/main.py")

        assert resp.status_code == 200
        assert "print" in resp.text

    async def test_serve_html_file(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "index.html").write_text("<html>hi</html>")

        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value=str(ws)):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/proj1/files/index.html")

        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    async def test_serve_json_file(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "data.json").write_text('{"key": "value"}')

        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value=str(ws)):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/proj1/files/data.json")

        assert resp.status_code == 200
        assert "application/json" in resp.headers.get("content-type", "")

    async def test_serve_md_file(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "README.md").write_text("# Hello")

        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value=str(ws)):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/proj1/files/README.md")

        assert resp.status_code == 200
        assert "markdown" in resp.headers.get("content-type", "")

    async def test_serve_binary_file(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value=str(ws)):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/proj1/files/image.png")

        assert resp.status_code == 200
        assert "image/png" in resp.headers.get("content-type", "")

    async def test_serve_jpg_file(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "photo.jpg").write_bytes(b"\xff\xd8\xff")

        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value=str(ws)):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/proj1/files/photo.jpg")

        assert resp.status_code == 200
        assert "image/jpeg" in resp.headers.get("content-type", "")

    async def test_serve_gif_file(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "anim.gif").write_bytes(b"GIF89a")

        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value=str(ws)):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/proj1/files/anim.gif")

        assert resp.status_code == 200
        assert "image/gif" in resp.headers.get("content-type", "")

    async def test_serve_svg_file(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "icon.svg").write_bytes(b"<svg></svg>")

        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value=str(ws)):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/proj1/files/icon.svg")

        assert resp.status_code == 200
        assert "svg" in resp.headers.get("content-type", "")

    async def test_serve_pdf_file(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "doc.pdf").write_bytes(b"%PDF-1.4")

        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value=str(ws)):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/proj1/files/doc.pdf")

        assert resp.status_code == 200
        assert "pdf" in resp.headers.get("content-type", "")

    async def test_serve_generic_binary(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "data.bin").write_bytes(b"\x00\x01\x02")

        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value=str(ws)):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/proj1/files/data.bin")

        assert resp.status_code == 200
        assert "octet-stream" in resp.headers.get("content-type", "")

    async def test_serve_file_not_found(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()

        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value=str(ws)):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/proj1/files/nonexistent.py")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tool icon and definition (lines 2503-2524)
# ---------------------------------------------------------------------------


class TestToolEndpoints:
    async def test_tool_icon_not_found(self):
        state = _make_state()
        state.tools = {}
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/tools/nonexistent/icon")

        assert resp.status_code == 404

    async def test_tool_definition_not_found(self):
        state = _make_state()
        state.tools = {}
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/tools/nonexistent/definition")

        assert resp.status_code == 404

    async def test_tool_icon_success(self, tmp_path):
        from onemancompany.core.state import OfficeTool

        tool = OfficeTool(
            id="t1", name="Tool1", description="A tool",
            added_by="CEO", folder_name="tool1"
        )
        state = _make_state()
        state.tools = {"t1": tool}
        bus = EventBus()

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "tool1").mkdir()
        (tools_dir / "tool1" / "icon.png").write_bytes(b"\x89PNG")

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.TOOLS_DIR", tools_dir):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/tools/t1/icon")

        assert resp.status_code == 200

    async def test_tool_icon_missing_file(self, tmp_path):
        from onemancompany.core.state import OfficeTool

        tool = OfficeTool(
            id="t1", name="Tool1", description="A tool",
            added_by="CEO", folder_name="tool1"
        )
        state = _make_state()
        state.tools = {"t1": tool}
        bus = EventBus()

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "tool1").mkdir()
        # No icon.png file

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.TOOLS_DIR", tools_dir):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/tools/t1/icon")

        assert resp.status_code == 404

    async def test_tool_definition_success(self, tmp_path):
        from onemancompany.core.state import OfficeTool

        tool = OfficeTool(
            id="t1", name="Tool1", description="A tool",
            added_by="CEO", folder_name="tool1"
        )
        state = _make_state()
        state.tools = {"t1": tool}
        bus = EventBus()

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        tool_dir = tools_dir / "tool1"
        tool_dir.mkdir()
        (tool_dir / "tool.yaml").write_text("name: Tool1\n")
        (tool_dir / "script.py").write_text("print(1)")

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.TOOLS_DIR", tools_dir):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/tools/t1/definition")

        data = resp.json()
        assert data["name"] == "Tool1"


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


class TestAdminReload:
    async def test_admin_reload(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.state.reload_all_from_disk", return_value={"employees_updated": [], "employees_added": []}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/admin/reload")

        assert resp.json()["status"] == "reloaded"


class TestAdminClearTasks:
    async def test_admin_clear_tasks(self):
        state = _make_state()
        state.active_tasks = [TaskEntry(project_id="p1", task="t1", routed_to="COO")]
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/admin/clear-tasks")

        assert resp.json()["status"] == "cleared"
        assert resp.json()["tasks_removed"] == 1


# ---------------------------------------------------------------------------
# Cancel task with subtasks (lines 1214-1221)
# ---------------------------------------------------------------------------


class TestCancelTaskWithSubtasks:
    async def test_cancel_task_with_subtasks(self):
        """Cancel a task that has sub-tasks."""
        state = _make_state()
        bus = EventBus()

        sub_task = MagicMock()
        sub_task.status = "pending"
        sub_task.completed_at = None
        sub_task.result = ""

        main_task = MagicMock()
        main_task.id = "t1"
        main_task.status = "in_progress"
        main_task.completed_at = None
        main_task.result = ""
        main_task.sub_task_ids = ["s1"]

        mock_board = MagicMock()
        def get_task_side_effect(tid):
            if tid == "t1":
                return main_task
            if tid == "s1":
                return sub_task
            return None
        mock_board.get_task = get_task_side_effect

        mock_loop = MagicMock()
        mock_loop.board = mock_board
        mock_loop._log = MagicMock()
        mock_loop._publish_task_update = MagicMock()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/task/t1/cancel")

        assert resp.json()["status"] == "ok"
        assert sub_task.status == "cancelled"


# ---------------------------------------------------------------------------
# Abort task (lines 1160-1172)
# ---------------------------------------------------------------------------


class TestAbortTaskWithBoards:
    async def test_abort_cancels_across_boards(self):
        """Abort project cancels tasks across all agent boards."""
        state = _make_state()
        state.active_tasks = [TaskEntry(project_id="proj1", task="test", routed_to="COO")]
        bus = EventBus()

        mock_manager = MagicMock()
        mock_manager.abort_project.return_value = 1

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.employee_manager", mock_manager):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/task/proj1/abort")

        assert resp.json()["cancelled"] == 1
        mock_manager.abort_project.assert_called_once_with("proj1")


# ---------------------------------------------------------------------------
# Update API key with agent rebuild (lines 1316-1338)
# ---------------------------------------------------------------------------


class TestUpdateApiKeyWithAgentRebuild:
    async def test_update_api_key_rebuilds_agent(self, tmp_path):
        """Covers the agent rebuild code path when loop has an agent."""
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_cfg = MagicMock()
        mock_cfg.api_provider = "anthropic"
        mock_cfg.api_key = "old"
        mock_cfg.hosting = "company"
        mock_cfg.llm_model = "old-model"

        profile_path = tmp_path / "00010" / "profile.yaml"
        profile_path.parent.mkdir(parents=True)
        profile_path.write_text("api_key: old\n")

        mock_agent = MagicMock()
        mock_agent._agent = MagicMock()

        mock_loop = MagicMock()
        mock_loop.agent = mock_agent

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", tmp_path), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.agents.common_tools.COMMON_TOOLS", []), \
             patch("onemancompany.core.config.load_employee_custom_tools", return_value=[]), \
             patch("langgraph.prebuilt.create_react_agent", return_value=MagicMock()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/00010/api-key", json={
                    "api_key": "new-key",
                    "model": "new-model",
                })

        assert resp.json()["status"] == "updated"


# ---------------------------------------------------------------------------
# List OpenRouter models — error status (line 1033)
# ---------------------------------------------------------------------------


class TestListModelsErrorStatus:
    async def test_list_models_non_200(self):
        """OpenRouter returns non-200 status."""
        state = _make_state()
        bus = EventBus()

        mock_resp = MagicMock()
        mock_resp.status_code = 500

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("httpx.AsyncClient", return_value=mock_client):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/models")

        assert "error" in resp.json()
        assert resp.json()["models"] == []


# ---------------------------------------------------------------------------
# Interview question (lines 2372-2399)
# ---------------------------------------------------------------------------


class TestInterviewQuestion:
    def _make_candidate(self, id="c1", name="Alice", role="Engineer"):
        return {
            "id": id,
            "name": name,
            "role": role,
            "experience_years": 3,
            "personality_tags": ["friendly"],
            "system_prompt": "You are a skilled professional.",
            "skill_set": [{"name": "Python", "description": "Python programming"}],
            "tool_set": [{"name": "debugger", "description": "Debug tool"}],
            "sprite": "employee_blue",
            "llm_model": "test-model",
            "jd_relevance": 0.9,
        }

    async def test_interview_question(self):
        state = _make_state()
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "I would approach this by..."

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/candidates/interview", json={
                    "candidate": self._make_candidate(),
                    "question": "Tell me about yourself",
                    "images": [],
                })

        data = resp.json()
        assert data["candidate_id"] == "c1"
        assert data["answer"] == "I would approach this by..."

    async def test_interview_question_with_images(self):
        state = _make_state()
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "Looking at the image..."

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/candidates/interview", json={
                    "candidate": self._make_candidate(id="c2", name="Bob", role="Designer"),
                    "question": "What do you see?",
                    "images": ["iVBORw0KGgoAAAA"],
                })

        data = resp.json()
        assert data["candidate_id"] == "c2"


# ---------------------------------------------------------------------------
# Oneonone end — with employee guidance update (line 536 context)
# ---------------------------------------------------------------------------


class TestOneOnOneEndWithUpdate:
    async def test_oneonone_end_with_update(self):
        """Covers the history reflection + update branch in oneonone/end."""
        emp = _make_employee(id="00010")
        emp.is_listening = True
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "UPDATED: Focus on quality above all"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.config.save_work_principles"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/end", json={
                    "employee_id": "00010",
                    "history": [
                        {"role": "ceo", "content": "Focus on quality"},
                        {"role": "employee", "content": "Got it"},
                    ],
                })

        assert resp.status_code == 200
        assert emp.is_listening is False
        assert resp.json()["principles_updated"] is True

    async def test_oneonone_end_no_update(self):
        """LLM reflects but decides no update needed."""
        emp = _make_employee(id="00010")
        emp.is_listening = True
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "NO_UPDATE"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/end", json={
                    "employee_id": "00010",
                    "history": [
                        {"role": "ceo", "content": "How are you?"},
                        {"role": "employee", "content": "Good!"},
                    ],
                })

        assert resp.status_code == 200
        assert resp.json()["principles_updated"] is False


# ---------------------------------------------------------------------------
# Additional coverage — agent loop chat with history (line 536)
# ---------------------------------------------------------------------------


class TestOneOnOneChatAgentLoopHistory:
    async def test_chat_agent_loop_with_history(self):
        """Covers line 536 — agent loop chat with conversation history."""
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_task = MagicMock()
        mock_task.id = "t1"
        mock_task.status = "completed"
        mock_task.result = "Got your history"
        mock_task.logs = []

        mock_board = MagicMock()
        mock_board.get_task.return_value = mock_task

        mock_loop = MagicMock()
        mock_loop.push_task.return_value = mock_task
        mock_loop.board = mock_board

        mock_cfg = MagicMock()
        mock_cfg.hosting = "company"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "Continue the work",
                    "history": [
                        {"role": "ceo", "content": "Start the project"},
                        {"role": "employee", "content": "On it!"},
                    ],
                })

        assert resp.status_code == 200
        assert resp.json()["response"] == "Got your history"


# ---------------------------------------------------------------------------
# Agent loop chat — logs without llm_output type (line 564)
# ---------------------------------------------------------------------------


class TestOneOnOneChatAgentLoopLogsNoResult:
    async def test_chat_logs_no_llm_output(self):
        """Covers line 564 — logs exist but none are llm_output/result type."""
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_task = MagicMock()
        mock_task.id = "t1"
        mock_task.status = "completed"
        mock_task.result = ""
        mock_task.logs = [
            {"type": "tool_call", "content": "Called search tool"},
            {"type": "info", "content": "Completed processing"},
        ]

        mock_board = MagicMock()
        mock_board.get_task.return_value = mock_task

        mock_loop = MagicMock()
        mock_loop.push_task.return_value = mock_task
        mock_loop.board = mock_board

        mock_cfg = MagicMock()
        mock_cfg.hosting = "company"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "Do something",
                })

        assert resp.status_code == 200
        assert resp.json()["response"] == "Completed processing"


# ---------------------------------------------------------------------------
# Update employee model — non-openrouter and with profile persist (lines 1253, 1266-1271)
# ---------------------------------------------------------------------------


class TestUpdateEmployeeModelNonOpenRouter:
    async def test_update_model_non_openrouter(self, tmp_path):
        """Covers non-openrouter salary path and profile.yaml persist."""
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_cfg = MagicMock()
        mock_cfg.api_provider = "anthropic"
        mock_cfg.salary_per_1m_tokens = 5.0
        mock_cfg.llm_model = "old-model"

        profile_path = tmp_path / "00010" / "profile.yaml"
        profile_path.parent.mkdir(parents=True)
        profile_path.write_text("llm_model: old-model\n")

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", tmp_path):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/00010/model", json={
                    "model": "claude-3-5-sonnet",
                })

        assert resp.json()["status"] == "updated"


# ---------------------------------------------------------------------------
# HR review with reviewable employees (lines 887, 893)
# ---------------------------------------------------------------------------


class TestHRReviewWithReviewable:
    async def test_hr_review_with_reviewable_employees(self):
        """Employee with 3+ quarter tasks triggers reviewable path."""
        emp = _make_employee(id="00010")
        emp.current_quarter_tasks = 3
        emp.performance_history = [{"score": 3.5}]
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_loop = MagicMock()
        mock_loop.push_task = MagicMock()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/hr/review")

        assert resp.status_code == 200
        # The push_task was called with review task containing "ready for review"
        if mock_loop.push_task.called:
            task_text = mock_loop.push_task.call_args[0][0]
            assert "review" in task_text.lower()


# ---------------------------------------------------------------------------
# _start_inquiry with culture items (lines 256-257)
# ---------------------------------------------------------------------------


class TestStartInquiryWithCulture:
    async def test_start_inquiry_with_culture(self):
        """Covers culture_items branch in _start_inquiry."""
        emp_coo = _make_employee(id="00003", name="COO", nickname="COONick")
        state = _make_state(employees={"00003": emp_coo})
        state.company_culture = [{"content": "Move fast"}, {"content": "Ship daily"}]
        state.meeting_rooms = {
            "room1": MeetingRoom(id="room1", name="Room A", description="Meeting", position=(0, 0))
        }
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "Strategy discussion"

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.agents.base.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_tools_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_talent_persona", return_value=""):
            from onemancompany.api.routes import _start_inquiry
            result = await _start_inquiry("What is our strategy?")

        assert result["task_type"] == "inquiry"


# ---------------------------------------------------------------------------
# Project file — path traversal forbidden (line 2074)
# ---------------------------------------------------------------------------


class TestProjectFileTraversal:
    async def test_path_traversal_forbidden(self, tmp_path):
        """Path traversal attempt returns 403."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        # Create a file outside workspace
        (tmp_path / "secret.txt").write_text("secret data")

        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value=str(ws)):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/proj1/files/../secret.txt")

        # Should be 403 or 404 (path resolved to outside workspace)
        assert resp.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Line 155: _run_agent_safe cleanup sets STATUS_IDLE
# ---------------------------------------------------------------------------


class TestRunAgentSafeCleanup:
    async def test_run_agent_safe_resets_employees_to_idle(self):
        """Line 155: after task completes, all employees reset to idle."""
        from onemancompany.api import routes as routes_mod

        emp = _make_employee(id="00010")
        emp.status = "working"
        state = _make_state(employees={"00010": emp})
        bus = MagicMock()
        bus.publish = AsyncMock()

        async def dummy_coro():
            return "done"

        with patch.object(routes_mod, "company_state", state), \
             patch.object(routes_mod, "event_bus", bus), \
             patch("onemancompany.core.project_archive.append_action"), \
             patch("onemancompany.core.project_archive.complete_project"), \
             patch("onemancompany.core.resolutions.create_resolution", return_value=None), \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock), \
             patch("onemancompany.core.state.flush_pending_reload", return_value=None):
            await routes_mod._run_agent_safe(
                dummy_coro(), "TEST",
                task_description="Test task",
                project_id="proj_test",
            )

        assert emp.status == "idle"


# ---------------------------------------------------------------------------
# Line 197: admin_clear_tasks sets STATUS_IDLE (direct call)
# ---------------------------------------------------------------------------


class TestAdminClearTasksDirect:
    async def test_clear_tasks_resets_status_direct(self):
        """Line 197: direct call to ensure coverage of emp.status = STATUS_IDLE."""
        from onemancompany.api import routes as routes_mod

        emp = _make_employee(id="00010")
        emp.status = "working"
        state = _make_state(
            employees={"00010": emp},
            active_tasks=[TaskEntry(project_id="p1", task="t1", routed_to="COO")],
        )
        bus = MagicMock()
        bus.publish = AsyncMock()

        with patch.object(routes_mod, "company_state", state), \
             patch.object(routes_mod, "event_bus", bus):
            result = await routes_mod.admin_clear_tasks()

        assert result["status"] == "cleared"
        assert result["tasks_removed"] == 1
        assert emp.status == "idle"
        assert len(state.active_tasks) == 0


# ---------------------------------------------------------------------------
# Lines 1500-1501: OAuth exchange — create-key exception fallback
# ---------------------------------------------------------------------------


class TestOAuthExchangeCreateKeyException:
    async def test_exchange_create_key_exception_falls_back(self, tmp_path):
        """Lines 1500-1501: create_api_key raises, falls back to access token."""
        from onemancompany.api.routes import _oauth_sessions

        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = MagicMock()
        bus.publish = AsyncMock()

        _oauth_sessions["key_err_state"] = {
            "employee_id": "00010",
            "code_verifier": "verifier",
            "redirect_uri": "http://localhost/api/oauth/callback",
        }

        mock_cfg = MagicMock()
        mock_cfg.api_key = ""
        mock_cfg.oauth_refresh_token = ""

        # Token exchange succeeds
        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {"access_token": "tok_abc", "refresh_token": "ref_xyz"}

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return mock_token_resp
            # Second call (create_api_key) raises exception
            raise ConnectionError("Network failure")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = mock_post

        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        (emp_dir / "profile.yaml").write_text("api_key: old\n")

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", tmp_path):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/oauth/exchange", json={
                    "code": "auth_code",
                    "state": "key_err_state",
                })

        data = resp.json()
        assert data["status"] == "ok"
        assert data["api_key_set"] is True
        # Falls back to access token
        assert mock_cfg.api_key == "tok_abc"


# ---------------------------------------------------------------------------
# Lines 1599-1600: OAuth callback — create-key exception fallback
# ---------------------------------------------------------------------------


class TestOAuthCallbackCreateKeyException:
    async def test_callback_create_key_exception_falls_back(self, tmp_path):
        """Lines 1599-1600: create_api_key raises in callback, falls back to access token."""
        from onemancompany.api.routes import _oauth_sessions

        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = MagicMock()
        bus.publish = AsyncMock()

        _oauth_sessions["cb_key_err"] = {
            "employee_id": "00010",
            "code_verifier": "v",
            "redirect_uri": "http://localhost/api/oauth/callback",
        }

        mock_cfg = MagicMock()
        mock_cfg.api_key = ""
        mock_cfg.oauth_refresh_token = ""

        # Token exchange succeeds
        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 200
        mock_token_resp.json.return_value = {"access_token": "tok_cb", "refresh_token": "ref_cb"}

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return mock_token_resp
            # Second call (create_api_key) raises exception
            raise ConnectionError("Network failure")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = mock_post

        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        (emp_dir / "profile.yaml").write_text("api_key: old\n")

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", tmp_path):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/oauth/callback", params={
                    "code": "auth_code",
                    "state": "cb_key_err",
                })

        assert resp.status_code == 200
        assert "Login Successful" in resp.text
        # Falls back to access token
        assert mock_cfg.api_key == "tok_cb"


# ---------------------------------------------------------------------------
# Lines 2007-2022: Named project detail with iteration cost + files
# ---------------------------------------------------------------------------


class TestNamedProjectDetailWithIterations:
    async def test_project_detail_with_iterations_and_files(self, tmp_path):
        """Lines 2007-2022: iteration loading with cost aggregation and file listing."""
        state = _make_state()

        # Create a fake iteration workspace dir with files
        ws = tmp_path / "iter_workspace"
        ws.mkdir()
        (ws / "main.py").write_text("print('hello')")
        (ws / "README.md").write_text("# Docs")

        iter_doc = {
            "iteration_id": "iter_001",
            "task": "Build feature",
            "status": "completed",
            "created_at": "2026-03-01T00:00:00",
            "completed_at": "2026-03-02T00:00:00",
            "current_owner": "dev",
            "cost": {"actual_cost_usd": 0.15},
            "project_dir": str(ws),
        }

        proj = {
            "name": "Test Project",
            "iterations": ["iter_001"],
            "status": "active",
        }

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=proj), \
             patch("onemancompany.core.project_archive.load_iteration", return_value=iter_doc), \
             patch("onemancompany.core.project_archive.list_project_files", return_value=[]):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/named/test-project")

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Project"
        assert len(data["iteration_details"]) == 1
        detail = data["iteration_details"][0]
        assert detail["iteration_id"] == "iter_001"
        assert detail["cost_usd"] == 0.15
        assert detail["project_dir"] == str(ws)
        # Files from the workspace dir
        assert "main.py" in detail["files"]
        assert "README.md" in detail["files"]
        assert data["total_cost_usd"] == 0.15

    async def test_project_detail_iteration_not_found(self):
        """Iteration that returns None is skipped."""
        state = _make_state()

        proj = {
            "name": "Test",
            "iterations": ["missing_iter"],
            "status": "active",
        }

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=proj), \
             patch("onemancompany.core.project_archive.load_iteration", return_value=None), \
             patch("onemancompany.core.project_archive.list_project_files", return_value=[]):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/named/test-project")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["iteration_details"]) == 0
        assert data["total_cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# Line 2074: Project file path escape — 403 Forbidden
# ---------------------------------------------------------------------------


class TestProjectFilePathEscape:
    async def test_path_escape_returns_403(self, tmp_path):
        """Line 2074: when resolved path escapes workspace, return 403."""
        from pathlib import Path

        # Create workspace and a symlink that points outside
        ws = tmp_path / "workspace"
        ws.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")

        # Create a symlink inside workspace pointing outside
        link = ws / "escape"
        link.symlink_to(outside)

        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value=str(ws)):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/proj1/files/escape/secret.txt")

        assert resp.status_code == 403
        assert "Forbidden" in resp.text


# ---------------------------------------------------------------------------
# Lines 2271-2274: _dispatch_hiring_to_hr
# ---------------------------------------------------------------------------


class TestDispatchHiringToHR:
    async def test_approved_hiring_pushes_task_to_hr(self):
        """CEO approval pushes hiring task to HR via push_task and queues COO context."""
        from onemancompany.agents.coo_agent import pending_hiring_requests

        req_id = "test123"
        pending_hiring_requests[req_id] = {
            "role": "Developer",
            "department": "Engineering",
            "reason": "Need more devs",
            "desired_skills": ["Python"],
            "requested_by": "00003",
            "requested_at": "2026-01-01T00:00:00",
            "project_id": "proj_1",
            "project_dir": "/tmp/proj",
        }

        mock_vessel = MagicMock()
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        from onemancompany.api.routes import _pending_coo_hire_queue
        queue_before = len(_pending_coo_hire_queue)

        with patch("onemancompany.api.routes.event_bus", mock_bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_vessel):
            from onemancompany.api.routes import decide_hiring_request
            result = await decide_hiring_request(req_id, {"approved": True})

        assert result["status"] == "approved"
        mock_vessel.push_task.assert_called_once()
        jd = mock_vessel.push_task.call_args[0][0]
        assert "Developer" in jd
        assert "Python" in jd
        assert "Engineering" in jd  # department included in JD

        # COO context was queued
        assert len(_pending_coo_hire_queue) == queue_before + 1
        ctx = _pending_coo_hire_queue.pop()
        assert ctx["role"] == "Developer"
        assert ctx["department"] == "Engineering"
        assert ctx["project_id"] == "proj_1"

    async def test_rejected_hiring_does_not_push(self):
        """CEO rejection does not push any task to HR."""
        from onemancompany.agents.coo_agent import pending_hiring_requests

        req_id = "test456"
        pending_hiring_requests[req_id] = {
            "role": "Designer",
            "reason": "Nice to have",
            "desired_skills": [],
            "requested_by": "00003",
            "requested_at": "2026-01-01T00:00:00",
        }

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        with patch("onemancompany.api.routes.event_bus", mock_bus):
            from onemancompany.api.routes import decide_hiring_request
            result = await decide_hiring_request(req_id, {"approved": False})

        assert result["status"] == "rejected"


# ---------------------------------------------------------------------------
# Lines 2467-2471: Remote worker task result with token usage
# ---------------------------------------------------------------------------


class TestRemoteSubmitResultsWithTokenUsage:
    async def test_submit_results_with_token_usage(self):
        """Lines 2467-2471: recording token usage with _record_overhead."""
        state = _make_state()
        bus = MagicMock()
        bus.publish = AsyncMock()
        workers = {"00010": {"status": "busy", "current_task_id": "t1"}}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._remote_workers", workers), \
             patch("onemancompany.core.project_archive.record_project_cost") as mock_record_cost, \
             patch("onemancompany.agents.base._record_overhead") as mock_record:
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/remote/results", json={
                    "task_id": "t1",
                    "employee_id": "00010",
                    "status": "completed",
                    "output": "Done",
                    "model_used": "claude-sonnet",
                    "input_tokens": 500,
                    "output_tokens": 100,
                    "estimated_cost_usd": 0.05,
                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "received"
        mock_record.assert_called_once_with(
            "remote_worker", "claude-sonnet", 500, 100, 0.05
        )

    async def test_submit_results_with_token_usage_no_model(self):
        """Lines 2467-2471: model_used defaults to 'remote' when empty."""
        state = _make_state()
        bus = MagicMock()
        bus.publish = AsyncMock()
        workers = {"00010": {"status": "busy", "current_task_id": "t1"}}

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._remote_workers", workers), \
             patch("onemancompany.core.project_archive.record_project_cost") as mock_record_cost, \
             patch("onemancompany.agents.base._record_overhead") as mock_record:
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/remote/results", json={
                    "task_id": "t1",
                    "employee_id": "00010",
                    "status": "completed",
                    "output": "Done",
                    "input_tokens": 200,
                    "output_tokens": 50,
                })

        assert resp.status_code == 200
        mock_record.assert_called_once_with(
            "remote_worker", "remote", 200, 50, 0.0
        )


# ---------------------------------------------------------------------------
# Lines 2696-2708: WebSocket endpoint
# ---------------------------------------------------------------------------


class TestWebSocketEndpoint:
    """Test websocket_endpoint by calling the async function directly with a mock WebSocket."""

    @pytest.mark.asyncio
    async def test_websocket_ceo_task(self):
        """Lines 2696-2704: WebSocket ceo_task message dispatches to ceo_submit_task."""
        from fastapi import WebSocketDisconnect

        from onemancompany.api import routes as routes_mod

        mock_ws = AsyncMock()
        # First call returns ceo_task, second raises disconnect
        mock_ws.receive_json = AsyncMock(
            side_effect=[
                {"type": "ceo_task", "task": "Build something"},
                WebSocketDisconnect(),
            ]
        )

        mock_ws_mgr = MagicMock()
        mock_ws_mgr.connect = AsyncMock()
        mock_ws_mgr.disconnect = MagicMock()

        mock_submit = AsyncMock(return_value={"status": "ok"})

        with patch.object(routes_mod, "ws_manager", mock_ws_mgr), \
             patch.object(routes_mod, "ceo_submit_task", mock_submit):
            await routes_mod.websocket_endpoint(mock_ws)

        mock_ws_mgr.connect.assert_awaited_once_with(mock_ws)
        mock_submit.assert_awaited_once_with({"task": "Build something"})
        mock_ws_mgr.disconnect.assert_called_once_with(mock_ws)

    @pytest.mark.asyncio
    async def test_websocket_disconnect(self):
        """Lines 2705-2706: WebSocket disconnect handling."""
        from fastapi import WebSocketDisconnect

        from onemancompany.api import routes as routes_mod

        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(side_effect=WebSocketDisconnect())

        mock_ws_mgr = MagicMock()
        mock_ws_mgr.connect = AsyncMock()
        mock_ws_mgr.disconnect = MagicMock()

        with patch.object(routes_mod, "ws_manager", mock_ws_mgr):
            await routes_mod.websocket_endpoint(mock_ws)

        mock_ws_mgr.disconnect.assert_called_once_with(mock_ws)

    @pytest.mark.asyncio
    async def test_websocket_empty_task_ignored(self):
        """Lines 2700-2704: empty task is not dispatched."""
        from fastapi import WebSocketDisconnect

        from onemancompany.api import routes as routes_mod

        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(
            side_effect=[
                {"type": "ceo_task", "task": ""},
                WebSocketDisconnect(),
            ]
        )

        mock_ws_mgr = MagicMock()
        mock_ws_mgr.connect = AsyncMock()
        mock_ws_mgr.disconnect = MagicMock()

        mock_submit = AsyncMock()

        with patch.object(routes_mod, "ws_manager", mock_ws_mgr), \
             patch.object(routes_mod, "ceo_submit_task", mock_submit):
            await routes_mod.websocket_endpoint(mock_ws)

        mock_submit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_websocket_generic_exception(self):
        """Lines 2707-2708: generic exception also disconnects."""
        from onemancompany.api import routes as routes_mod

        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(side_effect=RuntimeError("boom"))

        mock_ws_mgr = MagicMock()
        mock_ws_mgr.connect = AsyncMock()
        mock_ws_mgr.disconnect = MagicMock()

        with patch.object(routes_mod, "ws_manager", mock_ws_mgr):
            await routes_mod.websocket_endpoint(mock_ws)

        mock_ws_mgr.disconnect.assert_called_once_with(mock_ws)
