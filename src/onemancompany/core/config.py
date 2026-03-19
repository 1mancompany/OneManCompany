from __future__ import annotations

import json
from pathlib import Path

import yaml
from loguru import logger
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

# Source root — for package-relative resources (frontend, talent_market, etc.)
SOURCE_ROOT = Path(__file__).parent.parent.parent.parent

# Data root — all runtime/company data lives under cwd/.onemancompany/
# This allows the package to be installed anywhere while data stays portable.
DATA_DIR_NAME = ".onemancompany"
DATA_ROOT = Path.cwd() / DATA_DIR_NAME


COMPANY_DIR = DATA_ROOT / "company"

# ---------------------------------------------------------------------------
# Directory paths (all company data lives under .onemancompany/company/)
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

# ---------------------------------------------------------------------------
# Common filenames used across modules
# ---------------------------------------------------------------------------
PROFILE_FILENAME = "profile.yaml"
TASK_TREE_FILENAME = "task_tree.yaml"
GUIDANCE_FILENAME = "guidance.yaml"
MANIFEST_FILENAME = "manifest.json"
NODES_DIR_NAME = "nodes"
SOUL_FILENAME = "SOUL.md"
DOT_ENV_FILENAME = ".env"
TOOL_YAML_FILENAME = "tool.yaml"
PROJECT_YAML_FILENAME = "project.yaml"
MANIFEST_YAML_FILENAME = "manifest.yaml"
VESSEL_YAML_FILENAME = "vessel.yaml"
TALENT_PERSONA_FILENAME = "talent_persona.md"
MCP_CONFIG_FILENAME = "mcp_config.json"
CONVERSATIONS_DIR_NAME = "conversations"
PROGRESS_LOG_FILENAME = "progress.log"

# ---------------------------------------------------------------------------
# Common identifiers — canonical strings for sender/role/scope fields
# ---------------------------------------------------------------------------
SYSTEM_SENDER = "system"
MEETING_SYSTEM_SENDER = "Meeting System"


# ---------------------------------------------------------------------------
# Environment & logging
# ---------------------------------------------------------------------------
from enum import Enum

ENV_OMC_DEBUG = "OMC_DEBUG"
ENV_OMC_EMPLOYEE_ID = "OMC_EMPLOYEE_ID"
ENV_OMC_TASK_ID = "OMC_TASK_ID"
ENV_OMC_PROJECT_ID = "OMC_PROJECT_ID"
ENV_OMC_PROJECT_DIR = "OMC_PROJECT_DIR"
ENV_OMC_SERVER_URL = "OMC_SERVER_URL"

# .env variable names (used in onboarding and settings)
ENV_KEY_ANTHROPIC = "ANTHROPIC_API_KEY"
ENV_KEY_SKILLSMP = "SKILLSMP_API_KEY"
ENV_KEY_TALENT_MARKET = "TALENT_MARKET_API_KEY"
ENV_KEY_OPENROUTER = "OPENROUTER_API_KEY"
ENV_KEY_DEFAULT_PROVIDER = "DEFAULT_API_PROVIDER"
ENV_KEY_DEFAULT_MODEL = "DEFAULT_LLM_MODEL"
ENV_KEY_HOST = "HOST"
ENV_KEY_PORT = "PORT"
ENV_KEY_SANDBOX_ENABLED = "SANDBOX_ENABLED"
ENV_KEY_ANTHROPIC_AUTH = "ANTHROPIC_AUTH_METHOD"

# Provider name constants
PROVIDER_OPENROUTER = "openrouter"
PROVIDER_ANTHROPIC = "anthropic"

# Template directory name
COMPANY_TEMPLATE_DIR = "company"
CONFIG_YAML_FILENAME = "config.yaml"
ENCODING_UTF8 = "utf-8"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"


# ---------------------------------------------------------------------------
# OrgDir — enum of organizational knowledge directories
# ---------------------------------------------------------------------------


