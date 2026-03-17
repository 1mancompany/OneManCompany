"""Unit tests for core/layout.py — desk assignment for new hires."""

from __future__ import annotations

import pytest

from onemancompany.core.config import (
    DEPT_DESK_ROWS,
    DEPT_START_ROW,
    HR_ID,
    COO_ID,
    EA_ID,
    CSO_ID,
)
from onemancompany.core.state import CompanyState, Employee


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_emp(emp_id: str, department: str, desk_pos: tuple = (0, 0), **kw) -> Employee:
    return Employee(
        id=emp_id, name=f"Emp {emp_id}", role="Engineer",
        skills=["python"], department=department,
        employee_number=emp_id, desk_position=desk_pos,
        **kw,
    )


# ---------------------------------------------------------------------------
# get_next_desk_for_department
# ---------------------------------------------------------------------------

class TestGetNextDeskForDepartment:
    def test_first_desk_in_empty_department(self, monkeypatch):
        from onemancompany.core.layout import get_next_desk_for_department
        from onemancompany.core import state as state_mod

        cs = CompanyState()
        monkeypatch.setattr(state_mod, "company_state", cs)
        pos = get_next_desk_for_department(cs, "Engineering")
        # Should return a valid position (col, row) within a zone
        assert isinstance(pos, tuple)
        assert len(pos) == 2
        assert pos[1] in DEPT_DESK_ROWS

    def test_avoids_occupied_positions(self, monkeypatch):
        from onemancompany.core.layout import get_next_desk_for_department
        from onemancompany.core import state as state_mod

        cs = CompanyState()
        monkeypatch.setattr(state_mod, "company_state", cs)
        # Add employee to occupy the first desk position
        first_pos = get_next_desk_for_department(cs, "Engineering")
        cs.employees["00010"] = _make_emp("00010", "Engineering", desk_pos=first_pos)

        second_pos = get_next_desk_for_department(cs, "Engineering")
        assert second_pos != first_pos

    def test_remote_employees_dont_occupy_desks(self, monkeypatch):
        from onemancompany.core.layout import get_next_desk_for_department
        from onemancompany.core import state as state_mod

        cs = CompanyState()
        monkeypatch.setattr(state_mod, "company_state", cs)
        # Add remote employee — should not block desk positions
        cs.employees["00010"] = _make_emp(
            "00010", "Engineering", desk_pos=(5, 3), remote=True,
        )

        pos = get_next_desk_for_department(cs, "Engineering")
        # The remote employee's position should be available
        assert isinstance(pos, tuple)

    def test_executives_excluded_from_department(self, monkeypatch):
        from onemancompany.core.layout import get_next_desk_for_department
        from onemancompany.core import state as state_mod

        cs = CompanyState()
        monkeypatch.setattr(state_mod, "company_state", cs)
        # Add executive — should not be counted in department layout
        cs.employees[HR_ID] = Employee(
            id=HR_ID, name="HR", role="HR", skills=[],
            department="HR", employee_number=HR_ID,
        )

        pos = get_next_desk_for_department(cs, "Engineering")
        assert isinstance(pos, tuple)


# ---------------------------------------------------------------------------
# compute_layout
# ---------------------------------------------------------------------------

