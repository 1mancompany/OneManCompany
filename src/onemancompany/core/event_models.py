"""Typed event payloads — replaces untyped dict payloads in CompanyEvent.

Each event type gets a dedicated Pydantic model. During the transition period,
``dict`` is still accepted as a fallback for un-migrated events.
"""

from __future__ import annotations

from typing import Union

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Payload models
# ---------------------------------------------------------------------------

class TaskStartedPayload(BaseModel):
    task_id: str = ""
    employee_id: str = ""
    description: str = ""


class TaskCompletedPayload(BaseModel):
    task_id: str = ""
    employee_id: str = ""
    success: bool = True
    output_summary: str = ""
    cost_usd: float = 0.0


class AgentThinkingPayload(BaseModel):
    employee_id: str = ""
    message: str = ""
    content: str = ""
    tool_name: str | None = None


class AgentDonePayload(BaseModel):
    role: str = ""
    summary: str = ""
    employee_id: str = ""


class AgentLogPayload(BaseModel):
    employee_id: str = ""
    log_type: str = ""
    content: str = ""


class TaskUpdatePayload(BaseModel):
    employee_id: str = ""
    task_id: str = ""
    status: str = ""
    summary: str = ""


class CandidatesReadyPayload(BaseModel):
    batch_id: str = ""
    jd: str = ""
    candidates: list[dict] = []


class ResolutionReadyPayload(BaseModel):
    resolution_id: str = ""
    edit_count: int = 0
    project_id: str | None = None


class ResolutionDecidedPayload(BaseModel):
    resolution_id: str = ""
    project_id: str = ""
    results: list[dict] = []


class MeetingChatPayload(BaseModel):
    meeting_id: str = ""
    speaker_id: str = ""
    speaker_name: str = ""
    content: str = ""


class MeetingBookedPayload(BaseModel):
    meeting_id: str = ""
    room_id: str = ""
    booked_by: str = ""
    participants: list[str] = []


class EmployeeHiredPayload(BaseModel):
    name: str = ""
    nickname: str = ""
    role: str = ""
    employee_id: str = ""


class EmployeeFiredPayload(BaseModel):
    name: str = ""
    nickname: str = ""
    employee_id: str = ""
    reason: str = ""


class EmployeeReviewedPayload(BaseModel):
    employee_id: str = ""
    score: float = 0.0
    quarter: int = 0


class FileEditProposedPayload(BaseModel):
    edit_id: str = ""
    file_path: str = ""
    employee_id: str = ""
    reason: str = ""


class GuidancePayload(BaseModel):
    employee_id: str = ""
    content: str = ""


class StateSnapshotPayload(BaseModel):
    """Empty payload — triggers frontend full refresh."""
    pass


class InquiryPayload(BaseModel):
    employee_id: str = ""
    inquiry_id: str = ""
    question: str = ""


class WorkflowUpdatedPayload(BaseModel):
    workflow_id: str = ""
    phase: str = ""


class CompanyCultureUpdatedPayload(BaseModel):
    items: list[dict] = []


# ---------------------------------------------------------------------------
# Union type — transition period allows dict fallback
# ---------------------------------------------------------------------------

EventPayload = Union[
    TaskStartedPayload,
    TaskCompletedPayload,
    AgentThinkingPayload,
    AgentDonePayload,
    AgentLogPayload,
    TaskUpdatePayload,
    CandidatesReadyPayload,
    ResolutionReadyPayload,
    ResolutionDecidedPayload,
    MeetingChatPayload,
    MeetingBookedPayload,
    EmployeeHiredPayload,
    EmployeeFiredPayload,
    EmployeeReviewedPayload,
    FileEditProposedPayload,
    GuidancePayload,
    StateSnapshotPayload,
    InquiryPayload,
    WorkflowUpdatedPayload,
    CompanyCultureUpdatedPayload,
    dict,  # fallback for un-migrated events
]
