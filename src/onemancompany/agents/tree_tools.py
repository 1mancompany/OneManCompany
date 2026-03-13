"""Tree tools — dispatch_child, accept_child, reject_child.

These tools allow parent tasks to dispatch subtasks to employees,
then accept or reject results. They operate on a TaskTree persisted
as task_tree.yaml in the project directory.
"""
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool
from loguru import logger

from onemancompany.core.task_lifecycle import TaskPhase
from onemancompany.core.task_tree import TaskTree

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_tree(project_dir: str) -> TaskTree:
    """Get TaskTree from memory cache (loading from disk if needed)."""
    from onemancompany.core.task_tree import get_tree
    path = Path(project_dir) / "task_tree.yaml"
    if not path.exists():
        logger.warning("task_tree.yaml not found at %s", path)
        return TaskTree(project_id="")
    return get_tree(path)


def _save_tree(project_dir: str, tree: TaskTree) -> None:
    """Schedule async save of the TaskTree."""
    from onemancompany.core.task_tree import save_tree_async
    path = Path(project_dir) / "task_tree.yaml"
    save_tree_async(path)


def _get_current_node(tree: TaskTree, task_id: str):
    """Look up the TaskNode for the given task/node ID."""
    return tree.get_node(task_id)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def dispatch_child(
    employee_id: str,
    description: str,
    acceptance_criteria: list[str],
    timeout_seconds: int = 3600,
    depends_on: list[str] | None = None,
    fail_strategy: str = "block",
) -> dict:
    """Dispatch a child task to an employee with acceptance criteria.

    Creates a child node in the task tree and schedules it for execution.
    The child must complete and be accepted before this task can finish.

    If depends_on is provided, the child will only be scheduled when all dependency
    nodes reach a terminal status. Until then the child is created in the tree
    but not scheduled.

    Args:
        employee_id: Target employee ID
        description: What the employee should do
        acceptance_criteria: List of measurable criteria the result must meet
        timeout_seconds: Max seconds allowed for the child task (default 3600)
        depends_on: List of TaskNode IDs that must complete before this child starts
        fail_strategy: "block" (default) or "continue" — what to do if a dependency fails
    """
    from onemancompany.core.vessel import _current_vessel, _current_task_id

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return {"status": "error", "message": "No agent context."}

    # Load tree, find current node
    # Get project_dir from parent node
    from onemancompany.core.vessel import employee_manager
    # Find the entry for the current task_id in schedule
    project_dir = ""
    tree_path_str = ""
    for entries in employee_manager._schedule.values():
        for e in entries:
            if e.node_id == task_id:
                tree_path_str = e.tree_path
                project_dir = str(Path(e.tree_path).parent)
                break
        if tree_path_str:
            break

    if not project_dir or not tree_path_str:
        return {"status": "error", "message": "No project directory in current task context."}

    # Validate employee exists
    from onemancompany.core.store import load_employee
    if not load_employee(employee_id):
        return {"status": "error", "message": f"Employee {employee_id} not found."}

    tree = _load_tree(project_dir)
    current_node = _get_current_node(tree, task_id)
    if not current_node:
        return {"status": "error", "message": "Current task not found in task tree."}

    # EA can only dispatch to O-level executives
    from onemancompany.core.config import EA_ID, HR_ID, COO_ID, CSO_ID
    if current_node.employee_id == EA_ID:
        allowed_targets = {HR_ID, COO_ID, CSO_ID}
        if employee_id not in allowed_targets:
            return {
                "status": "error",
                "message": (
                    f"EA不能直接分派任务给 {employee_id}。"
                    f"请分派给对应负责人: HR({HR_ID}), COO({COO_ID}), CSO({CSO_ID})。"
                ),
            }

    # Normalize depends_on
    depends_on = depends_on or []

    # Validate depends_on IDs exist in tree
    for dep_id in depends_on:
        if not tree.get_node(dep_id):
            return {
                "status": "error",
                "message": f"Dependency node {dep_id} not found in task tree.",
            }

    # Add child node
    child = tree.add_child(
        parent_id=task_id,
        employee_id=employee_id,
        description=description,
        acceptance_criteria=acceptance_criteria,
        timeout_seconds=timeout_seconds,
        depends_on=depends_on,
        fail_strategy=fail_strategy,
    )
    child.project_id = current_node.project_id
    child.project_dir = project_dir

    # --- CEO request interception ---
    CEO_EMPLOYEE_ID = "00001"
    if employee_id == CEO_EMPLOYEE_ID:
        child.node_type = "ceo_request"
        _save_tree(project_dir, tree)
        # Persist task index entry for taskboard
        from onemancompany.core.store import append_task_index_entry
        append_task_index_entry(employee_id, child.id, tree_path_str)
        # Publish WebSocket event (async from sync context)
        from onemancompany.core.events import CompanyEvent, event_bus
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            loop.create_task(event_bus.publish(CompanyEvent(
                type="ceo_inbox_updated",
                payload={"node_id": child.id, "description": description},
                agent="SYSTEM",
            )))
        except RuntimeError:
            logger.warning("No event loop for ceo_inbox_updated publish")
        return {
            "status": "dispatched",
            "node_id": child.id,
            "employee_id": employee_id,
            "description": description,
            "node_type": "ceo_request",
            "ceo_request": True,
            "message": "Task dispatched to CEO inbox. CEO will respond when available.",
        }

    # --- Normal employee dispatch (existing logic) ---
    # Check if dependencies are already satisfied
    deps_resolved = tree.all_deps_resolved(child.id)

    if not deps_resolved:
        _save_tree(project_dir, tree)
        # Persist task index entry for taskboard even though not yet scheduled
        from onemancompany.core.store import append_task_index_entry
        append_task_index_entry(employee_id, child.id, tree_path_str)
        return {
            "status": "dispatched_waiting",
            "node_id": child.id,
            "employee_id": employee_id,
            "description": description,
            "dependency_status": "waiting",
        }

    # Save tree and schedule via employee_manager
    _save_tree(project_dir, tree)
    employee_manager.schedule_node(employee_id, child.id, tree_path_str)
    employee_manager._schedule_next(employee_id)

    return {
        "status": "dispatched",
        "node_id": child.id,
        "employee_id": employee_id,
        "description": description,
        "dependency_status": "resolved",
    }