class TestComputeLayout:
    def test_empty_state_produces_layout(self, monkeypatch):
        from onemancompany.core.layout import compute_layout
        from onemancompany.core import state as state_mod

        cs = CompanyState()
        monkeypatch.setattr(state_mod, "company_state", cs)
        layout = compute_layout(cs)
        assert "zones" in layout
        assert "executive_row" in layout
        assert "canvas_rows" in layout

    def test_assigns_desk_positions_to_employees(self, monkeypatch):
        from unittest.mock import patch as _patch
        from onemancompany.core.layout import compute_layout
        from onemancompany.core import state as state_mod

        cs = CompanyState()
        cs.employees["00010"] = _make_emp("00010", "Engineering")
        cs.employees["00011"] = _make_emp("00011", "Engineering")
        monkeypatch.setattr(state_mod, "company_state", cs)

        persisted = {}
        def capture_persist(updates):
            persisted.update(updates)

        with _patch("onemancompany.core.layout._persist_positions", side_effect=capture_persist):
            compute_layout(cs)

        # Positions should be assigned (not default 0,0)
        assert "00010" in persisted
        assert "00011" in persisted
        assert tuple(persisted["00010"]) != (0, 0)
        assert tuple(persisted["00011"]) != (0, 0)
        assert persisted["00010"] != persisted["00011"]

    def test_multiple_departments_get_separate_zones(self, monkeypatch):
        from onemancompany.core.layout import compute_layout
        from onemancompany.core import state as state_mod

        cs = CompanyState()
        cs.employees["00010"] = _make_emp("00010", "Engineering")
        cs.employees["00011"] = _make_emp("00011", "Design")
        monkeypatch.setattr(state_mod, "company_state", cs)

        layout = compute_layout(cs)
        zones = layout["zones"]
        assert len(zones) == 2
        dept_names = {z["department"] for z in zones}
        assert dept_names == {"Engineering", "Design"}

    def test_executive_positions_assigned(self, monkeypatch):
        from onemancompany.core.layout import compute_layout
        from onemancompany.core import state as state_mod

        cs = CompanyState()
        cs.employees[HR_ID] = Employee(
            id=HR_ID, name="HR", role="HR", skills=[],
            department="HR", employee_number=HR_ID,
        )
        cs.employees[COO_ID] = Employee(
            id=COO_ID, name="COO", role="COO", skills=[],
            department="Operations", employee_number=COO_ID,
        )
        monkeypatch.setattr(state_mod, "company_state", cs)

        compute_layout(cs)

        # Executives should be on exec row
        assert cs.employees[HR_ID].desk_position[1] == 0  # EXEC_ROW_GY
        assert cs.employees[COO_ID].desk_position[1] == 0

    def test_sorts_by_level_desc(self, monkeypatch):
        from onemancompany.core.layout import compute_layout
        from onemancompany.core import state as state_mod

        cs = CompanyState()
        cs.employees["00010"] = _make_emp("00010", "Engineering", level=1)
        cs.employees["00011"] = _make_emp("00011", "Engineering", level=3)
        monkeypatch.setattr(state_mod, "company_state", cs)

        compute_layout(cs)

        # Higher-level employee should be in first row
        pos_senior = cs.employees["00011"].desk_position
        pos_junior = cs.employees["00010"].desk_position
        # Senior (level 3) should be placed first (earlier row or same row earlier col)
        assert pos_senior[1] <= pos_junior[1]


# ---------------------------------------------------------------------------
# persist_all_desk_positions
# ---------------------------------------------------------------------------

class TestPersistAllDeskPositions:
    def test_updates_profile_yaml(self, tmp_path, monkeypatch):
        """_persist_positions writes desk positions to profile.yaml on disk."""
        import yaml
        from unittest.mock import patch as _patch
        from onemancompany.core.layout import compute_layout, _persist_positions
        import onemancompany.core.config as cfg
        from onemancompany.core import state as state_mod

        monkeypatch.setattr(cfg, "EMPLOYEES_DIR", tmp_path)

        # Create employee profile on disk
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        (emp_dir / "profile.yaml").write_text("name: Test\ndesk_position: [0, 0]\n")

        cs = CompanyState()
        cs.employees["00010"] = _make_emp("00010", "Engineering")
        monkeypatch.setattr(state_mod, "company_state", cs)

        # compute_layout reads from store (bridged to cs.employees),
        # computes positions, and calls _persist_positions to write YAML
        compute_layout(cs)

        data = yaml.safe_load((emp_dir / "profile.yaml").read_text())
        # Position should have been updated from the default [0, 0]
        assert data["desk_position"] != [0, 0]

    def test_skips_missing_profile(self, tmp_path, monkeypatch):
        """_persist_positions skips employees without profile.yaml — no crash."""
        from onemancompany.core.layout import compute_layout
        import onemancompany.core.config as cfg
        from onemancompany.core import state as state_mod

        monkeypatch.setattr(cfg, "EMPLOYEES_DIR", tmp_path)

        cs = CompanyState()
        cs.employees["00010"] = _make_emp("00010", "Engineering")
        monkeypatch.setattr(state_mod, "company_state", cs)

        # No directory / file created — should not raise
        compute_layout(cs)


