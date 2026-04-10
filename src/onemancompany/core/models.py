"""Typed core models — single source of truth for all data structures.

Replaces scattered dicts with Pydantic models for validation and IDE support.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EmployeeRole(str, Enum):
    HR = "Human Resources"
    COO = "Chief Operating Officer"
    EA = "Executive Assistant"
    CSO = "Chief Sales Officer"
    ENGINEER = "Engineer"
    DESIGNER = "Designer"
    ARTIST = "Artist"
    DEVOPS = "DevOps"
    QA = "QA"
    ANALYST = "Analyst"
    MARKETING = "Marketing"


class Department(str, Enum):
    HR = "HR"
    OPERATIONS = "Operations"
    ENGINEERING = "Engineering"
    DESIGN = "Design"
    SALES = "Sales"
    EXECUTIVE = "Executive"
    CEO_OFFICE = "CEO Office"
    MARKETING = "Marketing"


class ConversationType(str, Enum):
    """Types of conversation channels."""
    CEO_INBOX = "ceo_inbox"
    ONE_ON_ONE = "oneonone"
    EA_CHAT = "ea_chat"
    PROJECT = "project"


class ConversationPhase(str, Enum):
    """Lifecycle phases of a conversation."""
    ACTIVE = "active"
    ARCHIVED = "archived"
    CLOSING = "closing"
    CLOSED = "closed"


class ToolCategory(str, Enum):
    """Tool permission categories."""
    BASE = "base"
    GATED = "gated"
    ROLE = "role"
    ASSET = "asset"


from onemancompany.core.task_lifecycle import TaskPhase


class DecisionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class HostingMode(str, Enum):
    COMPANY = "company"
    SELF = "self"
    OPENCLAW = "openclaw"
    REMOTE = "remote"


class AuthMethod(str, Enum):
    API_KEY = "api_key"
    OAUTH = "oauth"


class EventType(str, Enum):
    """CompanyEvent type identifiers — single source of truth."""
    STATE_SNAPSHOT = "state_snapshot"
    FRONTEND_UPDATE_AVAILABLE = "frontend_update_available"
    CODE_UPDATE_AVAILABLE = "code_update_available"
    BACKEND_RESTART_SCHEDULED = "backend_restart_scheduled"
    CEO_TASK_SUBMITTED = "ceo_task_submitted"
    CEO_SESSION_MESSAGE = "ceo_session_message"
    AGENT_DONE = "agent_done"
    AGENT_LOG = "agent_log"
    AGENT_TASK_UPDATE = "agent_task_update"
    TREE_UPDATE = "tree_update"
    MEETING_BOOKED = "meeting_booked"
    MEETING_CHAT = "meeting_chat"
    MEETING_AGENDA_UPDATE = "meeting_agenda_update"
    MEETING_RELEASED = "meeting_released"
    GUIDANCE_START = "guidance_start"
    GUIDANCE_END = "guidance_end"
    EMPLOYEE_HIRED = "employee_hired"
    EMPLOYEE_FIRED = "employee_fired"
    EMPLOYEE_REHIRED = "employee_rehired"
    HIRING_REQUEST_READY = "hiring_request_ready"
    HIRING_REQUEST_DECIDED = "hiring_request_decided"
    CANDIDATES_READY = "candidates_ready"
    ONBOARDING_PROGRESS = "onboarding_progress"
    WORKFLOW_UPDATED = "workflow_updated"
    OKR_UPDATED = "okr_updated"
    COMPANY_CULTURE_UPDATED = "company_culture_updated"
    COMPANY_DIRECTION_UPDATED = "company_direction_updated"
    FILE_EDIT_APPLIED = "file_edit_applied"
    FILE_EDIT_REJECTED = "file_edit_rejected"
    REVIEW_REMINDER = "review_reminder"
    OPEN_POPUP = "open_popup"
    REQUEST_CREDENTIALS = "request_credentials"
    CREDENTIALS_SUBMITTED = "credentials_submitted"
    CONVERSATION_PHASE = "conversation_phase"
    CONVERSATION_MESSAGE = "conversation_message"
    REMOTE_WORKER_REGISTERED = "remote_worker_registered"
    REMOTE_TASK_COMPLETED = "remote_task_completed"
    SALES_TASK_SUBMITTED = "sales_task_submitted"
    BACKGROUND_TASK_UPDATE = "background_task_update"
    CRON_STATUS_CHANGE = "cron_status_change"
    TALENT_PROFILE_ERROR = "talent_profile_error"
    ACTIVITY = "activity"
    # Additional types from legacy Literal definition
    EMPLOYEE_REVIEWED = "employee_reviewed"
    TOOL_ADDED = "tool_added"
    AGENT_THINKING = "agent_thinking"
    GUIDANCE_NOTED = "guidance_noted"
    MEETING_DENIED = "meeting_denied"
    MEETING_REPORT_READY = "meeting_report_ready"
    ROUTINE_PHASE = "routine_phase"
    FILE_EDIT_PROPOSED = "file_edit_proposed"
    RESOLUTION_READY = "resolution_ready"
    RESOLUTION_DECIDED = "resolution_decided"
    ONBOARDING_STARTED = "onboarding_started"
    ONBOARDING_COMPLETED = "onboarding_completed"
    PROBATION_REVIEW = "probation_review"
    PIP_STARTED = "pip_started"
    PIP_RESOLVED = "pip_resolved"
    EXIT_INTERVIEW_STARTED = "exit_interview_started"
    EXIT_INTERVIEW_COMPLETED = "exit_interview_completed"
    INTERVIEW_ROUND_COMPLETED = "interview_round_completed"
    MEETING_REPORT_COMPLETE = "meeting_report_complete"
    RECURRING_ACTION_ITEMS = "recurring_action_items"
    DISPATCH_STATUS_CHANGE = "dispatch_status_change"


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

class PerformanceRecord(BaseModel):
    """A single quarter's performance review result."""
    quarter: int
    score: float = Field(ge=0.0, le=5.0)
    tasks_completed: int = 0
    reviewer: str = ""
    notes: str = ""
    recorded_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------

