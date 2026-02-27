from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

# ---------------------------------------------------------------------------
# Directory paths
# ---------------------------------------------------------------------------
EMPLOYEES_DIR = PROJECT_ROOT / "employees"
EX_EMPLOYEES_DIR = PROJECT_ROOT / "ex-employees"
ASSETS_DIR = PROJECT_ROOT / "assets"
TOOLS_DIR = ASSETS_DIR / "tools"
ROOMS_DIR = ASSETS_DIR / "rooms"
RULES_DIR = PROJECT_ROOT / "company_rules"
PROJECTS_DIR = PROJECT_ROOT / "projects"
CULTURE_WALL_FILE = PROJECT_ROOT / "culture_wall.yaml"
PROFILE_TEMPLATE = EMPLOYEES_DIR / "profile_template.yaml"

# ---------------------------------------------------------------------------
# Founding member IDs (permanent employee numbers)
# ---------------------------------------------------------------------------
CEO_ID = "00001"
HR_ID = "00002"
COO_ID = "00003"

# ---------------------------------------------------------------------------
# Employee level system
# ---------------------------------------------------------------------------
MAX_NORMAL_LEVEL = 3        # highest level for regular employees
FOUNDING_LEVEL = 4          # founding employees
CEO_LEVEL = 5               # CEO

# ---------------------------------------------------------------------------
# Performance & quarterly review
# ---------------------------------------------------------------------------
TASKS_PER_QUARTER = 3                          # tasks needed before a review
VALID_SCORES = {3.25, 3.5, 3.75}              # allowed performance tiers
SCORE_NEEDS_IMPROVEMENT = 3.25
SCORE_QUALIFIED = 3.5
SCORE_EXCELLENT = 3.75
QUARTERS_FOR_PROMOTION = 3                     # consecutive excellent quarters
MAX_PERFORMANCE_HISTORY = 3                    # quarters of history to keep

# ---------------------------------------------------------------------------
# Employee status
# ---------------------------------------------------------------------------
STATUS_IDLE = "idle"
STATUS_WORKING = "working"
STATUS_IN_MEETING = "in_meeting"

# ---------------------------------------------------------------------------
# Role-to-department mapping
# ---------------------------------------------------------------------------
ROLE_DEPARTMENT_MAP: dict[str, str] = {
    "Engineer": "技术研发部",
    "DevOps": "技术研发部",
    "QA": "技术研发部",
    "Designer": "设计部",
    "Analyst": "数据分析部",
    "Marketing": "市场营销部",
}
DEFAULT_DEPARTMENT = "综合部"

# ---------------------------------------------------------------------------
# Prompt truncation limits (characters)
# ---------------------------------------------------------------------------
MAX_SUMMARY_LEN = 300
MAX_PRINCIPLES_LEN = 400
MAX_WORKFLOW_CONTEXT_LEN = 800
MAX_DISCUSSION_SUMMARY_LEN = 500

# ---------------------------------------------------------------------------
# Desk position grid layout
# ---------------------------------------------------------------------------
DESK_GRID_COLS = 5
DESK_START_X = 2
DESK_START_Y = 2
DESK_SPACING_X = 3
DESK_SPACING_Y = 3

# ---------------------------------------------------------------------------
# Task routing keywords
# ---------------------------------------------------------------------------
HR_KEYWORDS = [
    "hire", "recruit", "employee", "staff", "review", "performance",
    "fire", "dismiss", "terminate",
    "招聘", "员工", "评价", "评估", "花名", "晋升", "开除", "解雇", "辞退",
]


class EmployeeConfig(BaseModel):
    """Configuration loaded from employees/{id}/profile.yaml."""

    name: str
    role: str
    skills: list[str]
    nickname: str = ""  # Chinese alias
    level: int = 1  # 1-3 normal, 4 founding, 5 CEO
    department: str = ""  # assigned by HR
    desk_position: list[int]
    sprite: str = "employee_default"
    llm_model: str = ""  # empty = use default
    temperature: float = 0.7
    employee_number: str = ""  # 5-digit ID string
    current_quarter_tasks: int = 0
    performance_history: list[dict] = []


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # OpenRouter API
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Default model
    default_llm_model: str = "moonshotai/kimi-k2.5"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    hr_review_interval_seconds: int = 300


settings = Settings()