# ---------------------------------------------------------------------------
# Remote employees excluded from layout
# ---------------------------------------------------------------------------

class TestRemoteEmployeesExcluded:
    def test_remote_employees_not_in_zones(self, monkeypatch):
        """Line 75: remote employees skip office desks."""
        from unittest.mock import patch as _patch
        from onemancompany.core.layout import compute_layout
        from onemancompany.core import state as state_mod

        cs = CompanyState()
        cs.employees["00010"] = _make_emp("00010", "Engineering", remote=True)
        cs.employees["00011"] = _make_emp("00011", "Engineering")
        monkeypatch.setattr(state_mod, "company_state", cs)

        persisted = {}
        def capture_persist(updates):
            persisted.update(updates)

        with _patch("onemancompany.core.layout._persist_positions", side_effect=capture_persist):
            layout = compute_layout(cs)

        zones = layout["zones"]
        # Only one non-remote employee, so one zone
        assert len(zones) >= 1
        # Remote employee should NOT have a persisted position
        assert "00010" not in persisted
        # Non-remote employee gets assigned a non-default position
        assert "00011" in persisted
        assert tuple(persisted["00011"]) != (0, 0)


# ---------------------------------------------------------------------------
# Custom departments not in DEPT_ORDER
# ---------------------------------------------------------------------------

class TestCustomDepartments:
    def test_custom_department_gets_zone(self, monkeypatch):
        """Line 119: departments not in DEPT_ORDER appended."""
        from onemancompany.core.layout import compute_layout
        from onemancompany.core import state as state_mod

        cs = CompanyState()
        cs.employees["00010"] = _make_emp("00010", "CustomDeptXYZ")
        monkeypatch.setattr(state_mod, "company_state", cs)

        layout = compute_layout(cs)
        zone_depts = {z["department"] for z in layout["zones"]}
        assert "CustomDeptXYZ" in zone_depts


# ---------------------------------------------------------------------------
# Zone computation with many departments
# ---------------------------------------------------------------------------

class TestZoneComputation:
    def test_too_many_departments_for_grid(self, monkeypatch):
        """Line 132: remaining_cols < 0 scenario."""
        from onemancompany.core.layout import compute_layout
        from onemancompany.core import state as state_mod

        cs = CompanyState()
        monkeypatch.setattr(state_mod, "company_state", cs)
        # Create many departments to exhaust grid columns
        for i in range(12):
            cs.employees[f"001{i:02d}"] = _make_emp(f"001{i:02d}", f"Dept_{i}")

        layout = compute_layout(cs)
        zones = layout["zones"]
        assert len(zones) == 12

    def test_empty_department_zone(self):
        """Line 172: _assign_desks_in_zone with empty employees returns early."""
        from onemancompany.core.layout import _assign_desks_in_zone, DeptZone

        zone = DeptZone(department="Empty", start_col=0, end_col=5)
        _assign_desks_in_zone(zone, [])  # should not raise

    def test_very_narrow_zone_uses_center(self):
        """Line 187: No desk columns fit — use zone center."""
        from onemancompany.core.layout import _assign_desks_in_zone, DeptZone

        zone = DeptZone(department="Narrow", start_col=0, end_col=2)
        # _assign_desks_in_zone expects dicts (store format), not Employee objects
        emp_dict = {"id": "00010", "name": "Emp 00010", "role": "Engineer",
                    "skills": ["python"], "department": "Narrow",
                    "employee_number": "00010", "desk_position": [0, 0], "level": 1}
        _assign_desks_in_zone(zone, [emp_dict])
        # Should be placed at center
        assert emp_dict["desk_position"][0] == 1  # 0 + 2//2


# ---------------------------------------------------------------------------
# get_next_desk_for_department — all slots full
# ---------------------------------------------------------------------------

