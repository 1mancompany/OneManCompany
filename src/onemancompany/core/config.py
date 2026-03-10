from __future__ import annotations

import json
from pathlib import Path

import yaml
from loguru import logger
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
COMPANY_DIR = PROJECT_ROOT / "company"

# ---------------------------------------------------------------------------
# Directory paths (all company data lives under company/)
# ---------------------------------------------------------------------------
HR_DIR = COMPANY_DIR / "human_resource"
EMPLOYEES_DIR = HR_DIR / "employees"
EX_EMPLOYEES_DIR = HR_DIR / "ex-employees"
ASSETS_DIR = COMPANY_DIR / "assets"
TOOLS_DIR = ASSETS_DIR / "tools"
ROOMS_DIR = ASSETS_DIR / "rooms"
PLUGINS_DIR = ASSETS_DIR / "plugins"
BUSINESS_DIR = COMPANY_DIR / "business"
WORKFLOWS_DIR = BUSINESS_DIR / "workflows"
PROJECTS_DIR = BUSINESS_DIR / "projects"
REPORTS_DIR = BUSINESS_DIR / "reports"
MEETING_REPORTS_DIR = REPORTS_DIR / "meeting_reports"
RESOLUTIONS_DIR = BUSINESS_DIR / "resolutions"
COMPANY_CULTURE_FILE = COMPANY_DIR / "company_culture.yaml"
COMPANY_DIRECTION_FILE = COMPANY_DIR / "company_direction.yaml"
SHARED_PROMPTS_DIR = COMPANY_DIR / "shared_prompts"
SOP_DIR = COMPANY_DIR / "operations" / "sops"
PROFILE_TEMPLATE = EMPLOYEES_DIR / "profile_template.yaml"

# Talent market
TALENT_MARKET_DIR = Path(__file__).parent.parent / "talent_market"
TALENTS_DIR = TALENT_MARKET_DIR / "talents"

# ---------------------------------------------------------------------------
# Founding member IDs (permanent employee numbers)
# ---------------------------------------------------------------------------
CEO_ID = "00001"
HR_ID = "00002"
COO_ID = "00003"
EA_ID = "00004"
CSO_ID = "00005"

# All founding executive IDs (excluding CEO)
EXEC_IDS: frozenset[str] = frozenset({HR_ID, COO_ID, EA_ID, CSO_ID})
# All founding IDs including CEO
FOUNDING_IDS: frozenset[str] = frozenset({CEO_ID}) | EXEC_IDS

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
PROBATION_TASKS = 2                            # tasks to complete during probation
PIP_DEADLINE_QUARTERS = 1                      # quarters to resolve a PIP

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
    "Engineer": "Engineering",
    "DevOps": "Engineering",
    "QA": "Engineering",
    "Designer": "Design",
    "Analyst": "Analytics",
    "Marketing": "Marketing",
}
DEFAULT_DEPARTMENT = "General"

# ---------------------------------------------------------------------------
# Prompt truncation limits (characters)
# ---------------------------------------------------------------------------
MAX_SUMMARY_LEN = 300
MAX_PRINCIPLES_LEN = 400
MAX_WORKFLOW_CONTEXT_LEN = 800
MAX_DISCUSSION_SUMMARY_LEN = 500

# ---------------------------------------------------------------------------
# Desk position grid layout (legacy — kept for compatibility)
# ---------------------------------------------------------------------------
DESK_GRID_COLS = 5
DESK_START_X = 2
DESK_START_Y = 2
DESK_SPACING_X = 3
DESK_SPACING_Y = 3

# ---------------------------------------------------------------------------
# Department-based office layout
# ---------------------------------------------------------------------------
EXEC_ROW_GY = 0          # grid-Y for executive row
DEPT_START_ROW = 3        # first grid-Y for department zones (gap from exec row to avoid click overlap)
DEPT_END_ROW = 9          # last grid-Y for department zones
DEPT_MIN_ZONE_WIDTH = 3   # minimum columns per department zone
DEPT_DESK_SPACING_X = 3   # horizontal spacing between desks within a zone
DEPT_DESK_ROWS = [3, 6, 9]  # grid-Y rows where desks can be placed

