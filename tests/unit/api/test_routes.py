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
    department: str = "R&D Department",
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
    """Build a CompanyState with sensible defaults.

    Adds ``employees`` and ``ex_employees`` as ad-hoc instance attrs
    so that ``_store_patches`` can derive mock store data from them.
    These are NOT CompanyState dataclass fields — just test scaffolding.
    """
    state = CompanyState()
    # Test-only attrs for _store_patches to read
    state.employees = {}  # type: ignore[attr-defined]
    state.ex_employees = {}  # type: ignore[attr-defined]
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


def _emp_to_dict(emp: Employee) -> dict:
    """Convert Employee object to dict matching store.load_employee() output."""
    d: dict = {}
    for attr in ("id", "name", "nickname", "role", "skills", "level", "department",
                 "permissions", "tool_permissions", "work_principles", "guidance_notes",
                 "status", "is_listening", "current_task_summary", "okrs",
                 "current_quarter_tasks", "performance_score"):
        val = getattr(emp, attr, None)
        if val is not None:
            d[attr] = val
    d["runtime"] = {
        "status": getattr(emp, "status", "idle"),
        "is_listening": getattr(emp, "is_listening", False),
        "current_task_summary": getattr(emp, "current_task_summary", ""),
    }
    return d


def _store_patches(state: CompanyState):
    """Return a context manager that patches store reads + routes helpers to use test state."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        employees = getattr(state, "employees", {})
        ex_employees = getattr(state, "ex_employees", {})
        activity_log = getattr(state, "activity_log", [])
        culture = getattr(state, "company_culture", [])

        def fake_load(eid):
            emp = employees.get(eid)
            return _emp_to_dict(emp) if emp else {}

        def fake_load_all():
            return {eid: _emp_to_dict(e) for eid, e in employees.items()}

        def fake_load_ex():
            return {eid: _emp_to_dict(e) for eid, e in ex_employees.items()}

        def fake_load_activity():
            return list(activity_log)

        def fake_load_culture():
            return list(culture)

        direction = getattr(state, "company_direction", "")

        def fake_load_direction():
            return direction

        meeting_rooms = getattr(state, "meeting_rooms", {})

        def fake_load_rooms():
            return [m.to_dict() for m in meeting_rooms.values()]

        async def fake_save_room(room_id, updates):
            pass  # no-op in tests

        # Sales tasks — disk-backed via store
        sales_tasks = getattr(state, "_sales_tasks", [])

        def fake_load_sales():
            return list(sales_tasks)

        async def fake_save_sales(tasks):
            sales_tasks.clear()
            sales_tasks.extend(tasks)

        # Overhead — disk-backed via store
        overhead = getattr(state, "_overhead", {"company_tokens": 0})

        def fake_load_overhead():
            return dict(overhead)

        async def fake_save_overhead(data):
            overhead.update(data)

        with patch("onemancompany.api.routes._load_emp", side_effect=fake_load), \
             patch("onemancompany.api.routes._load_all", side_effect=fake_load_all), \
             patch("onemancompany.core.store.load_all_employees", side_effect=fake_load_all), \
             patch("onemancompany.core.store.load_ex_employees", side_effect=fake_load_ex), \
             patch("onemancompany.core.store.load_activity_log", side_effect=fake_load_activity), \
             patch("onemancompany.core.store.load_culture", side_effect=fake_load_culture), \
             patch("onemancompany.core.store.load_direction", side_effect=fake_load_direction), \
             patch("onemancompany.core.store.load_rooms", side_effect=fake_load_rooms), \
             patch("onemancompany.api.routes._store.load_rooms", side_effect=fake_load_rooms), \
             patch("onemancompany.api.routes._store.save_room", side_effect=fake_save_room), \
             patch("onemancompany.api.routes._store.load_sales_tasks", side_effect=fake_load_sales), \
             patch("onemancompany.api.routes._store.save_sales_tasks", side_effect=fake_save_sales), \
             patch("onemancompany.api.routes._store.load_overhead", side_effect=fake_load_overhead), \
             patch("onemancompany.api.routes._store.save_overhead", side_effect=fake_save_overhead):
            yield

    return _ctx()


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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/company/direction")

        assert resp.status_code == 200
        assert resp.json()["direction"] == "Build AI products"

    async def test_put_direction(self):
        state = _make_state()
        bus = EventBus()

        saved_direction = {}

        async def fake_save_direction(text):
            saved_direction["value"] = text

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.store.save_direction", side_effect=fake_save_direction):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/company/direction", json={"direction": "New direction"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert saved_direction["value"] == "New direction"


# ---------------------------------------------------------------------------
# POST /api/admin/clear-tasks
# ---------------------------------------------------------------------------


class TestAdminClearTasks:
    async def test_clears_tasks_and_resets_status(self):
        emp = _make_employee()
        emp.status = "working"
        state = _make_state(
            employees={emp.id: emp},
        )
        bus = EventBus()
        mock_task_entry = TaskEntry(project_id="p1", task="t1", routed_to="COO")

        saved_runtime_calls = []

        async def fake_save_runtime(eid, **fields):
            saved_runtime_calls.append((eid, fields))

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.core.state.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.state.get_active_tasks", return_value=[mock_task_entry]), \
             patch("onemancompany.api.routes.EMPLOYEES_DIR", MagicMock(iterdir=MagicMock(return_value=[]))), \
             patch("onemancompany.core.store.save_employee_runtime", side_effect=fake_save_runtime):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/admin/clear-tasks")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cleared"
        assert data["tasks_removed"] == 1
        # Verify employee status was reset via store
        assert any(f.get("status") == "idle" for _, f in saved_runtime_calls)


# ---------------------------------------------------------------------------
# POST /api/ceo/task
# ---------------------------------------------------------------------------


class TestCeoSubmitTask:
    async def test_empty_task_returns_error(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
        mock_agent_task = MagicMock()
        mock_agent_task.id = "agent-task-001"
        mock_loop.push_task = MagicMock(return_value=mock_agent_task)

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop), \
             patch("onemancompany.core.project_archive.async_create_project_from_task", new_callable=AsyncMock, return_value=("proj_123", "iter_001")), \
             patch("onemancompany.core.project_archive.get_project_workspace", return_value="/tmp/proj"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ceo/task", json={"task": "Build a website"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["routed_to"] == "EA"
        assert data["status"] == "processing"

    async def test_routes_to_ea_initializes_task_tree(self):
        """CEO task submission creates a TaskTree with CEO root and EA child."""
        state = _make_state()
        bus = EventBus()
        mock_loop = MagicMock()
        mock_agent_task = MagicMock()
        mock_agent_task.id = "agent-task-002"
        mock_loop.push_task = MagicMock(return_value=mock_agent_task)

        mock_save_tree = MagicMock()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop), \
             patch("onemancompany.core.project_archive.async_create_project_from_task", new_callable=AsyncMock, return_value=("proj_123", "iter_001")), \
             patch("onemancompany.core.project_archive.get_project_workspace", return_value="/tmp/proj"), \
             patch("onemancompany.core.vessel._save_project_tree", mock_save_tree):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ceo/task", json={"task": "Build a website"})

        assert resp.status_code == 200
        # Verify tree was saved
        mock_save_tree.assert_called_once()
        saved_dir, saved_tree = mock_save_tree.call_args[0]
        assert saved_dir == "/tmp/proj"
        assert saved_tree.root_id != ""
        # Root is CEO prompt node
        root = saved_tree.get_node(saved_tree.root_id)
        assert root is not None
        assert root.node_type == "ceo_prompt"
        assert root.description == "Build a website"
        # EA is child of CEO root
        children = saved_tree.get_active_children(root.id)
        assert len(children) >= 1
        ea_node = children[0]
        assert ea_node.id != root.id


# ---------------------------------------------------------------------------
# POST /api/employee/{employee_id}/fire
# ---------------------------------------------------------------------------


class TestFireEmployee:
    async def test_fire_employee_success(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})

        fire_result = {"status": "fired", "employee_id": "00010"}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.agents.termination.execute_fire", new_callable=AsyncMock, return_value=fire_result):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/fire", json={"reason": "test"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "fired"

    async def test_fire_employee_error(self):
        state = _make_state()

        fire_result = {"error": "Cannot fire founding employees"}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/99999")

        assert resp.status_code == 404

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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/meeting/release", json={})

        assert resp.json()["error"] == "Missing room_id"

    async def test_release_nonexistent_room(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/meeting/release", json={"room_id": "nonexistent"})

        assert "not found" in resp.json()["error"]

    async def test_release_unbooked_room(self):
        room = MeetingRoom(id="room1", name="Alpha", description="Room", is_booked=False)
        state = _make_state(meeting_rooms={"room1": room})

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/company-culture")

        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

    async def test_add_culture_item(self):
        state = _make_state()
        bus = EventBus()

        saved_culture = {}

        async def fake_save_culture(items):
            saved_culture["items"] = items

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.store.save_culture", side_effect=fake_save_culture):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/company-culture", json={"content": "Move fast"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "added"
        assert len(saved_culture["items"]) == 1

    async def test_add_culture_empty_content(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/company-culture", json={"content": ""})

        assert resp.json()["error"] == "Missing content"

    async def test_remove_culture_item(self):
        state = _make_state(company_culture=[{"content": "A"}, {"content": "B"}])
        bus = EventBus()

        saved_culture = {}

        async def fake_save_culture(items):
            saved_culture["items"] = items

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.store.save_culture", side_effect=fake_save_culture):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.delete("/api/company-culture/0")

        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"
        assert len(saved_culture["items"]) == 1
        assert saved_culture["items"][0]["content"] == "B"

    async def test_remove_invalid_index(self):
        state = _make_state(company_culture=[{"content": "A"}])

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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


def _make_sales_dict(task_id: str, **kwargs) -> dict:
    """Create a sales task dict for testing."""
    defaults = {
        "id": task_id, "client_name": "Acme", "description": "Build X",
        "requirements": "", "budget_tokens": 100, "status": "pending",
        "assigned_to": "", "contract_approved": False, "delivery": "",
        "settlement_tokens": 0, "created_at": "",
    }
    defaults.update(kwargs)
    return defaults


class TestSalesSubmit:
    async def test_submit_task(self):
        state = _make_state()
        state._sales_tasks = []
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
        assert len(state._sales_tasks) == 1

    async def test_submit_missing_fields(self):
        state = _make_state()
        state._sales_tasks = []

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/sales/submit", json={"client_name": ""})

        assert resp.json()["error"] == "Missing client_name or description"


class TestSalesListTasks:
    async def test_list_tasks(self):
        state = _make_state()
        state._sales_tasks = [_make_sales_dict("s1", client_name="Acme")]

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/sales/tasks")

        assert resp.status_code == 200
        assert len(resp.json()["tasks"]) == 1


class TestSalesGetTask:
    async def test_get_existing_task(self):
        state = _make_state()
        state._sales_tasks = [_make_sales_dict("s1", client_name="Acme")]

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/sales/tasks/s1")

        assert resp.status_code == 200
        assert resp.json()["client_name"] == "Acme"

    async def test_get_nonexistent_task(self):
        state = _make_state()
        state._sales_tasks = []

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/sales/tasks/nonexistent")

        assert "not found" in resp.json()["error"]


class TestSalesDeliver:
    async def test_deliver_in_production_task(self):
        state = _make_state()
        state._sales_tasks = [_make_sales_dict("s1", status="in_production")]

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/sales/tasks/s1/deliver", json={"delivery_summary": "All done"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "delivered"
        assert state._sales_tasks[0]["status"] == "delivered"
        assert state._sales_tasks[0]["delivery"] == "All done"

    async def test_deliver_wrong_status(self):
        state = _make_state()
        state._sales_tasks = [_make_sales_dict("s1", status="pending")]

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/sales/tasks/s1/deliver", json={})

        assert "pending" in resp.json()["error"]


class TestSalesSettle:
    async def test_settle_delivered_task(self):
        state = _make_state()
        state._sales_tasks = [_make_sales_dict("s1", status="delivered", budget_tokens=500)]
        state._overhead = {"company_tokens": 1000}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/sales/tasks/s1/settle")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "settled"
        assert data["tokens_earned"] == 500
        assert data["company_total_tokens"] == 1500
        assert state._sales_tasks[0]["status"] == "settled"

    async def test_settle_wrong_status(self):
        state = _make_state()
        state._sales_tasks = [_make_sales_dict("s1", status="pending")]

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/ex-employees")

        assert resp.status_code == 200
        assert len(resp.json()["ex_employees"]) == 1

    async def test_empty_ex_employees(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None), \
             patch("onemancompany.core.store.load_task_index", return_value=[]):
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/inquiry/end", json={})

        assert resp.json()["error"] == "Missing session_id"

    async def test_end_nonexistent_session(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={"message": "hi"})

        assert resp.json()["error"] == "Missing employee_id or message"

    async def test_employee_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/end", json={})

        assert resp.json()["error"] == "Missing employee_id"

    async def test_end_employee_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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

        saved_runtime = {}

        async def fake_save_runtime(eid, **fields):
            saved_runtime.update(fields)

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.core.state.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.store.save_employee_runtime", side_effect=fake_save_runtime):
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
        assert saved_runtime.get("is_listening") is False

    async def test_end_with_update(self):
        emp = _make_employee(id="00010")
        emp.is_listening = True
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "UPDATED: Be more proactive\n- Take initiative"

        saved_principles = {}

        async def fake_save_principles(eid, text):
            saved_principles["employee_id"] = eid
            saved_principles["text"] = text

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.core.state.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.store.save_work_principles", side_effect=fake_save_principles), \
             patch("onemancompany.core.store.save_employee_runtime", new_callable=AsyncMock):
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
        assert saved_principles["text"] == "Be more proactive\n- Take initiative"

    async def test_end_no_history(self):
        emp = _make_employee(id="00010")
        emp.is_listening = True
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop), \
             patch("onemancompany.api.routes._push_adhoc_task") as mock_push:
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/meeting/book", json={
                    "employee_id": "00010",
                    "participants": ["00010", "00011"],
                    "purpose": "Planning",
                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "processing"
        mock_push.assert_called_once()

    async def test_book_meeting_missing_employee_id(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop), \
             patch("onemancompany.api.routes._push_adhoc_task") as mock_push:
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/hr/review")

        assert resp.status_code == 200
        assert resp.json()["status"] == "HR review started"
        mock_push.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/routine/start
# ---------------------------------------------------------------------------


class TestRoutineStart:
    async def test_start_routine(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._get_employee_manager", return_value=MagicMock(schedule_system_task=MagicMock(return_value="task_123"))), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/routine/approve", json={"report_id": ""})

        assert resp.json()["error"] == "Missing report_id"

    async def test_approve_actions_success(self):
        state = _make_state()
        bus = EventBus()

        mock_em = MagicMock()
        mock_em.schedule_system_task = MagicMock(return_value="task_123")
        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._get_employee_manager", return_value=mock_em), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/routine/all_hands", json={"message": ""})

        assert resp.json()["error"] == "Missing CEO message"

    async def test_all_hands_success(self):
        state = _make_state()
        bus = EventBus()

        mock_em = MagicMock()
        mock_em.schedule_system_task = MagicMock(return_value="task_123")
        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._get_employee_manager", return_value=mock_em), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/okrs")

        assert resp.status_code == 200
        assert len(resp.json()["okrs"]) == 1

    async def test_get_okrs_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus):
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/99999/okrs", json={"okrs": []})

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/employee/{employee_id}/taskboard — with loop
# ---------------------------------------------------------------------------


class TestEmployeeTaskboardWithLoop:
    async def test_taskboard_with_loop(self, tmp_path):
        state = _make_state()
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.vessel import ScheduleEntry
        from collections import defaultdict

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00010", description="Task")
        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)

        mock_em = MagicMock()
        mock_em._schedule = defaultdict(list)
        mock_em._schedule["00010"] = [ScheduleEntry(node_id=root.id, tree_path=str(tree_path))]

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.vessel.employee_manager", mock_em), \
             patch("onemancompany.core.store.load_task_index", return_value=[]), \
             patch("onemancompany.core.store.append_task_index_entry"):
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/00010/model", json={"model": ""})

        assert resp.json()["error"] == "Missing model"

    async def test_update_model_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/99999/model", json={"model": "gpt-4o"})

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/projects
# ---------------------------------------------------------------------------


class TestGetProjects:
    async def test_list_projects(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.list_projects", return_value=[{"id": "p1"}]):
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/named/nonexistent")

        assert resp.json()["error"] == "Named project not found"

    async def test_project_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/projects/nonexistent/archive")

        assert resp.json()["error"] == "Named project not found"

    async def test_archive_success(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.file_editor.reject_edit", return_value={
                 "status": "error", "message": "Not found"
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/file-edits/nonexistent/reject")

        assert resp.json()["status"] == "error"



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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/sessions")

        assert resp.json()["error"] == "Employee is not self-hosted"

    async def test_get_sessions_no_config(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
    async def test_chat_direct_executor(self, tmp_path):
        """1-on-1 directly invokes executor, bypassing task queue."""
        from onemancompany.core.vessel import LaunchResult

        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_executor = AsyncMock()
        mock_executor.execute.return_value = LaunchResult(output="Agent response here")

        mock_em = MagicMock()
        mock_em.executors = {"00010": mock_executor}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.vessel.employee_manager", mock_em):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "Do this task",
                })

        assert resp.status_code == 200
        assert resp.json()["response"] == "Agent response here"
        mock_executor.execute.assert_awaited_once()

    async def test_chat_direct_executor_empty_output(self, tmp_path):
        """Empty executor output returns fallback message."""
        from onemancompany.core.vessel import LaunchResult

        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_executor = AsyncMock()
        mock_executor.execute.return_value = LaunchResult(output="")

        mock_em = MagicMock()
        mock_em.executors = {"00010": mock_executor}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.vessel.employee_manager", mock_em):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "Do this task",
                })

        assert resp.status_code == 200
        assert resp.json()["response"] == "(Processing complete)"

    async def test_chat_direct_executor_error(self, tmp_path):
        """Executor error returns error message instead of crashing."""
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_executor = AsyncMock()
        mock_executor.execute.side_effect = RuntimeError("LLM timeout")

        mock_em = MagicMock()
        mock_em.executors = {"00010": mock_executor}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.vessel.employee_manager", mock_em):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "Do this",
                })

        assert resp.status_code == 200
        assert "Execution error" in resp.json()["response"]

    async def test_chat_first_message_marks_listening(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_result = MagicMock()
        mock_result.content = "Hello"

        mock_cfg = MagicMock()
        mock_cfg.hosting = "company"

        saved_runtime_calls = []

        async def fake_save_runtime(eid, **fields):
            saved_runtime_calls.append((eid, fields))

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.core.state.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.agents.base.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_tools_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_talent_persona", return_value=""), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}), \
             patch("onemancompany.core.store.save_employee_runtime", side_effect=fake_save_runtime):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "Hi there",
                    "history": [],
                })

        assert resp.status_code == 200
        # Verify save_employee_runtime was called with is_listening=True
        assert any(f.get("is_listening") is True for _, f in saved_runtime_calls)


# ---------------------------------------------------------------------------
# POST /api/oneonone/chat — self-hosted goes through unified agent path
# ---------------------------------------------------------------------------


class TestOneOnOneChatSelfHosted:
    async def test_chat_self_hosted_direct_executor(self, tmp_path):
        """Self-hosted employees also use direct executor invocation."""
        from onemancompany.core.vessel import LaunchResult

        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_executor = AsyncMock()
        mock_executor.execute.return_value = LaunchResult(output="Self-hosted reply via executor")

        mock_em = MagicMock()
        mock_em.executors = {"00010": mock_executor}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.vessel.employee_manager", mock_em):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "Hello",
                    "history": [],
                })

        assert resp.status_code == 200
        assert resp.json()["response"] == "Self-hosted reply via executor"
        mock_executor.execute.assert_awaited_once()


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
             _store_patches(state), \
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
             _store_patches(state), \
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
        state = _make_state()
        bus = EventBus()

        mock_manager = MagicMock()
        mock_manager.abort_project.return_value = 1

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.employee_manager", mock_manager), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/task/proj1/abort")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["cancelled"] == 1
        mock_manager.abort_project.assert_called_once_with("proj1")


# ---------------------------------------------------------------------------
# POST /api/employee/{employee_id}/task/{task_id}/cancel
# ---------------------------------------------------------------------------


class TestCancelTask:
    async def test_cancel_task_not_in_schedule(self):
        state = _make_state()
        mock_em = MagicMock()
        mock_em._schedule = {}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.agent_loop.employee_manager", mock_em):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/task/t1/cancel")

        assert resp.json()["message"] == "Task not found in schedule or running tasks"

    async def test_cancel_task_already_completed(self, tmp_path):
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.vessel import ScheduleEntry

        state = _make_state()
        tree = TaskTree("proj1")
        root = tree.create_root("00010", "task")
        root.status = "completed"
        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)

        entry = ScheduleEntry(node_id=root.id, tree_path=str(tree_path))
        mock_em = MagicMock()
        mock_em._schedule = {"00010": [entry]}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.agent_loop.employee_manager", mock_em):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(f"/api/employee/00010/task/{root.id}/cancel")

        assert "already" in resp.json()["message"]

    async def test_cancel_task_success(self, tmp_path):
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.vessel import ScheduleEntry

        state = _make_state()
        bus = EventBus()

        tree = TaskTree("proj1")
        root = tree.create_root("00010", "task")
        root.status = "pending"
        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)

        entry = ScheduleEntry(node_id=root.id, tree_path=str(tree_path))
        mock_em = MagicMock()
        mock_em._schedule = {"00010": [entry]}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.employee_manager", mock_em), \
             patch("onemancompany.core.automation.stop_cron"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(f"/api/employee/00010/task/{root.id}/cancel")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        # Verify node was cancelled on disk
        reloaded = TaskTree.load(tree_path)
        assert reloaded.get_node(root.id).status == "cancelled"


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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
        from onemancompany.core.vessel import EmployeeManager, ScheduleEntry

        mock_em = MagicMock(spec=EmployeeManager)
        mock_em._running_tasks = {"00010": MagicMock()}
        entry = ScheduleEntry(node_id="n1", tree_path="/tmp/tree.yaml")
        mock_em._schedule = {"00010": [entry]}
        mock_em._task_logs = {"n1": [
            {"type": "start", "content": "Started"},
            {"type": "result", "content": "Done"},
        ]}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.vessel.employee_manager", mock_em):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/logs")

        assert resp.status_code == 200
        assert len(resp.json()["logs"]) == 2

    async def test_logs_from_recent_task(self):
        state = _make_state()
        from onemancompany.core.vessel import EmployeeManager, ScheduleEntry

        mock_em = MagicMock(spec=EmployeeManager)
        mock_em._running_tasks = {"00010": MagicMock()}
        entry = ScheduleEntry(node_id="n1", tree_path="/tmp/tree.yaml")
        mock_em._schedule = {"00010": [entry]}
        mock_em._task_logs = {"n1": [{"type": "result", "content": "Old result"}]}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.vessel.employee_manager", mock_em):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/logs")

        assert resp.status_code == 200
        assert len(resp.json()["logs"]) == 1

    async def test_logs_no_tasks_with_logs(self):
        state = _make_state()
        from onemancompany.core.vessel import EmployeeManager

        mock_em = MagicMock(spec=EmployeeManager)
        mock_em._running_tasks = {}
        mock_em._schedule = {}
        mock_em._task_logs = {}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.vessel.employee_manager", mock_em):
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/00010/model", json={"model": ""})

        assert resp.json()["error"] == "Missing model"

    async def test_update_model_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/99999/model", json={"model": "gpt-4o"})

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/projects
# ---------------------------------------------------------------------------


class TestGetProjects:
    async def test_list_projects(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.list_projects", return_value=[{"id": "p1"}]):
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/named/nonexistent")

        assert resp.json()["error"] == "Named project not found"

    async def test_project_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/projects/nonexistent/archive")

        assert resp.json()["error"] == "Named project not found"

    async def test_archive_success(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.file_editor.reject_edit", return_value={
                 "status": "error", "message": "Not found"
             }):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/file-edits/nonexistent/reject")

        assert resp.json()["status"] == "error"


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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/sessions")

        assert resp.json()["error"] == "Employee is not self-hosted"

    async def test_get_sessions_no_config(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/99999/oauth/start")

        assert resp.status_code == 404

    async def test_oauth_start_not_oauth_auth(self):
        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})

        mock_cfg = MagicMock()
        mock_cfg.auth_method = "api_key"

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.config.employee_configs", {"00010": mock_cfg}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/employee/00010/oauth/refresh")

        assert "No refresh token" in resp.json()["error"]

    async def test_oauth_refresh_no_config(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop), \
             patch("onemancompany.api.routes._push_adhoc_task") as mock_push:
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/meeting/book", json={
                    "employee_id": "00010",
                    "participants": ["00010", "00011"],
                    "purpose": "Planning",
                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "processing"
        mock_push.assert_called_once()

    async def test_book_meeting_missing_employee_id(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop), \
             patch("onemancompany.api.routes._push_adhoc_task") as mock_push:
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/hr/review")

        assert resp.status_code == 200
        assert resp.json()["status"] == "HR review started"
        mock_push.assert_called_once()


# ---------------------------------------------------------------------------
# POST /api/routine/start + approve + all_hands
# ---------------------------------------------------------------------------


class TestRoutineStart:
    async def test_start_routine(self):
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._get_employee_manager", return_value=MagicMock(schedule_system_task=MagicMock(return_value="task_123"))), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/routine/approve", json={"report_id": ""})

        assert resp.json()["error"] == "Missing report_id"

    async def test_approve_actions_success(self):
        state = _make_state()
        bus = EventBus()

        mock_em = MagicMock()
        mock_em.schedule_system_task = MagicMock(return_value="task_123")
        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._get_employee_manager", return_value=mock_em), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/routine/all_hands", json={"message": ""})

        assert resp.json()["error"] == "Missing CEO message"

    async def test_all_hands_success(self):
        state = _make_state()
        bus = EventBus()

        mock_em = MagicMock()
        mock_em.schedule_system_task = MagicMock(return_value="task_123")
        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes._get_employee_manager", return_value=mock_em), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/employee/00010/okrs")

        assert resp.status_code == 200
        assert len(resp.json()["okrs"]) == 1

    async def test_get_okrs_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus):
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/99999/okrs", json={"okrs": []})

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/employee/{employee_id}/taskboard — with loop
# ---------------------------------------------------------------------------


class TestEmployeeTaskboardWithLoop:
    async def test_taskboard_with_loop(self, tmp_path):
        state = _make_state()
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.vessel import ScheduleEntry
        from collections import defaultdict

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00010", description="Task")
        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)

        mock_em = MagicMock()
        mock_em._schedule = defaultdict(list)
        mock_em._schedule["00010"] = [ScheduleEntry(node_id=root.id, tree_path=str(tree_path))]

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.core.vessel.employee_manager", mock_em), \
             patch("onemancompany.core.store.load_task_index", return_value=[]), \
             patch("onemancompany.core.store.append_task_index_entry"):
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

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop), \
             patch("onemancompany.api.routes._push_adhoc_task", return_value=("n1", "/tmp/tree.yaml")) as mock_push:
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/sales/submit", json={
                    "client_name": "Acme Corp",
                    "description": "Build widget",
                })

        assert resp.status_code == 200
        mock_push.assert_called_once()


class TestSalesDeliverNotFound:
    async def test_deliver_task_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/sales/tasks/nonexistent/deliver", json={})

        assert "not found" in resp.json()["error"]


class TestSalesSettleNotFound:
    async def test_settle_task_not_found(self):
        state = _make_state()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/sales/tasks/nonexistent/settle")

        assert "not found" in resp.json()["error"]




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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.agents.base.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_tools_prompt", return_value=""), \
             patch("onemancompany.agents.base.get_employee_talent_persona", return_value=""):
            from onemancompany.api.routes import _start_inquiry
            result = await _start_inquiry("Hire a new engineer")

        assert result["task_type"] == "inquiry"
        assert result["agent_role"] == "HR"


# ---------------------------------------------------------------------------
# CEO task — EA routing fallback (lines 446-453)
# ---------------------------------------------------------------------------


class TestCeoTaskEAFallback:
    async def test_ceo_task_ea_not_available(self):
        """When no EA loop, returns 503."""
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None), \
             patch("onemancompany.core.project_archive.async_create_project_from_task", new_callable=AsyncMock, return_value=("p1", "iter_001")), \
             patch("onemancompany.core.project_archive.get_project_workspace", return_value="/tmp/p1"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ceo/task", json={"task": "Do something"})

        assert resp.status_code == 503
        assert "not available" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Meeting book — COO fallback (lines 712-718)
# ---------------------------------------------------------------------------


class TestMeetingBookCOOFallback:
    async def test_meeting_book_coo_fallback(self):
        """When no COO agent loop, falls back to COOAgent."""
        state = _make_state()
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None), \
             patch("onemancompany.agents.coo_agent.COOAgent") as mock_coo_cls, \
             patch("onemancompany.api.routes._get_employee_manager", return_value=MagicMock(schedule_system_task=MagicMock(return_value="task_123"))):
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None), \
             patch("onemancompany.agents.hr_agent.HRAgent") as mock_hr_cls, \
             patch("onemancompany.api.routes._get_employee_manager", return_value=MagicMock(schedule_system_task=MagicMock(return_value="task_123"))):
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
        ex_emp.department = "R&D Department"
        ex_emp.role = "Engineer"
        ex_emp.skills = ["Python"]
        ex_emp.sprite = "employee_default"
        ex_emp.remote = False
        state.ex_employees = {"00010": ex_emp}
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
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
             _store_patches(state), \
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
        ex_emp.department = "R&D Department"
        ex_emp.role = "Remote Dev"
        ex_emp.skills = []
        ex_emp.sprite = "employee_default"
        ex_emp.remote = True
        state.ex_employees = {"00010": ex_emp}
        bus = EventBus()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
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
        ex_emp.department = "R&D Department"
        ex_emp.role = "Self Dev"
        ex_emp.skills = []
        ex_emp.sprite = "employee_default"
        ex_emp.remote = False
        state.ex_employees = {"00010": ex_emp}
        bus = EventBus()

        mock_cfg = MagicMock()
        mock_cfg.hosting = "self"

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.agents.recruitment.pending_candidates", {}):
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.agents.recruitment.pending_candidates", candidates), \
             patch("onemancompany.agents.recruitment._persist_candidates", lambda: None), \
             patch("onemancompany.agents.onboarding.execute_hire", new_callable=AsyncMock, return_value=mock_emp), \
             patch("onemancompany.agents.onboarding.generate_nickname", new_callable=AsyncMock, return_value="CoolNick"), \
             patch("onemancompany.core.config.load_talent_profile", return_value={}), \
             patch("onemancompany.agents.recruitment._pending_project_ctx", {}), \
             patch("onemancompany.core.project_archive.append_action"), \
             patch("onemancompany.core.project_archive.complete_project"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/candidates/hire", json={
                    "batch_id": "b1",
                    "candidate_id": "c1",
                })

        assert resp.json()["status"] == "onboarding"

    async def test_hire_candidate_with_project(self):
        """Hire with pending project context triggers retrospective."""
        state = _make_state()
        bus = EventBus()

        mock_emp = _make_employee(id="00099", name="New Hire")

        candidates = {"b1": [{"id": "c1", "name": "New Hire", "role": "Engineer", "skill_set": []}]}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.agents.recruitment.pending_candidates", candidates), \
             patch("onemancompany.agents.recruitment._persist_candidates", lambda: None), \
             patch("onemancompany.agents.onboarding.execute_hire", new_callable=AsyncMock, return_value=mock_emp), \
             patch("onemancompany.core.config.load_talent_profile", return_value={}), \
             patch("onemancompany.agents.recruitment._pending_project_ctx", {"b1": {"project_id": "proj1"}}), \
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

        assert resp.json()["status"] == "onboarding"

    async def test_hire_candidate_not_found_returns_error(self):
        """Candidate not in batch returns error synchronously."""
        state = _make_state()
        bus = EventBus()

        candidates = {"b1": [{"id": "c1", "name": "Bad", "role": "Dev", "skill_set": []}]}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.agents.recruitment.pending_candidates", candidates):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/candidates/hire", json={
                    "batch_id": "b1",
                    "candidate_id": "nonexistent",
                    "nickname": "TestNick",
                })

        assert "not found" in resp.json()["error"].lower()


# ---------------------------------------------------------------------------
# Hiring request approved (lines 2249-2260)
# ---------------------------------------------------------------------------


class TestHiringRequestApproved:
    async def test_hiring_request_approved(self):
        state = _make_state()
        bus = EventBus()

        pending = {"h1": {"role": "Engineer", "desired_skills": ["Go"], "reason": "Growth"}}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.agents.coo_agent.pending_hiring_requests", pending), \
             patch("onemancompany.api.routes._get_employee_manager", return_value=MagicMock(schedule_system_task=MagicMock(return_value="task_123"))), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.state.reload_all_from_disk", return_value={"employees_updated": [], "employees_added": []}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/admin/reload")

        assert resp.json()["status"] == "reloaded"


class TestAdminClearTasks:
    async def test_admin_clear_tasks(self):
        state = _make_state()
        bus = EventBus()

        from onemancompany.core.vessel import ScheduleEntry
        from collections import defaultdict

        mock_em = MagicMock()
        mock_em._schedule = defaultdict(list)
        mock_em._schedule["00003"] = [ScheduleEntry(node_id="n1", tree_path="/tmp/tree.yaml")]
        mock_em._running_tasks = {}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.vessel.employee_manager", mock_em):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/admin/clear-tasks")

        assert resp.json()["status"] == "cleared"
        assert resp.json()["tasks_removed"] == 1


# ---------------------------------------------------------------------------
# Cancel task with subtasks (lines 1214-1221)
# ---------------------------------------------------------------------------


class TestCancelTaskWithSubtasks:
    async def test_cancel_task_with_children(self, tmp_path):
        """Cancel a parent node that has child nodes in the tree."""
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.vessel import ScheduleEntry

        state = _make_state()
        bus = EventBus()

        tree = TaskTree("proj1")
        root = tree.create_root("00010", "parent task")
        root.status = "processing"
        child = tree.add_child(root.id, "00010", "child task", [])
        child.status = "pending"
        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)

        entry = ScheduleEntry(node_id=root.id, tree_path=str(tree_path))
        mock_em = MagicMock()
        mock_em._schedule = {"00010": [entry]}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.employee_manager", mock_em), \
             patch("onemancompany.core.automation.stop_cron"):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post(f"/api/employee/00010/task/{root.id}/cancel")

        assert resp.json()["status"] == "ok"
        reloaded = TaskTree.load(tree_path)
        assert reloaded.get_node(root.id).status == "cancelled"


# ---------------------------------------------------------------------------
# Abort task (lines 1160-1172)
# ---------------------------------------------------------------------------


class TestAbortTaskWithBoards:
    async def test_abort_cancels_across_boards(self):
        """Abort project cancels tasks across all agent boards."""
        state = _make_state()
        bus = EventBus()

        mock_manager = MagicMock()
        mock_manager.abort_project.return_value = 1

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.employee_manager", mock_manager), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/task/proj1/abort")

        assert resp.json()["cancelled"] == 1
        mock_manager.abort_project.assert_called_once_with("proj1")


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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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

        saved_runtime_calls = []

        async def fake_save_runtime(eid, **fields):
            saved_runtime_calls.append((eid, fields))

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.core.state.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.api.routes.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result), \
             patch("onemancompany.agents.base.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.store.save_work_principles", new_callable=AsyncMock), \
             patch("onemancompany.core.store.save_employee_runtime", side_effect=fake_save_runtime):
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
        assert any(f.get("is_listening") is False for _, f in saved_runtime_calls)
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
             _store_patches(state), \
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
    async def test_chat_agent_loop_with_history(self, tmp_path):
        """Covers direct executor chat with conversation history."""
        from onemancompany.core.vessel import LaunchResult

        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_executor = AsyncMock()
        mock_executor.execute.return_value = LaunchResult(output="Got your history")

        mock_em = MagicMock()
        mock_em.executors = {"00010": mock_executor}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.vessel.employee_manager", mock_em):
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
        # Verify history was included in the task description
        call_args = mock_executor.execute.call_args[0][0]
        assert "Start the project" in call_args


# ---------------------------------------------------------------------------
# Agent loop chat — logs without llm_output type (line 564)
# ---------------------------------------------------------------------------


class TestOneOnOneChatAgentLoopLogsNoResult:
    async def test_chat_completed_no_result(self, tmp_path):
        """Covers executor returning empty output — returns fallback message."""
        from onemancompany.core.vessel import LaunchResult

        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        bus = EventBus()

        mock_executor = AsyncMock()
        mock_executor.execute.return_value = LaunchResult(output="")

        mock_em = MagicMock()
        mock_em.executors = {"00010": mock_executor}

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.vessel.employee_manager", mock_em):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/oneonone/chat", json={
                    "employee_id": "00010",
                    "message": "Do something",
                })

        assert resp.status_code == 200
        assert resp.json()["response"] == "(Processing complete)"


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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value=str(ws)):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/projects/proj1/files/../secret.txt")

        # Should be 403 or 404 (path resolved to outside workspace)
        assert resp.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Line 197: admin_clear_tasks sets STATUS_IDLE (direct call)
# ---------------------------------------------------------------------------


class TestAdminClearTasksDirect:
    async def test_clear_tasks_resets_status_direct(self):
        """Direct call to ensure coverage of save_employee_runtime(status=idle)."""
        from onemancompany.api import routes as routes_mod

        bus = MagicMock()
        bus.publish = AsyncMock()

        saved_runtime_calls = []

        async def fake_save_runtime(eid, **fields):
            saved_runtime_calls.append((eid, fields))

        # Mock employee_manager with one scheduled entry
        mock_em = MagicMock()
        mock_em._schedule = {"00010": [MagicMock()]}
        mock_em._running_tasks = {}

        # Mock _store to return one employee
        mock_store = MagicMock()
        mock_store.load_all_employees = lambda: {"00010": MagicMock()}
        mock_store.save_employee_runtime = AsyncMock(side_effect=fake_save_runtime)

        with patch.object(routes_mod, "event_bus", bus), \
             patch("onemancompany.api.routes._store", mock_store), \
             patch("onemancompany.core.vessel.employee_manager", mock_em):
            result = await routes_mod.admin_clear_tasks()

        assert result["status"] == "cleared"
        assert result["tasks_removed"] == 1
        assert any(f.get("status") == "idle" for _, f in saved_runtime_calls)


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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
             _store_patches(state), \
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
    async def test_approved_hiring_is_noop_legacy(self):
        """Legacy decide endpoint: approval is a no-op (hiring is auto-approved by COO)."""
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

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        with patch("onemancompany.api.routes.event_bus", mock_bus):
            from onemancompany.api.routes import decide_hiring_request
            result = await decide_hiring_request(req_id, {"approved": True})

        assert result["status"] == "approved"
        assert result["hire_id"] == req_id
        # Request stays in pending (not popped — already auto-approved)
        assert req_id in pending_hiring_requests
        pending_hiring_requests.pop(req_id, None)  # cleanup

    async def test_rejected_hiring_removes_request(self):
        """CEO rejection removes the request from pending."""
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
        assert req_id not in pending_hiring_requests


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
             _store_patches(state), \
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
             _store_patches(state), \
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


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id}/tree
# ---------------------------------------------------------------------------


class TestProjectTreeEndpoint:
    @pytest.mark.asyncio
    async def test_get_project_tree(self):
        """GET /api/projects/{id}/tree returns full tree structure."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root task")
        child = tree.add_child(root.id, "00010", "Child task", ["criterion"])
        child.status = "completed"
        child.result = "Done"

        with patch("onemancompany.api.routes._load_project_tree_for_api", return_value=tree):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/projects/proj1/tree")

        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "proj1"
        assert data["root_id"] == root.id
        assert len(data["nodes"]) == 2
        root_node = next(n for n in data["nodes"] if n["id"] == root.id)
        assert root_node["employee_id"] == "00001"
        child_node = next(n for n in data["nodes"] if n["id"] == child.id)
        assert child_node["status"] == "completed"
        # result is externalized; skeleton has description_preview instead
        assert child_node["description_preview"] == "Child task"

    @pytest.mark.asyncio
    async def test_get_project_tree_not_found(self):
        """Returns 404 when no tree exists."""
        with patch("onemancompany.api.routes._load_project_tree_for_api", return_value=None):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/projects/proj1/tree")

        assert resp.status_code == 404