class TestAllSlotsFull:
    def test_fallback_when_all_slots_occupied(self, monkeypatch):
        """Line 270: all slots full — place at end of zone."""
        from onemancompany.core.layout import get_next_desk_for_department
        from onemancompany.core import state as state_mod

        cs = CompanyState()
        monkeypatch.setattr(state_mod, "company_state", cs)
        # Fill up many positions
        for i in range(30):
            emp = _make_emp(f"00{i:03d}", "Engineering")
            cs.employees[f"00{i:03d}"] = emp

        # After adding many employees, still should return a valid position
        pos = get_next_desk_for_department(cs, "Engineering")
        assert isinstance(pos, tuple)
        assert len(pos) == 2

    def test_fallback_when_no_zone_found(self, monkeypatch):
        """Line 247: no target_zone — use fallback position."""
        from unittest.mock import patch
        from onemancompany.core.layout import get_next_desk_for_department, DeptZone
        from onemancompany.core import state as state_mod

        cs = CompanyState()
        monkeypatch.setattr(state_mod, "company_state", cs)

        # Mock _compute_zones to return a zone for a DIFFERENT department,
        # so the target department "Phantom" won't find its zone.
        fake_zones = [DeptZone(department="Other", start_col=0, end_col=10)]
        with patch("onemancompany.core.layout._compute_zones", return_value=fake_zones):
            pos = get_next_desk_for_department(cs, "Phantom")
        assert pos == (2, DEPT_START_ROW)


# ---------------------------------------------------------------------------
# Executive positions
# ---------------------------------------------------------------------------

class TestNarrowZoneFallback:
    def test_narrow_zone_desk_cols_empty(self, monkeypatch):
        """Line 262: zone too narrow for normal desk spacing — uses center column."""
        from unittest.mock import patch
        from onemancompany.core.layout import get_next_desk_for_department, DeptZone
        from onemancompany.core import state as state_mod

        cs = CompanyState()
        monkeypatch.setattr(state_mod, "company_state", cs)

        # Zone width = 2 (end - start = 2). col = start+1 = 1, end-1 = 1 => 1 < 1 is False
        # So desk_cols is empty, triggering line 262.
        fake_zones = [DeptZone(department="Tiny", start_col=0, end_col=2)]
        with patch("onemancompany.core.layout._compute_zones", return_value=fake_zones):
            pos = get_next_desk_for_department(cs, "Tiny")
        # desk_cols = [0 + 2//2] = [1], first desk row
        assert pos == (1, DEPT_DESK_ROWS[0])

    def test_all_slots_full_exact(self, monkeypatch):
        """All fixed desk rows occupied — returns first overflow (auto-expanded) row."""
        from unittest.mock import patch
        from onemancompany.core.layout import get_next_desk_for_department, DeptZone
        from onemancompany.core import state as state_mod

        cs = CompanyState()
        monkeypatch.setattr(state_mod, "company_state", cs)

        # Create a small zone with known dimensions
        fake_zones = [DeptZone(department="Full", start_col=0, end_col=5)]

        # Pre-occupy all possible desk positions in this zone
        # desk_cols: col = 1, 1 < 4 => yes; col=1+3=4, 4 < 4 is False. So desk_cols = [1]
        # All positions: (1, row) for each row in DEPT_DESK_ROWS
        # Use IDs that don't collide with _EXEC_IDS (00002-00005)
        for idx, row in enumerate(DEPT_DESK_ROWS):
            eid = f"{idx + 100:05d}"
            emp = _make_emp(eid, "Full", desk_pos=(1, row))
            cs.employees[eid] = emp

        with patch("onemancompany.core.layout._compute_zones", return_value=fake_zones):
            pos = get_next_desk_for_department(cs, "Full")
        # All DEPT_DESK_ROWS occupied → auto-expansion kicks in, returns first overflow row:
        # row_spacing = DEPT_DESK_ROWS[1] - DEPT_DESK_ROWS[0], first overflow = DEPT_DESK_ROWS[-1] + spacing
        row_spacing = DEPT_DESK_ROWS[1] - DEPT_DESK_ROWS[0]
        first_overflow = DEPT_DESK_ROWS[-1] + row_spacing
        assert pos == (1, first_overflow)