# Stable left-to-right ordering of departments
DEPT_ORDER = [
    "Engineering",
    "Design",
    "Analytics",
    "Marketing",
    "General",
]

# Department zone colors: department -> (floor1, floor2, label_color)
DEPT_COLORS: dict[str, tuple[str, str, str]] = {
    "Engineering": ("#1a2a3e", "#162636", "#4488cc"),   # blue tones
    "Design":      ("#2a1a3e", "#261636", "#aa44cc"),   # purple tones
    "Analytics":   ("#1a3a2e", "#163626", "#44cc88"),   # green tones
    "Marketing":   ("#3a2a1a", "#362616", "#cc8844"),   # orange tones
    "General":     ("#2a2a2a", "#262626", "#888888"),   # gray tones
}

# Legacy Chinese → English department mapping (for migrating old profiles)
DEPT_CN_TO_EN: dict[str, str] = {
    "技术研发部": "Engineering",
    "设计部": "Design",
    "数据分析部": "Analytics",
    "市场营销部": "Marketing",
    "综合部": "General",
    "人力资源部": "HR",
    "运营管理部": "Operations",
}

# Executive row floor colors
EXEC_FLOOR_COLORS = ("#2a2a20", "#26261e")  # gold tones

# ---------------------------------------------------------------------------
# Task routing keywords
# ---------------------------------------------------------------------------
HR_KEYWORDS = [
    "hire", "recruit", "employee", "staff", "review", "performance",
    "fire", "dismiss", "terminate",
    "招聘", "员工", "评价", "评估", "花名", "晋升", "开除", "解雇", "辞退",
]

ENGINEERING_DEPT = "Engineering"

# Default tool permissions by department (set during hiring)
# Note: read, ls, write, edit are now BASE_TOOLS (always available, no permission needed).
# Only gated tools need to be listed here.
DEFAULT_TOOL_PERMISSIONS: dict[str, list[str]] = {
    "Engineering": [
        "bash", "use_tool",
        "sandbox_execute_code", "sandbox_run_command", "sandbox_write_file", "sandbox_read_file",
    ],
    "Design": ["use_tool"],
    "Analytics": ["use_tool"],
    "Marketing": ["use_tool"],
    "General": [],
}
DEFAULT_TOOL_PERMISSIONS_FALLBACK: list[str] = []

SALES_KEYWORDS = [
    "sales", "sell", "client", "customer", "contract", "deal", "revenue",
    "销售", "客户", "合同", "订单", "营收", "商务", "签约",
]

# Inquiry routing table: list of (keywords, role, employee_id).
# Checked in order; first match wins. Last entry is the default fallback.
INQUIRY_ROUTES: list[tuple[list[str], str, str]] = [
    (HR_KEYWORDS, "HR", HR_ID),
    (SALES_KEYWORDS, "CSO", CSO_ID),
    ([], "COO", COO_ID),  # fallback — empty keywords = always matches
]


def route_inquiry(task: str) -> tuple[str, str]:
    """Route an inquiry task to the best agent. Returns (role, employee_id)."""
    task_lower = task.lower()
    for keywords, role, eid in INQUIRY_ROUTES:
        if not keywords or any(w in task_lower for w in keywords):
            return role, eid
    # Should never reach here due to fallback, but just in case
    return "COO", COO_ID


