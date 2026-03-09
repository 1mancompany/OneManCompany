"""Unit tests for core/state.py — comprehensive coverage for all uncovered functions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.state import (
    CompanyState,
    Employee,
    MeetingRoom,
    OfficeTool,
    SalesTask,
    TaskEntry,
)


# ---------------------------------------------------------------------------
# TaskEntry
# ---------------------------------------------------------------------------

class TestTaskEntry:
    def test_defaults(self):
        te = TaskEntry(project_id="proj1", task="Build app", routed_to="COO")
        assert te.status == "pending"
        assert te.current_owner == "coo"
        assert te.created_at  # auto-set

    def test_explicit_owner(self):
        te = TaskEntry(
            project_id="proj1", task="Build app", routed_to="COO",
            current_owner="00010",
        )
        assert te.current_owner == "00010"

    def test_to_dict(self):
        te = TaskEntry(
            project_id="proj1", task="Build app", routed_to="HR",
            iteration_id="iter_001", project_dir="/tmp/proj1",
            current_owner="00002", status="queued",
        )
        d = te.to_dict()
        assert d["project_id"] == "proj1"
        assert d["iteration_id"] == "iter_001"
        assert d["task"] == "Build app"
        assert d["routed_to"] == "HR"
        assert d["project_dir"] == "/tmp/proj1"
        assert d["current_owner"] == "00002"
        assert d["status"] == "queued"
        assert "created_at" in d


# ---------------------------------------------------------------------------
# OfficeTool
# ---------------------------------------------------------------------------

class TestOfficeTool:
    def test_defaults(self):
        ot = OfficeTool(id="t1", name="Hammer", description="A hammer", added_by="coo")
        assert ot.desk_position == (0, 0)
        assert ot.sprite == "desk_equipment"
        assert ot.allowed_users == []
        assert ot.files == []
        assert ot.folder_name == ""
        assert ot.has_icon is False

    def test_to_dict(self):
        ot = OfficeTool(
            id="t1", name="Hammer", description="A hammer", added_by="coo",
            desk_position=(3, 5), sprite="hammer_sprite",
            allowed_users=["00010"], files=["readme.md"],
            folder_name="hammer", has_icon=True,
        )
        d = ot.to_dict()
        assert d["id"] == "t1"
        assert d["name"] == "Hammer"
        assert d["description"] == "A hammer"
        assert d["added_by"] == "coo"
        assert d["desk_position"] == [3, 5]
        assert d["sprite"] == "hammer_sprite"
        assert d["allowed_users"] == ["00010"]
        assert d["files"] == ["readme.md"]
        assert d["folder_name"] == "hammer"
        assert d["has_icon"] is True


# ---------------------------------------------------------------------------
# SalesTask
# ---------------------------------------------------------------------------

class TestSalesTask:
    def test_defaults(self):
        st = SalesTask(id="s1", client_name="Acme", description="Build website")
        assert st.status == "pending"
        assert st.assigned_to == ""
        assert st.contract_approved is False
        assert st.delivery == ""
        assert st.settlement_tokens == 0
        assert st.created_at  # auto-set

    def test_explicit_created_at(self):
        st = SalesTask(
            id="s1", client_name="Acme", description="Build website",
            created_at="2025-01-01T00:00:00",
        )
        assert st.created_at == "2025-01-01T00:00:00"

    def test_to_dict(self):
        st = SalesTask(
            id="s1", client_name="Acme", description="Build website",
            requirements="Responsive design", budget_tokens=1000000,
            status="in_production", assigned_to="00005",
            contract_approved=True, delivery="delivered.zip",
            settlement_tokens=500000,
        )
        d = st.to_dict()
        assert d["id"] == "s1"
        assert d["client_name"] == "Acme"
        assert d["description"] == "Build website"
        assert d["requirements"] == "Responsive design"
        assert d["budget_tokens"] == 1000000
        assert d["status"] == "in_production"
        assert d["assigned_to"] == "00005"
        assert d["contract_approved"] is True
        assert d["delivery"] == "delivered.zip"
        assert d["settlement_tokens"] == 500000
        assert "created_at" in d


# ---------------------------------------------------------------------------
# CompanyState.to_json with various content
# ---------------------------------------------------------------------------

class TestCompanyStateToJson:
    def test_with_all_entity_types(self):
        cs = CompanyState()
        cs.employees["00010"] = Employee(
            id="00010", name="Test", role="Engineer", skills=["py"],
        )
        cs.ex_employees["00011"] = Employee(
            id="00011", name="Former", role="Designer", skills=["figma"],
        )
        cs.tools["t1"] = OfficeTool(
            id="t1", name="Tool", description="d", added_by="coo",
        )
        cs.meeting_rooms["r1"] = MeetingRoom(
            id="r1", name="Room A", description="Main room",
        )
        cs.active_tasks.append(
            TaskEntry(project_id="p1", task="Do stuff", routed_to="COO")
        )
        cs.sales_tasks["s1"] = SalesTask(
            id="s1", client_name="Acme", description="Build app",
        )
        cs.company_tokens = 5000

        j = cs.to_json()
        assert len(j["employees"]) == 1
        assert len(j["ex_employees"]) == 1
        assert len(j["tools"]) == 1
        assert len(j["meeting_rooms"]) == 1
        assert len(j["active_tasks"]) == 1
        assert len(j["sales_tasks"]) == 1
        assert j["company_tokens"] == 5000


# ---------------------------------------------------------------------------
# is_idle
# ---------------------------------------------------------------------------

class TestIsIdle:
    def test_idle_when_no_tasks(self, monkeypatch):
        import onemancompany.core.state as state_mod

        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        assert state_mod.is_idle() is True

    def test_not_idle_when_tasks_present(self, monkeypatch):
        import onemancompany.core.state as state_mod

        monkeypatch.setattr(
            state_mod.company_state, "active_tasks",
            [TaskEntry(project_id="p1", task="x", routed_to="COO")]
        )
        assert state_mod.is_idle() is False


# ---------------------------------------------------------------------------
# request_reload
# ---------------------------------------------------------------------------

class TestRequestReload:
    def test_immediate_reload_when_idle(self, monkeypatch):
        import onemancompany.core.state as state_mod

        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        mock_reload = MagicMock(return_value={"status": "ok"})
        monkeypatch.setattr(state_mod, "reload_all_from_disk", mock_reload)

        result = state_mod.request_reload()
        assert result == {"status": "ok"}
        mock_reload.assert_called_once()
        assert state_mod._reload_pending is False

    def test_deferred_when_busy(self, monkeypatch):
        import onemancompany.core.state as state_mod

        monkeypatch.setattr(
            state_mod.company_state, "active_tasks",
            [TaskEntry(project_id="p1", task="x", routed_to="COO")]
        )
        result = state_mod.request_reload()
        assert result["status"] == "deferred"
        assert state_mod._reload_pending is True

        # Clean up
        state_mod._reload_pending = False


# ---------------------------------------------------------------------------
# flush_pending_reload
# ---------------------------------------------------------------------------

class TestFlushPendingReload:
    def test_no_pending(self, monkeypatch):
        import onemancompany.core.state as state_mod

        monkeypatch.setattr(state_mod, "_reload_pending", False)
        result = state_mod.flush_pending_reload()
        assert result is None

    def test_pending_reload_executes(self, monkeypatch):
        import onemancompany.core.state as state_mod

        state_mod._reload_pending = True
        mock_reload = MagicMock(return_value={"status": "reloaded"})
        monkeypatch.setattr(state_mod, "reload_all_from_disk", mock_reload)

        result = state_mod.flush_pending_reload()
        assert result == {"status": "reloaded"}
        mock_reload.assert_called_once()
        assert state_mod._reload_pending is False


# ---------------------------------------------------------------------------
# Helpers for reload_all_from_disk tests
# ---------------------------------------------------------------------------

def _setup_reload_mocks(monkeypatch, config_mod, fresh_configs=None,
                        ex_configs=None, guidance=None, principles="",
                        culture=None, direction=""):
    """Set up all common mocks needed by reload_all_from_disk tests."""
    if fresh_configs is None:
        fresh_configs = {}
    if ex_configs is None:
        ex_configs = {}
    if guidance is None:
        guidance = []
    if culture is None:
        culture = []

    monkeypatch.setattr(config_mod, "employee_configs", {})
    monkeypatch.setattr(config_mod, "load_employee_configs", MagicMock(return_value=fresh_configs))
    monkeypatch.setattr(config_mod, "load_employee_guidance", MagicMock(return_value=guidance))
    monkeypatch.setattr(config_mod, "load_work_principles", MagicMock(return_value=principles))
    monkeypatch.setattr(config_mod, "reload_app_config", MagicMock(return_value={}))
    monkeypatch.setattr(config_mod, "load_ex_employee_configs", MagicMock(return_value=ex_configs))
    monkeypatch.setattr(config_mod, "load_company_culture", MagicMock(return_value=culture))
    monkeypatch.setattr(config_mod, "load_company_direction", MagicMock(return_value=direction))
    monkeypatch.setattr(config_mod, "invalidate_manifest_cache", MagicMock())


def _run_reload(state_mod, monkeypatch, asyncio_has_loop=False):
    """Execute reload_all_from_disk with all external deps mocked."""
    with patch("onemancompany.agents.coo_agent._load_assets_from_disk", MagicMock()):
        monkeypatch.setattr(state_mod, "compute_layout", MagicMock())
        if asyncio_has_loop:
            mock_loop = MagicMock()
            with patch("asyncio.get_running_loop", return_value=mock_loop):
                summary = state_mod.reload_all_from_disk()
            return summary, mock_loop
        else:
            with patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")):
                summary = state_mod.reload_all_from_disk()
            return summary, None


# ---------------------------------------------------------------------------
# reload_all_from_disk
# ---------------------------------------------------------------------------

class TestReloadAllFromDisk:
    def test_reload_updates_existing_employee(self, tmp_path, monkeypatch):
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core.config import EmployeeConfig

        # Pre-populate company_state with an existing employee
        existing_emp = Employee(
            id="00010", name="OldName", role="Engineer", skills=["python"],
            level=1, department="Engineering", nickname="old_nick",
        )
        monkeypatch.setattr(state_mod.company_state, "employees", {"00010": existing_emp})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        # Create employee profile on disk for the YAML-reading path inside reload
        emp_dir = tmp_path / "employees" / "00010"
        emp_dir.mkdir(parents=True)
        import yaml
        profile_data = {
            "name": "NewName", "role": "Engineer", "skills": ["python", "rust"],
            "level": 2, "department": "Engineering", "nickname": "new_nick",
            "desk_position": [1, 2], "sprite": "blue", "permissions": [],
            "current_quarter_tasks": 0, "performance_history": [],
            "remote": False, "probation": False, "onboarding_completed": True,
            "okrs": [], "pip": None,
        }
        with open(emp_dir / "profile.yaml", "w") as f:
            yaml.dump(profile_data, f)

        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", tmp_path / "employees")

        fresh_configs = {
            "00010": EmployeeConfig(
                name="NewName", role="Engineer", skills=["python", "rust"],
                level=2, department="Engineering", nickname="new_nick",
                desk_position=[1, 2], sprite="blue", permissions=[],
                current_quarter_tasks=0, performance_history=[], remote=False,
            )
        }
        _setup_reload_mocks(monkeypatch, config_mod, fresh_configs=fresh_configs)
        summary, _ = _run_reload(state_mod, monkeypatch)

        assert "00010" in state_mod.company_state.employees
        emp = state_mod.company_state.employees["00010"]
        assert emp.name == "NewName"
        assert emp.nickname == "new_nick"
        assert emp.level == 2
        assert emp.skills == ["python", "rust"]
        assert any(u["id"] == "00010" for u in summary["employees_updated"])

    def test_reload_adds_new_employee(self, tmp_path, monkeypatch):
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core.config import EmployeeConfig

        monkeypatch.setattr(state_mod.company_state, "employees", {})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})
        monkeypatch.setattr(state_mod.company_state, "_next_employee_number", 6)

        fresh_configs = {
            "00010": EmployeeConfig(
                name="NewHire", role="Designer", skills=["figma"],
                level=1, department="Design", desk_position=[3, 3],
                sprite="blue", permissions=[], remote=False,
            )
        }
        _setup_reload_mocks(monkeypatch, config_mod, fresh_configs=fresh_configs,
                            guidance=["be creative"], principles="create great work")
        summary, _ = _run_reload(state_mod, monkeypatch)

        assert "00010" in state_mod.company_state.employees
        assert state_mod.company_state.employees["00010"].name == "NewHire"
        assert "00010" in summary["employees_added"]
        # Employee number counter should be updated
        assert state_mod.company_state._next_employee_number == 11

    def test_reload_removes_deleted_employee(self, tmp_path, monkeypatch):
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod

        # Employee exists in state but not on disk
        old_emp = Employee(
            id="00010", name="Deleted", role="Engineer", skills=[],
        )
        monkeypatch.setattr(state_mod.company_state, "employees", {"00010": old_emp})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        _setup_reload_mocks(monkeypatch, config_mod)

        with patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None):
            summary, _ = _run_reload(state_mod, monkeypatch)

        assert "00010" not in state_mod.company_state.employees
        assert "00010" in summary.get("employees_removed", [])

    def test_reload_ex_employees(self, tmp_path, monkeypatch):
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core.config import EmployeeConfig

        monkeypatch.setattr(state_mod.company_state, "employees", {})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        ex_configs = {
            "00020": EmployeeConfig(
                name="ExEmployee", role="Engineer", skills=["java"],
                level=2, department="Engineering", desk_position=[0, 0],
                sprite="default", remote=False,
            )
        }

        _setup_reload_mocks(monkeypatch, config_mod, ex_configs=ex_configs)
        summary, _ = _run_reload(state_mod, monkeypatch)

        assert "00020" in state_mod.company_state.ex_employees
        assert state_mod.company_state.ex_employees["00020"].name == "ExEmployee"

    def test_reload_broadcasts_when_loop_available(self, tmp_path, monkeypatch):
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(state_mod.company_state, "employees", {})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        _setup_reload_mocks(monkeypatch, config_mod)
        summary, mock_loop = _run_reload(state_mod, monkeypatch, asyncio_has_loop=True)

        # create_task should have been called for the broadcast
        mock_loop.create_task.assert_called_once()

    def test_reload_with_non_numeric_employee_id(self, tmp_path, monkeypatch):
        """Test that ValueError on int(emp_num) is handled."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core.config import EmployeeConfig

        monkeypatch.setattr(state_mod.company_state, "employees", {})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})
        monkeypatch.setattr(state_mod.company_state, "_next_employee_number", 6)

        fresh_configs = {
            "alpha": EmployeeConfig(
                name="Alpha", role="Engineer", skills=["python"],
                level=1, department="Engineering", desk_position=[0, 0],
                sprite="default", permissions=[], remote=False,
            )
        }
        _setup_reload_mocks(monkeypatch, config_mod, fresh_configs=fresh_configs)
        summary, _ = _run_reload(state_mod, monkeypatch)

        assert "alpha" in state_mod.company_state.employees
        # _next_employee_number should remain unchanged (ValueError handled)
        assert state_mod.company_state._next_employee_number == 6

    def test_reload_department_changed(self, tmp_path, monkeypatch):
        """Lines 489-490: department field changed detection."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core.config import EmployeeConfig

        existing_emp = Employee(
            id="00010", name="Alice", role="Engineer", skills=["py"],
            level=1, department="OldDept", nickname="alice",
        )
        monkeypatch.setattr(state_mod.company_state, "employees", {"00010": existing_emp})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        emp_dir = tmp_path / "employees" / "00010"
        emp_dir.mkdir(parents=True)
        import yaml
        with open(emp_dir / "profile.yaml", "w") as f:
            yaml.dump({"probation": False, "onboarding_completed": True, "okrs": [], "pip": None}, f)
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", tmp_path / "employees")

        fresh_configs = {
            "00010": EmployeeConfig(
                name="Alice", role="Engineer", skills=["py"],
                level=1, department="NewDept", nickname="alice",
                desk_position=[0, 0], sprite="default", permissions=[], remote=False,
            )
        }
        _setup_reload_mocks(monkeypatch, config_mod, fresh_configs=fresh_configs)
        summary, _ = _run_reload(state_mod, monkeypatch)

        emp = state_mod.company_state.employees["00010"]
        assert emp.department == "NewDept"
        updated = [u for u in summary["employees_updated"] if u["id"] == "00010"]
        assert len(updated) == 1
        assert "department" in updated[0]["fields"]

    def test_reload_role_changed(self, tmp_path, monkeypatch):
        """Lines 492-493: role field changed detection."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core.config import EmployeeConfig

        existing_emp = Employee(
            id="00010", name="Bob", role="Engineer", skills=["py"],
            level=1, department="Eng", nickname="bob",
        )
        monkeypatch.setattr(state_mod.company_state, "employees", {"00010": existing_emp})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        emp_dir = tmp_path / "employees" / "00010"
        emp_dir.mkdir(parents=True)
        import yaml
        with open(emp_dir / "profile.yaml", "w") as f:
            yaml.dump({"probation": False, "onboarding_completed": True, "okrs": [], "pip": None}, f)
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", tmp_path / "employees")

        fresh_configs = {
            "00010": EmployeeConfig(
                name="Bob", role="Designer", skills=["py"],
                level=1, department="Eng", nickname="bob",
                desk_position=[0, 0], sprite="default", permissions=[], remote=False,
            )
        }
        _setup_reload_mocks(monkeypatch, config_mod, fresh_configs=fresh_configs)
        summary, _ = _run_reload(state_mod, monkeypatch)

        emp = state_mod.company_state.employees["00010"]
        assert emp.role == "Designer"
        updated = [u for u in summary["employees_updated"] if u["id"] == "00010"]
        assert "role" in updated[0]["fields"]

    def test_reload_current_quarter_tasks_changed(self, tmp_path, monkeypatch):
        """Lines 498-499: current_quarter_tasks field changed detection."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core.config import EmployeeConfig

        existing_emp = Employee(
            id="00010", name="Carol", role="Engineer", skills=["py"],
            level=1, department="Eng", nickname="carol",
            current_quarter_tasks=0,
        )
        monkeypatch.setattr(state_mod.company_state, "employees", {"00010": existing_emp})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        emp_dir = tmp_path / "employees" / "00010"
        emp_dir.mkdir(parents=True)
        import yaml
        with open(emp_dir / "profile.yaml", "w") as f:
            yaml.dump({"probation": False, "onboarding_completed": True, "okrs": [], "pip": None}, f)
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", tmp_path / "employees")

        fresh_configs = {
            "00010": EmployeeConfig(
                name="Carol", role="Engineer", skills=["py"],
                level=1, department="Eng", nickname="carol",
                desk_position=[0, 0], sprite="default", permissions=[], remote=False,
                current_quarter_tasks=5,
            )
        }
        _setup_reload_mocks(monkeypatch, config_mod, fresh_configs=fresh_configs)
        summary, _ = _run_reload(state_mod, monkeypatch)

        emp = state_mod.company_state.employees["00010"]
        assert emp.current_quarter_tasks == 5
        updated = [u for u in summary["employees_updated"] if u["id"] == "00010"]
        assert "current_quarter_tasks" in updated[0]["fields"]

    def test_reload_performance_history_changed(self, tmp_path, monkeypatch):
        """Lines 501-502: performance_history field changed detection."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core.config import EmployeeConfig

        existing_emp = Employee(
            id="00010", name="Dave", role="Engineer", skills=["py"],
            level=1, department="Eng", nickname="dave",
            performance_history=[],
        )
        monkeypatch.setattr(state_mod.company_state, "employees", {"00010": existing_emp})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        emp_dir = tmp_path / "employees" / "00010"
        emp_dir.mkdir(parents=True)
        import yaml
        with open(emp_dir / "profile.yaml", "w") as f:
            yaml.dump({"probation": False, "onboarding_completed": True, "okrs": [], "pip": None}, f)
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", tmp_path / "employees")

        new_history = [{"quarter": "Q1", "score": 4.5}]
        fresh_configs = {
            "00010": EmployeeConfig(
                name="Dave", role="Engineer", skills=["py"],
                level=1, department="Eng", nickname="dave",
                desk_position=[0, 0], sprite="default", permissions=[], remote=False,
                performance_history=new_history,
            )
        }
        _setup_reload_mocks(monkeypatch, config_mod, fresh_configs=fresh_configs)
        summary, _ = _run_reload(state_mod, monkeypatch)

        emp = state_mod.company_state.employees["00010"]
        assert emp.performance_history == new_history
        updated = [u for u in summary["employees_updated"] if u["id"] == "00010"]
        assert "performance_history" in updated[0]["fields"]

    def test_reload_permissions_changed(self, tmp_path, monkeypatch):
        """Lines 504-505: permissions field changed detection."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core.config import EmployeeConfig

        existing_emp = Employee(
            id="00010", name="Eve", role="Engineer", skills=["py"],
            level=1, department="Eng", nickname="eve",
            permissions=[],
        )
        monkeypatch.setattr(state_mod.company_state, "employees", {"00010": existing_emp})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        emp_dir = tmp_path / "employees" / "00010"
        emp_dir.mkdir(parents=True)
        import yaml
        with open(emp_dir / "profile.yaml", "w") as f:
            yaml.dump({"probation": False, "onboarding_completed": True, "okrs": [], "pip": None}, f)
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", tmp_path / "employees")

        fresh_configs = {
            "00010": EmployeeConfig(
                name="Eve", role="Engineer", skills=["py"],
                level=1, department="Eng", nickname="eve",
                desk_position=[0, 0], sprite="default",
                permissions=["web_search", "company_file_access"],
                remote=False,
            )
        }
        _setup_reload_mocks(monkeypatch, config_mod, fresh_configs=fresh_configs)
        summary, _ = _run_reload(state_mod, monkeypatch)

        emp = state_mod.company_state.employees["00010"]
        assert emp.permissions == ["web_search", "company_file_access"]
        updated = [u for u in summary["employees_updated"] if u["id"] == "00010"]
        assert "permissions" in updated[0]["fields"]

    def test_reload_guidance_notes_changed(self, tmp_path, monkeypatch):
        """Lines 507-508: guidance_notes field changed detection."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core.config import EmployeeConfig

        existing_emp = Employee(
            id="00010", name="Frank", role="Engineer", skills=["py"],
            level=1, department="Eng", nickname="frank",
            guidance_notes=[],
        )
        monkeypatch.setattr(state_mod.company_state, "employees", {"00010": existing_emp})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        emp_dir = tmp_path / "employees" / "00010"
        emp_dir.mkdir(parents=True)
        import yaml
        with open(emp_dir / "profile.yaml", "w") as f:
            yaml.dump({"probation": False, "onboarding_completed": True, "okrs": [], "pip": None}, f)
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", tmp_path / "employees")

        fresh_configs = {
            "00010": EmployeeConfig(
                name="Frank", role="Engineer", skills=["py"],
                level=1, department="Eng", nickname="frank",
                desk_position=[0, 0], sprite="default", permissions=[], remote=False,
            )
        }
        new_guidance = ["Focus on code quality", "Review PRs daily"]
        _setup_reload_mocks(monkeypatch, config_mod, fresh_configs=fresh_configs,
                            guidance=new_guidance)
        summary, _ = _run_reload(state_mod, monkeypatch)

        emp = state_mod.company_state.employees["00010"]
        assert emp.guidance_notes == new_guidance
        updated = [u for u in summary["employees_updated"] if u["id"] == "00010"]
        assert "guidance_notes" in updated[0]["fields"]

    def test_reload_work_principles_changed(self, tmp_path, monkeypatch):
        """Lines 510-511: work_principles field changed detection."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core.config import EmployeeConfig

        existing_emp = Employee(
            id="00010", name="Grace", role="Engineer", skills=["py"],
            level=1, department="Eng", nickname="grace",
            work_principles="",
        )
        monkeypatch.setattr(state_mod.company_state, "employees", {"00010": existing_emp})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        emp_dir = tmp_path / "employees" / "00010"
        emp_dir.mkdir(parents=True)
        import yaml
        with open(emp_dir / "profile.yaml", "w") as f:
            yaml.dump({"probation": False, "onboarding_completed": True, "okrs": [], "pip": None}, f)
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", tmp_path / "employees")

        fresh_configs = {
            "00010": EmployeeConfig(
                name="Grace", role="Engineer", skills=["py"],
                level=1, department="Eng", nickname="grace",
                desk_position=[0, 0], sprite="default", permissions=[], remote=False,
            )
        }
        _setup_reload_mocks(monkeypatch, config_mod, fresh_configs=fresh_configs,
                            principles="Always write tests first")
        summary, _ = _run_reload(state_mod, monkeypatch)

        emp = state_mod.company_state.employees["00010"]
        assert emp.work_principles == "Always write tests first"
        updated = [u for u in summary["employees_updated"] if u["id"] == "00010"]
        assert "work_principles" in updated[0]["fields"]

    def test_reload_remote_changed(self, tmp_path, monkeypatch):
        """Lines 513-514: remote field changed detection."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core.config import EmployeeConfig

        existing_emp = Employee(
            id="00010", name="Hank", role="Engineer", skills=["py"],
            level=1, department="Eng", nickname="hank",
            remote=False,
        )
        monkeypatch.setattr(state_mod.company_state, "employees", {"00010": existing_emp})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        emp_dir = tmp_path / "employees" / "00010"
        emp_dir.mkdir(parents=True)
        import yaml
        with open(emp_dir / "profile.yaml", "w") as f:
            yaml.dump({"probation": False, "onboarding_completed": True, "okrs": [], "pip": None}, f)
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", tmp_path / "employees")

        fresh_configs = {
            "00010": EmployeeConfig(
                name="Hank", role="Engineer", skills=["py"],
                level=1, department="Eng", nickname="hank",
                desk_position=[0, 0], sprite="default", permissions=[],
                remote=True,
            )
        }
        _setup_reload_mocks(monkeypatch, config_mod, fresh_configs=fresh_configs)
        summary, _ = _run_reload(state_mod, monkeypatch)

        emp = state_mod.company_state.employees["00010"]
        assert emp.remote is True
        updated = [u for u in summary["employees_updated"] if u["id"] == "00010"]
        assert "remote" in updated[0]["fields"]

    def test_reload_probation_changed(self, tmp_path, monkeypatch):
        """Lines 527-528: probation field changed detection."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core.config import EmployeeConfig

        existing_emp = Employee(
            id="00010", name="Iris", role="Engineer", skills=["py"],
            level=1, department="Eng", nickname="iris",
            probation=False,
        )
        monkeypatch.setattr(state_mod.company_state, "employees", {"00010": existing_emp})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        emp_dir = tmp_path / "employees" / "00010"
        emp_dir.mkdir(parents=True)
        import yaml
        with open(emp_dir / "profile.yaml", "w") as f:
            yaml.dump({"probation": True, "onboarding_completed": True, "okrs": [], "pip": None}, f)
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", tmp_path / "employees")

        fresh_configs = {
            "00010": EmployeeConfig(
                name="Iris", role="Engineer", skills=["py"],
                level=1, department="Eng", nickname="iris",
                desk_position=[0, 0], sprite="default", permissions=[], remote=False,
            )
        }
        _setup_reload_mocks(monkeypatch, config_mod, fresh_configs=fresh_configs)
        summary, _ = _run_reload(state_mod, monkeypatch)

        emp = state_mod.company_state.employees["00010"]
        assert emp.probation is True
        updated = [u for u in summary["employees_updated"] if u["id"] == "00010"]
        assert "probation" in updated[0]["fields"]

    def test_reload_onboarding_completed_changed(self, tmp_path, monkeypatch):
        """Lines 530-531: onboarding_completed field changed detection."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core.config import EmployeeConfig

        existing_emp = Employee(
            id="00010", name="Jack", role="Engineer", skills=["py"],
            level=1, department="Eng", nickname="jack",
            onboarding_completed=True,
        )
        monkeypatch.setattr(state_mod.company_state, "employees", {"00010": existing_emp})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        emp_dir = tmp_path / "employees" / "00010"
        emp_dir.mkdir(parents=True)
        import yaml
        with open(emp_dir / "profile.yaml", "w") as f:
            yaml.dump({"probation": False, "onboarding_completed": False, "okrs": [], "pip": None}, f)
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", tmp_path / "employees")

        fresh_configs = {
            "00010": EmployeeConfig(
                name="Jack", role="Engineer", skills=["py"],
                level=1, department="Eng", nickname="jack",
                desk_position=[0, 0], sprite="default", permissions=[], remote=False,
            )
        }
        _setup_reload_mocks(monkeypatch, config_mod, fresh_configs=fresh_configs)
        summary, _ = _run_reload(state_mod, monkeypatch)

        emp = state_mod.company_state.employees["00010"]
        assert emp.onboarding_completed is False
        updated = [u for u in summary["employees_updated"] if u["id"] == "00010"]
        assert "onboarding_completed" in updated[0]["fields"]

    def test_reload_okrs_changed(self, tmp_path, monkeypatch):
        """Lines 533-534: okrs field changed detection."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core.config import EmployeeConfig

        existing_emp = Employee(
            id="00010", name="Kate", role="Engineer", skills=["py"],
            level=1, department="Eng", nickname="kate",
            okrs=[],
        )
        monkeypatch.setattr(state_mod.company_state, "employees", {"00010": existing_emp})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        emp_dir = tmp_path / "employees" / "00010"
        emp_dir.mkdir(parents=True)
        import yaml
        new_okrs = [{"objective": "Ship v2", "key_results": ["Launch beta"]}]
        with open(emp_dir / "profile.yaml", "w") as f:
            yaml.dump({"probation": False, "onboarding_completed": True, "okrs": new_okrs, "pip": None}, f)
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", tmp_path / "employees")

        fresh_configs = {
            "00010": EmployeeConfig(
                name="Kate", role="Engineer", skills=["py"],
                level=1, department="Eng", nickname="kate",
                desk_position=[0, 0], sprite="default", permissions=[], remote=False,
            )
        }
        _setup_reload_mocks(monkeypatch, config_mod, fresh_configs=fresh_configs)
        summary, _ = _run_reload(state_mod, monkeypatch)

        emp = state_mod.company_state.employees["00010"]
        assert emp.okrs == new_okrs
        updated = [u for u in summary["employees_updated"] if u["id"] == "00010"]
        assert "okrs" in updated[0]["fields"]

    def test_reload_pip_changed(self, tmp_path, monkeypatch):
        """Lines 536-537: pip field changed detection."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core.config import EmployeeConfig

        existing_emp = Employee(
            id="00010", name="Leo", role="Engineer", skills=["py"],
            level=1, department="Eng", nickname="leo",
            pip=None,
        )
        monkeypatch.setattr(state_mod.company_state, "employees", {"00010": existing_emp})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        emp_dir = tmp_path / "employees" / "00010"
        emp_dir.mkdir(parents=True)
        import yaml
        new_pip = {"reason": "Performance below expectations", "deadline": "2026-04-01"}
        with open(emp_dir / "profile.yaml", "w") as f:
            yaml.dump({"probation": False, "onboarding_completed": True, "okrs": [], "pip": new_pip}, f)
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", tmp_path / "employees")

        fresh_configs = {
            "00010": EmployeeConfig(
                name="Leo", role="Engineer", skills=["py"],
                level=1, department="Eng", nickname="leo",
                desk_position=[0, 0], sprite="default", permissions=[], remote=False,
            )
        }
        _setup_reload_mocks(monkeypatch, config_mod, fresh_configs=fresh_configs)
        summary, _ = _run_reload(state_mod, monkeypatch)

        emp = state_mod.company_state.employees["00010"]
        assert emp.pip == new_pip
        updated = [u for u in summary["employees_updated"] if u["id"] == "00010"]
        assert "pip" in updated[0]["fields"]

    def test_reload_removes_employee_and_pops_agent_loop(self, tmp_path, monkeypatch):
        """Lines 580-582: Employee removed from disk — agent_loops.pop called."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod

        old_emp = Employee(
            id="00010", name="Removed", role="Engineer", skills=[],
        )
        monkeypatch.setattr(state_mod.company_state, "employees", {"00010": old_emp})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        _setup_reload_mocks(monkeypatch, config_mod)

        mock_handle = MagicMock()
        mock_vessels = {"00010": mock_handle}
        mock_manager = MagicMock()
        mock_manager.vessels = mock_vessels
        with patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_handle), \
             patch("onemancompany.core.agent_loop.employee_manager", mock_manager):
            summary, _ = _run_reload(state_mod, monkeypatch)

        assert "00010" not in state_mod.company_state.employees
        assert "00010" in summary.get("employees_removed", [])
        # vessels should have been popped
        assert "00010" not in mock_vessels

    def test_reload_removes_employee_agent_loop_exception(self, tmp_path, monkeypatch):
        """Lines 581-582: except Exception: pass when agent_loop import/call fails."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod

        old_emp = Employee(
            id="00010", name="Removed", role="Engineer", skills=[],
        )
        monkeypatch.setattr(state_mod.company_state, "employees", {"00010": old_emp})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        _setup_reload_mocks(monkeypatch, config_mod)

        # Make get_agent_loop raise an exception to trigger the except branch
        with patch("onemancompany.core.agent_loop.get_agent_loop", side_effect=RuntimeError("agent loop error")):
            summary, _ = _run_reload(state_mod, monkeypatch)

        assert "00010" not in state_mod.company_state.employees
        assert "00010" in summary.get("employees_removed", [])

    def test_reload_broadcast_coroutine_body(self, tmp_path, monkeypatch):
        """Line 626: cover the _broadcast() coroutine body (event_bus.publish)."""
        import asyncio
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        import onemancompany.core.events as events_mod

        monkeypatch.setattr(state_mod.company_state, "employees", {})
        monkeypatch.setattr(state_mod.company_state, "active_tasks", [])
        monkeypatch.setattr(state_mod.company_state, "ex_employees", {})

        _setup_reload_mocks(monkeypatch, config_mod)

        # We need to capture the coroutine passed to create_task and actually await it
        captured_coro = None

        def capture_create_task(coro):
            nonlocal captured_coro
            captured_coro = coro

        mock_loop = MagicMock()
        mock_loop.create_task = capture_create_task

        # Patch event_bus at the source module BEFORE reload_all_from_disk runs,
        # so the local import inside the function picks up the mock
        mock_event_bus = MagicMock()
        mock_event_bus.publish = AsyncMock()
        monkeypatch.setattr(events_mod, "event_bus", mock_event_bus)

        with patch("onemancompany.agents.coo_agent._load_assets_from_disk", MagicMock()):
            monkeypatch.setattr(state_mod, "compute_layout", MagicMock())
            with patch("asyncio.get_running_loop", return_value=mock_loop):
                summary = state_mod.reload_all_from_disk()

        # Now actually run the captured coroutine to cover line 626
        assert captured_coro is not None
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(captured_coro)
        finally:
            loop.close()

        mock_event_bus.publish.assert_called_once()


