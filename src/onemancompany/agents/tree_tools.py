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
    """Load a TaskTree from {project_dir}/task_tree.yaml."""
    path = Path(project_dir) / "task_tree.yaml"
    if not path.exists():
        logger.warning("task_tree.yaml not found at %s", path)
        return TaskTree(project_id="")
    return TaskTree.load(path)


def _save_tree(project_dir: str, tree: TaskTree) -> None:
    """Save a TaskTree to {project_dir}/task_tree.yaml."""
    path = Path(project_dir) / "task_tree.yaml"
    tree.save(path)


def _get_current_node_id(tree: TaskTree, task_id: str) -> str | None:
    """Look up the TaskNode ID for a given AgentTask ID via tree's task_id_map."""
    return tree.task_id_map.get(task_id)


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

    Creates a child node in the task tree and pushes a task to the target employee.
    The child must complete and be accepted before this task can finish.

    If depends_on is provided, the child will only be pushed when all dependency
    nodes reach a terminal status (accepted/failed/cancelled). Until then the child
    is created in the tree but not pushed to the employee's board.

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

    task = vessel.board.get_task(task_id)
    if not task:
        return {"status": "error", "message": "Current task not found."}

    # Validate employee exists
    from onemancompany.core.store import load_employee
    if not load_employee(employee_id):
        return {"status": "error", "message": f"Employee {employee_id} not found."}

    project_dir = task.project_dir
    if not project_dir:
        return {"status": "error", "message": "No project directory in current task."}

    # Load tree, find current node
    tree = _load_tree(project_dir)
    current_node_id = _get_current_node_id(tree, task_id)
    if not current_node_id or not tree.get_node(current_node_id):
        return {"status": "error", "message": "Current task not found in task tree."}

    # EA can only dispatch to O-level executives
    from onemancompany.core.config import EA_ID, HR_ID, COO_ID, CSO_ID
    current_node = tree.get_node(current_node_id)
    if current_node and current_node.employee_id == EA_ID:
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
        parent_id=current_node_id,
        employee_id=employee_id,
        description=description,
        acceptance_criteria=acceptance_criteria,
        timeout_seconds=timeout_seconds,
        depends_on=depends_on,
        fail_strategy=fail_strategy,
    )

    # Check if dependencies are already satisfied
    deps_resolved = tree.all_deps_terminal(child.id)

    if not deps_resolved:
        # Dependencies unmet — save tree but skip pushing the task
        _save_tree(project_dir, tree)
        return {
            "status": "dispatched_waiting",
            "node_id": child.id,
            "employee_id": employee_id,
            "description": description,
            "dependency_status": "waiting",
        }

    # Push task to target employee
    from onemancompany.core.vessel import employee_manager
    handle = employee_manager.get_handle(employee_id)
    if not handle:
        return {"status": "error", "message": f"No handle for employee {employee_id}."}

    agent_task = handle.push_task(description, project_id=task.project_id, project_dir=project_dir)
    tree.task_id_map[agent_task.id] = child.id
    _save_tree(project_dir, tree)

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

    task = vessel.board.get_task(task_id)
    if not task or not task.project_dir:
        return {"status": "error", "message": "No project context."}

    tree = _load_tree(task.project_dir)
    node = tree.get_node(node_id)
    if not node:
        return {"status": "error", "message": f"Node {node_id} not found."}

    node.set_status(TaskPhase.ACCEPTED)
    node.acceptance_result = {"passed": True, "notes": notes}
    _save_tree(task.project_dir, tree)

    # Trigger dependency resolution for dependents
    from onemancompany.core.vessel import _trigger_dep_resolution
    _trigger_dep_resolution(task.project_dir, tree, node)

    return {"status": "accepted", "node_id": node_id, "notes": notes}


