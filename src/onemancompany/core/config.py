from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
EMPLOYEES_DIR = PROJECT_ROOT / "employees"
EX_EMPLOYEES_DIR = PROJECT_ROOT / "ex-employees"  # 离职员工档案
EQUIPMENT_DIR = PROJECT_ROOT / "equipment_room"  # 设备间
RULES_DIR = PROJECT_ROOT / "company_rules"  # 规章制度
PROJECTS_DIR = PROJECT_ROOT / "projects"  # 项目档案
CULTURE_WALL_FILE = PROJECT_ROOT / "culture_wall.yaml"  # 公司文化墙
PROFILE_TEMPLATE = EMPLOYEES_DIR / "profile_template.yaml"


class EmployeeConfig(BaseModel):
    """Configuration loaded from employees/{id}/profile.yaml."""

    name: str
    role: str
    skills: list[str]
    nickname: str = ""  # 花名 — 创始员工三个字，其他员工两个字
    level: int = 1  # 级别: 1-3 普通, 4 创始, 5 CEO
    department: str = ""  # 部门 — 由HR分配
    desk_position: list[int]
    sprite: str = "employee_default"
    llm_model: str = ""  # empty → use default
    temperature: float = 0.7
    employee_number: str = ""  # 工号 — 5位数字
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


def load_equipment() -> tuple[dict, dict]:
    """Scan equipment_room/ directory. Returns (tools_dict, meeting_rooms_dict) of raw dicts."""
    tools: dict[str, dict] = {}
    meeting_rooms: dict[str, dict] = {}
    if not EQUIPMENT_DIR.exists():
        return tools, meeting_rooms
    for eq_file in sorted(EQUIPMENT_DIR.iterdir()):
        if eq_file.suffix != ".yaml" or not eq_file.is_file():
            continue
        with open(eq_file) as f:
            data = yaml.safe_load(f) or {}
        eq_id = eq_file.stem
        if data.get("type") == "meeting_room":
            meeting_rooms[eq_id] = data
        else:
            tools[eq_id] = data
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