# ---------------------------------------------------------------------------
# _seed_employees (lines 287-311 fallback path, lines 324-325 ValueError)
# ---------------------------------------------------------------------------

class TestSeedEmployees:
    def test_seed_fallback_when_no_configs(self, monkeypatch):
        """Lines 287-311: _seed_employees() fallback when employee_configs is empty."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod

        # Clear existing employees so we can verify the fallback populates them
        monkeypatch.setattr(state_mod.company_state, "employees", {})

        # Make employee_configs empty so the fallback path is taken
        monkeypatch.setattr(config_mod, "employee_configs", {})

        state_mod._seed_employees()

        # Verify all four founding employees were created
        assert config_mod.HR_ID in state_mod.company_state.employees
        assert config_mod.COO_ID in state_mod.company_state.employees
        assert config_mod.EA_ID in state_mod.company_state.employees
        assert config_mod.CSO_ID in state_mod.company_state.employees

        hr = state_mod.company_state.employees[config_mod.HR_ID]
        assert hr.name == "Sam HR"
        assert hr.role == "HR"
        assert hr.department == "HR"
        assert hr.desk_position == (3, 2)
        assert hr.sprite == "hr"

        coo = state_mod.company_state.employees[config_mod.COO_ID]
        assert coo.name == "Alex COO"
        assert coo.role == "COO"
        assert coo.department == "Operations"

        ea = state_mod.company_state.employees[config_mod.EA_ID]
        assert ea.name == "Pat EA"
        assert ea.role == "EA"
        assert ea.department == "CEO Office"

        cso = state_mod.company_state.employees[config_mod.CSO_ID]
        assert cso.name == "Morgan CSO"
        assert cso.role == "CSO"
        assert cso.department == "Sales"

    def test_seed_with_non_numeric_employee_id(self, tmp_path, monkeypatch):
        """Lines 324-325: _seed_employees() ValueError when int(emp_num) fails."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core.config import EmployeeConfig

        monkeypatch.setattr(state_mod.company_state, "employees", {})
        monkeypatch.setattr(state_mod.company_state, "_next_employee_number", 0)

        # Create a non-numeric employee folder on disk for the YAML path
        emp_dir = tmp_path / "employees" / "alpha"
        emp_dir.mkdir(parents=True)
        import yaml
        with open(emp_dir / "profile.yaml", "w") as f:
            yaml.dump({"probation": False, "onboarding_completed": True}, f)

        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", tmp_path / "employees")

        mock_cfg = EmployeeConfig(
            name="AlphaWorker", role="Engineer", skills=["python"],
            level=1, department="Eng", desk_position=[0, 0],
            sprite="default", permissions=[], remote=False,
        )
        monkeypatch.setattr(config_mod, "employee_configs", {"alpha": mock_cfg})
        monkeypatch.setattr(config_mod, "load_employee_guidance", MagicMock(return_value=[]))
        monkeypatch.setattr(config_mod, "load_work_principles", MagicMock(return_value=""))

        state_mod._seed_employees()

        assert "alpha" in state_mod.company_state.employees
        assert state_mod.company_state.employees["alpha"].name == "AlphaWorker"
        # _next_employee_number stays at 6 (set at line 313), not changed by ValueError
        assert state_mod.company_state._next_employee_number == 6


