"""Resolution system — batch file-edit review for CEO approval.

During a task, file edits are collected silently. When the task completes,
all edits are bundled into a Resolution YAML and presented to the CEO
for batch review (approve / reject / defer per edit).
"""
from __future__ import annotations

import contextvars
import hashlib
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from onemancompany.core.config import RESOLUTIONS_DIR
from onemancompany.core.models import DecisionStatus

# ---------------------------------------------------------------------------
# Context variable: tracks the current project_id during agent execution
# ---------------------------------------------------------------------------
current_project_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_project_id", default=""
)

# ---------------------------------------------------------------------------
# In-memory accumulator: project_id -> list of edit dicts
# ---------------------------------------------------------------------------
_task_edits: dict[str, list[dict]] = {}


def _compute_md5(content: str) -> str:
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def collect_edit(project_id: str, edit: dict) -> None:
    """Append an edit to the in-memory accumulator for a project."""
    if project_id not in _task_edits:
        _task_edits[project_id] = []
    _task_edits[project_id].append(edit)


def create_resolution(project_id: str, task_description: str) -> dict | None:
    """Flush the accumulator for *project_id* into a Resolution YAML file.

    Returns the resolution dict (suitable as an event payload), or None if
    no edits were accumulated.
    """
    edits = _task_edits.pop(project_id, [])
    if not edits:
        return None

    RESOLUTIONS_DIR.mkdir(parents=True, exist_ok=True)

    resolution_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]

    resolution_edits = []
    for idx, edit in enumerate(edits):
        old_content = edit.get("old_content", "")
        resolution_edits.append({
            "edit_id": edit.get("edit_id", f"edit_{idx:03d}"),
            "file_path": edit.get("file_path", ""),
            "rel_path": edit.get("rel_path", ""),
            "old_content": old_content,
            "new_content": edit.get("new_content", ""),
            "reason": edit.get("reason", ""),
            "proposed_by": edit.get("proposed_by", ""),
            "original_md5": _compute_md5(old_content),
            "decision": None,
            "decided_at": None,
            "executed": False,
            "expired": False,
        })

    resolution = {
        "resolution_id": resolution_id,
        "project_id": project_id,
        "task": task_description,
        "created_at": datetime.now().isoformat(),
        "status": DecisionStatus.PENDING.value,
        "edits": resolution_edits,
    }

    path = RESOLUTIONS_DIR / f"{resolution_id}.yaml"
    with open(path, "w") as f:
        yaml.dump(resolution, f, allow_unicode=True, default_flow_style=False)

    return resolution


def load_resolution(resolution_id: str) -> dict | None:
    """Read a resolution from its YAML file."""
    path = RESOLUTIONS_DIR / f"{resolution_id}.yaml"
    if not path.exists():
        return None
    with open(path) as f:
        return yaml.safe_load(f)


def _save_resolution(resolution: dict) -> None:
    """Persist a resolution dict back to its YAML file."""
    path = RESOLUTIONS_DIR / f"{resolution['resolution_id']}.yaml"
    with open(path, "w") as f:
        yaml.dump(resolution, f, allow_unicode=True, default_flow_style=False)


def list_resolutions(status_filter: str | None = None) -> list[dict]:
    """List all resolutions (summary only)."""
    if not RESOLUTIONS_DIR.exists():
        return []
    results = []
    for p in sorted(RESOLUTIONS_DIR.iterdir(), reverse=True):
        if p.suffix != ".yaml":
            continue
        with open(p) as f:
            data = yaml.safe_load(f)
        if not data:
            continue
        if status_filter and data.get("status") != status_filter:
            continue
        results.append({
            "resolution_id": data.get("resolution_id"),
            "project_id": data.get("project_id"),
            "task": data.get("task"),
            "created_at": data.get("created_at"),
            "status": data.get("status"),
            "edit_count": len(data.get("edits", [])),
        })
    return results


def list_deferred_edits() -> list[dict]:
    """List all edits with decision=defer across all resolutions.

    For each deferred edit, check the current file MD5 against the
    original_md5 and update the expired flag.
    """
    if not RESOLUTIONS_DIR.exists():
        return []
    results = []
    for p in sorted(RESOLUTIONS_DIR.iterdir(), reverse=True):
        if p.suffix != ".yaml":
            continue
        with open(p) as f:
            data = yaml.safe_load(f)
        if not data:
            continue
        for edit in data.get("edits", []):
            if edit.get("decision") != "defer":
                continue
            if edit.get("executed"):
                continue
            # Check staleness
            file_path = Path(edit.get("file_path", ""))
            if file_path.exists():
                current_md5 = _compute_md5(file_path.read_text(encoding="utf-8"))
                edit["expired"] = current_md5 != edit.get("original_md5", "")
            else:
                edit["expired"] = True
            results.append({
                "resolution_id": data.get("resolution_id"),
                "project_id": data.get("project_id"),
                "task": data.get("task"),
                "edit_id": edit.get("edit_id"),
                "rel_path": edit.get("rel_path"),
                "reason": edit.get("reason"),
                "proposed_by": edit.get("proposed_by"),
                "expired": edit.get("expired", False),
                "decided_at": edit.get("decided_at"),
            })
    return results


