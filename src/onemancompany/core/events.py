from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

EventType = Literal[
    "employee_hired",
    "employee_fired",
    "employee_reviewed",
    "employee_rehired",
    "tool_added",
    "ceo_task_submitted",
    "agent_thinking",
    "agent_done",
    "agent_log",
    "agent_task_update",
    "state_snapshot",
    "guidance_start",
    "guidance_noted",
    "guidance_end",
    "meeting_booked",
    "meeting_released",
    "meeting_denied",
    "meeting_chat",
    "meeting_report_ready",
    "routine_phase",
    "workflow_updated",
    "candidates_ready",
    "company_culture_updated",
    "file_edit_proposed",
    "file_edit_applied",
    "file_edit_rejected",
    "resolution_ready",
    "resolution_decided",
    "inquiry_started",
    "inquiry_ended",
    "okr_updated",
    "onboarding_started",
    "onboarding_completed",
    "probation_review",
    "pip_started",
    "pip_resolved",
    "exit_interview_started",
    "exit_interview_completed",
    "interview_round_completed",
    "hiring_request_ready",
    "hiring_request_decided",
]


@dataclass
class CompanyEvent:
    type: EventType
    payload: dict
    agent: str = "system"


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[CompanyEvent]] = []

    def subscribe(self) -> asyncio.Queue[CompanyEvent]:
        q: asyncio.Queue[CompanyEvent] = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[CompanyEvent]) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    async def publish(self, event: CompanyEvent) -> None:
        for q in self._subscribers:
            await q.put(event)


event_bus = EventBus()
