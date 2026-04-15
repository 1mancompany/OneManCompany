"""Tests for layout.py coverage gaps — overflow fallback and persist_all_desk_positions."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_persist_all_desk_positions_is_noop():
    """Line 426: persist_all_desk_positions is a no-op."""
    from onemancompany.core.layout import persist_all_desk_positions
    # Should not raise and returns None
    result = persist_all_desk_positions(None)
    assert result is None


def test_get_next_desk_overflow_fallback(monkeypatch):
    """Line 360: when all desk positions are occupied, returns overflow position."""
    from onemancompany.core import layout as layout_mod

    # Reduce DEPT_DESK_ROWS to one row so we only need to fill one row + overflow rows
    monkeypatch.setattr(layout_mod, "DEPT_DESK_ROWS", [5])
    monkeypatch.setattr(layout_mod, "DEPT_DESK_SPACING_X", 100)  # only 1 desk col per zone

    # Create many employees all in the same department, occupying all possible desk slots
    # We need to cover row 5 + 10 overflow rows (3 spacing each) = 11 rows
    employees = {}
    for i in range(20):
        emp_id = f"00{100 + i}"
        employees[emp_id] = {
            "name": f"Emp {i}",
            "department": "TestDept",
            "desk_position": [1, 5 + i * 3],  # fill slot at each row
        }

    with patch("onemancompany.core.store.load_all_employees", return_value=employees):
        result = layout_mod.get_next_desk_for_department(None, "TestDept")

    # Should return a valid position (overflow)
    assert isinstance(result, tuple)
    assert len(result) == 2
