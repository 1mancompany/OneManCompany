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


class ConversationPhase(str, Enum):
    """Lifecycle phases of a conversation."""
    ACTIVE = "active"
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
    REMOTE = "remote"


class ConversationType(str, Enum):
    CEO_INBOX = "ceo_inbox"
    ONE_ON_ONE = "oneonone"


class ConversationPhase(str, Enum):
    ACTIVE = "active"
    CLOSING = "closing"
    CLOSED = "closed"


class ToolCategory(str, Enum):
    BASE = "base"
    GATED = "gated"
    ROLE = "role"
    ASSET = "asset"


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