# ---------------------------------------------------------------------------
# make_title (line 123: founding level branch)
# ---------------------------------------------------------------------------

class TestMakeTitle:
    def test_founding_level(self):
        """Line 123: make_title when level >= FOUNDING_LEVEL returns LEVEL_NAMES value."""
        from onemancompany.core.state import make_title
        # Level 4 = Founding
        assert make_title(4, "Engineer") == "Founding"
        # Level 5 = CEO
        assert make_title(5, "COO") == "CEO"

    def test_founding_level_unknown(self):
        """Line 123: make_title when level >= FOUNDING_LEVEL but not in LEVEL_NAMES returns empty string."""
        from onemancompany.core.state import make_title
        assert make_title(99, "Engineer") == ""

    def test_normal_level(self):
        """Levels below founding generate 'Prefix Role' titles."""
        from onemancompany.core.state import make_title
        assert make_title(1, "Engineer") == "Junior Engineer"
        assert make_title(2, "Analyst") == "Mid Analyst"
        assert make_title(3, "Designer") == "Senior Designer"

    def test_unknown_level_prefix(self):
        """Level not in LEVEL_NAMES but below founding uses 'Lv.N' prefix."""
        from onemancompany.core.state import make_title
        assert make_title(0, "Engineer") == "Lv.0 Engineer"