def load_employee_configs() -> dict[str, EmployeeConfig]:
    """Scan employees/ directory. Each subfolder with a profile.yaml is an employee."""
    if not EMPLOYEES_DIR.exists():
        return {}
    result: dict[str, EmployeeConfig] = {}
    for emp_dir in sorted(EMPLOYEES_DIR.iterdir()):
        if not emp_dir.is_dir():
            continue
        profile_path = emp_dir / "profile.yaml"
        if not profile_path.exists():
            continue
        with open(profile_path) as f:
            raw = yaml.safe_load(f) or {}
        emp_id = emp_dir.name
        result[emp_id] = EmployeeConfig(**raw)
    return result


def load_employee_skills(employee_id: str) -> dict[str, str]:
    """Load all skill files from employees/{id}/skills/ as {name: content} dict."""
    skills_dir = EMPLOYEES_DIR / employee_id / "skills"
    if not skills_dir.exists():
        return {}
    result: dict[str, str] = {}
    for skill_file in sorted(skills_dir.iterdir()):
        if skill_file.suffix == ".md" and skill_file.is_file():
            result[skill_file.stem] = skill_file.read_text(encoding="utf-8")
    return result


def load_employee_guidance(employee_id: str) -> list[str]:
    """Load persisted guidance notes from employees/{id}/guidance.yaml."""
    guidance_path = EMPLOYEES_DIR / employee_id / "guidance.yaml"
    if not guidance_path.exists():
        return []
    with open(guidance_path) as f:
        data = yaml.safe_load(f)
    if isinstance(data, list):
        return data
    return []


def save_employee_guidance(employee_id: str, notes: list[str]) -> None:
    """Persist guidance notes to employees/{id}/guidance.yaml."""
    emp_dir = EMPLOYEES_DIR / employee_id
    emp_dir.mkdir(parents=True, exist_ok=True)
    guidance_path = emp_dir / "guidance.yaml"
    with open(guidance_path, "w") as f:
        yaml.dump(notes, f, allow_unicode=True, default_flow_style=False)


def load_work_principles(employee_id: str) -> str:
    """Load work principles from employees/{id}/work_principles.md."""
    path = EMPLOYEES_DIR / employee_id / "work_principles.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def save_work_principles(employee_id: str, content: str) -> None:
    """Persist work principles to employees/{id}/work_principles.md."""
    emp_dir = EMPLOYEES_DIR / employee_id
    emp_dir.mkdir(parents=True, exist_ok=True)
    path = emp_dir / "work_principles.md"
    path.write_text(content, encoding="utf-8")


def ensure_employee_dir(employee_id: str) -> Path:
    """Ensure employees/{id}/ and employees/{id}/skills/ directories exist."""
    emp_dir = EMPLOYEES_DIR / employee_id
    skills_dir = emp_dir / "skills"
    emp_dir.mkdir(parents=True, exist_ok=True)
    skills_dir.mkdir(exist_ok=True)
    return emp_dir


def save_employee_profile(employee_id: str, config: EmployeeConfig) -> None:
    """Save a profile.yaml for a new employee, using profile_template.yaml if available."""
    emp_dir = ensure_employee_dir(employee_id)
    profile_path = emp_dir / "profile.yaml"

    if PROFILE_TEMPLATE.exists():
        template_text = PROFILE_TEMPLATE.read_text(encoding="utf-8")
        desk = config.desk_position if config.desk_position else [0, 0]
        skills_lines = "\n".join(f"  - {s}" for s in config.skills) if config.skills else "  []"
        # Format performance history for YAML
        if config.performance_history:
            perf_lines = "\n".join(
                f"  - {{score: {q['score']}, tasks: {q.get('tasks', 3)}}}"
                for q in config.performance_history
            )
        else:
            perf_lines = "  []"
        from onemancompany.core.state import make_title
        rendered = template_text.format(
            name=config.name,
            nickname=config.nickname,
            employee_number=config.employee_number,
            level=config.level,
            title=make_title(config.level, config.role),
            department=config.department,
            role=config.role,
            desk_x=desk[0],
            desk_y=desk[1],
            sprite=config.sprite,
            llm_model=config.llm_model or settings.default_llm_model,
            temperature=config.temperature,
            current_quarter_tasks=config.current_quarter_tasks,
            performance_history=perf_lines,
            skills=skills_lines,
        )
        profile_path.write_text(rendered, encoding="utf-8")
    else:
        data = config.model_dump()
        with open(profile_path, "w") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    # Keep in-memory employee_configs in sync
    employee_configs[employee_id] = config