class TestExecutivePositions:
    def test_all_executives_positioned(self, monkeypatch):
        """Lines 199-213: All executive positions assigned."""
        from onemancompany.core.layout import compute_layout
        from onemancompany.core import state as state_mod

        cs = CompanyState()
        monkeypatch.setattr(state_mod, "company_state", cs)
        cs.employees[HR_ID] = Employee(
            id=HR_ID, name="HR", role="HR", skills=[],
            department="HR", employee_number=HR_ID,
        )
        cs.employees[COO_ID] = Employee(
            id=COO_ID, name="COO", role="COO", skills=[],
            department="Operations", employee_number=COO_ID,
        )
        cs.employees[EA_ID] = Employee(
            id=EA_ID, name="EA", role="EA", skills=[],
            department="Executive", employee_number=EA_ID,
        )
        cs.employees[CSO_ID] = Employee(
            id=CSO_ID, name="CSO", role="CSO", skills=[],
            department="Security", employee_number=CSO_ID,
        )

        compute_layout(cs)
        for eid in [HR_ID, COO_ID, EA_ID, CSO_ID]:
            assert cs.employees[eid].desk_position[1] == 0


# ---------------------------------------------------------------------------
# compute_asset_layout
# ---------------------------------------------------------------------------

class TestComputeAssetLayout:
    def test_no_assets(self):
        from onemancompany.core.layout import compute_asset_layout

        cs = CompanyState()
        layout = {}
        compute_asset_layout(cs, layout)
        assert "canvas_rows" in layout

    def test_tools_with_icons(self):
        from onemancompany.core.layout import compute_asset_layout
        from onemancompany.core.state import OfficeTool

        cs = CompanyState()
        cs.tools = {
            "t1": OfficeTool(
                id="t1", name="Tool1", description="desc",
                added_by="COO", desk_position=(0, 0),
                sprite="desk_equipment", allowed_users=[],
                files=["icon.png"], folder_name="t1",
                has_icon=True,
            ),
        }
        layout = {}
        compute_asset_layout(cs, layout)
        assert layout["canvas_rows"] >= 15
        # Tool should be positioned
        assert cs.tools["t1"].desk_position != (0, 0)

    def test_many_tools_wrap_rows(self):
        from onemancompany.core.layout import compute_asset_layout
        from onemancompany.core.state import OfficeTool

        cs = CompanyState()
        for i in range(10):
            cs.tools[f"t{i}"] = OfficeTool(
                id=f"t{i}", name=f"Tool{i}", description="desc",
                added_by="COO", desk_position=(0, 0),
                sprite="desk_equipment", allowed_users=[],
                files=["icon.png"], folder_name=f"t{i}",
                has_icon=True,
            )
        layout = {}
        compute_asset_layout(cs, layout)
        assert layout["canvas_rows"] >= 15

    def test_rooms_positioned_below_tools(self):
        from onemancompany.core.layout import compute_asset_layout
        from onemancompany.core.state import OfficeTool, MeetingRoom

        cs = CompanyState()
        cs.tools = {
            "t1": OfficeTool(
                id="t1", name="Tool", description="d",
                added_by="COO", desk_position=(0, 0),
                sprite="desk_equipment", allowed_users=[],
                files=["icon.png"], folder_name="t1",
                has_icon=True,
            ),
        }
        cs.meeting_rooms = {
            "r1": MeetingRoom(
                id="r1", name="Room", description="d",
                capacity=6, position=(0, 0), sprite="meeting_room",
            ),
        }
        layout = {}
        compute_asset_layout(cs, layout)
        assert layout["rooms_row"] > layout["tools_row"]

    def test_many_rooms_wrap_rows(self):
        from onemancompany.core.layout import compute_asset_layout
        from onemancompany.core.state import MeetingRoom

        cs = CompanyState()
        for i in range(8):
            cs.meeting_rooms[f"r{i}"] = MeetingRoom(
                id=f"r{i}", name=f"Room{i}", description="d",
                capacity=6, position=(0, 0), sprite="meeting_room",
            )
        layout = {}
        compute_asset_layout(cs, layout)
        assert layout["canvas_rows"] >= 15