class OrgDir(str, Enum):
    """Organizational knowledge directory categories and their disk locations.

    Used by COO's deposit_company_knowledge tool and any code that needs
    to resolve where company-level knowledge is stored.
    """
    WORKFLOW = "workflow"       # Workflows, SOPs, and guidance (all operational docs)
    CULTURE = "culture"        # Company culture values
    DIRECTION = "direction"    # Company strategic direction

    @property
    def disk_path(self) -> Path:
        """Return the absolute disk path for this category."""
        return _ORG_DIR_PATHS[self]

    @property
    def description(self) -> str:
        """Human-readable description of this category."""
        return _ORG_DIR_DESCRIPTIONS[self]


_ORG_DIR_PATHS: dict["OrgDir", Path] = {
    OrgDir.WORKFLOW: WORKFLOWS_DIR,
    OrgDir.CULTURE: COMPANY_CULTURE_FILE,
    OrgDir.DIRECTION: COMPANY_DIRECTION_FILE,
}

_ORG_DIR_DESCRIPTIONS: dict["OrgDir", str] = {
    OrgDir.WORKFLOW: "Workflows, SOPs, and operational guidance (.md files)",
    OrgDir.CULTURE: "Company culture values (YAML)",
    OrgDir.DIRECTION: "Company strategic direction (YAML)",
}

# Talent market — built-in talents (source-relative), cloned talents (runtime)
TALENT_MARKET_DIR = Path(__file__).parent.parent / "talent_market"
TALENTS_DIR = TALENT_MARKET_DIR / "talents"  # built-in (general-assistant, etc.)
TALENTS_RUNTIME_DIR = DATA_ROOT / "talent_market" / "talents"  # cloned from market

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
# PIP deadline enforced by auto-triggered review: employee hits TASKS_PER_QUARTER → HR review fires automatically

