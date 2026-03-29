"""Meeting minutes storage — archive and query past meetings."""
from __future__ import annotations

import yaml
from datetime import datetime
from pathlib import Path

from loguru import logger

from onemancompany.core.config import COMPANY_DIR, read_text_utf, write_text_utf

MINUTES_DIR = COMPANY_DIR / "meeting_minutes"


def archive_meeting(
    room_id: str,
    topic: str,
    project_id: str,
    participants: list[str],
    messages: list[dict],
    conclusion: str,
) -> str:
    """Archive a completed meeting. Returns minute_id."""
    MINUTES_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    minute_id = f"{room_id}_{ts}"
    doc = {
        "minute_id": minute_id,
        "room_id": room_id,
        "topic": topic,
        "project_id": project_id,
        "participants": participants,
        "start_time": messages[0]["time"] if messages else "",
        "end_time": messages[-1]["time"] if messages else "",
        "archived_at": datetime.now().isoformat(),
        "messages": messages,
        "conclusion": conclusion,
    }
    path = MINUTES_DIR / f"{minute_id}.yaml"
    write_text_utf(path, yaml.dump(doc, allow_unicode=True, default_flow_style=False))
    logger.info(
        "[meeting_minutes] Archived meeting {} ({} messages)",
        minute_id,
        len(messages),
    )
    return minute_id


def load_minute(minute_id: str) -> dict:
    """Load a single meeting minute by ID."""
    path = MINUTES_DIR / f"{minute_id}.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(read_text_utf(path)) or {}


def query_minutes(
    room_id: str = "",
    project_id: str = "",
    employee_id: str = "",
    limit: int = 10,
) -> list[dict]:
    """Query archived minutes with optional filters."""
    if not MINUTES_DIR.exists():
        return []
    results = []
    for f in sorted(MINUTES_DIR.iterdir(), reverse=True):
        if f.suffix != ".yaml":
            continue
        doc = yaml.safe_load(read_text_utf(f)) or {}
        if room_id and doc.get("room_id") != room_id:
            continue
        if project_id and doc.get("project_id") != project_id:
            continue
        if employee_id and employee_id not in doc.get("participants", []):
            continue
        summary = {k: v for k, v in doc.items() if k != "messages"}
        summary["message_count"] = len(doc.get("messages", []))
        results.append(summary)
        if len(results) >= limit:
            break
    return results