def decide_resolution(resolution_id: str, decisions: dict[str, str]) -> dict:
    """Apply CEO decisions to each edit in a resolution.

    *decisions* maps edit_id -> "approve" | "reject" | "defer".
    Approved edits are executed immediately (backup + write).
    """
    resolution = load_resolution(resolution_id)
    if not resolution:
        return {"status": "error", "message": "Resolution not found"}

    from onemancompany.core.file_editor import pending_file_edits, execute_edit as _raw_execute

    results = []
    now = datetime.now().isoformat()

    for edit in resolution.get("edits", []):
        eid = edit["edit_id"]
        decision = decisions.get(eid)
        if not decision:
            continue

        edit["decision"] = decision
        edit["decided_at"] = now

        if decision == "approve":
            # Put the edit into pending_file_edits so execute_edit can find it
            pending_file_edits[eid] = {
                "edit_id": eid,
                "file_path": edit["file_path"],
                "rel_path": edit["rel_path"],
                "old_content": edit["old_content"],
                "new_content": edit["new_content"],
                "reason": edit["reason"],
                "proposed_by": edit["proposed_by"],
                "created_at": edit.get("decided_at", now),
            }
            exec_result = _raw_execute(eid)
            edit["executed"] = exec_result.get("status") == "applied"
            results.append({"edit_id": eid, "decision": "approve", **exec_result})

        elif decision == "reject":
            edit["executed"] = False
            results.append({"edit_id": eid, "decision": "reject", "status": DecisionStatus.REJECTED.value})

        elif decision == "defer":
            edit["executed"] = False
            # Store current file MD5 for staleness tracking
            file_path = Path(edit["file_path"])
            if file_path.exists():
                edit["original_md5"] = _compute_md5(
                    file_path.read_text(encoding="utf-8")
                )
            results.append({"edit_id": eid, "decision": "defer", "status": DecisionStatus.DEFERRED.value})

    # Update status: decided if all edits have a decision
    all_decided = all(e.get("decision") is not None for e in resolution.get("edits", []))
    if all_decided:
        has_deferred = any(e.get("decision") == "defer" for e in resolution.get("edits", []))
        resolution["status"] = DecisionStatus.PENDING.value if has_deferred else "decided"
    else:
        resolution["status"] = DecisionStatus.PENDING.value

    _save_resolution(resolution)
    return {"status": "ok", "results": results}


def execute_deferred_edit(resolution_id: str, edit_id: str) -> dict:
    """Execute a previously deferred edit. Check MD5 first — fail if expired."""
    resolution = load_resolution(resolution_id)
    if not resolution:
        return {"status": "error", "message": "Resolution not found"}

    edit = None
    for e in resolution.get("edits", []):
        if e["edit_id"] == edit_id:
            edit = e
            break

    if not edit:
        return {"status": "error", "message": "Edit not found in resolution"}

    if edit.get("decision") != "defer":
        return {"status": "error", "message": "Edit is not deferred"}

    if edit.get("executed"):
        return {"status": "error", "message": "Edit already executed"}

    # Check staleness
    file_path = Path(edit["file_path"])
    if file_path.exists():
        current_md5 = _compute_md5(file_path.read_text(encoding="utf-8"))
        if current_md5 != edit.get("original_md5", ""):
            edit["expired"] = True
            _save_resolution(resolution)
            return {"status": "error", "message": "File has changed since deferral (expired)"}
    else:
        # File doesn't exist — it's either new or was deleted
        if edit.get("old_content"):
            edit["expired"] = True
            _save_resolution(resolution)
            return {"status": "error", "message": "Original file no longer exists (expired)"}

    # Execute via file_editor
    from onemancompany.core.file_editor import pending_file_edits, execute_edit as _raw_execute

    pending_file_edits[edit_id] = {
        "edit_id": edit_id,
        "file_path": edit["file_path"],
        "rel_path": edit["rel_path"],
        "old_content": edit["old_content"],
        "new_content": edit["new_content"],
        "reason": edit["reason"],
        "proposed_by": edit["proposed_by"],
        "created_at": datetime.now().isoformat(),
    }
    exec_result = _raw_execute(edit_id)
    edit["executed"] = exec_result.get("status") == "applied"
    edit["decision"] = "approve"
    edit["decided_at"] = datetime.now().isoformat()

    # Check if all edits are now fully decided & executed
    all_done = all(
        e.get("decision") in ("approve", "reject") and
        (e.get("executed") or e.get("decision") == "reject")
        for e in resolution.get("edits", [])
    )
    if all_done:
        resolution["status"] = "decided"

    _save_resolution(resolution)
    return {"status": "ok", **exec_result}


# ---------------------------------------------------------------------------
# Snapshot provider — in-flight task edits
# ---------------------------------------------------------------------------

from onemancompany.core.snapshot import snapshot_provider  # noqa: E402


@snapshot_provider("resolutions")
class _ResolutionsSnapshot:
    @staticmethod
    def save() -> dict:
        if not _task_edits:
            return {}
        return {"task_edits": _task_edits}

    @staticmethod
    def restore(data: dict) -> None:
        restored = data.get("task_edits", {})
        if restored:
            _task_edits.update(restored)
