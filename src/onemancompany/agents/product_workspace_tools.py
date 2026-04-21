"""Product workspace tools for LangChain agents.

Provides promote_to_product — merges a project worktree back into the shared
product workspace (main branch).
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool
from loguru import logger

from onemancompany.core import product_workspace as pw


# ---------------------------------------------------------------------------
# Context resolver
# ---------------------------------------------------------------------------


def _resolve_product_workspace() -> tuple[Path, Path, str]:
    """Resolve the current execution context to find the product workspace.

    Returns (workspace_dir, worktree_path, project_id).
    """
    from onemancompany.core.vessel import _current_vessel
    from onemancompany.core.project_archive import load_named_project
    from onemancompany.core.product import find_slug_by_product_id
    from onemancompany.core.config import PRODUCTS_DIR, PROJECTS_DIR

    vessel = _current_vessel.get()
    if not vessel:
        raise ValueError("No active vessel context")

    # Get project_id from the running node
    project_id = ""
    if hasattr(vessel, "_running_node") and vessel._running_node:
        project_id = vessel._running_node.project_id or ""
    if not project_id:
        raise ValueError("Current task is not part of a project")

    proj_doc = load_named_project(project_id)
    if not proj_doc:
        raise ValueError(f"Project {project_id} not found")

    product_id = proj_doc.get("product_id", "")
    if not product_id:
        raise ValueError(f"Project {project_id} is not linked to a product")

    slug = find_slug_by_product_id(product_id)
    if not slug:
        raise ValueError(f"Product not found for id={product_id}")

    workspace_dir = PRODUCTS_DIR / slug / "workspace"
    worktree_path = PROJECTS_DIR / project_id / "product_worktree"

    if not workspace_dir.exists():
        raise ValueError(f"Product workspace not initialized for {slug}")
    if not worktree_path.exists():
        raise ValueError(f"Product worktree not found at {worktree_path}")

    return workspace_dir, worktree_path, project_id


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
async def promote_to_product(abort: bool = False) -> str:
    """Merge your product workspace changes into the shared product.

    Syncs with the latest product state, then merges your changes.
    If there are conflicts, returns both versions for each conflicted file.
    Edit the files in your product workspace to resolve, then call again.

    Args:
        abort: Set to True to abort an in-progress merge and restore clean state.
    """
    workspace_dir, worktree_path, project_id = _resolve_product_workspace()
    logger.debug(
        "promote_to_product: project_id={} abort={} ws={} wt={}",
        project_id, abort, workspace_dir, worktree_path,
    )

    result = pw.promote(workspace_dir, worktree_path, project_id, abort=abort)
    status = result.get("status", "")
    message = result.get("message", "")
    conflicts = result.get("conflicts", [])

    if status == "conflict" and conflicts:
        lines = [f"Merge conflicts detected in {len(conflicts)} file(s):\n"]
        for c in conflicts:
            lines.append(f"--- {c['file']} ---")
            lines.append(f"YOUR VERSION:\n{c['your_version']}")
            lines.append(f"PRODUCT VERSION:\n{c['product_version']}")
            lines.append("")
        lines.append(
            "Edit the conflicted files in your product workspace to resolve, "
            "then call promote_to_product() again."
        )
        return "\n".join(lines)

    return message


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

PRODUCT_WORKSPACE_TOOLS = [promote_to_product]