@tool
def accept_child(node_id: str, notes: str = "") -> dict:
    """Accept a child task's result after reviewing it.

    Args:
        node_id: The TaskNode ID of the child to accept
        notes: Optional acceptance notes
    """
    from onemancompany.core.vessel import _current_vessel, _current_task_id

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return {"status": "error", "message": "No agent context."}

    # Find project_dir from current task context
    from onemancompany.core.vessel import employee_manager
    project_dir = ""
    for entries in employee_manager._schedule.values():
        for e in entries:
            if e.node_id == task_id:
                project_dir = str(Path(e.tree_path).parent)
                break
        if project_dir:
            break

    if not project_dir:
        return {"status": "error", "message": "No project context."}

    tree = _load_tree(project_dir)
    node = tree.get_node(node_id)
    if not node:
        return {"status": "error", "message": f"Node {node_id} not found."}

    node.set_status(TaskPhase.ACCEPTED)
    node.acceptance_result = {"passed": True, "notes": notes}
    _save_tree(project_dir, tree)

    # Trigger dependency resolution for dependents
    from onemancompany.core.vessel import _trigger_dep_resolution
    _trigger_dep_resolution(project_dir, tree, node)

    return {"status": "accepted", "node_id": node_id, "notes": notes}


@tool
def reject_child(node_id: str, reason: str, retry: bool = True) -> dict:
    """Reject a child task's result.

    Args:
        node_id: The TaskNode ID of the child to reject
        reason: Why the result was rejected
        retry: If True, schedule a correction task. If False, mark as failed.
    """
    from onemancompany.core.vessel import _current_vessel, _current_task_id

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return {"status": "error", "message": "No agent context."}

    from onemancompany.core.vessel import employee_manager
    project_dir = ""
    tree_path_str = ""
    for entries in employee_manager._schedule.values():
        for e in entries:
            if e.node_id == task_id:
                project_dir = str(Path(e.tree_path).parent)
                tree_path_str = e.tree_path
                break
        if project_dir:
            break

    if not project_dir:
        return {"status": "error", "message": "No project context."}

    tree = _load_tree(project_dir)
    node = tree.get_node(node_id)
    if not node:
        return {"status": "error", "message": f"Node {node_id} not found."}

    node.acceptance_result = {"passed": False, "notes": reason}

    if retry:
        from onemancompany.core.vessel import employee_manager as em
        if node.employee_id not in em.executors:
            return {"status": "error", "message": f"No handle for employee {node.employee_id}, cannot push correction task."}

        # Reset to pending and re-schedule
        node.set_status(TaskPhase.PENDING)
        node.result = ""
        node.description = (
            f"修正任务: {node.description}\n\n"
            f"拒绝原因: {reason}\n\n"
            f"验收标准:\n" + "\n".join(f"- {c}" for c in node.acceptance_criteria)
        )
        _save_tree(project_dir, tree)

        em.schedule_node(node.employee_id, node.id, tree_path_str)
        em._schedule_next(node.employee_id)

        return {"status": "rejected_retry", "node_id": node_id, "reason": reason}
    else:
        node.set_status(TaskPhase.FAILED)
        _save_tree(project_dir, tree)

        from onemancompany.core.vessel import _trigger_dep_resolution
        _trigger_dep_resolution(project_dir, tree, node)

        return {"status": "rejected_failed", "node_id": node_id, "reason": reason}


