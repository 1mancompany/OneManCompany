from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from onemancompany.core.config import (
    FOUNDING_LEVEL,
    STATUS_IDLE,
)


@dataclass
class TaskEntry:
    """A tracked task in the task queue."""

    project_id: str
    task: str
    routed_to: str  # "HR" or "COO"
    project_dir: str = ""  # absolute path to project workspace
    current_owner: str = ""  # employee_id of current owner
    status: str = "running"  # running / queued
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.current_owner:
            self.current_owner = self.routed_to.lower()

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "task": self.task,
            "routed_to": self.routed_to,
            "project_dir": self.project_dir,
            "current_owner": self.current_owner,
            "status": self.status,
            "created_at": self.created_at,
        }


@dataclass
class MeetingRoom:
    """Meeting room — must be booked before use."""

    id: str
    name: str
    description: str
    capacity: int = 6
    position: tuple[int, int] = (0, 0)
    sprite: str = "meeting_room"
    # Booking state
    booked_by: str = ""  # employee_id who booked it
    participants: list[str] = field(default_factory=list)  # employee_ids in the meeting
    is_booked: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "capacity": self.capacity,
            "position": list(self.position),
            "sprite": self.sprite,
            "booked_by": self.booked_by,
            "participants": self.participants,
            "is_booked": self.is_booked,
        }


LEVEL_NAMES = {1: "Junior", 2: "Mid", 3: "Senior", 4: "Founding", 5: "CEO"}

ROLE_TITLES = {
    "Engineer": "Engineer", "DevOps": "Engineer", "QA": "Engineer",
    "Designer": "Designer", "Analyst": "Analyst", "Marketing": "Marketing",
    "HR": "HR", "COO": "COO",
}


def make_title(level: int, role: str) -> str:
    """Generate title like 'Junior Engineer', 'Mid Analyst'."""
    if level >= FOUNDING_LEVEL:
        return LEVEL_NAMES.get(level, "")
    prefix = LEVEL_NAMES.get(level, f"Lv.{level}")
    role_name = ROLE_TITLES.get(role, role)
    return f"{prefix} {role_name}"


@dataclass
class Employee:
    id: str
    name: str
    role: str
    skills: list[str]
    nickname: str = ""  # Chinese alias
    level: int = 1  # 1-3 normal, 4 founding, 5 CEO
    department: str = ""  # assigned by HR
    employee_number: str = ""  # 5-digit string e.g. "00008"
    current_quarter_tasks: int = 0
    performance_history: list[dict] = field(default_factory=list)
    desk_position: tuple[int, int] = (0, 0)
    sprite: str = "employee_default"
    guidance_notes: list[str] = field(default_factory=list)
    work_principles: str = ""  # loaded from employees/{id}/work_principles.md
    permissions: list[str] = field(default_factory=list)  # access control: company_file_access, web_search, backend_code_maintenance, etc.
    status: str = STATUS_IDLE
    is_listening: bool = False

    @property
    def title(self) -> str:
        return make_title(self.level, self.role)

    @property
    def latest_score(self) -> float:
        """Most recent quarter score, or 3.5 if no history."""
        if self.performance_history:
            return self.performance_history[-1].get("score", 3.5)
        return 3.5

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "employee_number": self.employee_number,
            "name": self.name,
            "nickname": self.nickname,
            "level": self.level,
            "title": self.title,
            "department": self.department,
            "current_quarter_tasks": self.current_quarter_tasks,
            "performance_history": self.performance_history,
            "role": self.role,
            "skills": self.skills,
            "desk_position": list(self.desk_position),
            "sprite": self.sprite,
            "guidance_notes": self.guidance_notes,
            "work_principles": self.work_principles,
            "permissions": self.permissions,
            "status": self.status,
            "is_listening": self.is_listening,
        }


@dataclass
class OfficeTool:
    id: str
    name: str
    description: str
    added_by: str
    desk_position: tuple[int, int] = (0, 0)
    sprite: str = "desk_equipment"
    allowed_users: list[str] = field(default_factory=list)  # empty = open access
    files: list[str] = field(default_factory=list)  # filenames in tool folder (excl. tool.yaml)
    folder_name: str = ""  # slug used as folder name

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "added_by": self.added_by,
            "desk_position": list(self.desk_position),
            "sprite": self.sprite,
            "allowed_users": self.allowed_users,
            "files": self.files,
            "folder_name": self.folder_name,
        }


