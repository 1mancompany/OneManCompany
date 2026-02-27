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