# ---------------------------------------------------------------------------
# Employee status
# ---------------------------------------------------------------------------
class EmployeeStatus(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    IN_MEETING = "in_meeting"

STATUS_IDLE = EmployeeStatus.IDLE
STATUS_WORKING = EmployeeStatus.WORKING
STATUS_IN_MEETING = EmployeeStatus.IN_MEETING


class DirtyCategory(str, Enum):
    """Resource categories for sync tick dirty tracking."""
    EMPLOYEES = "employees"
    EX_EMPLOYEES = "ex_employees"
    ROOMS = "rooms"
    TOOLS = "tools"
    TASK_QUEUE = "task_queue"
    CULTURE = "culture"
    ACTIVITY_LOG = "activity_log"
    SALES_TASKS = "sales_tasks"
    DIRECTION = "direction"
    CANDIDATES = "candidates"
    OVERHEAD = "overhead"
    OFFICE_LAYOUT = "office_layout"


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
# Tree growth limits (circuit breaker)
# ---------------------------------------------------------------------------
MAX_REVIEW_ROUNDS = 3       # Max review rounds per parent before CEO escalation
MAX_CHILDREN_PER_NODE = 10  # Max active children per parent node
MAX_TREE_DEPTH = 6          # Max nesting depth for dispatch_child

# ---------------------------------------------------------------------------
# Department-based office layout
# ---------------------------------------------------------------------------
EXEC_ROW_GY = 0          # grid-Y for executive row
EXEC_ROW_HEIGHT = 2       # executive row spans 2 grid rows (0-1)
DEPT_START_ROW = 4        # first grid-Y for department zones (gap from exec area)
DEPT_END_ROW = 10         # last grid-Y for department zones
DEPT_MIN_ZONE_WIDTH = 3   # minimum columns per department zone
DEPT_DESK_SPACING_X = 3   # horizontal spacing between desks within a zone
DEPT_DESK_ROWS = [4, 7, 10]  # grid-Y rows where desks can be placed

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

# Floor tile style key per department — used by frontend TileAtlas to pick tile variant
DEPT_FLOOR_STYLES: dict[str, str] = {
    "Engineering": "stone_blue",
    "Design": "wood_warm",
    "Analytics": "tile_green",
    "Marketing": "carpet_red",
    "General": "stone_gray",
}

# Executive row floor colors
EXEC_FLOOR_COLORS = ("#2a2a20", "#26261e")  # gold tones

# ---------------------------------------------------------------------------
# Task routing keywords
# ---------------------------------------------------------------------------
HR_KEYWORDS = [
    "hire", "recruit", "employee", "staff", "review", "performance",
    "fire", "dismiss", "terminate",
    "promotion", "nickname", "evaluation", "assessment",
]

ENGINEERING_DEPT = "Engineering"

# Default tool permissions by department (set during hiring)
# Note: read, ls, write, edit are now BASE_TOOLS (always available, no permission needed).
# Only gated tools need to be listed here.
DEFAULT_TOOL_PERMISSIONS: dict[str, list[str]] = {
    "Engineering": [
        "bash", "use_tool",
    ],
    "Design": ["use_tool"],
    "Analytics": ["use_tool"],
    "Marketing": ["use_tool"],
    "General": [],
}
DEFAULT_TOOL_PERMISSIONS_FALLBACK: list[str] = []

SALES_KEYWORDS = [
    "sales", "sell", "client", "customer", "contract", "deal", "revenue",
    "order", "business", "signing",
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


# ---------------------------------------------------------------------------
# LLM Provider Registry — data-driven provider dispatch
# ---------------------------------------------------------------------------
# chat_class: "openai" = ChatOpenAI (OpenAI-compatible), "anthropic" = ChatAnthropic
# env_key: Settings field name for the company-level API key
# health_url: endpoint for zero-token health check (None = skip)
# health_auth: "bearer" = Authorization: Bearer, "anthropic" = x-api-key + anthropic-version

class ProviderConfig(BaseModel):
    """Configuration for a single LLM API provider."""
    base_url: str = ""             # OpenAI-compatible base URL (empty = provider default)
    chat_class: str = "openai"     # "openai" | "anthropic"
    env_key: str = ""              # Settings field name for company-level API key
    health_url: str = ""           # Zero-token health check endpoint
    health_auth: str = "bearer"    # "bearer" | "anthropic"


PROVIDER_REGISTRY: dict[str, ProviderConfig] = {
    "openrouter": ProviderConfig(
        base_url="https://openrouter.ai/api/v1",
        env_key="openrouter_api_key",
        health_url="https://openrouter.ai/api/v1/auth/key",
    ),
    "openai": ProviderConfig(
        base_url="https://api.openai.com/v1",
        env_key="openai_api_key",
        health_url="https://api.openai.com/v1/models",
    ),
    "anthropic": ProviderConfig(
        base_url="",
        chat_class="anthropic",
        env_key="anthropic_api_key",
        health_url="https://api.anthropic.com/v1/models",
        health_auth="anthropic",
    ),
    "kimi": ProviderConfig(
        base_url="https://api.moonshot.cn/v1",
        env_key="kimi_api_key",
        health_url="https://api.moonshot.cn/v1/models",
    ),
    "deepseek": ProviderConfig(
        base_url="https://api.deepseek.com",
        env_key="deepseek_api_key",
        health_url="https://api.deepseek.com/models",
    ),
    "qwen": ProviderConfig(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        env_key="qwen_api_key",
        health_url="https://dashscope.aliyuncs.com/compatible-mode/v1/models",
    ),
    "zhipu": ProviderConfig(
        base_url="https://open.bigmodel.cn/api/paas/v4",
        env_key="zhipu_api_key",
        health_url="https://open.bigmodel.cn/api/paas/v4/models",
    ),
    "groq": ProviderConfig(
        base_url="https://api.groq.com/openai/v1",
        env_key="groq_api_key",
        health_url="https://api.groq.com/openai/v1/models",
    ),
    "together": ProviderConfig(
        base_url="https://api.together.xyz/v1",
        env_key="together_api_key",
        health_url="https://api.together.xyz/v1/models",
    ),
    "google": ProviderConfig(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        env_key="google_api_key",
        health_url="https://generativelanguage.googleapis.com/v1beta/models",
    ),
    "minimax": ProviderConfig(
        base_url="https://api.minimax.chat/v1",
        env_key="minimax_api_key",
        health_url="https://api.minimax.chat/v1/models",
    ),
}


def get_provider(name: str) -> ProviderConfig | None:
    """Look up a provider by name (case-insensitive)."""
    return PROVIDER_REGISTRY.get(name.lower())


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
    api_provider: str = "openrouter"  # provider name from PROVIDER_REGISTRY
    api_key: str = ""  # Custom API key (used when api_provider != "openrouter")
    hosting: str = "company"  # "company" = company-hosted (server manages agent loop) | "self" = self-hosted (external process, e.g. Claude Code)
    auth_method: str = "api_key"  # "api_key" | "oauth" (OAuth PKCE for Anthropic)
    oauth_refresh_token: str = ""  # OAuth refresh token (long-lived)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(DATA_ROOT / DOT_ENV_FILENAME),
        env_file_encoding=ENCODING_UTF8,
        extra="ignore",
    )

    # --- LLM Provider API Keys (auto-discovered by PROVIDER_REGISTRY) ---
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    anthropic_auth_method: str = "api_key"  # "api_key" | "oauth"
    anthropic_refresh_token: str = ""
    kimi_api_key: str = ""
    deepseek_api_key: str = ""
    qwen_api_key: str = ""
    zhipu_api_key: str = ""
    groq_api_key: str = ""
    together_api_key: str = ""
    google_api_key: str = ""
    minimax_api_key: str = ""

    # Default model
    default_llm_model: str = "moonshotai/kimi-k2.5"

    # FastSkills MCP
    skillsmp_api_key: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    hr_review_interval_seconds: int = 300


settings = Settings()


def update_env_var(key: str, value: str) -> None:
    """Update or add a variable in the .env file, then reload settings."""
    env_path = DATA_ROOT / DOT_ENV_FILENAME
    lines: list[str] = []
    found = False
    if env_path.exists():
        lines = env_path.read_text(encoding=ENCODING_UTF8).splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
                lines[i] = f"{key}={value}"
                found = True
                break
    if not found:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding=ENCODING_UTF8)
    reload_settings()


