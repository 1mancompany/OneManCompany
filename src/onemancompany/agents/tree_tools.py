"""Tree tools — dispatch_child, accept_child, reject_child.

These tools allow parent tasks to dispatch subtasks to employees,
then accept or reject results. They operate on a TaskTree persisted
as task_tree.yaml in the project directory.
"""
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool
from loguru import logger

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
def dispatch_child(employee_id: str, description: str, acceptance_criteria: list[str], timeout_seconds: int = 3600) -> dict:
    """Dispatch a child task to an employee with acceptance criteria.

    Creates a child node in the task tree and pushes a task to the target employee.
    The child must complete and be accepted before this task can finish.

    Args:
        employee_id: Target employee ID
        description: What the employee should do
        acceptance_criteria: List of measurable criteria the result must meet
        timeout_seconds: Max seconds allowed for the child task (default 3600)
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
    from onemancompany.core.state import company_state
    if employee_id not in company_state.employees:
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

    # Add child node
    child = tree.add_child(
        parent_id=current_node_id,
        employee_id=employee_id,
        description=description,
        acceptance_criteria=acceptance_criteria,
        timeout_seconds=timeout_seconds,
    )

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

    node.status = "accepted"
    node.acceptance_result = {"passed": True, "notes": notes}
    _save_tree(task.project_dir, tree)

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
        node.status = "pending"
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
        node.status = "failed"
        _save_tree(task.project_dir, tree)
        return {"status": "rejected_failed", "node_id": node_id, "reason": reason}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

from onemancompany.core.tool_registry import tool_registry, ToolMeta

tool_registry.register(dispatch_child, ToolMeta(name="dispatch_child", category="base"))
tool_registry.register(accept_child, ToolMeta(name="accept_child", category="base"))
tool_registry.register(reject_child, ToolMeta(name="reject_child", category="base"))
