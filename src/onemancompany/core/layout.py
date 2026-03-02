"""Department-based office layout engine.

Computes department zones and assigns desk positions so employees
are visually grouped by department and sorted by level within each zone.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from onemancompany.core.config import (
    CEO_ID,
    COO_ID,
    CSO_ID,
    DEPT_CN_TO_EN,
    DEPT_COLORS,
    DEPT_DESK_ROWS,
    DEPT_DESK_SPACING_X,
    DEPT_END_ROW,
    DEPT_MIN_ZONE_WIDTH,
    DEPT_ORDER,
    DEPT_START_ROW,
    EA_ID,
    EXEC_FLOOR_COLORS,
    EXEC_ROW_GY,
    FOUNDING_LEVEL,
    HR_ID,
)

# All executive IDs (excluded from department layout)
_EXEC_IDS = {HR_ID, COO_ID, EA_ID, CSO_ID}


@dataclass
class DeptZone:
    department: str
    start_col: int
    end_col: int  # exclusive
    floor1: str = ""
    floor2: str = ""
    label_color: str = ""
    label_en: str = ""  # English name for frontend watermark

    def to_dict(self) -> dict:
        return {
            "department": self.department,
            "start_col": self.start_col,
            "end_col": self.end_col,
            "floor1": self.floor1,
            "floor2": self.floor2,
            "label_color": self.label_color,
            "label_en": self.label_en,
        }


def compute_layout(company_state) -> dict:
    """Compute department zones and assign desk positions for all employees.

    Updates employee desk_position in-place and returns layout metadata
    for the frontend to use for rendering.
    """
    from onemancompany.core.state import Employee

    # Migrate any legacy Chinese department names to English
    for emp in company_state.employees.values():
        if emp.department in DEPT_CN_TO_EN:
            emp.department = DEPT_CN_TO_EN[emp.department]

    # Separate executives from department employees
    dept_groups: dict[str, list[Employee]] = {}
    for emp in company_state.employees.values():
        if emp.id in _EXEC_IDS:
            continue  # executives handled separately
        dept = emp.department or "General"
        dept_groups.setdefault(dept, []).append(emp)

    # Compute zones
    zones = _compute_zones(dept_groups)

    # Assign desk positions within each zone
    for zone in zones:
        employees = dept_groups.get(zone.department, [])
        _assign_desks_in_zone(zone, employees)

    # Executive positions
    _assign_executive_positions(company_state)

    # Build layout metadata for frontend
    layout = {
        "zones": [z.to_dict() for z in zones],
        "executive_row": EXEC_ROW_GY,
        "exec_floor_colors": list(EXEC_FLOOR_COLORS),
        "dept_start_row": DEPT_START_ROW,
        "dept_end_row": DEPT_END_ROW,
    }

    # Compute asset positions (tools & meeting rooms) below employee area
    compute_asset_layout(company_state, layout)

    company_state.office_layout = layout
    return layout


def _compute_zones(dept_groups: dict[str, list]) -> list[DeptZone]:
    """Allocate column ranges for each department proportionally.

    Uses DEPT_ORDER for stable left-to-right ordering.
    Only departments with employees get zones.
    """
    TOTAL_COLS = 20

    # Filter to departments that actually have employees, in order
    active_depts = [d for d in DEPT_ORDER if d in dept_groups]
    # Add any departments not in DEPT_ORDER (custom departments)
    for d in dept_groups:
        if d not in active_depts:
            active_depts.append(d)

    if not active_depts:
        return []

    # Calculate proportional widths based on headcount
    counts = {d: len(dept_groups[d]) for d in active_depts}
    total_employees = sum(counts.values())

    # Start with minimum width for each, then distribute remaining
    remaining_cols = TOTAL_COLS - DEPT_MIN_ZONE_WIDTH * len(active_depts)
    if remaining_cols < 0:
        # Too many departments for the grid — give equal space
        remaining_cols = 0

    widths: dict[str, int] = {}
    for d in active_depts:
        base = DEPT_MIN_ZONE_WIDTH
        if remaining_cols > 0 and total_employees > 0:
            extra = int(remaining_cols * counts[d] / total_employees)
            base += extra
        widths[d] = base

    # Distribute any leftover columns to the largest department
    used = sum(widths.values())
    leftover = TOTAL_COLS - used
    if leftover > 0 and active_depts:
        largest = max(active_depts, key=lambda d: counts[d])
        widths[largest] += leftover

    # Build zones with column ranges
    zones: list[DeptZone] = []
    col = 0
    for d in active_depts:
        w = widths[d]
        colors = DEPT_COLORS.get(d, ("#2a2a2a", "#262626", "#888888"))
        zones.append(DeptZone(
            department=d,
            start_col=col,
            end_col=col + w,
            floor1=colors[0],
            floor2=colors[1],
            label_color=colors[2],
            label_en=d,
        ))
        col += w

    return zones


def _assign_desks_in_zone(zone: DeptZone, employees: list) -> None:
    """Place employees within a zone, sorted by level DESC then employee number."""
    if not employees:
        return

    # Sort: highest level first, then by employee number
    employees.sort(key=lambda e: (-e.level, e.employee_number))

    # Available desk columns within the zone (spaced by DEPT_DESK_SPACING_X)
    zone_width = zone.end_col - zone.start_col
    desk_cols = []
    col = zone.start_col + 1  # 1-col padding from zone edge
    while col < zone.end_col - 1:
        desk_cols.append(col)
        col += DEPT_DESK_SPACING_X

    # If no desk columns fit, use the zone center
    if not desk_cols:
        desk_cols = [zone.start_col + zone_width // 2]

    # Place employees row by row
    idx = 0
    for row_gy in DEPT_DESK_ROWS:
        for col_gx in desk_cols:
            if idx >= len(employees):
                return
            employees[idx].desk_position = (col_gx, row_gy)
            idx += 1


def _assign_executive_positions(company_state) -> None:
    """Place CEO, HR, EA, COO, CSO in the executive row."""
    # CEO at center (grid-x 9) — drawn separately by frontend

    exec_positions = {
        HR_ID: (3, EXEC_ROW_GY),
        EA_ID: (7, EXEC_ROW_GY),
        COO_ID: (11, EXEC_ROW_GY),
        CSO_ID: (15, EXEC_ROW_GY),
    }
    for emp_id, pos in exec_positions.items():
        emp = company_state.employees.get(emp_id)
        if emp:
            emp.desk_position = pos


def get_next_desk_for_department(company_state, department: str) -> tuple[int, int]:
    """Find the next available desk position for a given department.

    Used by HR when hiring — returns a valid position before the full
    layout recompute (which happens after the employee is added).
    """
    # Build current department groups (excluding executives)
    dept_groups: dict[str, list] = {}
    for emp in company_state.employees.values():
        if emp.id in _EXEC_IDS:
            continue
        dept = emp.department or "General"
        dept_groups.setdefault(dept, []).append(emp)

    # Ensure the target department exists in groups (even if empty)
    if department not in dept_groups:
        dept_groups[department] = []

    # Compute zones with current + new department
    zones = _compute_zones(dept_groups)

    # Find the zone for the target department
    target_zone = None
    for z in zones:
        if z.department == department:
            target_zone = z
            break

    if not target_zone:
        # Fallback
        return (2, DEPT_START_ROW)

    # Get occupied positions in this zone
    occupied = set()
    for emp in dept_groups.get(department, []):
        occupied.add(tuple(emp.desk_position))

    # Find first free desk slot in the zone
    zone_width = target_zone.end_col - target_zone.start_col
    desk_cols = []
    col = target_zone.start_col + 1
    while col < target_zone.end_col - 1:
        desk_cols.append(col)
        col += DEPT_DESK_SPACING_X
    if not desk_cols:
        desk_cols = [target_zone.start_col + zone_width // 2]

    for row_gy in DEPT_DESK_ROWS:
        for col_gx in desk_cols:
            if (col_gx, row_gy) not in occupied:
                return (col_gx, row_gy)

    # All slots full — place at end of zone
    return (target_zone.start_col + 1, DEPT_DESK_ROWS[0])


TOTAL_COLS = 20
ASSET_GAP_Y = 3  # vertical spacing between asset rows (tools occupy ~1 tile, rooms ~2)
TOOL_SPACING_X = 3  # horizontal spacing between tools
ROOM_SPACING_X = 4  # horizontal spacing between meeting rooms (room is 2 tiles wide)
MIN_CANVAS_ROWS = 15


def compute_asset_layout(company_state, layout: dict) -> None:
    """Assign non-overlapping positions for tools and meeting rooms.

    Places tools in a row below the employee area, meeting rooms below tools.
    Updates positions in-place and sets layout['canvas_rows'].
    """
    # Asset area starts below the department zone
    asset_start_gy = DEPT_END_ROW + 2  # gap after dept area

    # --- Tools row (only tools with icons get canvas positions) ---
    tool_list = [t for t in company_state.tools.values() if t.has_icon]
    tool_row_gy = asset_start_gy
    max_tool_rows = 0
    if tool_list:
        col = 1
        row_offset = 0
        for tool in tool_list:
            if col + 1 > TOTAL_COLS - 1:
                col = 1
                row_offset += ASSET_GAP_Y
            tool.desk_position = (col, tool_row_gy + row_offset)
            col += TOOL_SPACING_X
        max_tool_rows = row_offset + 1

    # --- Meeting rooms row (below tools) ---
    room_list = list(company_state.meeting_rooms.values())
    room_start_gy = tool_row_gy + max(max_tool_rows, 1) + 2 if tool_list else asset_start_gy
    max_room_rows = 0
    if room_list:
        col = 1
        row_offset = 0
        for room in room_list:
            if col + 2 > TOTAL_COLS - 1:
                col = 1
                row_offset += ASSET_GAP_Y + 1  # rooms are taller
            room.position = (col, room_start_gy + row_offset)
            col += ROOM_SPACING_X
        max_room_rows = row_offset + 3  # rooms are 2 tiles + label

    # Calculate required canvas rows (grid-y + 3 wall offset + padding)
    max_gy = DEPT_END_ROW  # minimum
    if tool_list:
        max_gy = max(max_gy, tool_row_gy + max_tool_rows + 1)
    if room_list:
        max_gy = max(max_gy, room_start_gy + max_room_rows)

    canvas_rows = max(MIN_CANVAS_ROWS, max_gy + 3 + 2)  # +3 for wall, +2 padding
    layout["canvas_rows"] = canvas_rows
    layout["tools_row"] = tool_row_gy
    layout["rooms_row"] = room_start_gy


def persist_all_desk_positions(company_state) -> None:
    """Update all employee profile.yaml files with current desk positions."""
    import yaml

    from onemancompany.core.config import EMPLOYEES_DIR

    for emp_id, emp in company_state.employees.items():
        profile_path = EMPLOYEES_DIR / emp_id / "profile.yaml"
        if not profile_path.exists():
            continue
        with open(profile_path) as f:
            data = yaml.safe_load(f) or {}
        data["desk_position"] = list(emp.desk_position)
        with open(profile_path, "w") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