@dataclass
class CompanyState:
    employees: dict[str, Employee] = field(default_factory=dict)
    ex_employees: dict[str, Employee] = field(default_factory=dict)
    tools: dict[str, OfficeTool] = field(default_factory=dict)
    meeting_rooms: dict[str, MeetingRoom] = field(default_factory=dict)
    ceo_tasks: list[str] = field(default_factory=list)
    active_tasks: list[TaskEntry] = field(default_factory=list)
    activity_log: list[dict] = field(default_factory=list)
    company_culture: list[dict] = field(default_factory=list)
    office_layout: dict = field(default_factory=dict)
    _next_employee_number: int = 0  # auto-increment counter

    def to_json(self) -> dict:
        return {
            "employees": [e.to_dict() for e in self.employees.values()],
            "ex_employees": [e.to_dict() for e in self.ex_employees.values()],
            "tools": [t.to_dict() for t in self.tools.values()],
            "meeting_rooms": [m.to_dict() for m in self.meeting_rooms.values()],
            "ceo_tasks": self.ceo_tasks[-10:],
            "active_tasks": [t.to_dict() for t in self.active_tasks],
            "activity_log": self.activity_log[-20:],
            "company_culture": self.company_culture,
            "office_layout": self.office_layout,
        }

    def next_employee_number(self) -> str:
        """Generate next 5-digit employee number."""
        num = self._next_employee_number
        self._next_employee_number += 1
        return f"{num:05d}"


# Singleton
company_state = CompanyState()


def _seed_employees() -> None:
    """Seed employees from employees/{emp_num}/profile.yaml + guidance.yaml + work_principles.md.

    Folder names ARE employee numbers (e.g., employees/00002/).
    """
    from onemancompany.core.config import HR_ID, COO_ID, employee_configs, load_employee_guidance, load_work_principles

    if not employee_configs:
        # Fallback defaults if no employee folders exist
        company_state.employees[HR_ID] = Employee(
            id=HR_ID, name="Sam HR", role="HR",
            skills=["hiring", "reviews", "people_management"],
            department="HR", employee_number=HR_ID,
            desk_position=(3, 2), sprite="hr",
        )
        company_state.employees[COO_ID] = Employee(
            id=COO_ID, name="Alex COO", role="COO",
            skills=["operations", "tool_management", "strategy"],
            department="Operations", employee_number=COO_ID,
            desk_position=(6, 2), sprite="coo",
        )
        return

    company_state._next_employee_number = 4  # start after founding employees

    for emp_num, cfg in employee_configs.items():
        # Folder name IS the employee number — use it as both id and employee_number
        guidance = load_employee_guidance(emp_num)
        principles = load_work_principles(emp_num)
        # Ensure counter stays ahead of any assigned numbers
        try:
            num_val = int(emp_num)
            if num_val >= company_state._next_employee_number:
                company_state._next_employee_number = num_val + 1
        except ValueError:
            pass
        company_state.employees[emp_num] = Employee(
            id=emp_num,
            name=cfg.name,
            nickname=cfg.nickname,
            level=cfg.level,
            department=cfg.department,
            role=cfg.role,
            skills=cfg.skills,
            employee_number=emp_num,
            current_quarter_tasks=cfg.current_quarter_tasks,
            performance_history=list(cfg.performance_history),
            desk_position=tuple(cfg.desk_position),
            sprite=cfg.sprite,
            guidance_notes=guidance,
            work_principles=principles,
            permissions=list(cfg.permissions),
        )


def _seed_ex_employees() -> None:
    """Seed ex-employees from ex-employees/ directory (folders named by employee_number)."""
    from onemancompany.core.config import load_ex_employee_configs

    for emp_num, cfg in load_ex_employee_configs().items():
        company_state.ex_employees[emp_num] = Employee(
            id=emp_num,
            name=cfg.name,
            nickname=cfg.nickname,
            level=cfg.level,
            department=cfg.department,
            role=cfg.role,
            skills=cfg.skills,
            employee_number=emp_num,
            current_quarter_tasks=cfg.current_quarter_tasks,
            performance_history=list(cfg.performance_history),
            desk_position=tuple(cfg.desk_position),
            sprite=cfg.sprite,
        )


def _seed_company_culture() -> None:
    """Load company culture items from company_culture.yaml."""
    from onemancompany.core.config import load_company_culture

    company_state.company_culture = load_company_culture()


_seed_employees()
_seed_ex_employees()
_seed_company_culture()

# Compute initial department-based office layout
from onemancompany.core.layout import compute_layout  # noqa: E402
compute_layout(company_state)


# Whether a reload is pending (deferred because agents were busy)
_reload_pending: bool = False


def is_idle() -> bool:
    """Return True if no agent tasks are currently running."""
    return len(company_state.active_tasks) == 0


def request_reload() -> dict:
    """Request a soft reload — executes immediately if idle, defers if busy.

    Returns the reload summary if executed, or a deferred notice.
    """
    global _reload_pending
    if is_idle():
        _reload_pending = False
        return reload_all_from_disk()
    else:
        _reload_pending = True
        return {"status": "deferred", "reason": "agents are busy"}


def flush_pending_reload() -> dict | None:
    """If a reload was deferred, execute it now. Called when agents finish."""
    global _reload_pending
    if _reload_pending:
        _reload_pending = False
        return reload_all_from_disk()
    return None


