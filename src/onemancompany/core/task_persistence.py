"""Task persistence — tree-based recovery and task history.

AgentTask-based per-file persistence has been removed.
All task state flows through TaskTree YAML files.
This module provides tree-based schedule recovery on restart.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

import yaml

from onemancompany.core.config import EMPLOYEES_DIR, PROJECT_YAML_FILENAME, TASK_TREE_FILENAME, read_text_utf
from onemancompany.core.task_lifecycle import RESOLVED, TaskPhase, NodeType

_CLOSED_PROJECT_STATUSES = {"archived", "completed", "failed", "cancelled"}


def _project_metadata_paths(tree_path: Path) -> list[Path]:
    """Return possible project/iteration metadata files for a task tree."""
    project_dir = tree_path.parent
    candidates = [
        project_dir / PROJECT_YAML_FILENAME,
    ]
    if project_dir.parent.name == "iterations":
        candidates.extend([
            project_dir.parent / f"{project_dir.name}.yaml",
            project_dir.parent.parent / PROJECT_YAML_FILENAME,
        ])
    else:
        candidates.extend([
            project_dir.parent / PROJECT_YAML_FILENAME,
            project_dir.parent.parent / PROJECT_YAML_FILENAME,
        ])

    seen: set[Path] = set()
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def _project_statuses(tree_path: Path) -> list[str]:
    """Load all known status values for the tree's project/iteration."""
    statuses: list[str] = []
    for metadata_path in _project_metadata_paths(tree_path):
        if not metadata_path.exists():
            continue
        try:
            doc = yaml.safe_load(read_text_utf(metadata_path)) or {}
        except Exception as exc:
            logger.debug("Skipping unreadable project metadata {}: {}", metadata_path, exc)
            continue
        status = doc.get("status")
        if status:
            statuses.append(str(status))
    return statuses


def _is_project_archived(tree_path: Path) -> bool:
    """Check if the project containing this tree file is archived."""
    return "archived" in _project_statuses(tree_path)


def _is_project_closed(tree_path: Path) -> bool:
    """Check if the project/iteration is already final and should not recover work."""
    return any(status in _CLOSED_PROJECT_STATUSES for status in _project_statuses(tree_path))


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _tasks_dir(employee_id: str) -> Path:
    """Return the tasks directory for an employee: employees/{id}/tasks/."""
    return EMPLOYEES_DIR / employee_id / "tasks"


# ---------------------------------------------------------------------------
# Tree-based schedule recovery
# ---------------------------------------------------------------------------

