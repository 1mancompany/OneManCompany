"""ACP events bridge — converts ACP session updates to generic CompanyEvents.

All ACP session updates become a single ACP_UPDATE event. The frontend
dispatches on payload.kind, so adding new ACP update types requires only
a new frontend renderer — zero changes to this bridge.
"""

from __future__ import annotations

from typing import Any

from onemancompany.core.events import CompanyEvent
from onemancompany.core.models import EventType


def acp_update_to_event(employee_id: str, kind: str, data: dict[str, Any]) -> CompanyEvent:
    """Create a generic ACP_UPDATE event from an ACP session update."""
    return CompanyEvent(
        type=EventType.ACP_UPDATE,
        payload={"kind": kind, "employee_id": employee_id, "data": data},
        agent=employee_id,
    )