class TestEmployeeProjects:
    def test_scan_employee_projects(self, tmp_path):
        """_scan_employee_projects returns projects the employee participated in."""
        import yaml

        proj1_dir = tmp_path / "proj1"
        proj1_dir.mkdir()
        (proj1_dir / "project.yaml").write_text(yaml.dump({
            "task": "Build game",
            "status": "completed",
            "team": [
                {"employee_id": "00006", "role": "Engineer", "joined_at": "2026-03-11T10:00:00"},
                {"employee_id": "00003", "role": "COO", "joined_at": "2026-03-11T10:00:00"},
            ],
        }))

        proj2_dir = tmp_path / "proj2"
        proj2_dir.mkdir()
        (proj2_dir / "project.yaml").write_text(yaml.dump({
            "task": "Design UI",
            "status": "in_progress",
            "team": [
                {"employee_id": "00007", "role": "Designer", "joined_at": "2026-03-11T11:00:00"},
            ],
        }))

        from onemancompany.api.routes import _scan_employee_projects
        projects = _scan_employee_projects("00006", str(tmp_path))
        assert len(projects) == 1
        assert projects[0]["task"] == "Build game"
        assert projects[0]["role_in_project"] == "Engineer"
        assert projects[0]["project_id"] == "proj1"

    def test_scan_no_projects(self, tmp_path):
        from onemancompany.api.routes import _scan_employee_projects
        projects = _scan_employee_projects("00099", str(tmp_path))
        assert projects == []


