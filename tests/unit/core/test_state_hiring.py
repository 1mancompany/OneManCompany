"""Unit tests for core/state.py — Employee, CompanyState, make_title (hiring-related)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from onemancompany.core.state import CompanyState, Employee, make_title, LEVEL_NAMES


# ---------------------------------------------------------------------------
# make_title
# ---------------------------------------------------------------------------

class TestMakeTitle:
    def test_junior_engineer(self):
        assert make_title(1, "Engineer") == "Junior Engineer"

    def test_mid_designer(self):
        assert make_title(2, "Designer") == "Mid Designer"

    def test_senior_analyst(self):
        assert make_title(3, "Analyst") == "Senior Analyst"

    def test_founding_level(self):
        assert make_title(4, "Engineer") == "Founding"

    def test_ceo_level(self):
        assert make_title(5, "CEO") == "CEO"

    def test_custom_role_at_normal_level(self):
        # Unknown role should use the role name directly
        title = make_title(1, "CustomRole")
        assert title == "Junior CustomRole"

    def test_unknown_level(self):
        title = make_title(99, "Engineer")
        # Level 99 has no LEVEL_NAME entry and is >= FOUNDING_LEVEL
        # FOUNDING_LEVEL = 4, so level 99 >= 4 returns LEVEL_NAMES.get(99, "") = ""
        assert title == ""


# ---------------------------------------------------------------------------
# Employee dataclass
# ---------------------------------------------------------------------------

class TestEmployee:
    def test_creation_defaults(self):
        emp = Employee(id="00010", name="Test", role="Engineer", skills=["python"])
        assert emp.level == 1
        assert emp.department == ""
        assert emp.status == "idle"
        assert emp.remote is False
        assert emp.desk_position == (0, 0)
        assert emp.performance_history == []
        assert emp.salary_per_1m_tokens == 0.0

    def test_title_property(self):
        emp = Employee(id="00010", name="Test", role="Engineer", skills=[], level=2)
        assert emp.title == "Mid Engineer"

    def test_latest_score_with_history(self):
        emp = Employee(
            id="00010", name="Test", role="Engineer", skills=[],
            performance_history=[{"score": 3.25}, {"score": 3.75}],
        )
        assert emp.latest_score == 3.75

    def test_latest_score_empty_history(self):
        emp = Employee(id="00010", name="Test", role="Engineer", skills=[])
        assert emp.latest_score == 3.5  # default

    def test_to_dict(self):
        emp = Employee(
            id="00010", name="Test Dev", role="Engineer", skills=["python"],
            nickname="追风", level=2, department="Engineering",
            employee_number="00010", desk_position=(5, 3),
            sprite="employee_blue",
        )
        d = emp.to_dict()
        assert d["id"] == "00010"
        assert d["name"] == "Test Dev"
        assert d["nickname"] == "追风"
        assert d["level"] == 2
        assert d["title"] == "Mid Engineer"
        assert d["desk_position"] == [5, 3]
        assert d["skills"] == ["python"]
        assert d["remote"] is False

    def test_to_dict_includes_all_fields(self):
        emp = Employee(
            id="x", name="N", role="R", skills=[],
            remote=True, salary_per_1m_tokens=5.0,
            status="working", is_listening=True,
            current_task_summary="doing something",
        )
        d = emp.to_dict()
        assert d["remote"] is True
        assert d["salary_per_1m_tokens"] == 5.0
        assert d["status"] == "working"
        assert d["is_listening"] is True
        assert d["current_task_summary"] == "doing something"


# ---------------------------------------------------------------------------
# CompanyState.next_employee_number
# ---------------------------------------------------------------------------

class TestCompanyStateNextEmployeeNumber:
    def test_starts_from_zero(self):
        cs = CompanyState()
        assert cs.next_employee_number() == "00000"
        assert cs.next_employee_number() == "00001"
        assert cs.next_employee_number() == "00002"

    def test_custom_start(self):
        cs = CompanyState()
        cs._next_employee_number = 100
        assert cs.next_employee_number() == "00100"
        assert cs.next_employee_number() == "00101"

    def test_five_digit_format(self):
        cs = CompanyState()
        cs._next_employee_number = 12345
        num = cs.next_employee_number()
        assert num == "12345"
        assert len(num) == 5


# ---------------------------------------------------------------------------
# CompanyState.to_json
# ---------------------------------------------------------------------------

class TestCompanyStateToJson:
    def test_empty_state(self):
        cs = CompanyState()
        with patch("onemancompany.core.state.get_active_tasks", return_value=[]):
            j = cs.to_json()
        assert j["employees"] == []
        assert j["ex_employees"] == []
        assert j["tools"] == []
        assert j["active_tasks"] == []

    def test_with_employees(self):
        cs = CompanyState()
        cs.employees["00010"] = Employee(
            id="00010", name="Test", role="Engineer", skills=["python"],
        )
        j = cs.to_json()
        assert len(j["employees"]) == 1
        assert j["employees"][0]["id"] == "00010"


# ---------------------------------------------------------------------------
# LEVEL_NAMES
# ---------------------------------------------------------------------------

class TestLevelNames:
    def test_all_levels_mapped(self):
        assert LEVEL_NAMES[1] == "Junior"
        assert LEVEL_NAMES[2] == "Mid"
        assert LEVEL_NAMES[3] == "Senior"
        assert LEVEL_NAMES[4] == "Founding"
        assert LEVEL_NAMES[5] == "CEO"
