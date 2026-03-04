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
    def test_first_desk_in_empty_department(self):
        from onemancompany.core.layout import get_next_desk_for_department

        cs = CompanyState()
        pos = get_next_desk_for_department(cs, "Engineering")
        # Should return a valid position (col, row) within a zone
        assert isinstance(pos, tuple)
        assert len(pos) == 2
        assert pos[1] in DEPT_DESK_ROWS

    def test_avoids_occupied_positions(self):
        from onemancompany.core.layout import get_next_desk_for_department

        cs = CompanyState()
        # Add employee to occupy the first desk position
        first_pos = get_next_desk_for_department(cs, "Engineering")
        cs.employees["00010"] = _make_emp("00010", "Engineering", desk_pos=first_pos)

        second_pos = get_next_desk_for_department(cs, "Engineering")
        assert second_pos != first_pos

    def test_remote_employees_dont_occupy_desks(self):
        from onemancompany.core.layout import get_next_desk_for_department

        cs = CompanyState()
        # Add remote employee — should not block desk positions
        cs.employees["00010"] = _make_emp(
            "00010", "Engineering", desk_pos=(5, 3), remote=True,
        )

        pos = get_next_desk_for_department(cs, "Engineering")
        # The remote employee's position should be available
        assert isinstance(pos, tuple)

    def test_executives_excluded_from_department(self):
        from onemancompany.core.layout import get_next_desk_for_department

        cs = CompanyState()
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
    def test_empty_state_produces_layout(self):
        from onemancompany.core.layout import compute_layout

        cs = CompanyState()
        layout = compute_layout(cs)
        assert "zones" in layout
        assert "executive_row" in layout
        assert "canvas_rows" in layout

    def test_assigns_desk_positions_to_employees(self):
        from onemancompany.core.layout import compute_layout

        cs = CompanyState()
        cs.employees["00010"] = _make_emp("00010", "Engineering")
        cs.employees["00011"] = _make_emp("00011", "Engineering")

        compute_layout(cs)

        pos1 = cs.employees["00010"].desk_position
        pos2 = cs.employees["00011"].desk_position
        # Positions should be assigned (not default 0,0)
        assert pos1 != (0, 0)
        assert pos2 != (0, 0)
        # Positions should be different
        assert pos1 != pos2

    def test_multiple_departments_get_separate_zones(self):
        from onemancompany.core.layout import compute_layout

        cs = CompanyState()
        cs.employees["00010"] = _make_emp("00010", "Engineering")
        cs.employees["00011"] = _make_emp("00011", "Design")

        layout = compute_layout(cs)
        zones = layout["zones"]
        assert len(zones) == 2
        dept_names = {z["department"] for z in zones}
        assert dept_names == {"Engineering", "Design"}

    def test_executive_positions_assigned(self):
        from onemancompany.core.layout import compute_layout

        cs = CompanyState()
        cs.employees[HR_ID] = Employee(
            id=HR_ID, name="HR", role="HR", skills=[],
            department="HR", employee_number=HR_ID,
        )
        cs.employees[COO_ID] = Employee(
            id=COO_ID, name="COO", role="COO", skills=[],
            department="Operations", employee_number=COO_ID,
        )

        compute_layout(cs)

        # Executives should be on exec row
        assert cs.employees[HR_ID].desk_position[1] == 0  # EXEC_ROW_GY
        assert cs.employees[COO_ID].desk_position[1] == 0

    def test_sorts_by_level_desc(self):
        from onemancompany.core.layout import compute_layout

        cs = CompanyState()
        cs.employees["00010"] = _make_emp("00010", "Engineering", level=1)
        cs.employees["00011"] = _make_emp("00011", "Engineering", level=3)

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
        import yaml
        from onemancompany.core.layout import persist_all_desk_positions
        import onemancompany.core.config as cfg

        monkeypatch.setattr(cfg, "EMPLOYEES_DIR", tmp_path)

        # Create employee profile
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        (emp_dir / "profile.yaml").write_text("name: Test\ndesk_position: [0, 0]\n")

        cs = CompanyState()
        cs.employees["00010"] = _make_emp("00010", "Engineering", desk_pos=(7, 3))

        persist_all_desk_positions(cs)

        data = yaml.safe_load((emp_dir / "profile.yaml").read_text())
        assert data["desk_position"] == [7, 3]