class EmployeeConfig(BaseModel):
    """Configuration loaded from employees/{id}/profile.yaml."""

    name: str
    role: str
    skills: list[str]
    nickname: str = ""  # Chinese alias
    level: int = 1  # 1-3 normal, 4 founding, 5 CEO
    department: str = ""  # assigned by HR
    desk_position: list[int] = []
    sprite: str = "employee_default"
    llm_model: str = ""  # empty = use default
    temperature: float = 0.7
    image_model: str = ""  # e.g. "nano-banana" for image generation
    employee_number: str = ""  # 5-digit ID string
    current_quarter_tasks: int = 0
    performance_history: list[dict] = []
    permissions: list[str] = []  # e.g. ["company_file_access", "web_search", "backend_code_maintenance"]
    tool_permissions: list[str] = []  # LangChain tool names this employee is authorized to use
    remote: bool = False  # True = remote worker, False = on-site
    salary_per_1m_tokens: float = 0.0  # Salary in USD per 1M tokens (avg of input+output cost)
    probation: bool = True  # new hires start on probation
    okrs: list[dict] = []  # OKR objectives
    pip: dict | None = None  # Performance Improvement Plan (if active)
    onboarding_completed: bool = False  # set True after onboarding routine
    api_provider: str = "openrouter"  # "openrouter" | "anthropic"
    api_key: str = ""  # Custom API key (used when api_provider != "openrouter")
    hosting: str = "company"  # "company" = 员工宿舍 (server manages agent loop) | "self" = 自带住宿 (external process, e.g. Claude Code)
    auth_method: str = "api_key"  # "api_key" | "oauth" (OAuth PKCE for Anthropic)
    oauth_refresh_token: str = ""  # OAuth refresh token (long-lived)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # OpenRouter API
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Anthropic API (company-level fallback for employees with api_provider=anthropic)
    anthropic_api_key: str = ""
    anthropic_auth_method: str = "api_key"  # "api_key" | "oauth"
    anthropic_refresh_token: str = ""

    # Default model
    default_llm_model: str = "moonshotai/kimi-k2.5"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    hr_review_interval_seconds: int = 300


settings = Settings()


def update_env_var(key: str, value: str) -> None:
    """Update or add a variable in the .env file, then reload settings."""
    env_path = PROJECT_ROOT / ".env"
    lines: list[str] = []
    found = False
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
                lines[i] = f"{key}={value}"
                found = True
                break
    if not found:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    reload_settings()


def reload_settings() -> None:
    """Re-read .env into the global settings singleton."""
    global settings
    settings = Settings()


# ---------------------------------------------------------------------------
# Application config (config.yaml at project root)
# ---------------------------------------------------------------------------
APP_CONFIG_PATH = PROJECT_ROOT / "config.yaml"

# Cached in-memory copy — read once at import, refreshed by reload_app_config()
_app_config: dict = {}