@tool
def reject_child(node_id: str, reason: str, retry: bool = True) -> dict:
    """Reject a child task's result.

    Args:
        node_id: The TaskNode ID of the child to reject
        reason: Why the result was rejected
        retry: If True, push a correction task to the same employee. If False, mark as failed.
    """
    from onemancompany.core.vessel import _current_vessel, _current_task_id

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return {"status": "error", "message": "No agent context."}

    task = vessel.board.get_task(task_id)
    if not task or not task.project_dir:
        return {"status": "error", "message": "No project context."}

    tree = _load_tree(task.project_dir)
    node = tree.get_node(node_id)
    if not node:
        return {"status": "error", "message": f"Node {node_id} not found."}

    node.acceptance_result = {"passed": False, "notes": reason}

    if retry:
        from onemancompany.core.vessel import employee_manager
        handle = employee_manager.get_handle(node.employee_id)
        if not handle:
            logger.error("reject_child: no handle for employee {}, cannot push correction task", node.employee_id)
            return {"status": "error", "message": f"No handle for employee {node.employee_id}, cannot push correction task."}

        # Reset to pending, push correction task
        node.set_status(TaskPhase.PENDING)
        node.result = ""
        _save_tree(task.project_dir, tree)

        correction_desc = (
            f"修正任务: {node.description}\n\n"
            f"拒绝原因: {reason}\n\n"
            f"验收标准:\n" + "\n".join(f"- {c}" for c in node.acceptance_criteria)
        )
        agent_task = handle.push_task(
            correction_desc,
            project_id=task.project_id,
            project_dir=task.project_dir,
        )
        tree.task_id_map[agent_task.id] = node.id
        _save_tree(task.project_dir, tree)

        return {"status": "rejected_retry", "node_id": node_id, "reason": reason}
    else:
        node.set_status(TaskPhase.FAILED)
        _save_tree(task.project_dir, tree)

        # Trigger dependency resolution for dependents (failed is terminal)
        from onemancompany.core.vessel import _trigger_dep_resolution
        _trigger_dep_resolution(task.project_dir, tree, node)

        return {"status": "rejected_failed", "node_id": node_id, "reason": reason}


@tool
def unblock_child(node_id: str, new_description: str = "") -> dict:
    """Unblock a BLOCKED task, optionally with updated instructions.

    Removes failed/cancelled dependencies from depends_on and re-evaluates.
    If remaining deps are met, pushes the task to the employee's board.

    Args:
        node_id: The blocked task node ID.
        new_description: Updated task description (optional).
    """
    from onemancompany.core.vessel import _current_vessel, _current_task_id

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return {"status": "error", "message": "No agent context."}

    task = vessel.board.get_task(task_id)
    if not task or not task.project_dir:
        return {"status": "error", "message": "No project context."}

    tree = _load_tree(task.project_dir)
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
    _save_tree(task.project_dir, tree)

    # Check if remaining deps are met
    if tree.all_deps_terminal(node.id):
        from onemancompany.core.vessel import employee_manager
        handle = employee_manager.get_handle(node.employee_id)
        if handle:
            agent_task = handle.push_task(
                node.description,
                project_id=task.project_id,
                project_dir=task.project_dir,
            )
            tree.task_id_map[agent_task.id] = node.id
            _save_tree(task.project_dir, tree)
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

    task = vessel.board.get_task(task_id)
    if not task or not task.project_dir:
        return {"status": "error", "message": "No project context."}

    tree = _load_tree(task.project_dir)
    node = tree.get_node(node_id)
    if not node:
        return {"status": "error", "message": f"Node {node_id} not found."}
    if node.is_terminal:
        return {"status": "error", "message": f"Node {node_id} already terminal ({node.status})."}

    node.set_status(TaskPhase.CANCELLED)
    node.result = reason or "Cancelled by parent"
    _save_tree(task.project_dir, tree)

    # Trigger dependency resolution (cancelled is terminal)
    from onemancompany.core.vessel import _trigger_dep_resolution
    _trigger_dep_resolution(task.project_dir, tree, node)

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