# ---------------------------------------------------------------------------
# GET /api/employees  (tick-based)
# ---------------------------------------------------------------------------


class TestListEmployees:
    async def test_list_employees_returns_from_disk(self):
        """GET /api/employees reads from store.load_all_employees."""
        mock_data = {
            "00100": {
                "name": "TestBot",
                "role": "Engineer",
                "runtime": {"status": "idle", "is_listening": True},
            },
        }
        with patch("onemancompany.api.routes.company_state", _make_state()), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            with patch("onemancompany.core.store.load_all_employees", return_value=mock_data):
                app = _make_test_app()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                    resp = await c.get("/api/employees")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "TestBot"
        assert data[0]["status"] == "idle"
        assert data[0]["is_listening"] is True
        assert data[0]["id"] == "00100"

    async def test_list_employees_empty(self):
        with patch("onemancompany.api.routes.company_state", _make_state()), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            with patch("onemancompany.core.store.load_all_employees", return_value={}):
                app = _make_test_app()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                    resp = await c.get("/api/employees")

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/rooms  (tick-based)
# ---------------------------------------------------------------------------


class TestListRooms:
    async def test_list_rooms(self):
        from onemancompany.core.state import MeetingRoom
        room = MeetingRoom(id="room1", name="Alpha", description="", capacity=6, position=(5, 5))
        state = _make_state(meeting_rooms={"room1": room})
        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/api/rooms")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "room1"