def _read_app_config_from_disk() -> dict:
    """Read config.yaml from disk. Returns empty dict if missing."""
    if not APP_CONFIG_PATH.exists():
        return {}
    with open(APP_CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def load_app_config() -> dict:
    """Return the cached application config (call reload_app_config() to refresh)."""
    return _app_config


def reload_app_config() -> dict:
    """Re-read config.yaml from disk into the in-memory cache. Returns the new config."""
    global _app_config
    _app_config = _read_app_config_from_disk()
    return _app_config


def is_hot_reload_enabled() -> bool:
    """Check whether config hot-reload is enabled."""
    return bool(_app_config.get("hot_reload", False))


# Load once at import time
_app_config = _read_app_config_from_disk()


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
    """Load skills from employees/{id}/skills/<name>/SKILL.md as {name: content} dict."""
    skills_dir = EMPLOYEES_DIR / employee_id / "skills"
    if not skills_dir.exists():
        return {}
    result: dict[str, str] = {}
    for entry in sorted(skills_dir.iterdir()):
        if entry.is_dir():
            skill_md = entry / "SKILL.md"
            if skill_md.is_file():
                result[entry.name] = skill_md.read_text(encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# YAML profile utilities — single source of truth for employee disk I/O
# ---------------------------------------------------------------------------


def load_employee_profile_yaml(employee_id: str) -> dict:
    """Load an employee's profile.yaml from disk. Returns empty dict if missing."""
    profile_path = EMPLOYEES_DIR / employee_id / "profile.yaml"
    if not profile_path.exists():
        return {}
    with open(profile_path) as f:
        return yaml.safe_load(f) or {}


def save_employee_profile_yaml(employee_id: str, data: dict) -> None:
    """Write a full profile dict to employees/{id}/profile.yaml."""
    profile_path = EMPLOYEES_DIR / employee_id / "profile.yaml"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    with open(profile_path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def update_employee_profile(employee_id: str, updates: dict) -> dict:
    """Read-modify-write: merge *updates* into an employee's profile.yaml.

    Returns the full updated profile dict.
    """
    data = load_employee_profile_yaml(employee_id)
    if not data:
        return {}
    data.update(updates)
    save_employee_profile_yaml(employee_id, data)
    return data


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
    """Persist work principles as an autoloaded skill."""
    skill_dir = EMPLOYEES_DIR / employee_id / "skills" / "work-principles"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_content = (
        "---\nname: Work Principles\n"
        "description: Core work principles from CEO guidance\n"
        "autoload: true\n---\n\n" + content
    )
    (skill_dir / "SKILL.md").write_text(skill_content, encoding="utf-8")


def get_workspace_dir(employee_id: str) -> Path:
    """Return the private workspace directory for an employee."""
    return EMPLOYEES_DIR / employee_id / "workspace"


def ensure_employee_dir(employee_id: str) -> Path:
    """Ensure employees/{id}/, skills/, and workspace/ directories exist."""
    emp_dir = EMPLOYEES_DIR / employee_id
    skills_dir = emp_dir / "skills"
    workspace_dir = emp_dir / "workspace"
    emp_dir.mkdir(parents=True, exist_ok=True)
    skills_dir.mkdir(exist_ok=True)
    workspace_dir.mkdir(exist_ok=True)
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
        perms_lines = "\n".join(f"  - {p}" for p in config.permissions) if config.permissions else "  []"
        tool_perms_lines = "\n".join(f"  - {t}" for t in config.tool_permissions) if config.tool_permissions else "  []"
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
            permissions=perms_lines,
            tool_permissions=tool_perms_lines,
            salary_per_1m_tokens=config.salary_per_1m_tokens,
            probation=str(config.probation).lower(),
            onboarding_completed=str(config.onboarding_completed).lower(),
            api_provider=config.api_provider,
            api_key=config.api_key,
            hosting=config.hosting,
            auth_method=config.auth_method,
        )
        profile_path.write_text(rendered, encoding="utf-8")
    else:
        data = config.model_dump()
        with open(profile_path, "w") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    # Keep in-memory employee_configs in sync
    employee_configs[employee_id] = config


def update_tool_permissions(employee_id: str, tool_permissions: list[str]) -> None:
    """Persist tool_permissions into an existing employee profile.yaml."""
    update_employee_profile(employee_id, {"tool_permissions": tool_permissions})
    if employee_id in employee_configs:
        employee_configs[employee_id].tool_permissions = list(tool_permissions)


def update_employee_performance(employee_id: str, current_quarter_tasks: int, performance_history: list[dict]) -> None:
    """Persist performance fields into an existing employee profile.yaml."""
    update_employee_profile(employee_id, {
        "current_quarter_tasks": current_quarter_tasks,
        "performance_history": performance_history,
    })


def update_employee_level(employee_id: str, level: int, title: str) -> None:
    """Persist level and title into an existing employee profile.yaml."""
    update_employee_profile(employee_id, {"level": level, "title": title})


def update_employee_field(employee_id: str, field: str, value) -> None:
    """Persist a single field into an existing employee profile.yaml."""
    update_employee_profile(employee_id, {field: value})


def slugify_tool_name(name: str) -> str:
    """Convert a tool name to a folder-safe slug.

    Lowercase, spaces→underscores, keep CJK chars and alphanumerics,
    remove other special characters.
    """
    import re as _re
    slug = name.lower().strip()
    slug = slug.replace(" ", "_")
    # Keep word chars (includes CJK via Unicode) and underscores
    slug = _re.sub(r'[^\w]', '', slug)
    # Collapse multiple underscores
    slug = _re.sub(r'_+', '_', slug).strip('_')
    return slug or "unnamed_tool"


def load_assets() -> tuple[dict, dict]:
    """Scan assets/tools/ and assets/rooms/ directories. Returns (tools_dict, rooms_dict).

    Tools can be either:
    - **Folder-based** (new): tools/{slug_name}/tool.yaml
    - **Flat YAML** (legacy): tools/{uuid}.yaml  — tagged with _legacy=True
    """
    tools: dict[str, dict] = {}
    meeting_rooms: dict[str, dict] = {}
    if TOOLS_DIR.exists():
        for entry in sorted(TOOLS_DIR.iterdir()):
            if entry.is_dir():
                # New folder-based format
                tool_yaml = entry / "tool.yaml"
                if tool_yaml.exists():
                    with open(tool_yaml) as fh:
                        data = yaml.safe_load(fh) or {}
                    data["_folder_name"] = entry.name
                    # List extra files in the folder (excluding tool.yaml)
                    data["_files"] = [
                        f.name for f in sorted(entry.iterdir())
                        if f.is_file() and f.name != "tool.yaml"
                    ]
                    tool_id = data.get("id", entry.name)
                    tools[tool_id] = data
            elif entry.suffix == ".yaml" and entry.is_file():
                # Legacy flat YAML format
                with open(entry) as fh:
                    data = yaml.safe_load(fh) or {}
                data["_legacy"] = True
                tools[entry.stem] = data
    if ROOMS_DIR.exists():
        for f in sorted(ROOMS_DIR.iterdir()):
            if f.suffix == ".yaml" and f.is_file():
                with open(f) as fh:
                    data = yaml.safe_load(fh) or {}
                meeting_rooms[f.stem] = data
    return tools, meeting_rooms


def migrate_legacy_tool(tool_id: str, data: dict, used_slugs: set[str] | None = None) -> tuple[str, str]:
    """Migrate a flat assets/tools/{uuid}.yaml to assets/tools/{slug}/tool.yaml.

    Returns (folder_name, new_tool_id) after migration.
    Handles slug collisions by appending the UUID suffix.
    """
    import shutil

    if used_slugs is None:
        used_slugs = set()

    name = data.get("name", tool_id)
    slug = slugify_tool_name(name)

    # Handle collision
    if slug in used_slugs:
        slug = f"{slug}_{tool_id}"
    used_slugs.add(slug)

    folder = TOOLS_DIR / slug
    folder.mkdir(parents=True, exist_ok=True)

    # Write tool.yaml with id field
    tool_data = {k: v for k, v in data.items() if not k.startswith("_")}
    tool_data["id"] = tool_id
    with open(folder / "tool.yaml", "w") as f:
        yaml.dump(tool_data, f, allow_unicode=True, default_flow_style=False)

    # Remove old flat file
    old_path = TOOLS_DIR / f"{tool_id}.yaml"
    if old_path.exists():
        old_path.unlink()

    return slug, tool_id


def load_workflows() -> dict[str, str]:
    """Load all workflow .md files from business/workflows/ as {filename_stem: content}."""
    if not WORKFLOWS_DIR.exists():
        return {}
    result: dict[str, str] = {}
    for f in sorted(WORKFLOWS_DIR.iterdir()):
        if f.suffix == ".md" and f.is_file():
            result[f.stem] = f.read_text(encoding="utf-8")
    return result


def save_workflow(name: str, content: str) -> None:
    """Save a workflow .md file to business/workflows/."""
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    path = WORKFLOWS_DIR / f"{name}.md"
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


def load_company_culture() -> list[dict]:
    """Load company culture items from company_culture.yaml."""
    if not COMPANY_CULTURE_FILE.exists():
        return []
    with open(COMPANY_CULTURE_FILE) as f:
        data = yaml.safe_load(f)
    if isinstance(data, list):
        return data
    return []


def save_company_culture(items: list[dict]) -> None:
    """Persist company culture items to company_culture.yaml."""
    with open(COMPANY_CULTURE_FILE, "w") as f:
        yaml.dump(items, f, allow_unicode=True, default_flow_style=False)


# ---------------------------------------------------------------------------
# Company direction
# ---------------------------------------------------------------------------

def load_company_direction() -> str:
    """Load company direction from company_direction.yaml."""
    if not COMPANY_DIRECTION_FILE.exists():
        return ""
    with open(COMPANY_DIRECTION_FILE) as f:
        data = yaml.safe_load(f)
    if isinstance(data, dict):
        return data.get("direction", "")
    return ""


def save_company_direction(direction: str) -> None:
    """Persist company direction to company_direction.yaml."""
    from datetime import datetime
    data = {
        "direction": direction,
        "updated_at": datetime.now().isoformat(),
        "updated_by": "CEO",
    }
    with open(COMPANY_DIRECTION_FILE, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


# ---------------------------------------------------------------------------
# Employee manifest (manifest.json)
# ---------------------------------------------------------------------------

MANIFEST_CACHE: dict[str, dict] = {}


def load_manifest(employee_id: str) -> dict | None:
    """Load manifest.json for an employee, with caching."""
    if employee_id in MANIFEST_CACHE:
        return MANIFEST_CACHE[employee_id]
    manifest_path = EMPLOYEES_DIR / employee_id / "manifest.json"
    if not manifest_path.exists():
        return None
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    MANIFEST_CACHE[employee_id] = data
    return data


def invalidate_manifest_cache(employee_id: str | None = None) -> None:
    """Clear manifest cache. If employee_id is None, clear all."""
    if employee_id is None:
        MANIFEST_CACHE.clear()
    else:
        MANIFEST_CACHE.pop(employee_id, None)


# ---------------------------------------------------------------------------
# Talent market helpers
# ---------------------------------------------------------------------------


def load_talent_profile(talent_id: str) -> dict:
    """Load a talent profile from talents/{id}/profile.yaml.

    Returns the parsed YAML as a dict, or empty dict if not found.
    """
    profile_path = TALENTS_DIR / talent_id / "profile.yaml"
    if not profile_path.exists():
        return {}
    with open(profile_path) as f:
        return yaml.safe_load(f) or {}




def load_talent_tools(talent_id: str) -> list[str]:
    """Load tool names declared in talents/{id}/tools/manifest.yaml.

    Returns a flat list of all tool names (builtin + custom).
    """
    manifest_path = TALENTS_DIR / talent_id / "tools" / "manifest.yaml"
    if not manifest_path.exists():
        return []
    with open(manifest_path) as f:
        data = yaml.safe_load(f) or {}
    tools: list[str] = list(data.get("builtin_tools", []))
    tools.extend(data.get("custom_tools", []))
    return tools


def load_talent_skills(talent_id: str) -> list[str]:
    """Load skill markdown files from talents/{id}/skills/.

    Returns a list of skill file contents (one string per .md file).
    """
    skills_dir = TALENTS_DIR / talent_id / "skills"
    if not skills_dir.exists():
        return []
    result: list[str] = []
    for skill_file in sorted(skills_dir.iterdir()):
        if skill_file.suffix == ".md" and skill_file.is_file():
            result.append(skill_file.read_text(encoding="utf-8"))
    return result


def list_available_talents() -> list[dict]:
    """List all available talent packages under talents/.

    Returns a list of dicts with basic talent info (id, name, role, remote).
    """
    if not TALENTS_DIR.exists():
        return []
    result: list[dict] = []
    for talent_dir in sorted(TALENTS_DIR.iterdir()):
        if not talent_dir.is_dir():
            continue
        profile_path = talent_dir / "profile.yaml"
        if not profile_path.exists():
            continue
        with open(profile_path) as f:
            data = yaml.safe_load(f) or {}
        result.append({
            "id": data.get("id", talent_dir.name),
            "name": data.get("name", talent_dir.name),
            "role": data.get("role", ""),
            "remote": data.get("remote", False),
            "description": data.get("description", ""),
            "api_provider": data.get("api_provider", "openrouter"),
        })
    return result


# Load all employee configs at import time
employee_configs = load_employee_configs()
