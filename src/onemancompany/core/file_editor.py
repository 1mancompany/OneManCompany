"""File Editor — file editing tool (requires CEO approval).

Agent proposes a file edit → queued for approval → CEO approves → backup original → execute edit.
"""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime
from pathlib import Path

from onemancompany.core.config import COMPANY_DIR, EMPLOYEES_DIR, ENCODING_UTF8, PROJECTS_DIR, SOURCE_ROOT
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.models import DecisionStatus

# Single-file constants
BACKUP_FOLDER_NAME = ".backups"
WORKSPACE_DIR_NAME = "workspace"
_PERM_BACKEND_CODE = "backend_code_maintenance"
_PATH_PREFIX_SRC = "src/"
_PATH_PREFIX_COMPANY = "company/"

# In-memory pending edits awaiting CEO approval
pending_file_edits: dict[str, dict] = {}


def _resolve_path(file_path: str, permissions: list[str] | None = None) -> Path | None:
    """Resolve a file path. Access scope depends on employee permissions.

    - All employees can access company/ (default scope).
    - ``backend_code_maintenance`` permission extends access to src/.
    """
    try:
        p = Path(file_path)
        if not p.is_absolute():
            # Paths starting with "src/" resolve relative to SOURCE_ROOT
            if file_path.startswith(_PATH_PREFIX_SRC):
                p = SOURCE_ROOT / p
            else:
                # Strip leading "company/" if present — COMPANY_DIR already
                # points to the company/ directory, so "company/foo" would
                # resolve to company/company/foo without this guard.
                if file_path.startswith(_PATH_PREFIX_COMPANY):
                    file_path = file_path[len(_PATH_PREFIX_COMPANY):]
                    p = COMPANY_DIR / file_path
                else:
                    p = COMPANY_DIR / p
        p = p.resolve()

        # All employees can access company/
        if str(p).startswith(str(COMPANY_DIR.resolve())):
            return p

        # backend_code_maintenance allows access to src/
        if permissions and _PERM_BACKEND_CODE in permissions:
            src_dir = (SOURCE_ROOT / "src").resolve()
            if str(p).startswith(str(src_dir)):
                return p

        return None
    except Exception:
        return None


def is_in_free_zone(resolved_path: Path, employee_id: str = "", project_dir: str = "") -> bool:
    """Check if a resolved path is in an employee's free zone (no approval needed).

    Free zones:
    - employees/{employee_id}/workspace/ — employee's private workspace
    - The current task's project_dir/ — project workspace
    """
    p = str(resolved_path)

    # Employee workspace
    if employee_id:
        workspace = str((EMPLOYEES_DIR / employee_id / WORKSPACE_DIR_NAME).resolve())
        if p.startswith(workspace):
            return True

    # Project workspace
    if project_dir:
        proj = str(Path(project_dir).resolve())
        if p.startswith(proj) and str(PROJECTS_DIR.resolve()) in proj:
            return True

    return False


def propose_edit(
    file_path: str,
    new_content: str,
    reason: str,
    proposed_by: str,
    permissions: list[str] | None = None,
) -> dict:
    """Create a pending file edit request. Returns edit metadata."""
    resolved = _resolve_path(file_path, permissions=permissions)
    if resolved is None:
        return {"status": "error", "message": f"Invalid path or outside project scope: {file_path}"}

    # Read current content for diff display
    old_content = ""
    if resolved.exists():
        try:
            old_content = resolved.read_text(encoding=ENCODING_UTF8)
        except Exception:
            old_content = "(unable to read original file)"

    edit_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]

    # Use relative path for display (relative to company/)
    try:
        rel_path = str(resolved.relative_to(COMPANY_DIR))
    except ValueError:
        rel_path = str(resolved)

    edit = {
        "edit_id": edit_id,
        "file_path": str(resolved),
        "rel_path": rel_path,
        "old_content": old_content,
        "new_content": new_content,
        "reason": reason,
        "proposed_by": proposed_by,
        "created_at": datetime.now().isoformat(),
    }
    pending_file_edits[edit_id] = edit
    return {"status": "pending_approval", "edit_id": edit_id, "rel_path": rel_path}


def execute_edit(edit_id: str) -> dict:
    """Execute an approved file edit: backup original in-place, write new content."""
    edit = pending_file_edits.pop(edit_id, None)
    if not edit:
        return {"status": "error", "message": "Edit request not found or expired"}

    file_path = Path(edit["file_path"])
    rel_path = edit["rel_path"]
    backup_path = None

    # Backup original file into a .backups/ subfolder next to the file
    if file_path.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = file_path.parent / BACKUP_FOLDER_NAME
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_name = f"{file_path.stem}_{timestamp}{file_path.suffix}"
        backup_path = backup_dir / backup_name
        shutil.copy2(str(file_path), str(backup_path))

    # Write new content
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(edit["new_content"], encoding=ENCODING_UTF8)

    # Request soft-reload — runs immediately if idle, defers if agents are busy
    from onemancompany.core.state import request_reload
    request_reload()

    return {
        "status": "applied",
        "rel_path": rel_path,
        "backup_path": str(backup_path) if backup_path else None,
    }


def reject_edit(edit_id: str) -> dict:
    """Reject and discard a pending file edit."""
    edit = pending_file_edits.pop(edit_id, None)
    if not edit:
        return {"status": "error", "message": "Edit request not found or expired"}
    return {"status": DecisionStatus.REJECTED.value, "rel_path": edit["rel_path"]}


def list_pending_edits() -> list[dict]:
    """List all pending file edit requests (summary only)."""
    return [
        {
            "edit_id": e["edit_id"],
            "rel_path": e["rel_path"],
            "reason": e["reason"],
            "proposed_by": e["proposed_by"],
            "created_at": e["created_at"],
        }
        for e in pending_file_edits.values()
    ]


# ---------------------------------------------------------------------------
# Snapshot provider
# ---------------------------------------------------------------------------

from onemancompany.core.snapshot import snapshot_provider  # noqa: E402


@snapshot_provider("file_editor")
class _FileEditorSnapshot:
    @staticmethod
    def save() -> dict:
        if not pending_file_edits:
            return {}
        return {"pending_file_edits": pending_file_edits}

    @staticmethod
    def restore(data: dict) -> None:
        restored = data.get("pending_file_edits", {})
        if restored:
            pending_file_edits.update(restored)