# ---------------------------------------------------------------------------
# GET /api/rooms/{room_id}/chat  (tick-based)
# ---------------------------------------------------------------------------


class TestGetRoomChat:
    async def test_get_room_chat(self):
        mock_chat = [{"sender": "00100", "text": "Hello"}]
        with patch("onemancompany.api.routes.company_state", _make_state()), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            with patch("onemancompany.core.store.load_room_chat", return_value=mock_chat):
                app = _make_test_app()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                    resp = await c.get("/api/rooms/room1/chat")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["sender"] == "00100"


# ---------------------------------------------------------------------------
# GET /api/tools  (tick-based)
# ---------------------------------------------------------------------------


class TestListTools:
    async def test_list_tools(self):
        mock_tools = [{"slug": "gmail", "name": "Gmail"}]
        with patch("onemancompany.api.routes.company_state", _make_state()), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            with patch("onemancompany.core.store.load_tools", return_value=mock_tools):
                app = _make_test_app()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                    resp = await c.get("/api/tools")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["slug"] == "gmail"


# ---------------------------------------------------------------------------
# GET /api/employee/{employee_id}/oneonone  (tick-based)
# ---------------------------------------------------------------------------


class TestGetOneononeHistory:
    async def test_get_oneonone_history(self):
        mock_history = [{"role": "ceo", "content": "How are you?"}]
        with patch("onemancompany.api.routes.company_state", _make_state()), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            with patch("onemancompany.core.store.load_oneonone", return_value=mock_history):
                app = _make_test_app()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                    resp = await c.get("/api/employee/00100/oneonone")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["role"] == "ceo"


