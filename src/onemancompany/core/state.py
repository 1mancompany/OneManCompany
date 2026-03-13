from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from onemancompany.core.config import (
    EMPLOYEES_DIR,
    FOUNDING_LEVEL,
    STATUS_IDLE,
)
from loguru import logger
from onemancompany.core.models import OverheadCosts


@dataclass
class TaskEntry:
    """A tracked task in the company task queue.

    Status values follow TaskPhase:
      pending, processing, holding, complete, needs_acceptance,
      accepted, rejected, rectification, reviewing, finished,
      failed, blocked, cancelled
    """

    project_id: str        # v1 = timestamp ID, v2 = project slug
    task: str
    routed_to: str = ""    # "HR", "COO", "EA", etc.
    task_type: str = "simple"  # "simple" or "project"
    iteration_id: str = ""  # v2 = iter_XXX, v1 = empty
    project_dir: str = ""  # absolute path to project workspace
    current_owner: str = ""  # employee_id of current owner
    status: str = "pending"  # follows TaskPhase values
    result: str = ""       # task output / report on completion
    created_at: str = ""
    completed_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.current_owner and self.routed_to:
            self.current_owner = self.routed_to.lower()

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "iteration_id": self.iteration_id,
            "task": self.task,
            "task_type": self.task_type,
            "routed_to": self.routed_to,
            "project_dir": self.project_dir,
            "current_owner": self.current_owner,
            "status": self.status,
            "result": self.result,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


def get_active_tasks() -> list[TaskEntry]:
    """Build active task list from EmployeeManager._schedule + TaskTree nodes.

    Reads scheduled nodes from the singleton EmployeeManager and loads
    their tree files to build TaskEntry objects.
    """
    from pathlib import Path
    from onemancompany.core.task_tree import TaskTree

    result: list[TaskEntry] = []
    try:
        from onemancompany.core.vessel import employee_manager
    except Exception:
        return result

    for employee_id, entries in employee_manager._schedule.items():
        for entry in entries:
            tp = Path(entry.tree_path)
            if not tp.exists():
                continue
            try:
                tree = TaskTree.load(tp)
                node = tree.get_node(entry.node_id)
                if not node:
                    continue
                result.append(TaskEntry(
                    project_id=node.project_id,
                    task=node.description,
                    task_type=node.task_type,
                    project_dir=node.project_dir,
                    current_owner=employee_id,
                    status=node.status,
                    result=node.result or "",
                    created_at=node.created_at or "",
                    completed_at=node.completed_at or "",
                ))
            except Exception as e:
                logger.warning("Failed to load task tree {}: {}", tree_path, e)
    return result


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
    "HR": "HR", "COO": "COO", "EA": "EA", "CSO": "CSO",
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
    performance_history: list = field(default_factory=list)  # list[PerformanceRecord | dict]
    desk_position: tuple[int, int] = (0, 0)
    sprite: str = "employee_default"
    guidance_notes: list[str] = field(default_factory=list)
    work_principles: str = ""  # loaded from employees/{id}/work_principles.md
    permissions: list[str] = field(default_factory=list)  # access control: company_file_access, web_search, backend_code_maintenance, etc.
    tool_permissions: list[str] = field(default_factory=list)  # LangChain tool names this employee can use
    remote: bool = False  # True = remote worker, False = on-site employee
    salary_per_1m_tokens: float = 0.0  # Salary in USD per 1M tokens
    probation: bool = False  # True during probation period
    okrs: list[dict] = field(default_factory=list)  # OKR objectives
    pip: dict | None = None  # Performance Improvement Plan
    onboarding_completed: bool = True  # False until onboarding routine finishes
    status: str = STATUS_IDLE
    is_listening: bool = False
    current_task_summary: str = ""
    api_online: bool = True       # heartbeat check result
    needs_setup: bool = False     # needs API key / OAuth login

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
            "tool_permissions": self.tool_permissions,
            "remote": self.remote,
            "salary_per_1m_tokens": self.salary_per_1m_tokens,
            "probation": self.probation,
            "okrs": self.okrs,
            "pip": self.pip,
            "onboarding_completed": self.onboarding_completed,
            "status": self.status,
            "is_listening": self.is_listening,
            "current_task_summary": self.current_task_summary,
            "api_online": self.api_online,
            "needs_setup": self.needs_setup,
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
    has_icon: bool = False  # True if icon.png exists in tool folder
    tool_type: str = "template"  # "template" | "script" | "reference"
    reference_url: str = ""

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
            "has_icon": self.has_icon,
            "tool_type": self.tool_type,
            "reference_url": self.reference_url,
        }


@dataclass
class CompanyState:
    tools: dict[str, OfficeTool] = field(default_factory=dict)
    meeting_rooms: dict[str, MeetingRoom] = field(default_factory=dict)
    ceo_tasks: list[str] = field(default_factory=list)
    office_layout: dict = field(default_factory=dict)
    overhead_costs: "OverheadCosts | None" = None  # in-memory accumulator for LLM cost tracking
    _next_employee_number: int = 0  # auto-increment counter

    def __post_init__(self) -> None:
        if self.overhead_costs is None:
            self.overhead_costs = OverheadCosts()
        # Legacy attrs for test scaffolding — tests set cs.employees[id] = emp
        # and conftest._bridge_store_to_company_state reads them via getattr().
        # Production code uses store.load_employee() instead.
        if not hasattr(self, "employees"):
            self.employees: dict = {}
        if not hasattr(self, "ex_employees"):
            self.ex_employees: dict = {}

    def next_employee_number(self) -> str:
        """Generate next 5-digit employee number."""
        num = self._next_employee_number
        self._next_employee_number += 1
        return f"{num:05d}"


# Singleton
company_state = CompanyState()


def _init_employee_counter() -> None:
    """Set the next employee number counter from existing employee dirs."""
    if not EMPLOYEES_DIR.exists():
        company_state._next_employee_number = 6
        return
    max_num = 5  # start after founding employees
    for emp_dir in EMPLOYEES_DIR.iterdir():
        if emp_dir.is_dir():
            try:
                num = int(emp_dir.name)
                if num > max_num:
                    max_num = num
            except ValueError:
                continue  # non-numeric directory name — skip
    company_state._next_employee_number = max_num + 1


_init_employee_counter()

# Compute initial department-based office layout
from onemancompany.core.layout import compute_layout  # noqa: E402
compute_layout(company_state)


# Whether a reload is pending (deferred because agents were busy)
_reload_pending: bool = False


def is_idle() -> bool:
    """Return True if no agent tasks are currently running."""
    return len(get_active_tasks()) == 0


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
    """Mark all categories dirty so the next sync tick triggers a frontend refresh.

    Since all business data reads go through store.py (disk is the single source
    of truth), there is no in-memory cache to invalidate.  We only need to:

    1. Reload app config (the one legitimate in-memory cache).
    2. Mark every data category dirty for the 3-second sync tick.
    3. Refresh the employee counter (in-memory counter for ID generation).
    4. Recompute office layout.
    """
    from onemancompany.core.config import invalidate_manifest_cache, reload_app_config
    from onemancompany.core.store import mark_dirty

    reload_app_config()

    mark_dirty(
        "employees", "ex_employees", "rooms", "tools", "task_queue",
        "culture", "activity_log", "sales_tasks", "direction",
    )
    invalidate_manifest_cache()

    _init_employee_counter()

    compute_layout(company_state)

    return {"status": "dirty_marked", "categories": "all"}


# Snapshot provider "company_state" removed — Task 13.
# Employee statuses are now persisted in profile.yaml runtime: section.
# Activity log, culture, sales, overhead all on disk via store.
