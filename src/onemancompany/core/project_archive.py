"""Project Archive — 项目留痕系统

Persists all project data to `projects/{project_id}.yaml` for review.
Each project records:
  - task input (CEO's original request)
  - participants
  - timeline of actions (who did what, in order)
  - final output / deliverables
  - status (in_progress / completed)
"""
from __future__ import annotations

import uuid
from datetime import datetime

import yaml

from onemancompany.core.config import PROJECTS_DIR


def _ensure_projects_dir() -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def create_project(task: str, routed_to: str, participants: list[str] | None = None) -> str:
    """Create a new project record. Returns the project_id."""
    _ensure_projects_dir()
    project_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    doc = {
        "project_id": project_id,
        "task": task,
        "routed_to": routed_to,
        "participants": participants or [],
        "current_owner": routed_to.lower(),  # 当前任务归属人
        "status": "in_progress",
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "timeline": [],
        "output": None,
    }
    _save_project(project_id, doc)
    return project_id


def append_action(project_id: str, employee_id: str, action: str, detail: str = "") -> None:
    """Append an action entry to the project timeline and update current_owner."""
    doc = load_project(project_id)
    if not doc:
        return
    doc["timeline"].append({
        "time": datetime.now().isoformat(),
        "employee_id": employee_id,
        "action": action,
        "detail": detail[:500],  # cap detail length
    })
    # Update current owner to whoever just performed an action
    if employee_id:
        doc["current_owner"] = employee_id
    _save_project(project_id, doc)


def complete_project(project_id: str, output: str = "") -> None:
    """Mark a project as completed, prune non-participants, and record output."""
    doc = load_project(project_id)
    if not doc:
        return
    doc["status"] = "completed"
    doc["completed_at"] = datetime.now().isoformat()
    doc["output"] = output
    doc["current_owner"] = ""  # no owner after completion

    # Prune participants to only those who actually contributed (have timeline entries)
    actual_contributors = {
        entry["employee_id"]
        for entry in doc.get("timeline", [])
        if entry.get("employee_id")
    }
    if actual_contributors:
        doc["participants"] = [
            pid for pid in doc.get("participants", [])
            if pid in actual_contributors
        ]

    _save_project(project_id, doc)


def load_project(project_id: str) -> dict | None:
    """Load a single project record."""
    path = PROJECTS_DIR / f"{project_id}.yaml"
    if not path.exists():
        return None
    with open(path) as f:
        return yaml.safe_load(f) or {}


def list_projects() -> list[dict]:
    """List all projects (summary only: id, task, status, created_at)."""
    _ensure_projects_dir()
    projects = []
    for f in sorted(PROJECTS_DIR.iterdir(), reverse=True):
        if f.suffix != ".yaml" or not f.is_file():
            continue
        with open(f) as fh:
            doc = yaml.safe_load(fh) or {}
        projects.append({
            "project_id": doc.get("project_id", f.stem),
            "task": doc.get("task", ""),
            "status": doc.get("status", "unknown"),
            "routed_to": doc.get("routed_to", ""),
            "current_owner": doc.get("current_owner", ""),
            "created_at": doc.get("created_at", ""),
            "completed_at": doc.get("completed_at"),
            "participant_count": len(doc.get("participants", [])),
            "action_count": len(doc.get("timeline", [])),
        })
    return projects


def _save_project(project_id: str, doc: dict) -> None:
    _ensure_projects_dir()
    path = PROJECTS_DIR / f"{project_id}.yaml"
    with open(path, "w") as f:
        yaml.dump(doc, f, allow_unicode=True, default_flow_style=False)