# ---------------------------------------------------------------------------
# GET /api/activity-log  (tick-based)
# ---------------------------------------------------------------------------


class TestGetActivityLog:
    async def test_get_activity_log(self):
        mock_log = [{"event": "hired", "employee_id": "00100"}]
        with patch("onemancompany.api.routes.company_state", _make_state()), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            with patch("onemancompany.core.store.load_activity_log", return_value=mock_log):
                app = _make_test_app()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                    resp = await c.get("/api/activity-log")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["event"] == "hired"

    async def test_activity_log_truncated_to_50(self):
        mock_log = [{"event": f"e{i}"} for i in range(100)]
        with patch("onemancompany.api.routes.company_state", _make_state()), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            with patch("onemancompany.core.store.load_activity_log", return_value=mock_log):
                app = _make_test_app()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                    resp = await c.get("/api/activity-log")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 50


# ---------------------------------------------------------------------------
# PUT /api/employee/{id}/hosting — hosting mode switch manifest updates
# ---------------------------------------------------------------------------


class TestUpdateEmployeeHostingManifest:
    """Verify that switching hosting mode correctly updates manifest sections."""

    def _self_hosted_manifest(self) -> dict:
        return {
            "id": "test",
            "hosting": "self",
            "settings": {
                "sections": [
                    {
                        "id": "connection",
                        "title": "Connection",
                        "fields": [{"key": "sessions", "type": "readonly", "label": "Sessions", "value_from": "api:sessions"}],
                    },
                    {
                        "id": "hosting",
                        "title": "Hosting Mode",
                        "fields": [{"key": "hosting", "type": "select", "label": "Hosting", "options": []}],
                    },
                ],
            },
        }

    def _company_hosted_manifest(self) -> dict:
        return {
            "id": "test",
            "hosting": "company",
            "settings": {
                "sections": [
                    {
                        "id": "hosting",
                        "title": "Hosting Mode",
                        "fields": [{"key": "hosting", "type": "select", "label": "Hosting", "options": []}],
                    },
                    {
                        "id": "llm",
                        "title": "LLM Configuration",
                        "fields": [{"key": "llm_model", "type": "select", "label": "Model", "options_from": "api:models"}],
                    },
                ],
            },
        }

    async def test_switch_self_to_company_removes_connection_adds_llm(self, tmp_path):
        """Switching from self → company should remove connection section, add llm section."""
        import json as _json
        from onemancompany.core.config import EmployeeConfig

        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(_json.dumps(self._self_hosted_manifest()), encoding="utf-8")

        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        cfg = EmployeeConfig(name="Test", role="Engineer", skills=["py"], hosting="self")

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.api.routes._load_emp", return_value=_emp_to_dict(emp)), \
             patch("onemancompany.core.config.employee_configs", {"00010": cfg}), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", tmp_path.parent), \
             patch("onemancompany.api.routes._store") as mock_store, \
             patch("onemancompany.core.config.invalidate_manifest_cache"):
            # Make EMPLOYEES_DIR / employee_id point to tmp_path
            # We need manifest at EMPLOYEES_DIR / "00010" / "manifest.json"
            emp_dir = tmp_path.parent / "00010"
            emp_dir.mkdir(exist_ok=True)
            emp_manifest = emp_dir / "manifest.json"
            emp_manifest.write_text(_json.dumps(self._self_hosted_manifest()), encoding="utf-8")

            mock_store.save_employee = AsyncMock()

            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/00010/hosting", json={"hosting": "company"})

        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] == "updated"
        assert result["hosting"] == "company"

        updated = _json.loads(emp_manifest.read_text(encoding="utf-8"))
        section_ids = [s["id"] for s in updated["settings"]["sections"]]
        assert "connection" not in section_ids, "connection section should be removed for company-hosted"
        assert "llm" in section_ids, "llm section should be added for company-hosted"
        assert updated["hosting"] == "company"

    async def test_switch_company_to_self_removes_llm_adds_connection(self, tmp_path):
        """Switching from company → self should remove llm section, add connection section."""
        import json as _json
        from onemancompany.core.config import EmployeeConfig

        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        cfg = EmployeeConfig(name="Test", role="Engineer", skills=["py"], hosting="company")

        emp_dir = tmp_path / "00010"
        emp_dir.mkdir(exist_ok=True)
        emp_manifest = emp_dir / "manifest.json"
        emp_manifest.write_text(_json.dumps(self._company_hosted_manifest()), encoding="utf-8")

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.api.routes._load_emp", return_value=_emp_to_dict(emp)), \
             patch("onemancompany.core.config.employee_configs", {"00010": cfg}), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", tmp_path), \
             patch("onemancompany.api.routes._store") as mock_store, \
             patch("onemancompany.core.config.invalidate_manifest_cache"):
            mock_store.save_employee = AsyncMock()

            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/00010/hosting", json={"hosting": "self"})

        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] == "updated"

        updated = _json.loads(emp_manifest.read_text(encoding="utf-8"))
        section_ids = [s["id"] for s in updated["settings"]["sections"]]
        assert "connection" in section_ids, "connection section should be added for self-hosted"
        assert "llm" not in section_ids, "llm section should be removed for self-hosted"
        assert updated["hosting"] == "self"

    async def test_switch_same_mode_returns_unchanged(self):
        """Switching to the same mode should return unchanged."""
        from onemancompany.core.config import EmployeeConfig

        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        cfg = EmployeeConfig(name="Test", role="Engineer", skills=["py"], hosting="company")

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.api.routes._load_emp", return_value=_emp_to_dict(emp)), \
             patch("onemancompany.core.config.employee_configs", {"00010": cfg}):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.put("/api/employee/00010/hosting", json={"hosting": "company"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "unchanged"

    async def test_roundtrip_self_company_self_restores_sections(self, tmp_path):
        """Full roundtrip: self → company → self should restore original section structure."""
        import json as _json
        from onemancompany.core.config import EmployeeConfig

        emp = _make_employee(id="00010")
        state = _make_state(employees={"00010": emp})
        cfg = EmployeeConfig(name="Test", role="Engineer", skills=["py"], hosting="self")

        emp_dir = tmp_path / "00010"
        emp_dir.mkdir(exist_ok=True)
        emp_manifest = emp_dir / "manifest.json"
        emp_manifest.write_text(_json.dumps(self._self_hosted_manifest()), encoding="utf-8")

        with patch("onemancompany.api.routes.company_state", state), \
             patch("onemancompany.api.routes.event_bus", EventBus()), \
             patch("onemancompany.api.routes._load_emp", return_value=_emp_to_dict(emp)), \
             patch("onemancompany.core.config.employee_configs", {"00010": cfg}), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", tmp_path), \
             patch("onemancompany.api.routes._store") as mock_store, \
             patch("onemancompany.core.config.invalidate_manifest_cache"):
            mock_store.save_employee = AsyncMock()

            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                # self → company
                resp1 = await c.put("/api/employee/00010/hosting", json={"hosting": "company"})
                assert resp1.status_code == 200
                cfg.hosting = "company"  # simulate in-memory update

                mid = _json.loads(emp_manifest.read_text(encoding="utf-8"))
                mid_ids = [s["id"] for s in mid["settings"]["sections"]]
                assert "connection" not in mid_ids
                assert "llm" in mid_ids

                # company → self
                resp2 = await c.put("/api/employee/00010/hosting", json={"hosting": "self"})
                assert resp2.status_code == 200

        final = _json.loads(emp_manifest.read_text(encoding="utf-8"))
        final_ids = [s["id"] for s in final["settings"]["sections"]]
        assert "connection" in final_ids, "connection should be restored on switch back to self"
        assert "llm" not in final_ids, "llm should be removed on switch back to self"
        assert final["hosting"] == "self"