def reload_all_from_disk() -> dict:
    """Re-read all disk data into company_state in-place (soft reload).

    Preserves runtime state (status, is_listening) for existing employees.
    Returns a summary dict of what changed.

    Prefer calling request_reload() which checks idle state first.
    """
    from onemancompany.core.config import (
        load_company_culture,
        load_employee_configs,
        load_employee_guidance,
        load_ex_employee_configs,
        load_work_principles,
    )
    import onemancompany.core.config as config_module

    summary: dict = {"employees_updated": [], "employees_added": [], "culture_reloaded": False, "assets_reloaded": False}

    # --- 1. Reload employee configs from disk ---
    fresh_configs = load_employee_configs()
    # Update the module-level employee_configs dict in-place
    config_module.employee_configs.clear()
    config_module.employee_configs.update(fresh_configs)

    seen_ids: set[str] = set()
    for emp_num, cfg in fresh_configs.items():
        seen_ids.add(emp_num)
        guidance = load_employee_guidance(emp_num)
        principles = load_work_principles(emp_num)

        if emp_num in company_state.employees:
            # Update mutable fields, preserve runtime state
            emp = company_state.employees[emp_num]
            changed_fields = []
            if emp.name != cfg.name:
                emp.name = cfg.name
                changed_fields.append("name")
            if emp.nickname != cfg.nickname:
                emp.nickname = cfg.nickname
                changed_fields.append("nickname")
            if emp.level != cfg.level:
                emp.level = cfg.level
                changed_fields.append("level")
            if emp.department != cfg.department:
                emp.department = cfg.department
                changed_fields.append("department")
            if emp.role != cfg.role:
                emp.role = cfg.role
                changed_fields.append("role")
            if emp.skills != cfg.skills:
                emp.skills = cfg.skills
                changed_fields.append("skills")
            if emp.current_quarter_tasks != cfg.current_quarter_tasks:
                emp.current_quarter_tasks = cfg.current_quarter_tasks
                changed_fields.append("current_quarter_tasks")
            if emp.performance_history != cfg.performance_history:
                emp.performance_history = list(cfg.performance_history)
                changed_fields.append("performance_history")
            if list(cfg.permissions) != emp.permissions:
                emp.permissions = list(cfg.permissions)
                changed_fields.append("permissions")
            if guidance != emp.guidance_notes:
                emp.guidance_notes = guidance
                changed_fields.append("guidance_notes")
            if principles != emp.work_principles:
                emp.work_principles = principles
                changed_fields.append("work_principles")
            if changed_fields:
                summary["employees_updated"].append({"id": emp_num, "fields": changed_fields})
        else:
            # New employee added to disk — seed into company_state
            try:
                num_val = int(emp_num)
                if num_val >= company_state._next_employee_number:
                    company_state._next_employee_number = num_val + 1
            except ValueError:
                pass
            company_state.employees[emp_num] = Employee(
                id=emp_num,
                name=cfg.name,
                nickname=cfg.nickname,
                level=cfg.level,
                department=cfg.department,
                role=cfg.role,
                skills=cfg.skills,
                employee_number=emp_num,
                current_quarter_tasks=cfg.current_quarter_tasks,
                performance_history=list(cfg.performance_history),
                desk_position=tuple(cfg.desk_position) if cfg.desk_position else (0, 0),
                sprite=cfg.sprite,
                guidance_notes=guidance,
                work_principles=principles,
                permissions=list(cfg.permissions),
            )
            summary["employees_added"].append(emp_num)

    # NOTE: Don't remove employees missing from disk mid-session

    # --- 2. Reload ex-employees ---
    fresh_ex = load_ex_employee_configs()
    for emp_num, cfg in fresh_ex.items():
        if emp_num not in company_state.ex_employees:
            company_state.ex_employees[emp_num] = Employee(
                id=emp_num,
                name=cfg.name,
                nickname=cfg.nickname,
                level=cfg.level,
                department=cfg.department,
                role=cfg.role,
                skills=cfg.skills,
                employee_number=emp_num,
                current_quarter_tasks=cfg.current_quarter_tasks,
                performance_history=list(cfg.performance_history),
                desk_position=tuple(cfg.desk_position) if cfg.desk_position else (0, 0),
                sprite=cfg.sprite,
            )

    # --- 3. Reload company culture ---
    company_state.company_culture = load_company_culture()
    summary["culture_reloaded"] = True

    # --- 4. Reload assets (tools + meeting rooms) ---
    from onemancompany.agents.coo_agent import _load_assets_from_disk
    _load_assets_from_disk()
    summary["assets_reloaded"] = True

    # --- 5. Recompute office layout ---
    compute_layout(company_state)

    # --- 6. Broadcast state_snapshot to all WebSocket clients ---
    from onemancompany.core.events import CompanyEvent, event_bus
    import asyncio

    async def _broadcast():
        await event_bus.publish(
            CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
        )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_broadcast())
    except RuntimeError:
        # No running event loop — skip broadcast (e.g., called during startup)
        pass

    return summary
