from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

EventType = Literal[
    "employee_hired",
    "employee_fired",
    "employee_reviewed",
    "tool_added",
    "ceo_task_submitted",
    "agent_thinking",
    "agent_done",
    "state_snapshot",
    "guidance_start",
    "guidance_noted",
    "guidance_end",
    "meeting_booked",
    "meeting_released",
    "meeting_denied",
    "routine_phase",
    "meeting_report_ready",
    "meeting_chat",
    "workflow_updated",
    "employee_rehired",
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
