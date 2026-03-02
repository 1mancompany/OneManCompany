"""Project Archive — project record and workspace system.

Each project is a directory under company/business/projects/{project_id}/ containing:
  - project.yaml  — metadata, timeline, status
  - Any other files produced during the project (code, results, media, etc.)

Employees can save artifacts to their project workspace via save_project_file().
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import yaml

from onemancompany.core.config import PROJECTS_DIR


def _project_dir(project_id: str) -> Path:
    """Return the directory path for a given project."""
    return PROJECTS_DIR / project_id


def _project_yaml(project_id: str) -> Path:
    """Return the project.yaml path for a given project."""
    return _project_dir(project_id) / "project.yaml"


def _ensure_project_dir(project_id: str) -> Path:
    """Ensure the project directory exists and return it."""
    d = _project_dir(project_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_project(task: str, routed_to: str, participants: list[str] | None = None) -> str:
    """Create a new project record. Returns the project_id."""
    project_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    project_dir = _ensure_project_dir(project_id)
    doc = {
        "project_id": project_id,
        "project_dir": str(project_dir),
        "task": task,
        "routed_to": routed_to,
        "participants": participants or [],
        "current_owner": routed_to.lower(),
        "status": "in_progress",
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "timeline": [],
        "output": None,
        "acceptance_criteria": [],
        "responsible_officer": "",
        "dispatches": [],
        "acceptance_result": None,
        "cost": {
            "budget_estimate_usd": 0.0,
            "actual_cost_usd": 0.0,
            "token_usage": {"input": 0, "output": 0, "total": 0},
            "breakdown": [],
        },
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
        "detail": detail[:500],
    })
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
    doc["current_owner"] = ""

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
    path = _project_yaml(project_id)
    if not path.exists():
        return None
    with open(path) as f:
        return yaml.safe_load(f) or {}


def get_project_dir(project_id: str) -> str:
    """Return the absolute path of a project's workspace directory."""
    return str(_project_dir(project_id))


def save_project_file(project_id: str, filename: str, content: str | bytes) -> dict:
    """Save a file into the project workspace directory.

    Args:
        project_id: The project ID.
        filename: File name or relative sub-path (e.g. "report.md" or "code/main.py").
        content: File content (str for text, bytes for binary).

    Returns:
        A dict with status and the saved file path.
    """
    project_dir = _ensure_project_dir(project_id)
    file_path = project_dir / filename

    # Security: ensure the resolved path stays within the project directory
    resolved = file_path.resolve()
    if not str(resolved).startswith(str(project_dir.resolve())):
        return {"status": "error", "message": f"Path escapes project directory: {filename}"}

    file_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        file_path.write_bytes(content)
    else:
        file_path.write_text(content, encoding="utf-8")

    return {"status": "ok", "path": str(file_path), "relative": filename}


def list_project_files(project_id: str) -> list[str]:
    """List all files in a project workspace (excluding project.yaml)."""
    project_dir = _project_dir(project_id)
    if not project_dir.exists():
        return []
    files = []
    for p in sorted(project_dir.rglob("*")):
        if p.is_file() and p.name != "project.yaml":
            files.append(str(p.relative_to(project_dir)))
    return files


def list_projects() -> list[dict]:
    """List all projects (summary only)."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    projects = []
    for d in sorted(PROJECTS_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        yaml_path = d / "project.yaml"
        if not yaml_path.exists():
            continue
        with open(yaml_path) as fh:
            doc = yaml.safe_load(fh) or {}
        projects.append({
            "project_id": doc.get("project_id", d.name),
            "task": doc.get("task", ""),
            "status": doc.get("status", "unknown"),
            "routed_to": doc.get("routed_to", ""),
            "current_owner": doc.get("current_owner", ""),
            "created_at": doc.get("created_at", ""),
            "completed_at": doc.get("completed_at"),
            "participant_count": len(doc.get("participants", [])),
            "action_count": len(doc.get("timeline", [])),
            "file_count": len(list_project_files(d.name)),
        })
    return projects


def set_acceptance_criteria(project_id: str, criteria: list[str], responsible_officer: str) -> None:
    """Set or update acceptance criteria and responsible officer."""
    doc = load_project(project_id)
    if not doc:
        return
    doc["acceptance_criteria"] = criteria
    doc["responsible_officer"] = responsible_officer
    _save_project(project_id, doc)


def record_dispatch(project_id: str, employee_id: str, description: str) -> None:
    """Record that a task was dispatched to an agent for this project."""
    doc = load_project(project_id)
    if not doc:
        return
    dispatches = doc.get("dispatches", [])
    dispatches.append({
        "employee_id": employee_id,
        "description": description[:200],
        "status": "in_progress",
        "dispatched_at": datetime.now().isoformat(),
    })
    doc["dispatches"] = dispatches
    _save_project(project_id, doc)


def record_dispatch_completion(project_id: str, employee_id: str) -> None:
    """Mark a dispatch as completed."""
    doc = load_project(project_id)
    if not doc:
        return
    for d in doc.get("dispatches", []):
        if d["employee_id"] == employee_id and d["status"] == "in_progress":
            d["status"] = "completed"
            d["completed_at"] = datetime.now().isoformat()
            break
    _save_project(project_id, doc)


def all_dispatches_complete(project_id: str) -> bool:
    """Check if all dispatches for a project are completed."""
    doc = load_project(project_id)
    if not doc:
        return True
    dispatches = doc.get("dispatches", [])
    if not dispatches:
        return True
    return all(d["status"] == "completed" for d in dispatches)


def set_acceptance_result(project_id: str, accepted: bool, officer_id: str, notes: str = "") -> None:
    """Record the acceptance result."""
    doc = load_project(project_id)
    if not doc:
        return
    doc["acceptance_result"] = {
        "accepted": accepted,
        "officer_id": officer_id,
        "notes": notes,
        "timestamp": datetime.now().isoformat(),
    }
    _save_project(project_id, doc)


def set_project_budget(project_id: str, budget_usd: float) -> None:
    """Set estimated budget for a project."""
    doc = load_project(project_id)
    if not doc:
        return
    cost = doc.setdefault("cost", {})
    cost["budget_estimate_usd"] = budget_usd
    _save_project(project_id, doc)


def record_project_cost(
    project_id: str,
    employee_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> None:
    """Append a cost entry to the project breakdown and update totals."""
    doc = load_project(project_id)
    if not doc:
        return
    cost = doc.setdefault("cost", {
        "budget_estimate_usd": 0.0,
        "actual_cost_usd": 0.0,
        "token_usage": {"input": 0, "output": 0, "total": 0},
        "breakdown": [],
    })
    cost["actual_cost_usd"] = cost.get("actual_cost_usd", 0.0) + cost_usd
    tokens = cost.setdefault("token_usage", {"input": 0, "output": 0, "total": 0})
    tokens["input"] = tokens.get("input", 0) + input_tokens
    tokens["output"] = tokens.get("output", 0) + output_tokens
    tokens["total"] = tokens.get("total", 0) + input_tokens + output_tokens
    breakdown = cost.setdefault("breakdown", [])
    breakdown.append({
        "employee_id": employee_id,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": cost_usd,
    })
    _save_project(project_id, doc)


def _save_project(project_id: str, doc: dict) -> None:
    _ensure_project_dir(project_id)
    path = _project_yaml(project_id)
    with open(path, "w") as f:
        yaml.dump(doc, f, allow_unicode=True, default_flow_style=False)