def update_employee_performance(employee_id: str, current_quarter_tasks: int, performance_history: list[dict]) -> None:
    """Persist performance fields into an existing employee profile.yaml."""
    profile_path = EMPLOYEES_DIR / employee_id / "profile.yaml"
    if not profile_path.exists():
        return
    with open(profile_path) as f:
        data = yaml.safe_load(f) or {}
    data["current_quarter_tasks"] = current_quarter_tasks
    data["performance_history"] = performance_history
    with open(profile_path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def update_employee_level(employee_id: str, level: int, title: str) -> None:
    """Persist level and title into an existing employee profile.yaml."""
    profile_path = EMPLOYEES_DIR / employee_id / "profile.yaml"
    if not profile_path.exists():
        return
    with open(profile_path) as f:
        data = yaml.safe_load(f) or {}
    data["level"] = level
    data["title"] = title
    with open(profile_path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def load_assets() -> tuple[dict, dict]:
    """Scan assets/tools/ and assets/rooms/ directories. Returns (tools_dict, rooms_dict)."""
    tools: dict[str, dict] = {}
    meeting_rooms: dict[str, dict] = {}
    if TOOLS_DIR.exists():
        for f in sorted(TOOLS_DIR.iterdir()):
            if f.suffix == ".yaml" and f.is_file():
                with open(f) as fh:
                    data = yaml.safe_load(fh) or {}
                tools[f.stem] = data
    if ROOMS_DIR.exists():
        for f in sorted(ROOMS_DIR.iterdir()):
            if f.suffix == ".yaml" and f.is_file():
                with open(f) as fh:
                    data = yaml.safe_load(fh) or {}
                meeting_rooms[f.stem] = data
    return tools, meeting_rooms


def load_workflows() -> dict[str, str]:
    """Load all workflow .md files from company_rules/ as {filename_stem: content}."""
    if not RULES_DIR.exists():
        return {}
    result: dict[str, str] = {}
    for f in sorted(RULES_DIR.iterdir()):
        if f.suffix == ".md" and f.is_file():
            result[f.stem] = f.read_text(encoding="utf-8")
    return result


def save_workflow(name: str, content: str) -> None:
    """Save a workflow .md file to company_rules/."""
    RULES_DIR.mkdir(parents=True, exist_ok=True)
    path = RULES_DIR / f"{name}.md"
    path.write_text(content, encoding="utf-8")


def load_ex_employee_configs() -> dict[str, EmployeeConfig]:
    """Scan ex-employees/ directory. Each subfolder with a profile.yaml is an ex-employee."""
    if not EX_EMPLOYEES_DIR.exists():
        return {}
    result: dict[str, EmployeeConfig] = {}
    for emp_dir in sorted(EX_EMPLOYEES_DIR.iterdir()):
        if not emp_dir.is_dir():
            continue
        profile_path = emp_dir / "profile.yaml"
        if not profile_path.exists():
            continue
        with open(profile_path) as f:
            raw = yaml.safe_load(f) or {}
        emp_id = emp_dir.name
        result[emp_id] = EmployeeConfig(**raw)
    return result


def move_employee_to_ex(employee_id: str) -> bool:
    """Move an employee folder from employees/ to ex-employees/."""
    import shutil

    src = EMPLOYEES_DIR / employee_id
    if not src.exists():
        return False
    EX_EMPLOYEES_DIR.mkdir(parents=True, exist_ok=True)
    dst = EX_EMPLOYEES_DIR / employee_id
    if dst.exists():
        shutil.rmtree(dst)
    shutil.move(str(src), str(dst))
    # Remove from in-memory configs
    employee_configs.pop(employee_id, None)
    return True


def move_ex_employee_back(employee_id: str) -> bool:
    """Move an ex-employee folder from ex-employees/ back to employees/."""
    import shutil

    src = EX_EMPLOYEES_DIR / employee_id
    if not src.exists():
        return False
    dst = EMPLOYEES_DIR / employee_id
    if dst.exists():
        shutil.rmtree(dst)
    shutil.move(str(src), str(dst))
    # Reload into in-memory configs
    profile_path = dst / "profile.yaml"
    if profile_path.exists():
        with open(profile_path) as f:
            raw = yaml.safe_load(f) or {}
        employee_configs[employee_id] = EmployeeConfig(**raw)
    return True


def load_culture_wall() -> list[dict]:
    """Load culture wall items from culture_wall.yaml."""
    if not CULTURE_WALL_FILE.exists():
        return []
    with open(CULTURE_WALL_FILE) as f:
        data = yaml.safe_load(f)
    if isinstance(data, list):
        return data
    return []


def save_culture_wall(items: list[dict]) -> None:
    """Persist culture wall items to culture_wall.yaml."""
    with open(CULTURE_WALL_FILE, "w") as f:
        yaml.dump(items, f, allow_unicode=True, default_flow_style=False)


# Load all employee configs at import time
employee_configs = load_employee_configs()
