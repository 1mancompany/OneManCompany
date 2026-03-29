"""CeoBroker — unified CEO conversation model.

Each project has a CeoSession with independent conversation history
and a FIFO queue of pending interactions (requests awaiting CEO reply).
CeoExecutor pushes interactions; CEO replies resolve them.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import yaml
from loguru import logger

from onemancompany.core.config import ENCODING_UTF8

if TYPE_CHECKING:
    from onemancompany.core.vessel import LaunchResult, TaskContext

CEO_SESSION_FILENAME = "ceo_session.yaml"


@dataclass
class CeoInteraction:
    """A single pending interaction awaiting CEO reply."""

    node_id: str
    tree_path: str
    project_id: str
    source_employee: str
    interaction_type: str  # "ceo_request" | "project_confirm"
    message: str
    future: asyncio.Future
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


class CeoSession:
    """Per-project CEO conversation session."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.history: list[dict] = []
        self._pending: deque[CeoInteraction] = deque()

    @property
    def has_pending(self) -> bool:
        return len(self._pending) > 0

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def enqueue(self, interaction: CeoInteraction) -> None:
        """Add interaction to FIFO queue and record in history."""
        self._pending.append(interaction)
        self.push_system_message(interaction.message, source=interaction.source_employee)
        logger.debug(
            "CeoSession[{}] enqueued interaction node_id={} type={}",
            self.project_id,
            interaction.node_id,
            interaction.interaction_type,
        )

    def pop_pending(self) -> CeoInteraction | None:
        """Pop the oldest pending interaction (FIFO)."""
        if self._pending:
            return self._pending.popleft()
        return None

    def push_system_message(self, text: str, source: str = "") -> dict:
        """Append a system message (from an employee) to history."""
        msg = {
            "role": "system",
            "text": text,
            "source": source,
            "timestamp": datetime.now().isoformat(),
        }
        self.history.append(msg)
        return msg

    def push_ceo_message(self, text: str) -> dict:
        """Append a CEO reply to history."""
        msg = {
            "role": "ceo",
            "text": text,
            "timestamp": datetime.now().isoformat(),
        }
        self.history.append(msg)
        return msg

    def save_history(self, project_dir: Path) -> None:
        """Persist conversation history to YAML."""
        path = project_dir / CEO_SESSION_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump({"history": self.history}, allow_unicode=True, sort_keys=False),
            encoding=ENCODING_UTF8,
        )
        logger.debug("CeoSession[{}] saved {} messages to {}", self.project_id, len(self.history), path)

    def load_history(self, project_dir: Path) -> None:
        """Load conversation history from YAML."""
        path = project_dir / CEO_SESSION_FILENAME
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding=ENCODING_UTF8)) or {}
            self.history = data.get("history", [])
            logger.debug("CeoSession[{}] loaded {} messages from {}", self.project_id, len(self.history), path)

    def to_summary(self) -> dict:
        """Return a serializable summary for API responses."""
        return {
            "project_id": self.project_id,
            "has_pending": self.has_pending,
            "pending_count": self.pending_count,
            "message_count": len(self.history),
            "last_message": self.history[-1] if self.history else None,
        }


class CeoBroker:
    """Central manager for all CEO per-project sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, CeoSession] = {}

    def get_or_create_session(self, project_id: str) -> CeoSession:
        if project_id not in self._sessions:
            self._sessions[project_id] = CeoSession(project_id=project_id)
        return self._sessions[project_id]

    def get_session(self, project_id: str) -> CeoSession | None:
        return self._sessions.get(project_id)

    def list_sessions(self) -> list[dict]:
        summaries = [s.to_summary() for s in self._sessions.values()]
        summaries.sort(key=lambda s: (not s["has_pending"], s["project_id"]))
        return summaries

    async def handle_input(self, project_id: str, text: str) -> dict:
        session = self.get_or_create_session(project_id)
        if session.has_pending:
            interaction = session.pop_pending()
            session.push_ceo_message(text)
            interaction.future.set_result(text)
            logger.info(
                "[CeoBroker] Resolved pending {} for project={} node={}",
                interaction.interaction_type, project_id, interaction.node_id,
            )
            return {"type": "resolved", "node_id": interaction.node_id}
        else:
            session.push_ceo_message(text)
            logger.info("[CeoBroker] No pending for project={} — followup", project_id)
            return {"type": "followup", "text": text}


class CeoExecutor:
    """Virtual executor for CEO (00001) — implements Launcher protocol (duck-typed).

    Does not call any LLM. Pushes the task as a message into the project's
    CeoSession, then waits for the CEO to reply in the TUI.
    """

    async def execute(
        self,
        task_description: str,
        context: TaskContext,
        on_log: Callable[[str, str], None] | None = None,
    ) -> LaunchResult:
        from onemancompany.core.events import CompanyEvent, event_bus
        from onemancompany.core.models import EventType
        from onemancompany.core.config import SYSTEM_AGENT
        from onemancompany.core.vessel import LaunchResult as _LaunchResult

        broker = get_ceo_broker()
        project_id = context.project_id or "default"
        session = broker.get_or_create_session(project_id)

        future = asyncio.get_event_loop().create_future()
        interaction = CeoInteraction(
            node_id=context.task_id,
            tree_path="",
            project_id=project_id,
            source_employee=context.employee_id,
            interaction_type="ceo_request",
            message=task_description,
            future=future,
        )
        session.enqueue(interaction)

        # Broadcast to frontend
        await event_bus.publish(CompanyEvent(
            type=EventType.CEO_SESSION_MESSAGE,
            payload={
                "project_id": project_id,
                "node_id": context.task_id,
                "message": task_description,
                "source_employee": context.employee_id,
                "interaction_type": "ceo_request",
            },
            agent=SYSTEM_AGENT,
        ))

        if on_log:
            on_log("ceo_request", f"Awaiting CEO reply for: {task_description[:100]}")

        logger.info("[CeoExecutor] Enqueued request for project={} node={}", project_id, context.task_id)

        ceo_response = await future

        if context.work_dir:
            session.save_history(Path(context.work_dir))

        return _LaunchResult(output=ceo_response, model_used="ceo")

    def is_ready(self) -> bool:
        return True


# Singleton
_broker: CeoBroker | None = None


def get_ceo_broker() -> CeoBroker:
    global _broker
    if _broker is None:
        _broker = CeoBroker()
    return _broker
