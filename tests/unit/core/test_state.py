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
    def test_with_all_entity_types(self, monkeypatch):
        from onemancompany.core import store as store_mod

        cs = CompanyState()
        cs.tools["t1"] = OfficeTool(
            id="t1", name="Tool", description="d", added_by="coo",
        )
        cs.meeting_rooms["r1"] = MeetingRoom(
            id="r1", name="Room A", description="Main room",
        )
        cs.sales_tasks["s1"] = SalesTask(
            id="s1", client_name="Acme", description="Build app",
        )
        cs.company_tokens = 5000

        # Mock store reads for employees, ex_employees, activity_log, culture
        monkeypatch.setattr(store_mod, "load_all_employees",
                            lambda: {"00010": {"id": "00010", "name": "Test", "role": "Engineer"}})
        monkeypatch.setattr(store_mod, "load_ex_employees",
                            lambda: {"00011": {"id": "00011", "name": "Former", "role": "Designer"}})
        monkeypatch.setattr(store_mod, "load_activity_log", lambda: [])
        monkeypatch.setattr(store_mod, "load_culture", lambda: [])

        mock_task = TaskEntry(project_id="p1", task="Do stuff", routed_to="COO")
        with patch("onemancompany.core.state.get_active_tasks", return_value=[mock_task]):
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

        monkeypatch.setattr(state_mod, "get_active_tasks", lambda: [])
        assert state_mod.is_idle() is True

    def test_not_idle_when_tasks_present(self, monkeypatch):
        import onemancompany.core.state as state_mod

        monkeypatch.setattr(
            state_mod, "get_active_tasks",
            lambda: [TaskEntry(project_id="p1", task="x", routed_to="COO")]
        )
        assert state_mod.is_idle() is False


# ---------------------------------------------------------------------------
# request_reload
# ---------------------------------------------------------------------------

class TestRequestReload:
    def test_immediate_reload_when_idle(self, monkeypatch):
        import onemancompany.core.state as state_mod

        monkeypatch.setattr(state_mod, "get_active_tasks", lambda: [])
        mock_reload = MagicMock(return_value={"status": "ok"})
        monkeypatch.setattr(state_mod, "reload_all_from_disk", mock_reload)

        result = state_mod.request_reload()
        assert result == {"status": "ok"}
        mock_reload.assert_called_once()
        assert state_mod._reload_pending is False

    def test_deferred_when_busy(self, monkeypatch):
        import onemancompany.core.state as state_mod

        monkeypatch.setattr(
            state_mod, "get_active_tasks",
            lambda: [TaskEntry(project_id="p1", task="x", routed_to="COO")]
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
# reload_all_from_disk — now just marks data dirty for next sync tick
# ---------------------------------------------------------------------------

class TestReloadAllFromDisk:
    def test_reload_marks_dirty(self, tmp_path, monkeypatch):
        """reload_all_from_disk now marks all data categories dirty for next sync tick."""
        import onemancompany.core.state as state_mod
        import onemancompany.core.config as config_mod
        from onemancompany.core import store as store_mod

        dirty_calls = []
        monkeypatch.setattr(store_mod, "mark_dirty", lambda *args: dirty_calls.extend(args))
        monkeypatch.setattr(config_mod, "reload_app_config", lambda: None)
        monkeypatch.setattr(config_mod, "invalidate_manifest_cache", lambda: None)
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        (tmp_path / "employees").mkdir()

        state_mod.reload_all_from_disk()
        assert "employees" in dirty_calls
        assert "ex_employees" in dirty_calls
        assert "activity_log" in dirty_calls




# ---------------------------------------------------------------------------
# _seed_employees (lines 287-311 fallback path, lines 324-325 ValueError)
# ---------------------------------------------------------------------------

class TestInitEmployeeCounter:
    def test_counter_from_empty_dir(self, tmp_path, monkeypatch):
        """_init_employee_counter sets counter to 6 when no employee dirs exist."""
        import onemancompany.core.state as state_mod

        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", tmp_path / "employees")
        (tmp_path / "employees").mkdir()
        state_mod.company_state._next_employee_number = 0

        state_mod._init_employee_counter()
        assert state_mod.company_state._next_employee_number == 6

    def test_counter_from_existing_employees(self, tmp_path, monkeypatch):
        """_init_employee_counter sets counter to max employee number + 1."""
        import onemancompany.core.state as state_mod

        emp_dir = tmp_path / "employees"
        emp_dir.mkdir()
        (emp_dir / "00010").mkdir()
        (emp_dir / "00020").mkdir()

        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", emp_dir)
        state_mod.company_state._next_employee_number = 0

        state_mod._init_employee_counter()
        assert state_mod.company_state._next_employee_number == 21

    def test_counter_skips_non_numeric_dirs(self, tmp_path, monkeypatch):
        """Non-numeric directory names are skipped without error."""
        import onemancompany.core.state as state_mod

        emp_dir = tmp_path / "employees"
        emp_dir.mkdir()
        (emp_dir / "alpha").mkdir()
        (emp_dir / "00008").mkdir()

        monkeypatch.setattr(state_mod, "EMPLOYEES_DIR", emp_dir)
        state_mod.company_state._next_employee_number = 0

        state_mod._init_employee_counter()
        assert state_mod.company_state._next_employee_number == 9


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