class CostRecord(BaseModel):
    """Single LLM call cost record (append-only)."""
    timestamp: datetime = Field(default_factory=datetime.now)
    category: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    task_id: str | None = None
    employee_id: str | None = None


class OverheadCosts(BaseModel):
    """Accumulative cost tracker — replaces mutable dict."""
    records: list[CostRecord] = Field(default_factory=list)

    # Legacy compat fields (updated alongside records for fast access)
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    by_category: dict[str, dict] = Field(default_factory=dict)

    def add(self, record: CostRecord) -> None:
        self.records.append(record)
        self.total_cost_usd += record.cost_usd
        self.total_input_tokens += record.input_tokens
        self.total_output_tokens += record.output_tokens
        cat = self.by_category.setdefault(record.category, {
            "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
        })
        cat["cost_usd"] += record.cost_usd
        cat["input_tokens"] += record.input_tokens
        cat["output_tokens"] += record.output_tokens

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens


# ---------------------------------------------------------------------------
# Agent execution result
# ---------------------------------------------------------------------------

class AgentResult(BaseModel):
    """Structured result from an agent task execution."""
    success: bool
    output: str
    artifacts: list[str] = []
    tool_calls_count: int = 0
    tokens_used: int = 0
    cost_usd: float = Field(ge=0.0, default=0.0)
    error: str | None = None
    attempt: int = 1
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Project models
# ---------------------------------------------------------------------------

class TimelineEntry(BaseModel):
    time: datetime = Field(default_factory=datetime.now)
    employee_id: str
    action: str
    detail: str


class ProjectIteration(BaseModel):
    id: str
    task: str
    status: TaskPhase = TaskPhase.PENDING
    acceptance_criteria: list[str] = []
    timeline: list[TimelineEntry] = []
    output: str = ""
    cost_usd: float = 0.0
    tokens_used: int = 0


class Project(BaseModel):
    id: str
    name: str
    slug: str
    created_at: datetime = Field(default_factory=datetime.now)
    iterations: list[ProjectIteration] = []
    workspace_path: str = ""


# ---------------------------------------------------------------------------
# Resolution models
# ---------------------------------------------------------------------------

class FileEditProposal(BaseModel):
    """A single file edit in a Resolution."""
    edit_id: str
    file_path: str
    rel_path: str = ""
    old_content: str = ""
    new_content: str = ""
    reason: str = ""
    proposed_by: str = ""
    original_md5: str = ""
    decision: DecisionStatus | None = None
    decided_at: datetime | None = None
    executed: bool = False
    expired: bool = False


class Resolution(BaseModel):
    """Batch file-edit review for CEO approval."""
    resolution_id: str
    project_id: str = ""
    task: str = ""
    employee_id: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    status: DecisionStatus = DecisionStatus.PENDING
    edits: list[FileEditProposal] = []
