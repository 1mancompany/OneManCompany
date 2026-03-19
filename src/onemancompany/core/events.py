from __future__ import annotations

import asyncio
from dataclasses import dataclass

from onemancompany.core.config import SYSTEM_SENDER
from onemancompany.core.models import EventType


@dataclass
class CompanyEvent:
    type: EventType
    payload: dict
    agent: str = SYSTEM_SENDER


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


async def open_popup(
    title: str,
    message: str = "",
    *,
    popup_type: str = "info",
    url: str = "",
    agent: str = "SYSTEM",
    buttons: list[dict] | None = None,
    confirm_label: str = "Confirm",
    callback_url: str = "",
) -> None:
    """Send a generic popup to the frontend.

    Args:
        title: Popup window title.
        message: Body text.
        popup_type: "info", "confirm", "url", or "oauth".
        url: URL to display / open (for "url" and "oauth" types).
        agent: Which agent triggered this.
        buttons: Custom buttons [{label, url?, callback_url?, primary?, close?}].
        confirm_label: Label for confirm button (type="confirm").
        callback_url: Backend URL to POST on confirm (type="confirm").
    """
    payload = {"type": popup_type, "title": title, "message": message, "agent": agent}
    if url:
        payload["url"] = url
    if buttons:
        payload["buttons"] = buttons
    if popup_type == "confirm":
        payload["confirm_label"] = confirm_label
        if callback_url:
            payload["callback_url"] = callback_url
    await event_bus.publish(CompanyEvent(type=EventType.OPEN_POPUP, payload=payload, agent=agent))