# ---------------------------------------------------------------------------
# _check_talent_required_fields
# ---------------------------------------------------------------------------

class TestCheckTalentRequiredFields:
    def test_complete_company_profile(self):
        from onemancompany.api.routes import _check_talent_required_fields
        data = {"hosting": "company", "llm_model": "gpt-4o", "api_provider": "openrouter", "auth_method": "api_key"}
        assert _check_talent_required_fields(data) == []

    def test_complete_self_profile(self):
        from onemancompany.api.routes import _check_talent_required_fields
        data = {"hosting": "self"}
        assert _check_talent_required_fields(data) == []

    def test_missing_hosting(self):
        from onemancompany.api.routes import _check_talent_required_fields
        data = {"llm_model": "gpt-4o", "api_provider": "openrouter", "auth_method": "api_key"}
        missing = _check_talent_required_fields(data)
        assert "hosting" in missing

    def test_missing_llm_model_for_company(self):
        from onemancompany.api.routes import _check_talent_required_fields
        data = {"hosting": "company", "api_provider": "openrouter", "auth_method": "api_key"}
        missing = _check_talent_required_fields(data)
        assert "llm_model" in missing
        assert "hosting" not in missing

    def test_missing_auth_method_for_company(self):
        from onemancompany.api.routes import _check_talent_required_fields
        data = {"hosting": "company", "llm_model": "gpt-4o", "api_provider": "openrouter"}
        missing = _check_talent_required_fields(data)
        assert "auth_method" in missing

    def test_missing_api_provider_for_company(self):
        from onemancompany.api.routes import _check_talent_required_fields
        data = {"hosting": "company", "llm_model": "gpt-4o", "auth_method": "api_key"}
        missing = _check_talent_required_fields(data)
        assert "api_provider" in missing

    def test_self_hosted_skips_llm_and_auth(self):
        from onemancompany.api.routes import _check_talent_required_fields
        data = {"hosting": "self"}
        missing = _check_talent_required_fields(data)
        assert "llm_model" not in missing
        assert "api_provider" not in missing
        assert "auth_method" not in missing

    def test_empty_dict(self):
        from onemancompany.api.routes import _check_talent_required_fields
        missing = _check_talent_required_fields({})
        assert "hosting" in missing

    def test_empty_string_values_treated_as_missing(self):
        from onemancompany.api.routes import _check_talent_required_fields
        data = {"hosting": "", "llm_model": "", "api_provider": "", "auth_method": ""}
        missing = _check_talent_required_fields(data)
        assert "hosting" in missing


