"""Talent Package Specification

Defines the directories, files, and field semantics required for a talent package.
The platform loads talents according to this spec to drive hiring, onboarding, and runtime behavior.

Directory structure:
    talents/{talent_id}/
    ├── profile.yaml          # Required — identity + hiring info
    ├── manifest.json         # Optional — frontend settings UI + capability declaration
    ├── launch.sh             # Optional — launch script for self-hosted employees
    ├── run_worker.py         # Optional — worker entry point for remote employees
    ├── skills/               # Optional — skill Markdown files
    │   ├── *.md              # Each file describes a skill, content injected into employee prompt
    ├── tools/                # Optional — tool declarations and custom tools
    │   ├── manifest.yaml     # Tool manifest (builtin_tools + custom_tools)
    │   └── *.py              # Custom LangChain @tool implementations
    └── functions/            # Optional — talent-provided function implementations
        ├── manifest.yaml     # Declares each function's metadata (name, description, scope)
        └── {name}.py         # LangChain @tool implementation (one .py can export multiple @tools)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class HostingMode(str, Enum):
    """Employee hosting mode."""
    COMPANY = "company"     # Company-hosted: platform-internal LangChain agent loop
    SELF = "self"           # Self-hosted: employee brings own runtime (e.g. Claude Code CLI)
    REMOTE = "remote"       # Remote: receives tasks via HTTP polling


class AuthMethod(str, Enum):
    """Authentication method."""
    API_KEY = "api_key"     # Use API key to call LLM
    OAUTH = "oauth"         # OAuth PKCE login (e.g. Anthropic OAuth)
    CLI = "cli"             # Use locally logged-in CLI credentials
    NONE = "none"           # No authentication needed (free models or self-provided credentials)


class SettingFieldType(str, Enum):
    """Supported field types in manifest.json settings.

    The frontend dynamically renders the corresponding UI control based on type.
    """
    TEXT = "text"                 # Single-line text input
    SECRET = "secret"            # Password input (masked display)
    NUMBER = "number"            # Number input (supports min/max/step)
    SELECT = "select"            # Single-select dropdown
    MULTI_SELECT = "multi_select"  # Multi-select dropdown
    TOGGLE = "toggle"            # Toggle switch (boolean)
    TEXTAREA = "textarea"        # Multi-line text
    OAUTH_BUTTON = "oauth_button"  # OAuth login button (triggers PKCE flow)
    COLOR = "color"              # Color picker
    FILE = "file"                # File upload
    READONLY = "readonly"        # Read-only display (value_from specifies data source)


# ---------------------------------------------------------------------------
# manifest.json data structures
# ---------------------------------------------------------------------------

@dataclass
class SettingField:
    """Definition of a single setting field in manifest.json.

    Attributes:
        key:          Field identifier, corresponds to key name in profile.yaml
        type:         Field type, determines frontend rendering
        label:        Label text displayed in frontend
        default:      Default value (optional)
        required:     Whether the field is required
        min:          Minimum value for number type
        max:          Maximum value for number type
        step:         Step size for number type
        options:      Option list for select/multi_select
        options_from: Dynamic options data source (e.g. "api:models")
        provider:     OAuth provider identifier (e.g. "anthropic")
        value_from:   Data source for readonly fields (e.g. "api:sessions")
    """
    key: str
    type: SettingFieldType
    label: str
    default: Any = None
    required: bool = False
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[str] = field(default_factory=list)
    options_from: str = ""
    provider: str = ""
    value_from: str = ""


@dataclass
class SettingSection:
    """A settings group in manifest.json.

    Attributes:
        id:     Group identifier (e.g. "connection", "session")
        title:  Group title displayed in frontend
        fields: List of fields in this group
    """
    id: str
    title: str
    fields: list[SettingField] = field(default_factory=list)


@dataclass
class ManifestPrompts:
    """Prompt file declarations in manifest.json.

    Attributes:
        system: System prompt file path (relative to talent directory), overrides default system prompt
        role:   Role prompt file path, overrides default role description
        skills: Skill file glob pattern list (e.g. ["skills/*.md"])
    """
    system: str = ""
    role: str = ""
    skills: list[str] = field(default_factory=lambda: ["skills/*.md"])


@dataclass
class ManifestTools:
    """Tool declarations in manifest.json.

    Attributes:
        builtin: List of platform built-in tool names (registered in SANDBOX_TOOLS/COMMON_TOOLS)
        custom:  List of custom tool file paths (.py files relative to talent directory)
    """
    builtin: list[str] = field(default_factory=list)
    custom: list[str] = field(default_factory=list)


@dataclass
class TalentManifest:
    """Complete manifest.json structure — drives frontend settings UI and capability declarations.

    manifest.json is an optional file. If a talent does not provide manifest.json,
    the platform will use information from profile.yaml to render a default settings UI.

    Attributes:
        id:                     Talent unique identifier
        name:                   Talent display name
        version:                Version number (semantic versioning)
        role:                   Role type (Engineer, Designer, QA, etc.)
        hosting:                Hosting mode
        settings:               Settings section list, drives frontend dynamic UI
        prompts:                Prompt file declarations
        tools:                  Tool declarations
        platform_capabilities:  Platform capability requirements list (e.g. file_upload, websocket)
    """
    id: str
    name: str
    version: str = "1.0.0"
    role: str = ""
    hosting: HostingMode = HostingMode.COMPANY
    settings: list[SettingSection] = field(default_factory=list)
    prompts: ManifestPrompts = field(default_factory=ManifestPrompts)
    tools: ManifestTools = field(default_factory=ManifestTools)
    platform_capabilities: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# profile.yaml data structures
# ---------------------------------------------------------------------------

@dataclass
class TalentProfile:
    """Complete profile.yaml structure — talent identity and hiring information.

    profile.yaml is a required file. The platform uses it to identify talents and display them to HR.

    Attributes:
        id:                     Talent unique identifier (matches directory name)
        name:                   Display name (e.g. "Coding Talent")
        description:            Talent description text, shown during HR recruitment
        role:                   Role type, determines post-onboarding department assignment
                                (Engineer -> R&D Dept, Designer -> Design Dept, etc.)
        remote:                 Whether remote work (True = no desk assigned)
        hosting:                Hosting mode (default "company")
        auth_method:            Authentication method (default "api_key")
        api_provider:           LLM API provider ("openrouter", "anthropic", etc.)
        llm_model:              Default LLM model identifier
        temperature:            Default inference temperature
        image_model:            Image generation model identifier (optional, used by Designer roles)
        hiring_fee:             Hiring fee (virtual currency, for HR evaluation)
        salary_per_1m_tokens:   Salary per million tokens (0 means auto-calculate by model)
        skills:                 Skill identifier list, corresponds to .md files under skills/
        tools:                  Tool name list (declares tools this talent uses)
        personality_tags:       Personality tag list (for HR matching)
        system_prompt_template: System prompt template (base instructions injected into employee agent)
    """
    id: str
    name: str
    description: str = ""
    role: str = "Engineer"
    remote: bool = False
    hosting: str = "company"
    auth_method: str = "api_key"
    api_provider: str = "openrouter"
    llm_model: str = ""
    temperature: float = 0.7
    image_model: str = ""
    hiring_fee: float = 0.0
    salary_per_1m_tokens: float = 0.0
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    personality_tags: list[str] = field(default_factory=list)
    system_prompt_template: str = ""
    claude_plugins: list[str] = field(default_factory=list)  # Self-hosted only: Claude CLI plugins to install


# ---------------------------------------------------------------------------
# tools/manifest.yaml data structures
# ---------------------------------------------------------------------------

@dataclass
class ToolsManifest:
    """Complete tools/manifest.yaml structure — tool manifest declaration.

    Declares the built-in and custom tools used by this talent.
    Built-in tool names reference tools registered in COMMON_TOOLS or SANDBOX_TOOLS.
    Custom tools point to .py files in the same directory, each exporting a LangChain @tool.

    Attributes:
        builtin_tools:  List of built-in tool names
                        Common values: sandbox_execute_code, sandbox_run_command,
                        sandbox_write_file, sandbox_read_file, web_search,
                        generate_image
        custom_tools:   List of custom tool module names (without .py suffix)
                        Each name corresponds to a @tool function exported in tools/{name}.py
    """
    builtin_tools: list[str] = field(default_factory=list)
    custom_tools: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# functions/manifest.yaml data structures
# ---------------------------------------------------------------------------

@dataclass
class AgentPromptSection:
    """A prompt section override in agent/manifest.yaml."""
    name: str
    file: str = ""
    priority: int = 50


@dataclass
class AgentManifest:
    """agent/manifest.yaml — talent agent loop customization declaration."""
    runner_module: str = ""
    runner_class: str = ""
    hooks_module: str = ""
    pre_task_hook: str = ""
    post_task_hook: str = ""
    prompt_sections: list[AgentPromptSection] = field(default_factory=list)


@dataclass
class VesselManifest:
    """vessel/vessel.yaml — talent-provided vessel DNA declaration.

    When a talent includes a vessel/ directory, this structure is used instead of AgentManifest.
    Fields correspond to sub-configurations of VesselConfig.
    """
    runner: dict = field(default_factory=dict)
    hooks: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    limits: dict = field(default_factory=dict)
    capabilities: dict = field(default_factory=dict)
    prompt_sections: list[AgentPromptSection] = field(default_factory=list)


@dataclass
class FunctionDeclaration:
    """A single function declaration in functions/manifest.yaml."""
    name: str
    description: str = ""
    scope: str = "personal"  # "company" | "personal"


@dataclass
class FunctionsManifest:
    """functions/manifest.yaml — talent-provided function declarations."""
    functions: list[FunctionDeclaration] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Complete talent package
# ---------------------------------------------------------------------------

@dataclass
class TalentPackage:
    """A complete talent package, aggregating all components.

    Filesystem layout:
        talents/{id}/
        ├── profile.yaml          # -> self.profile (required)
        ├── manifest.json         # -> self.manifest (optional)
        ├── launch.sh             # -> self.has_launch_script (optional, self-hosted)
        ├── run_worker.py         # -> (optional, remote)
        ├── skills/
        │   └── *.md              # -> self.skill_files
        ├── tools/
        │   ├── manifest.yaml     # -> self.tools_manifest (optional)
        │   └── *.py              # -> custom tool implementations
        └── functions/            # -> self.functions_manifest (optional)
            ├── manifest.yaml     # Declares each function's metadata
            └── {name}.py         # LangChain @tool implementation

    Onboarding flow (onboarding.py):
        1. HR browses available talents from talent market
        2. CEO confirms hiring -> execute_hire()
        3. Assign employee ID, department, desk
        4. Copy skills/ and tools/ from talent directory to employee directory
        5. Self-hosted employees additionally get launch.sh and connection.json
        6. Generate nickname, work principles
        7. Register with EmployeeManager

    Runtime behavior varies by hosting mode:
        - company: In-platform LangChain agent, executed by LangChainExecutor
        - self:    Independent process (e.g. Claude Code CLI), launched by ScriptExecutor
        - remote:  External worker polls task queue via HTTP
    """
    profile: TalentProfile
    manifest: TalentManifest | None = None
    tools_manifest: ToolsManifest | None = None
    functions_manifest: FunctionsManifest | None = None
    vessel_manifest: VesselManifest | None = None
    agent_manifest: AgentManifest | None = None  # Kept for backward compatibility
    skill_files: list[str] = field(default_factory=list)
    has_launch_script: bool = False
