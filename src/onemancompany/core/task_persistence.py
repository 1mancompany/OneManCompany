"""Task persistence — tree-based recovery and task history.

AgentTask-based per-file persistence has been removed.
All task state flows through TaskTree YAML files.
This module provides tree-based schedule recovery on restart.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

import yaml

from onemancompany.core.config import EMPLOYEES_DIR, PROJECT_YAML_FILENAME, TASK_TREE_FILENAME
from onemancompany.core.task_lifecycle import RESOLVED, TaskPhase


def _is_project_archived(tree_path: Path) -> bool:
    """Check if the project containing this tree file is archived."""
    project_yaml = tree_path.parent / PROJECT_YAML_FILENAME
    if not project_yaml.exists():
        return False
    try:
        doc = yaml.safe_load(project_yaml.read_text()) or {}
        return doc.get("status") == "archived"
    except Exception:
        return False


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
            # Skip archived projects — no need to restore tasks
            if _is_project_archived(tree_path):
                logger.debug("Skipping archived project tree: {}", tree_path)
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
                    node.set_status(TaskPhase.FINISHED)
                    orphan_modified = True
                    logger.info(
                        "Auto-finished orphaned COMPLETED node {} (parent {} is {})",
                        node.id, parent.id, parent.status,
                    )
            if orphan_modified:
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