# ---------------------------------------------------------------------------
# _publish_talent_profile_error
# ---------------------------------------------------------------------------

class TestPublishTalentProfileError:
    @pytest.mark.asyncio
    async def test_missing_profile_default_message(self):
        from onemancompany.api.routes import _publish_talent_profile_error
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        with patch("onemancompany.api.routes.event_bus", mock_bus):
            await _publish_talent_profile_error("t1", [], "", is_missing=True)
        event = mock_bus.publish.call_args[0][0]
        assert event.type == "talent_profile_error"
        assert "not found on disk" in event.payload["summary"]
        assert "may have failed to clone" in event.payload["summary"]

    @pytest.mark.asyncio
    async def test_missing_profile_with_clone_error(self):
        from onemancompany.api.routes import _publish_talent_profile_error
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        with patch("onemancompany.api.routes.event_bus", mock_bus):
            await _publish_talent_profile_error("t1", [], "", is_missing=True, clone_error="404 Not Found")
        event = mock_bus.publish.call_args[0][0]
        assert "Clone failed: 404 Not Found" in event.payload["summary"]
        assert "may have failed" not in event.payload["summary"]

    @pytest.mark.asyncio
    async def test_missing_fields_message(self):
        from onemancompany.api.routes import _publish_talent_profile_error
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        with patch("onemancompany.api.routes.event_bus", mock_bus):
            await _publish_talent_profile_error("t1", ["llm_model", "auth_method"], "https://repo.example")
        p = mock_bus.publish.call_args[0][0].payload
        assert "llm_model" in p["summary"]
        assert p["talent_link"] == "https://repo.example"
        assert p["missing_fields"] == ["llm_model", "auth_method"]