# ---------------------------------------------------------------------------
# Employee.latest_score (lines 166-168)
# ---------------------------------------------------------------------------

class TestEmployeeLatestScore:
    def test_latest_score_with_history(self):
        """Line 166-167: returns last entry's score."""
        emp = Employee(
            id="00010", name="Test", role="Engineer", skills=["py"],
            performance_history=[{"score": 2.0}, {"score": 4.8}],
        )
        assert emp.latest_score == 4.8

    def test_latest_score_no_history(self):
        """Line 168: returns 3.5 default when no performance history."""
        emp = Employee(
            id="00010", name="Test", role="Engineer", skills=["py"],
            performance_history=[],
        )
        assert emp.latest_score == 3.5

    def test_latest_score_missing_score_key(self):
        """Line 167: .get("score", 3.5) fallback when entry has no score key."""
        emp = Employee(
            id="00010", name="Test", role="Engineer", skills=["py"],
            performance_history=[{"quarter": "Q1"}],
        )
        assert emp.latest_score == 3.5


# ---------------------------------------------------------------------------
# CompanyState.next_employee_number (lines 269-271)
# ---------------------------------------------------------------------------

class TestNextEmployeeNumber:
    def test_next_employee_number(self):
        """Lines 269-271: generates zero-padded 5-digit employee number."""
        cs = CompanyState()
        cs._next_employee_number = 6
        assert cs.next_employee_number() == "00006"
        assert cs.next_employee_number() == "00007"
        assert cs._next_employee_number == 8

    def test_next_employee_number_large(self):
        """Handles larger numbers correctly."""
        cs = CompanyState()
        cs._next_employee_number = 99999
        assert cs.next_employee_number() == "99999"
        assert cs._next_employee_number == 100000
