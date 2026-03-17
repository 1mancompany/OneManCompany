"""Tests for tileset-specific layout fields added to zone data.

NOTE on mocking: load_all_employees and mark_dirty are lazy-imported inside
function bodies in layout.py, so they must be patched at their source module
(onemancompany.core.store), not at the importing layout module.
_persist_positions is a module-level function in layout.py, so it patches there.
"""
from unittest.mock import patch, MagicMock


def _make_fake_employees(dept_map: dict) -> dict:
    """Build fake employee dict: {emp_id: {department, level, ...}}."""
    employees = {}
    n = 6  # start after founding IDs (00001-00005)
    for dept, count in dept_map.items():
        for _ in range(count):
            eid = f"{n:05d}"
            employees[eid] = {
                "department": dept,
                "level": 1,
                "employee_number": eid,
                "desk_position": [0, 0],
                "remote": False,
            }
            n += 1
    return employees


@patch("onemancompany.core.store.load_all_employees")
@patch("onemancompany.core.store.mark_dirty")
@patch("onemancompany.core.layout._persist_positions")
def test_zones_include_floor_style(mock_persist, mock_dirty, mock_load):
    """Each zone dict must contain a 'floor_style' string."""
    from onemancompany.core.layout import compute_layout
    from onemancompany.core.config import DEPT_FLOOR_STYLES

    mock_load.return_value = _make_fake_employees({"Engineering": 2, "Design": 2})

    company_state = MagicMock()
    company_state.tools = {}
    company_state.meeting_rooms = {}

    layout = compute_layout(company_state)

    for zone in layout["zones"]:
        assert "floor_style" in zone, f"zone {zone['department']} missing floor_style"
        dept = zone["department"]
        expected = DEPT_FLOOR_STYLES.get(dept, "stone_gray")
        assert zone["floor_style"] == expected


@patch("onemancompany.core.store.load_all_employees")
@patch("onemancompany.core.store.mark_dirty")
@patch("onemancompany.core.layout._persist_positions")
def test_layout_includes_divider_cols(mock_persist, mock_dirty, mock_load):
    """Layout dict must contain 'divider_cols' listing columns between zones."""
    from onemancompany.core.layout import compute_layout

    mock_load.return_value = _make_fake_employees({"Engineering": 2, "Design": 2})

    company_state = MagicMock()
    company_state.tools = {}
    company_state.meeting_rooms = {}

    layout = compute_layout(company_state)

    assert "divider_cols" in layout
    dividers = layout["divider_cols"]
    assert isinstance(dividers, list)
    # With 2 departments there is 1 divider between them
    assert len(dividers) == 1


@patch("onemancompany.core.store.load_all_employees")
@patch("onemancompany.core.store.mark_dirty")
@patch("onemancompany.core.layout._persist_positions")
def test_divider_cols_at_zone_boundaries(mock_persist, mock_dirty, mock_load):
    """Divider column should sit at end_col of left zone."""
    from onemancompany.core.layout import compute_layout

    mock_load.return_value = _make_fake_employees(
        {"Engineering": 3, "Design": 3, "Marketing": 3}
    )

    company_state = MagicMock()
    company_state.tools = {}
    company_state.meeting_rooms = {}

    layout = compute_layout(company_state)

    zones = layout["zones"]
    dividers = layout["divider_cols"]

    # Each divider is at the end_col of the zone to its left
    for i, div_col in enumerate(dividers):
        left_zone = zones[i]
        assert div_col == left_zone["end_col"], (
            f"Divider {i} at col {div_col} should be at "
            f"{left_zone['department']}.end_col={left_zone['end_col']}"
        )


@patch("onemancompany.core.store.load_all_employees")
@patch("onemancompany.core.store.mark_dirty")
@patch("onemancompany.core.layout._persist_positions")
def test_no_dividers_with_single_department(mock_persist, mock_dirty, mock_load):
    """Single department → no dividers."""
    from onemancompany.core.layout import compute_layout

    mock_load.return_value = _make_fake_employees({"Engineering": 3})

    company_state = MagicMock()
    company_state.tools = {}
    company_state.meeting_rooms = {}

    layout = compute_layout(company_state)

    assert layout["divider_cols"] == []