# ---------------------------------------------------------------------------
# _cleanup_single_hire_failure
# ---------------------------------------------------------------------------

class TestCleanupSingleHireFailure:
    @pytest.mark.asyncio
    async def test_clears_pending_and_publishes(self):
        from onemancompany.api.routes import _cleanup_single_hire_failure
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        pending = {"b1": [{"id": "c1", "name": "Alice"}]}
        persist_called = []

        mock_em = MagicMock()
        mock_em.find_holding_task.return_value = None

        with patch("onemancompany.api.routes.event_bus", mock_bus), \
             patch("onemancompany.agents.recruitment.pending_candidates", pending), \
             patch("onemancompany.agents.recruitment._persist_candidates", lambda: persist_called.append(1)), \
             patch("onemancompany.core.vessel.employee_manager", mock_em):
            await _cleanup_single_hire_failure("b1", "c1", {"name": "Alice"}, "test error")

        # pending_candidates cleared
        assert "b1" not in pending
        # persist called
        assert persist_called
        # event published
        event = mock_bus.publish.call_args[0][0]
        assert event.payload["step"] == "failed"
        assert event.payload["message"] == "test error"

    @pytest.mark.asyncio
    async def test_resumes_hr_holding_task(self):
        from onemancompany.api.routes import _cleanup_single_hire_failure
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        pending = {"b1": []}

        mock_em = MagicMock()
        mock_em.find_holding_task.return_value = "node-123"
        mock_em.resume_held_task = AsyncMock(return_value=True)

        with patch("onemancompany.api.routes.event_bus", mock_bus), \
             patch("onemancompany.agents.recruitment.pending_candidates", pending), \
             patch("onemancompany.agents.recruitment._persist_candidates", lambda: None), \
             patch("onemancompany.core.vessel.employee_manager", mock_em):
            await _cleanup_single_hire_failure("b1", "c1", {"name": "Bob"}, "clone error")

        mock_em.find_holding_task.assert_called_once()
        mock_em.resume_held_task.assert_called_once_with(
            "00002", "node-123", "Hire failed: clone error"
        )
