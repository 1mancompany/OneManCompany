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

import yaml
from loguru import logger

from onemancompany.core.config import ENCODING_UTF8

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
