"""Unit tests for agents/coo_agent.py — COOAgent, asset management, meeting rooms, knowledge."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from onemancompany.core.state import CompanyState, Employee, MeetingRoom, OfficeTool


def _make_cs() -> CompanyState:
    cs = CompanyState()
    cs._next_employee_number = 100
    return cs


def _make_emp(emp_id: str, **kwargs) -> Employee:
    defaults = dict(
        id=emp_id, name=f"Emp {emp_id}", role="COO",
        skills=["management"], employee_number=emp_id, nickname="运营",
    )
    defaults.update(kwargs)
    return Employee(**defaults)


def _emp_to_dict(emp: Employee) -> dict:
    return {
        "id": emp.id, "name": emp.name, "role": emp.role,
        "skills": emp.skills, "nickname": emp.nickname,
        "level": getattr(emp, "level", 1),
        "department": getattr(emp, "department", ""),
        "tool_permissions": getattr(emp, "tool_permissions", []) or [],
        "guidance_notes": getattr(emp, "guidance_notes", []) or [],
        "runtime": {"status": "idle"},
    }


def _mock_store_for_employees(monkeypatch, employees: dict):
    from onemancompany.core import store as store_mod
    emp_dicts = {eid: _emp_to_dict(e) for eid, e in employees.items()}
    monkeypatch.setattr(store_mod, "load_employee",
                        lambda eid: emp_dicts.get(eid))
    monkeypatch.setattr(store_mod, "load_all_employees",
                        lambda: dict(emp_dicts))
    monkeypatch.setattr(store_mod, "load_employee_guidance",
                        lambda eid: (emp_dicts.get(eid) or {}).get("guidance_notes", []))
    monkeypatch.setattr(store_mod, "load_culture", lambda: [])
    monkeypatch.setattr(store_mod, "load_direction", lambda: "")


def _make_room(room_id: str, **kwargs) -> MeetingRoom:
    defaults = dict(
        id=room_id, name=f"Room {room_id}", description="Test room",
        capacity=6, position=(1, 8), sprite="meeting_room",
    )
    defaults.update(kwargs)
    return MeetingRoom(**defaults)


def _make_tool(tool_id: str, **kwargs) -> OfficeTool:
    defaults = dict(
        id=tool_id, name=f"Tool {tool_id}", description="Test tool",
        added_by="COO", desk_position=(5, 8), sprite="desk_equipment",
        allowed_users=[], files=[], folder_name="",
    )
    defaults.update(kwargs)
    return OfficeTool(**defaults)


# ---------------------------------------------------------------------------
# register_asset
# ---------------------------------------------------------------------------

class TestRegisterAsset:
    def test_registers_new_tool(self, tmp_path, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tmp_path / "tools")
        monkeypatch.setattr(coo_mod, "TOOLS_DIR", tmp_path / "tools")

        result = coo_mod.register_asset.invoke({
            "name": "Code Bot",
            "description": "Automated code review tool",
        })

        assert result["status"] == "success"
        assert result["name"] == "Code Bot"
        assert result["folder_name"] == "code_bot"
        assert len(cs.tools) == 1
        tool = list(cs.tools.values())[0]
        assert tool.name == "Code Bot"
        assert (tmp_path / "tools" / "code_bot" / "tool.yaml").exists()

    def test_registers_with_source_files(self, tmp_path, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tmp_path / "tools")
        monkeypatch.setattr(coo_mod, "TOOLS_DIR", tmp_path / "tools")

        # Create source project dir under PROJECTS_DIR
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        proj_dir = projects_dir / "myproj"
        proj_dir.mkdir()
        (proj_dir / "script.py").write_text("print('hello')")
        monkeypatch.setattr(config_mod, "PROJECTS_DIR", projects_dir)
        monkeypatch.setattr(coo_mod, "PROJECTS_DIR", projects_dir)

        result = coo_mod.register_asset.invoke({
            "name": "My Script",
            "description": "A script tool",
            "source_project_dir": str(proj_dir),
            "source_files": ["script.py"],
        })

        assert result["status"] == "success"
        assert "script.py" in result["files"]
        tool_folder = tmp_path / "tools" / "my_script"
        assert (tool_folder / "script.py").exists()

    def test_rejects_source_outside_projects(self, tmp_path, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tmp_path / "tools")
        monkeypatch.setattr(coo_mod, "TOOLS_DIR", tmp_path / "tools")
        monkeypatch.setattr(config_mod, "PROJECTS_DIR", tmp_path / "projects")
        monkeypatch.setattr(coo_mod, "PROJECTS_DIR", tmp_path / "projects")

        result = coo_mod.register_asset.invoke({
            "name": "Bad",
            "description": "Bad",
            "source_project_dir": "/etc/passwd",
            "source_files": ["something"],
        })
        assert result["status"] == "error"

    def test_path_traversal_in_source_files_skipped(self, tmp_path, monkeypatch):
        """Lines 258-259: Path traversal in source_files is skipped (continue)."""
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tmp_path / "tools")
        monkeypatch.setattr(coo_mod, "TOOLS_DIR", tmp_path / "tools")

        # Create source project dir under PROJECTS_DIR
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        proj_dir = projects_dir / "myproj"
        proj_dir.mkdir()
        (proj_dir / "legit.py").write_text("print('ok')")
        monkeypatch.setattr(config_mod, "PROJECTS_DIR", projects_dir)
        monkeypatch.setattr(coo_mod, "PROJECTS_DIR", projects_dir)

        result = coo_mod.register_asset.invoke({
            "name": "Traversal Test",
            "description": "Test path traversal",
            "source_project_dir": str(proj_dir),
            "source_files": ["../../../etc/passwd", "legit.py"],
        })

        assert result["status"] == "success"
        # Only legit.py should be copied, not the traversal path
        assert "legit.py" in result["files"]
        assert len(result["files"]) == 1

    def test_handles_slug_collision(self, tmp_path, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tmp_path / "tools")
        monkeypatch.setattr(coo_mod, "TOOLS_DIR", tmp_path / "tools")

        # Pre-create directory with same slug
        (tmp_path / "tools" / "code_bot").mkdir(parents=True)

        result = coo_mod.register_asset.invoke({
            "name": "Code Bot",
            "description": "Another code bot",
        })

        assert result["status"] == "success"
        # Folder name should be different (slug + uuid)
        assert result["folder_name"] != "code_bot"


# ---------------------------------------------------------------------------
# remove_tool
# ---------------------------------------------------------------------------

class TestRemoveTool:
    def test_removes_tool(self, tmp_path, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        tool = _make_tool("t1", name="Remover", folder_name="remover")
        cs.tools["t1"] = tool
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "TOOLS_DIR", tmp_path / "tools")

        # Create folder
        (tmp_path / "tools" / "remover").mkdir(parents=True)

        result = coo_mod.remove_tool.invoke({"tool_id": "t1"})

        assert result["status"] == "success"
        assert result["name"] == "Remover"
        assert "t1" not in cs.tools
        assert not (tmp_path / "tools" / "remover").exists()

    def test_remove_nonexistent(self, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)

        result = coo_mod.remove_tool.invoke({"tool_id": "bad"})
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# list_tools / list_assets
# ---------------------------------------------------------------------------

class TestListTools:
    def test_returns_tool_list(self, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.tools = {
            "t1": _make_tool("t1", name="Alpha", allowed_users=[]),
            "t2": _make_tool("t2", name="Beta", allowed_users=["00010"]),
        }
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)

        result = coo_mod.list_tools.invoke({})
        assert len(result) == 2
        names = {t["name"] for t in result}
        assert "Alpha" in names
        assert "Beta" in names
        # Check access field
        for t in result:
            if t["name"] == "Alpha":
                assert t["access"] == "open"
            elif t["name"] == "Beta":
                assert t["access"] == "restricted"


class TestListAssets:
    def test_includes_tools_and_rooms(self, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import store as store_mod

        cs = _make_cs()
        cs.tools = {"t1": _make_tool("t1", name="Tool1")}
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(store_mod, "load_rooms", lambda: [
            {"id": "r1", "name": "Room1", "description": "Test room",
             "capacity": 6, "is_booked": False, "booked_by": ""},
        ])

        result = coo_mod.list_assets.invoke({})
        assert len(result) == 2
        types = {a["type"] for a in result}
        assert "tool" in types
        assert "room" in types


# ---------------------------------------------------------------------------
# grant_tool_access / revoke_tool_access
# ---------------------------------------------------------------------------

class TestToolAccess:
    def test_grant_access(self, tmp_path, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        tool = _make_tool("t1", name="Special", folder_name="special")
        cs.tools["t1"] = tool
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "TOOLS_DIR", tmp_path / "tools")

        result = coo_mod.grant_tool_access.invoke({
            "tool_id": "t1", "employee_id": "00010",
        })

        assert result["status"] == "success"
        assert "00010" in tool.allowed_users

    def test_grant_idempotent(self, tmp_path, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        tool = _make_tool("t1", name="Special", allowed_users=["00010"], folder_name="special")
        cs.tools["t1"] = tool
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "TOOLS_DIR", tmp_path / "tools")

        result = coo_mod.grant_tool_access.invoke({
            "tool_id": "t1", "employee_id": "00010",
        })
        assert result["status"] == "success"
        assert tool.allowed_users.count("00010") == 1

    def test_grant_nonexistent_tool(self, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)

        result = coo_mod.grant_tool_access.invoke({
            "tool_id": "bad", "employee_id": "00010",
        })
        assert result["status"] == "error"

    def test_revoke_access(self, tmp_path, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        tool = _make_tool("t1", name="Special", allowed_users=["00010", "00020"], folder_name="special")
        cs.tools["t1"] = tool
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "TOOLS_DIR", tmp_path / "tools")

        result = coo_mod.revoke_tool_access.invoke({
            "tool_id": "t1", "employee_id": "00010",
        })

        assert result["status"] == "success"
        assert "00010" not in tool.allowed_users
        assert "00020" in tool.allowed_users

    def test_revoke_last_user_makes_open(self, tmp_path, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        tool = _make_tool("t1", name="Special", allowed_users=["00010"], folder_name="special")
        cs.tools["t1"] = tool
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "TOOLS_DIR", tmp_path / "tools")

        result = coo_mod.revoke_tool_access.invoke({
            "tool_id": "t1", "employee_id": "00010",
        })

        assert result["access"] == "open"
        assert tool.allowed_users == []


# ---------------------------------------------------------------------------
# Meeting room management
# ---------------------------------------------------------------------------

class TestListMeetingRooms:
    def test_lists_rooms(self, monkeypatch):
        from onemancompany.core import store as store_mod

        monkeypatch.setattr(store_mod, "load_rooms", lambda: [
            {"id": "r1", "name": "Room A", "capacity": 6,
             "is_booked": False, "booked_by": "", "participants": []},
            {"id": "r2", "name": "Room B", "capacity": 6,
             "is_booked": True, "booked_by": "00010", "participants": ["00010"]},
        ])

        from onemancompany.agents import coo_agent as coo_mod
        result = coo_mod.list_meeting_rooms.invoke({})
        assert len(result) == 2
        booked_rooms = [r for r in result if r["is_booked"]]
        assert len(booked_rooms) == 1


class TestBookMeetingRoom:
    def test_books_free_room(self, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.meeting_rooms = {"r1": _make_room("r1", name="Room A")}
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)

        result = coo_mod.book_meeting_room.invoke({
            "employee_id": "00010",
            "participants": ["00020"],
            "purpose": "Sync up",
        })

        assert result["status"] == "booked"
        assert cs.meeting_rooms["r1"].is_booked is True
        assert cs.meeting_rooms["r1"].booked_by == "00010"

    def test_denied_when_no_rooms_free(self, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.meeting_rooms = {
            "r1": _make_room("r1", name="Room A", is_booked=True, booked_by="00099"),
        }
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)

        result = coo_mod.book_meeting_room.invoke({
            "employee_id": "00010",
            "participants": ["00020"],
        })
        assert result["status"] == "denied"

    def test_denied_solo_meeting(self, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.meeting_rooms = {"r1": _make_room("r1")}
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)

        result = coo_mod.book_meeting_room.invoke({
            "employee_id": "00010",
            "participants": [],  # solo
        })
        assert result["status"] == "denied"

    def test_denied_capacity_exceeded(self, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.meeting_rooms = {"r1": _make_room("r1", capacity=2)}
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)

        result = coo_mod.book_meeting_room.invoke({
            "employee_id": "00010",
            "participants": ["00020", "00030"],  # 3 total > capacity 2
        })
        assert result["status"] == "denied"


class TestReleaseMeetingRoom:
    def test_releases_booked_room(self, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        room = _make_room("r1", is_booked=True, booked_by="00010")
        room.participants = ["00010", "00020"]
        cs.meeting_rooms = {"r1": room}
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)

        result = coo_mod.release_meeting_room.invoke({"room_id": "r1"})

        assert result["status"] == "released"
        assert room.is_booked is False
        assert room.booked_by == ""
        assert room.participants == []

    def test_release_unbooked_room(self, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.meeting_rooms = {"r1": _make_room("r1")}
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)

        result = coo_mod.release_meeting_room.invoke({"room_id": "r1"})
        assert result["status"] == "error"

    def test_release_nonexistent_room(self, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)

        result = coo_mod.release_meeting_room.invoke({"room_id": "bad"})
        assert result["status"] == "error"


class TestAddMeetingRoom:
    def test_adds_new_room(self, tmp_path, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "ROOMS_DIR", tmp_path / "rooms")

        result = coo_mod.add_meeting_room.invoke({
            "name": "Room C", "capacity": 10, "description": "Large room",
        })

        assert result["status"] == "success"
        assert result["name"] == "Room C"
        assert result["capacity"] == 10
        assert len(cs.meeting_rooms) == 1
        room = list(cs.meeting_rooms.values())[0]
        assert room.name == "Room C"
        assert room.capacity == 10

    def test_persists_to_disk(self, tmp_path, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "ROOMS_DIR", tmp_path / "rooms")

        result = coo_mod.add_meeting_room.invoke({
            "name": "Room D", "capacity": 4,
        })

        room_id = result["id"]
        room_file = tmp_path / "rooms" / f"{room_id}.yaml"
        assert room_file.exists()


# ---------------------------------------------------------------------------
# request_hiring
# ---------------------------------------------------------------------------

class TestRequestHiring:
    def test_submits_request(self, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import events as events_mod

        # Mock event loop for create_task
        mock_loop = MagicMock()
        monkeypatch.setattr("asyncio.get_event_loop", lambda: mock_loop)
        monkeypatch.setattr(events_mod, "event_bus", MagicMock())
        monkeypatch.setattr(coo_mod, "event_bus", MagicMock())

        coo_mod.pending_hiring_requests.clear()

        result = coo_mod.request_hiring.invoke({
            "role": "Game Developer",
            "reason": "Need game expertise",
            "desired_skills": ["unity", "c#"],
        })

        assert result["status"] == "auto_approved"
        assert "hire_id" in result
        assert len(coo_mod.pending_hiring_requests) == 1
        req = list(coo_mod.pending_hiring_requests.values())[0]
        assert req["role"] == "Game Developer"
        assert req["desired_skills"] == ["unity", "c#"]
        assert req["auto_approved"] is True


# ---------------------------------------------------------------------------
# deposit_company_knowledge
# ---------------------------------------------------------------------------

class TestDepositCompanyKnowledge:
    def test_deposit_workflow(self, tmp_path, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "WORKFLOWS_DIR", tmp_path / "workflows")
        monkeypatch.setattr(config_mod, "WORKFLOWS_DIR", tmp_path / "workflows")

        mock_save = MagicMock()
        monkeypatch.setattr(coo_mod, "save_workflow", mock_save)

        result = coo_mod.deposit_company_knowledge.invoke({
            "category": "workflow",
            "name": "onboarding",
            "content": "# Onboarding Workflow\nStep 1...",
        })

        assert result["status"] == "success"
        assert result["category"] == "workflow"
        mock_save.assert_called_once_with("onboarding", "# Onboarding Workflow\nStep 1...")

    def test_deposit_culture(self, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod, store as store_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)

        # Mock store.load_culture to return empty list initially
        monkeypatch.setattr(store_mod, "load_culture", lambda: [])
        # Mock save_culture (async) — the code uses create_task so mock at store level
        saved_items_captured = []
        async def _mock_save_culture(items):
            saved_items_captured.extend(items)
        monkeypatch.setattr(store_mod, "save_culture", _mock_save_culture)

        import asyncio
        # Provide a running loop so create_task works
        loop = asyncio.new_event_loop()
        monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)

        result = coo_mod.deposit_company_knowledge.invoke({
            "category": "culture",
            "name": "innovation",
            "content": "We value innovation above all",
        })

        # Run pending tasks
        loop.run_until_complete(asyncio.sleep(0))
        loop.close()

        assert result["status"] == "success"
        assert len(saved_items_captured) == 1
        assert saved_items_captured[0]["content"] == "We value innovation above all"

    def test_deposit_sop(self, tmp_path, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "SOP_DIR", tmp_path / "sops")

        result = coo_mod.deposit_company_knowledge.invoke({
            "category": "sop",
            "name": "deploy_process",
            "content": "# Deploy SOP\n1. Build\n2. Test\n3. Deploy",
        })

        assert result["status"] == "success"
        assert (tmp_path / "sops" / "deploy_process.md").exists()

    def test_deposit_guidance(self, tmp_path, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "SHARED_PROMPTS_DIR", tmp_path / "shared")

        result = coo_mod.deposit_company_knowledge.invoke({
            "category": "guidance",
            "name": "code_review",
            "content": "Always review code carefully",
        })

        assert result["status"] == "success"
        assert (tmp_path / "shared" / "code_review.md").exists()

    def test_deposit_direction(self, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)

        mock_save = MagicMock()
        monkeypatch.setattr(coo_mod, "save_company_direction", mock_save)

        result = coo_mod.deposit_company_knowledge.invoke({
            "category": "direction",
            "name": "2026_strategy",
            "content": "Focus on AI products",
        })

        assert result["status"] == "success"
        # Direction is now persisted to disk via save_company_direction, not cs
        mock_save.assert_called_once_with("Focus on AI products")

    def test_invalid_category(self, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)

        result = coo_mod.deposit_company_knowledge.invoke({
            "category": "nonsense",
            "name": "test",
            "content": "test",
        })
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# COOAgent class
# ---------------------------------------------------------------------------

class TestCOOAgent:
    def _make_agent(self, monkeypatch, cs=None, emp_overrides=None):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        if cs is None:
            cs = _make_cs()
        emp = _make_emp(config_mod.COO_ID)
        emps = {config_mod.COO_ID: emp}
        if emp_overrides:
            emps.update(emp_overrides)
        _mock_store_for_employees(monkeypatch, emps)

        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(base_mod, "load_employee_skills", lambda eid: {})
        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", Path("/nonexistent"))
        monkeypatch.setattr(base_mod, "SHARED_PROMPTS_DIR", Path("/nonexistent"))
        monkeypatch.setattr(coo_mod, "create_react_agent", lambda model, tools: MagicMock())

        from onemancompany.agents.coo_agent import COOAgent
        return COOAgent()

    def test_init(self, monkeypatch):
        from onemancompany.core.config import COO_ID

        agent = self._make_agent(monkeypatch)
        assert agent.role == "COO"
        assert agent.employee_id == COO_ID

    def test_build_prompt_contains_coo_prompt(self, monkeypatch):
        agent = self._make_agent(monkeypatch)
        prompt = agent._build_prompt()
        assert "COO" in prompt
        assert "manager" in prompt

    def test_build_prompt_contains_dynamic_context(self, monkeypatch):
        agent = self._make_agent(monkeypatch)
        prompt = agent._build_prompt()
        assert "Current Context" in prompt

    def test_build_prompt_with_culture(self, monkeypatch):
        from onemancompany.core import store as store_mod

        cs = _make_cs()
        agent = self._make_agent(monkeypatch, cs=cs)
        monkeypatch.setattr(store_mod, "load_culture",
                            lambda: [{"content": "Stay humble"}])
        prompt = agent._build_prompt()
        assert "Stay humble" in prompt

    def test_build_prompt_with_skills(self, monkeypatch):
        from onemancompany.agents import base as base_mod

        cs = _make_cs()
        monkeypatch.setattr(
            base_mod, "get_employee_skills_prompt",
            lambda eid: "\n\n## Active Skills\n### Work Principles\nAlways be decisive",
        )
        agent = self._make_agent(monkeypatch, cs=cs)
        prompt = agent._build_prompt()
        assert "Always be decisive" in prompt

    @pytest.mark.asyncio
    async def test_run(self, monkeypatch):
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod, events as events_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        emp = _make_emp(config_mod.COO_ID)
        _mock_store_for_employees(monkeypatch, {config_mod.COO_ID: emp})
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(base_mod, "load_employee_skills", lambda eid: {})
        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", Path("/nonexistent"))
        monkeypatch.setattr(base_mod, "SHARED_PROMPTS_DIR", Path("/nonexistent"))

        mock_publish = AsyncMock()
        monkeypatch.setattr(events_mod, "event_bus", MagicMock(publish=mock_publish))
        monkeypatch.setattr(base_mod, "event_bus", MagicMock(publish=mock_publish))

        monkeypatch.setattr(
            "onemancompany.core.agent_loop._current_loop",
            MagicMock(get=lambda x=None: None),
        )

        final_msg = MagicMock()
        final_msg.content = "Dispatched to engineer"
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": [final_msg]})
        monkeypatch.setattr(coo_mod, "create_react_agent", lambda model, tools: mock_agent)

        from onemancompany.agents.coo_agent import COOAgent
        agent = COOAgent()

        result = await agent.run("Execute project plan")
        assert result == "Dispatched to engineer"
        # _set_status is a no-op now; status persisted via store


# ---------------------------------------------------------------------------
# _persist_tool — folder_name auto-generation (line 167)
# ---------------------------------------------------------------------------

class TestPersistTool:
    def test_persist_tool_without_folder_name(self, tmp_path, monkeypatch):
        """Line 167: _persist_tool generates folder_name when empty."""
        from onemancompany.agents import coo_agent as coo_mod

        monkeypatch.setattr(coo_mod, "TOOLS_DIR", tmp_path / "tools")

        tool = _make_tool("t1", name="My Special Tool", folder_name="")
        coo_mod._persist_tool(tool)

        # folder_name should be auto-generated from name via slugify
        assert tool.folder_name != ""
        assert (tmp_path / "tools" / tool.folder_name / "tool.yaml").exists()

    def test_persist_tool_with_folder_name(self, tmp_path, monkeypatch):
        """_persist_tool uses existing folder_name."""
        from onemancompany.agents import coo_agent as coo_mod

        monkeypatch.setattr(coo_mod, "TOOLS_DIR", tmp_path / "tools")

        tool = _make_tool("t1", name="Tool", folder_name="existing_folder")
        coo_mod._persist_tool(tool)

        assert tool.folder_name == "existing_folder"
        assert (tmp_path / "tools" / "existing_folder" / "tool.yaml").exists()


# ---------------------------------------------------------------------------
# revoke_tool_access — nonexistent tool (line 379)
# ---------------------------------------------------------------------------

class TestRevokeToolAccessNonexistent:
    def test_revoke_nonexistent_tool(self, monkeypatch):
        """Line 379: revoke_tool_access returns error for nonexistent tool."""
        from onemancompany.agents import coo_agent as coo_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(coo_mod, "company_state", cs)

        result = coo_mod.revoke_tool_access.invoke({
            "tool_id": "nonexistent",
            "employee_id": "00010",
        })
        assert result["status"] == "error"
        assert "not found" in result["message"]