def recover_schedule_from_trees(
    employee_manager,
    projects_dir: Path,
    employees_dir: Path,
) -> None:
    """Scan all project and system trees, rebuild EmployeeManager._schedule.

    Called on server restart:
    - PROCESSING nodes -> set to PENDING (will be re-executed)
    - PENDING nodes with deps resolved -> schedule_node()
    - HOLDING nodes -> leave as-is (watchdog will handle)
    """
    from onemancompany.core.task_tree import get_tree, save_tree_async

    # 1. Scan all task_tree.yaml files under projects_dir
    if projects_dir.exists():
        for tree_path in projects_dir.rglob(TASK_TREE_FILENAME):
            # Skip closed projects — no need to restore tasks or stale handlers.
            if _is_project_closed(tree_path):
                logger.debug("Skipping closed project tree: {}", tree_path)
                continue
            try:
                tree = get_tree(tree_path)
            except Exception:
                logger.warning("Skipping corrupt tree file: {}", tree_path)
                continue

            modified = False
            for node in tree._nodes.values():
                if node.status == TaskPhase.PROCESSING.value:
                    node.status = TaskPhase.PENDING.value
                    modified = True

            if modified:
                save_tree_async(tree_path)

            for node in tree._nodes.values():
                # CEO_PROMPT nodes are containers, not executable tasks — skip
                if node.node_type in (NodeType.CEO_PROMPT, NodeType.CEO_PROMPT.value):
                    continue
                if node.status == TaskPhase.PENDING.value and tree.all_deps_resolved(node.id):
                    employee_manager.schedule_node(
                        node.employee_id, node.id, str(tree_path),
                    )
                elif node.status == TaskPhase.HOLDING.value:
                    # HOLDING nodes must be in schedule so resume_held_task()
                    # can find them after restart (watchdog or CEO inbox resume).
                    employee_manager.schedule_node(
                        node.employee_id, node.id, str(tree_path),
                    )

            # 1b. Auto-finish orphaned COMPLETED nodes whose parent is already RESOLVED.
            # These nodes were left behind when the server restarted before the
            # completion consumer could propagate their status upward.
            orphan_modified = False
            for node in tree._nodes.values():
                if node.status != TaskPhase.COMPLETED.value:
                    continue
                parent = tree.get_node(node.parent_id) if node.parent_id else None
                if parent and TaskPhase(parent.status) in RESOLVED:
                    node.set_status(TaskPhase.ACCEPTED)
                    node.acceptance_result = {"passed": True, "notes": "Auto-accepted on recovery: parent already resolved."}
                    node.set_status(TaskPhase.FINISHED)
                    orphan_modified = True
                    logger.info(
                        "Auto-finished orphaned COMPLETED node {} (parent {} is {})",
                        node.id, parent.id, parent.status,
                    )
            if orphan_modified:
                save_tree_async(tree_path)

            # 1c. After orphan cleanup, check if the project is now fully complete.
            # If so, advance CEO_PROMPT from PENDING → COMPLETED so the completion
            # flow can pick it up on the next heartbeat or confirmation cycle.
            if orphan_modified and tree.is_project_complete():
                ea_node = tree.get_ea_node()
                if ea_node:
                    ceo_root = tree.get_node(ea_node.parent_id) if ea_node.parent_id else None
                    if ceo_root and ceo_root.node_type in (NodeType.CEO_PROMPT, NodeType.CEO_PROMPT.value):
                        if ceo_root.status == TaskPhase.PENDING.value:
                            ceo_root.set_status(TaskPhase.PROCESSING)
                            ceo_root.set_status(TaskPhase.COMPLETED)
                            logger.info(
                                "[RECOVERY] Project complete after orphan cleanup — "
                                "CEO root {} → COMPLETED", ceo_root.id,
                            )
                            save_tree_async(tree_path)

    # 2. Scan system task trees (legacy system_tasks.yaml)
    if employees_dir.exists():
        for sys_path in employees_dir.rglob("system_tasks.yaml"):
            try:
                from onemancompany.core.system_tasks import SystemTaskTree
                emp_id = sys_path.parent.name
                sys_tree = SystemTaskTree.load(sys_path, emp_id)
            except Exception:
                logger.warning("Skipping corrupt system tree: {}", sys_path)
                continue

            modified = False
            for node in sys_tree.get_all_nodes():
                if node.status == TaskPhase.PROCESSING.value:
                    node.status = TaskPhase.PENDING.value
                    modified = True
                if node.status == TaskPhase.PENDING.value:
                    employee_manager.schedule_node(
                        node.employee_id, node.id, str(sys_path),
                    )

            if modified:
                sys_tree.save(sys_path)

    # 3. Scan adhoc task trees (employees/{id}/tasks/*_tree.yaml)
    #    Created by _push_adhoc_task() for HR reviews, meeting bookings, etc.
    if employees_dir.exists():
        for adhoc_path in employees_dir.rglob("tasks/*_tree.yaml"):
            try:
                tree = get_tree(adhoc_path)
            except Exception:
                logger.warning("Skipping corrupt adhoc tree: {}", adhoc_path)
                continue

            modified = False
            for node in tree._nodes.values():
                if node.status == TaskPhase.PROCESSING.value:
                    node.status = TaskPhase.PENDING.value
                    modified = True

            if modified:
                save_tree_async(adhoc_path)

            for node in tree._nodes.values():
                if node.status == TaskPhase.PENDING.value and tree.all_deps_resolved(node.id):
                    employee_manager.schedule_node(
                        node.employee_id, node.id, str(adhoc_path),
                    )
                    logger.info("[RECOVER] Restored adhoc task {} for employee {}",
                                node.id, node.employee_id)

    # 4. Rebuild ConversationService index from disk
    # (async recovery of stuck conversations is handled at server startup)
    from onemancompany.core.conversation import get_conversation_service
    conv_svc = get_conversation_service()
    conv_svc.rebuild_index()