@tool
def unblock_child(node_id: str, new_description: str = "") -> dict:
    """Unblock a BLOCKED task, optionally with updated instructions.

    Removes failed/cancelled dependencies from depends_on and re-evaluates.
    If remaining deps are met, schedules the task for execution.

    Args:
        node_id: The blocked task node ID.
        new_description: Updated task description (optional).
    """
    from onemancompany.core.vessel import _current_vessel, _current_task_id

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return {"status": "error", "message": "No agent context."}

    from onemancompany.core.vessel import employee_manager
    project_dir = ""
    tree_path_str = ""
    for entries in employee_manager._schedule.values():
        for e in entries:
            if e.node_id == task_id:
                project_dir = str(Path(e.tree_path).parent)
                tree_path_str = e.tree_path
                break
        if project_dir:
            break

    if not project_dir:
        return {"status": "error", "message": "No project context."}

    tree = _load_tree(project_dir)
    node = tree.get_node(node_id)
    if not node:
        return {"status": "error", "message": f"Node {node_id} not found."}
    if node.status != TaskPhase.BLOCKED.value:
        return {"status": "error", "message": f"Node {node_id} is {node.status}, not blocked."}

    # Remove failed/cancelled deps
    _terminal_bad = {TaskPhase.FAILED.value, TaskPhase.CANCELLED.value}
    node.depends_on = [
        d for d in node.depends_on
        if tree.get_node(d) and tree.get_node(d).status not in _terminal_bad
    ]
    if new_description:
        node.description = new_description
    node.set_status(TaskPhase.PENDING)
    _save_tree(project_dir, tree)

    # Check if remaining deps are met
    if tree.all_deps_resolved(node.id):
        employee_manager.schedule_node(node.employee_id, node.id, tree_path_str)
        employee_manager._schedule_next(node.employee_id)
        return {"status": "unblocked_and_dispatched", "node_id": node_id}

    return {"status": "unblocked_waiting", "node_id": node_id,
            "waiting_on": node.depends_on}


@tool
def cancel_child(node_id: str, reason: str = "") -> dict:
    """Cancel a task node. Triggers dependency resolution for dependents.

    Args:
        node_id: The task node ID to cancel.
        reason: Cancellation reason (optional).
    """
    from onemancompany.core.vessel import _current_vessel, _current_task_id

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return {"status": "error", "message": "No agent context."}

    from onemancompany.core.vessel import employee_manager
    project_dir = ""
    for entries in employee_manager._schedule.values():
        for e in entries:
            if e.node_id == task_id:
                project_dir = str(Path(e.tree_path).parent)
                break
        if project_dir:
            break

    if not project_dir:
        return {"status": "error", "message": "No project context."}

    tree = _load_tree(project_dir)
    node = tree.get_node(node_id)
    if not node:
        return {"status": "error", "message": f"Node {node_id} not found."}
    if node.is_resolved:
        return {"status": "error", "message": f"Node {node_id} already resolved ({node.status})."}

    node.set_status(TaskPhase.CANCELLED)
    node.result = reason or "Cancelled by parent"
    _save_tree(project_dir, tree)

    from onemancompany.core.vessel import _trigger_dep_resolution
    _trigger_dep_resolution(project_dir, tree, node)

    return {"status": "cancelled", "node_id": node_id}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

from onemancompany.core.tool_registry import tool_registry, ToolMeta

tool_registry.register(dispatch_child, ToolMeta(name="dispatch_child", category="base"))
tool_registry.register(accept_child, ToolMeta(name="accept_child", category="base"))
tool_registry.register(reject_child, ToolMeta(name="reject_child", category="base"))
tool_registry.register(unblock_child, ToolMeta(name="unblock_child", category="base"))
tool_registry.register(cancel_child, ToolMeta(name="cancel_child", category="base"))
