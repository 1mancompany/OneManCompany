"""File Editor — 文件编辑工具（需CEO审批）

Agent 提出文件编辑请求 → 存入待审队列 → CEO 审批 → 备份原文件 → 执行编辑。
"""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime
from pathlib import Path

from onemancompany.core.config import PROJECT_ROOT
from onemancompany.core.events import CompanyEvent, event_bus

# Backup directory for file edits
BACKUPS_DIR = PROJECT_ROOT / "backups"

# In-memory pending edits awaiting CEO approval
pending_file_edits: dict[str, dict] = {}


def _resolve_path(file_path: str) -> Path | None:
    """Resolve a file path relative to PROJECT_ROOT. Returns None if outside root."""
    try:
        p = Path(file_path)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        p = p.resolve()
        # Security: must stay within PROJECT_ROOT
        if not str(p).startswith(str(PROJECT_ROOT.resolve())):
            return None
        return p
    except Exception:
        return None


def propose_edit(
    file_path: str,
    new_content: str,
    reason: str,
    proposed_by: str,
) -> dict:
    """Create a pending file edit request. Returns edit metadata."""
    resolved = _resolve_path(file_path)
    if resolved is None:
        return {"status": "error", "message": f"路径不合法或超出项目范围: {file_path}"}

    # Read current content for diff display
    old_content = ""
    if resolved.exists():
        try:
            old_content = resolved.read_text(encoding="utf-8")
        except Exception:
            old_content = "(无法读取原文件)"

    edit_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]

    # Use relative path for display
    try:
        rel_path = str(resolved.relative_to(PROJECT_ROOT))
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
    """Execute an approved file edit: backup original, write new content."""
    edit = pending_file_edits.pop(edit_id, None)
    if not edit:
        return {"status": "error", "message": "编辑请求未找到或已过期"}

    file_path = Path(edit["file_path"])
    rel_path = edit["rel_path"]
    backup_path = None

    # Backup original file if it exists
    if file_path.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Mirror the directory structure under backups/
        backup_rel = Path(rel_path)
        backup_dir = BACKUPS_DIR / backup_rel.parent
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_name = f"{backup_rel.stem}_{timestamp}{backup_rel.suffix}"
        backup_path = backup_dir / backup_name
        shutil.copy2(str(file_path), str(backup_path))

    # Write new content
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(edit["new_content"], encoding="utf-8")

    return {
        "status": "applied",
        "rel_path": rel_path,
        "backup_path": str(backup_path) if backup_path else None,
    }


def reject_edit(edit_id: str) -> dict:
    """Reject and discard a pending file edit."""
    edit = pending_file_edits.pop(edit_id, None)
    if not edit:
        return {"status": "error", "message": "编辑请求未找到或已过期"}
    return {"status": "rejected", "rel_path": edit["rel_path"]}


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
