from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TaskEntry:
    """A tracked task in the task queue."""

    project_id: str
    task: str
    routed_to: str  # "HR" or "COO"
    current_owner: str = ""  # 当前任务归属人 (employee_id)
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
            "current_owner": self.current_owner,
            "status": self.status,
            "created_at": self.created_at,
        }


@dataclass
class MeetingRoom:
    """会议室 — 员工沟通前需要先预约。"""

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


LEVEL_NAMES = {1: "初级", 2: "中级", 3: "高级", 4: "创始", 5: "CEO"}

ROLE_TITLES = {
    "Engineer": "工程师", "DevOps": "工程师", "QA": "工程师",
    "Designer": "设计师", "Analyst": "研究员", "Marketing": "营销专员",
    "HR": "HR", "COO": "COO",
}


def make_title(level: int, role: str) -> str:
    """Generate title like '初级工程师', '中级研究员'."""
    if level >= 4:
        return LEVEL_NAMES.get(level, "")
    prefix = LEVEL_NAMES.get(level, f"Lv.{level}")
    role_name = ROLE_TITLES.get(role, role)
    return f"{prefix}{role_name}"


@dataclass
class Employee:
    id: str
    name: str
    role: str
    skills: list[str]
    nickname: str = ""  # 花名 — 创始员工三字，其他两字
    level: int = 1  # 级别: 1-3 普通员工, 4 创始员工, 5 CEO
    department: str = ""  # 部门 — 由HR分配
    employee_number: str = ""  # 工号 — 5位数字字符串，如 "00000"
    current_quarter_tasks: int = 0  # 当前季度已完成任务数 (0-3)
    performance_history: list[dict] = field(default_factory=list)  # 过去3季度: [{"score": 3.5, "tasks": 3}, ...]
    desk_position: tuple[int, int] = (0, 0)
    sprite: str = "employee_default"
    guidance_notes: list[str] = field(default_factory=list)
    work_principles: str = ""  # 工作准则 — 从 employees/{id}/work_principles.md 加载
    status: str = "idle"  # idle / working / in_meeting
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

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "added_by": self.added_by,
            "desk_position": list(self.desk_position),
            "sprite": self.sprite,
        }


@dataclass
class CompanyState:
    employees: dict[str, Employee] = field(default_factory=dict)
    ex_employees: dict[str, Employee] = field(default_factory=dict)  # 离职员工
    tools: dict[str, OfficeTool] = field(default_factory=dict)
    meeting_rooms: dict[str, MeetingRoom] = field(default_factory=dict)
    ceo_tasks: list[str] = field(default_factory=list)
    active_tasks: list[TaskEntry] = field(default_factory=list)  # 任务队列
    activity_log: list[dict] = field(default_factory=list)
    culture_wall: list[dict] = field(default_factory=list)  # 公司文化墙条目
    _next_employee_number: int = 0  # 工号计数器

    def to_json(self) -> dict:
        return {
            "employees": [e.to_dict() for e in self.employees.values()],
            "ex_employees": [e.to_dict() for e in self.ex_employees.values()],
            "tools": [t.to_dict() for t in self.tools.values()],
            "meeting_rooms": [m.to_dict() for m in self.meeting_rooms.values()],
            "ceo_tasks": self.ceo_tasks[-10:],
            "active_tasks": [t.to_dict() for t in self.active_tasks],
            "activity_log": self.activity_log[-20:],
            "culture_wall": self.culture_wall,
        }

    def next_employee_number(self) -> str:
        """Generate next 5-digit employee number."""
        num = self._next_employee_number
        self._next_employee_number += 1
        return f"{num:05d}"


# Singleton
company_state = CompanyState()


def _seed_employees() -> None:
    """Seed employees from employees/{id}/profile.yaml + guidance.yaml + work_principles.md.

    Also loads ex-employees from ex-employees/ directory.
    """
    from onemancompany.core.config import employee_configs, load_employee_guidance, load_work_principles

    if not employee_configs:
        # Fallback defaults if no employee folders exist
        company_state.employees["hr"] = Employee(
            id="hr", name="Sam HR", role="HR",
            skills=["hiring", "reviews", "people_management"],
            department="人力资源部",
            desk_position=(3, 2), sprite="hr",
        )
        company_state.employees["coo"] = Employee(
            id="coo", name="Alex COO", role="COO",
            skills=["operations", "tool_management", "strategy"],
            department="运营管理部",
            desk_position=(6, 2), sprite="coo",
        )
        return

    # Assign employee numbers: CEO=00001, HR=00002, COO=00003, others=00004+
    FIXED_NUMBERS = {"hr": "00002", "coo": "00003"}
    company_state._next_employee_number = 4  # start after founding employees

    for emp_id, cfg in employee_configs.items():
        guidance = load_employee_guidance(emp_id)
        principles = load_work_principles(emp_id)
        emp_num = cfg.employee_number or FIXED_NUMBERS.get(emp_id, "")
        if not emp_num:
            emp_num = company_state.next_employee_number()
        else:
            # Ensure counter stays ahead of any assigned numbers
            try:
                num_val = int(emp_num)
                if num_val >= company_state._next_employee_number:
                    company_state._next_employee_number = num_val + 1
            except ValueError:
                pass
        company_state.employees[emp_id] = Employee(
            id=emp_id,
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
        )


def _seed_ex_employees() -> None:
    """Seed ex-employees from ex-employees/ directory."""
    from onemancompany.core.config import load_ex_employee_configs

    for emp_id, cfg in load_ex_employee_configs().items():
        company_state.ex_employees[emp_id] = Employee(
            id=emp_id,
            name=cfg.name,
            nickname=cfg.nickname,
            level=cfg.level,
            department=cfg.department,
            role=cfg.role,
            skills=cfg.skills,
            current_quarter_tasks=cfg.current_quarter_tasks,
            performance_history=list(cfg.performance_history),
            desk_position=tuple(cfg.desk_position),
            sprite=cfg.sprite,
        )


def _seed_culture_wall() -> None:
    """Load culture wall items from culture_wall.yaml."""
    from onemancompany.core.config import load_culture_wall

    company_state.culture_wall = load_culture_wall()


_seed_employees()
_seed_ex_employees()
_seed_culture_wall()