def reload_settings() -> None:
    """Re-read .env into the global settings singleton."""
    global settings
    settings = Settings()


# ---------------------------------------------------------------------------
# Application config (config.yaml at project root)
# ---------------------------------------------------------------------------
APP_CONFIG_PATH = DATA_ROOT / "config.yaml"

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
        profile_path = emp_dir / PROFILE_FILENAME
        if not profile_path.exists():
            continue
        with open(profile_path) as f:
            raw = yaml.safe_load(f) or {}
        emp_id = emp_dir.name
        try:
            result[emp_id] = EmployeeConfig(**raw)
        except Exception as e:
            logger.warning("Skipping corrupt profile {}: {}", emp_id, e)
            continue
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
                result[entry.name] = skill_md.read_text(encoding=ENCODING_UTF8)
    return result


# ---------------------------------------------------------------------------
# YAML profile utilities — single source of truth for employee disk I/O
# ---------------------------------------------------------------------------


def load_employee_profile_yaml(employee_id: str) -> dict:
    """Load an employee's profile.yaml from disk. Returns empty dict if missing."""
    profile_path = EMPLOYEES_DIR / employee_id / PROFILE_FILENAME
    if not profile_path.exists():
        return {}
    with open(profile_path) as f:
        return yaml.safe_load(f) or {}


