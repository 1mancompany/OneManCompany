"""Journal — append-only evidence chain for audit and debugging.

Every key decision (task dispatch, completion, resolution, cost, drift) is
recorded as an immutable JSON file with SHA256 content-addressing.

Storage layout::

    company/journal/{agent_name}/
        task_dispatch-{timestamp}-{hash}.json
        task_completed-{timestamp}-{hash}.json
        ...
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from onemancompany.core.config import COMPANY_DIR, ENCODING_UTF8

# Single-file constants
JOURNAL_DIR_NAME = "journal"


class EvidenceKind(str, Enum):
    TASK_DISPATCH = "task_dispatch"
    TASK_COMPLETED = "task_completed"
    TOOL_CALL = "tool_call"
    RESOLUTION_DECIDED = "resolution_decided"
    PERFORMANCE_REVIEW = "performance_review"
    COST_RECORDED = "cost_recorded"
    DRIFT_DETECTED = "drift_detected"
    STATE_TRANSITION = "state_transition"
    CEO_MESSAGE = "ceo_message"
    ERROR_CLASSIFIED = "error_classified"


class EvidenceRecord(BaseModel):
    kind: EvidenceKind
    agent: str
    task_id: str | None = None
    employee_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    payload: dict = Field(default_factory=dict)


class Journal:
    """Append-only evidence store with SHA256 content-addressing."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self.base = Path(base_dir) if base_dir else COMPANY_DIR / JOURNAL_DIR_NAME

    def write_sync(self, record: EvidenceRecord) -> str:
        """Write a record to disk. Returns the file path."""
        dir_path = self.base / record.agent
        dir_path.mkdir(parents=True, exist_ok=True)

        content = record.model_dump_json(indent=2)
        digest = hashlib.sha256(content.encode()).hexdigest()[:12]
        ts = int(record.created_at.timestamp())
        filename = f"{record.kind.value}-{ts}-{digest}.json"

        file_path = dir_path / filename
        file_path.write_text(content, encoding=ENCODING_UTF8)
        return str(file_path)

    def query(
        self,
        agent: str | None = None,
        kind: EvidenceKind | None = None,
        limit: int = 100,
    ) -> list[EvidenceRecord]:
        """Query records by agent and/or kind."""
        search_dir = self.base / agent if agent else self.base
        if not search_dir.exists():
            return []

        results: list[EvidenceRecord] = []
        for f in sorted(search_dir.rglob("*.json"), reverse=True):
            if kind and not f.name.startswith(kind.value):
                continue
            try:
                record = EvidenceRecord.model_validate_json(f.read_text(encoding=ENCODING_UTF8))
                results.append(record)
            except Exception as _e:
                logger.debug("Skipping corrupted evidence record {}: {}", f.name, _e)
                continue
            if len(results) >= limit:
                break
        return results

    def count(self, agent: str | None = None, kind: EvidenceKind | None = None) -> int:
        """Count records matching the filter."""
        search_dir = self.base / agent if agent else self.base
        if not search_dir.exists():
            return 0
        count = 0
        for f in search_dir.rglob("*.json"):
            if kind and not f.name.startswith(kind.value):
                continue
            count += 1
        return count


# Global singleton
journal = Journal()