def save_employee_profile_yaml(employee_id: str, data: dict) -> None:
    """Write a full profile dict to employees/{id}/profile.yaml."""
    profile_path = EMPLOYEES_DIR / employee_id / PROFILE_FILENAME
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    with open(profile_path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


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
                # Skip chat history files (e.g., *_chat.yaml)
                if f.stem.endswith("_chat"):
                    continue
                with open(f) as fh:
                    data = yaml.safe_load(fh) or {}
                if not isinstance(data, dict):
                    logger.warning("Skipping malformed room file {}: expected dict, got {}", f.name, type(data).__name__)
                    continue
                meeting_rooms[f.stem] = data
    return tools, meeting_rooms


def load_workflows() -> dict[str, str]:
    """Load all workflow .md files from business/workflows/ as {filename_stem: content}."""
    if not WORKFLOWS_DIR.exists():
        return {}
    result: dict[str, str] = {}
    for f in sorted(WORKFLOWS_DIR.iterdir()):
        if f.suffix == ".md" and f.is_file():
            result[f.stem] = f.read_text(encoding=ENCODING_UTF8)
    return result


def save_workflow(name: str, content: str) -> None:
    """Save a workflow .md file to business/workflows/."""
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    path = WORKFLOWS_DIR / f"{name}.md"
    path.write_text(content, encoding=ENCODING_UTF8)


def load_ex_employee_configs() -> dict[str, EmployeeConfig]:
    """Scan ex-employees/ directory. Each subfolder with a profile.yaml is an ex-employee."""
    if not EX_EMPLOYEES_DIR.exists():
        return {}
    result: dict[str, EmployeeConfig] = {}
    for emp_dir in sorted(EX_EMPLOYEES_DIR.iterdir()):
        if not emp_dir.is_dir():
            continue
        profile_path = emp_dir / PROFILE_FILENAME
        if not profile_path.exists():
            continue
        emp_id = emp_dir.name
        try:
            with open(profile_path) as f:
                raw = yaml.safe_load(f) or {}
            result[emp_id] = EmployeeConfig(**raw)
        except Exception as e:
            logger.warning("Skipping corrupt ex-employee profile {}: {}", emp_id, e)
            continue
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
    manifest_path = EMPLOYEES_DIR / employee_id / MANIFEST_FILENAME
    if not manifest_path.exists():
        return None
    data = json.loads(manifest_path.read_text(encoding=ENCODING_UTF8))
    MANIFEST_CACHE[employee_id] = data
    return data


def invalidate_manifest_cache(employee_id: str | None = None) -> None:
    """Clear manifest cache. If employee_id is None, clear all."""
    if employee_id is None:
        MANIFEST_CACHE.clear()
    else:
        MANIFEST_CACHE.pop(employee_id, None)


def load_custom_settings(employee_id: str) -> dict:
    """Load custom settings (target_email, polling_interval, etc.) from settings.json."""
    path = EMPLOYEES_DIR / employee_id / "settings.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding=ENCODING_UTF8))


def save_custom_settings(employee_id: str, updates: dict) -> dict:
    """Merge updates into settings.json and return the full settings dict."""
    path = EMPLOYEES_DIR / employee_id / "settings.json"
    current = {}
    if path.exists():
        current = json.loads(path.read_text(encoding=ENCODING_UTF8))
    current.update(updates)
    path.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding=ENCODING_UTF8)
    return current


# ---------------------------------------------------------------------------
# Talent market helpers
# ---------------------------------------------------------------------------


def load_talent_profile(talent_id: str) -> dict:
    """Load a talent profile from talents/{id}/profile.yaml.

    Searches runtime dir (cloned talents) first, then built-in src dir.
    Returns the parsed YAML as a dict, or empty dict if not found.
    """
    for base in (TALENTS_RUNTIME_DIR, TALENTS_DIR):
        profile_path = base / talent_id / PROFILE_FILENAME
        if profile_path.exists():
            with open(profile_path) as f:
                return yaml.safe_load(f) or {}
    return {}




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
            result.append(skill_file.read_text(encoding=ENCODING_UTF8))
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
        profile_path = talent_dir / PROFILE_FILENAME
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


class _LazyEmployeeConfigs(dict):
    """Lazy-loading dict that reads employee configs from disk on demand.

    No import-time cache — every access reads from disk via load_employee_configs().
    This is a transitional shim; callers should migrate to store.load_employee().
    """

    def __getitem__(self, key):
        fresh = load_employee_configs()
        return fresh[key]

    def get(self, key, default=None):
        fresh = load_employee_configs()
        return fresh.get(key, default)

    def __contains__(self, key):
        fresh = load_employee_configs()
        return key in fresh

    def __iter__(self):
        fresh = load_employee_configs()
        return iter(fresh)

    def items(self):
        fresh = load_employee_configs()
        return fresh.items()

    def values(self):
        fresh = load_employee_configs()
        return fresh.values()

    def keys(self):
        fresh = load_employee_configs()
        return fresh.keys()

    def __len__(self):
        fresh = load_employee_configs()
        return len(fresh)

    def __bool__(self):
        fresh = load_employee_configs()
        return bool(fresh)

    # Mutation methods are no-ops (no cache to update)
    def __setitem__(self, key, value):
        pass  # no-op — disk is the source of truth

    def __delitem__(self, key):
        pass  # no-op

    def pop(self, key, *args):
        pass  # no-op

    def clear(self):
        pass  # no-op

    def update(self, *args, **kwargs):
        pass  # no-op


employee_configs: dict[str, EmployeeConfig] = _LazyEmployeeConfigs()
