"""Tree tools — dispatch_child, accept_child, reject_child.

These tools allow parent tasks to dispatch subtasks to employees,
then accept or reject results. They operate on a TaskTree persisted
as task_tree.yaml in the project directory.
"""
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool
from loguru import logger

from onemancompany.core.config import CEO_ID, ENCODING_UTF8, PROJECT_YAML_FILENAME, SYSTEM_AGENT, TASK_TREE_FILENAME
from onemancompany.core.models import EventType
from onemancompany.core.task_lifecycle import NodeType, TaskPhase
from onemancompany.core.task_tree import TaskTree

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_tree(project_dir: str) -> TaskTree:
    """Get TaskTree from memory cache (loading from disk if needed)."""
    from onemancompany.core.task_tree import get_tree
    path = Path(project_dir) / TASK_TREE_FILENAME
    if not path.exists():
        logger.warning("task_tree.yaml not found at %s", path)
        return TaskTree(project_id="")
    return get_tree(path)


def _find_entry_for_task(task_id: str) -> tuple[str, str]:
    """Find (project_dir, tree_path) for a task_id in schedule or running entries.

    Running tasks are popped from _schedule, so we also check _current_entries.
    Returns ("", "") if not found.
    """
    from onemancompany.core.vessel import employee_manager

    # Check schedule first
    for entries in employee_manager._schedule.values():
        for e in entries:
            if e.node_id == task_id:
                return str(Path(e.tree_path).parent), e.tree_path

    # Check currently running tasks (popped from schedule)
    for e in employee_manager._current_entries.values():
        if e.node_id == task_id:
            return str(Path(e.tree_path).parent), e.tree_path

    return "", ""


def _save_tree(project_dir: str, tree: TaskTree) -> None:
    """Schedule async save of the TaskTree."""
    from onemancompany.core.task_tree import save_tree_async
    path = Path(project_dir) / TASK_TREE_FILENAME
    save_tree_async(path)


def _resolve_project_root(project_dir: str) -> Path | None:
    """Resolve project root dir containing project.yaml from an iteration dir.

    project_dir is typically projects/{slug}/iterations/iter_NNN/.
    project.yaml lives at projects/{slug}/project.yaml.
    """
    d = Path(project_dir)
    # Check project_dir itself first
    if (d / PROJECT_YAML_FILENAME).exists():
        return d
    # Walk up (max 3 levels) looking for project.yaml
    for _ in range(3):
        d = d.parent
        if (d / PROJECT_YAML_FILENAME).exists():
            return d
    return None


def _add_to_project_team(project_dir: str, employee_id: str) -> None:
    """Add employee to project.yaml team list (idempotent)."""
    import yaml
    root = _resolve_project_root(project_dir)
    if root is None:
        logger.debug("No project.yaml found from {}", project_dir)
        return
    project_yaml = root / PROJECT_YAML_FILENAME
    try:
        data = yaml.safe_load(project_yaml.read_text(encoding=ENCODING_UTF8)) or {}
        team = data.get("team", [])
        if any(m.get("employee_id") == employee_id for m in team):
            return  # already in team
        from datetime import datetime
        team.append({
            "employee_id": employee_id,
            "role": "",
            "joined_at": datetime.now().isoformat(),
        })
        data["team"] = team
        project_yaml.write_text(
            yaml.dump(data, allow_unicode=True, sort_keys=False),
            encoding=ENCODING_UTF8,
        )
    except Exception:
        logger.warning("Failed to add {} to project team in {}", employee_id, project_dir)


def _get_current_node(tree: TaskTree, task_id: str):
    """Look up the TaskNode for the given task/node ID."""
    return tree.get_node(task_id)


def _create_standalone_ceo_request(
    description: str,
    requester_task_id: str,
    vessel,
) -> dict:
    """Create a CEO inbox request without requiring a task tree context.

    Used when agents running system/adhoc tasks (no tree) need to escalate to CEO.
    """
    import uuid
    node_id = f"ceo_req_{uuid.uuid4().hex[:8]}"

    # Publish WebSocket event so CEO sees it
    import asyncio
    from onemancompany.core.events import CompanyEvent, event_bus
    from onemancompany.core.vessel import employee_manager
    coro = event_bus.publish(CompanyEvent(
        type=EventType.CEO_INBOX_UPDATED,
        payload={
            "node_id": node_id,
            "description": description,
            "source_task_id": requester_task_id,
            "source_employee": vessel.employee_id if vessel else "unknown",
        },
        agent=SYSTEM_AGENT,
    ))
    main_loop = getattr(employee_manager, "_event_loop", None)
    if main_loop and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, main_loop)
    else:
        logger.warning("No event loop for standalone ceo_inbox_updated publish")

    return {
        "status": "dispatched",
        "node_id": node_id,
        "employee_id": CEO_ID,
        "description": description,
        "node_type": NodeType.CEO_REQUEST,
        "ceo_request": True,
        "message": "Task dispatched to CEO inbox. CEO will respond when available.",
    }


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
    """
    from onemancompany.core.vessel import _current_vessel, _current_task_id

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return {"status": "error", "message": "No agent context."}

    # Load tree, find current node
    project_dir, tree_path_str = _find_entry_for_task(task_id)

    if not project_dir or not tree_path_str:
        # --- Standalone CEO request (no tree context, e.g. system/adhoc tasks) ---
        if employee_id == CEO_ID:
            return _create_standalone_ceo_request(
                description=description,
                requester_task_id=task_id,
                vessel=vessel,
            )
        return {"status": "error", "message": "No project directory in current task context."}

    # Validate employee exists
    from onemancompany.core.store import load_employee
    if not load_employee(employee_id):
        return {"status": "error", "message": f"Employee {employee_id} not found."}

    from onemancompany.core.task_tree import get_tree_lock
    tree_lock = get_tree_lock(tree_path_str)

    with tree_lock:
        tree = _load_tree(project_dir)
        current_node = _get_current_node(tree, task_id)
        if not current_node:
            return {"status": "error", "message": "Current task not found in task tree."}

        # EA dispatch restrictions
        from onemancompany.core.config import EA_ID, HR_ID, COO_ID, CSO_ID
        if current_node.employee_id == EA_ID:
            # Self-dispatch guard — always blocked
            if employee_id == EA_ID:
                return {
                    "status": "error",
                    "message": "EA cannot dispatch tasks to itself. Please dispatch to an appropriate team member.",
                }
            # O-level restriction — only in standard mode
            if tree.mode != "simple":
                allowed_targets = {HR_ID, COO_ID, CSO_ID}
                if employee_id not in allowed_targets:
                    suggestion = f"COO({COO_ID})"
                    return {
                        "status": "error",
                        "message": (
                            f"EA cannot directly dispatch tasks to {employee_id}. "
                            f"Please dispatch_child to the corresponding O-level executive instead: HR({HR_ID}), COO({COO_ID}), CSO({CSO_ID}). "
                            f"Hint: for development/design/operations tasks, dispatch to {suggestion} to organize team execution. "
                            f"Please immediately re-call dispatch_child with the correct employee_id."
                        ),
                    }

        # --- Circuit breaker: children count limit ---
        from onemancompany.core.config import MAX_CHILDREN_PER_NODE, MAX_TREE_DEPTH
        active_children = tree.get_active_children(task_id)
        if len(active_children) >= MAX_CHILDREN_PER_NODE:
            return {
                "status": "error",
                "message": f"Child task limit reached ({MAX_CHILDREN_PER_NODE}). Please consolidate existing tasks or escalate.",
            }

        # --- Circuit breaker: tree depth limit ---
        depth = 0
        walker = current_node
        while walker.parent_id:
            depth += 1
            walker = tree.get_node(walker.parent_id)
            if not walker:
                break
        if depth + 1 >= MAX_TREE_DEPTH:
            return {
                "status": "error",
                "message": f"Task tree has reached maximum depth ({MAX_TREE_DEPTH}). Cannot dispatch further. Please complete directly or escalate.",
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

        # --- CEO request interception (idempotency check BEFORE creating child) ---
        if employee_id == CEO_ID:
            from onemancompany.core.task_lifecycle import TaskPhase as _TP
            existing = [
                c for c in tree.get_children(task_id)
                if c.node_type == NodeType.CEO_REQUEST
                and c.status not in (_TP.FINISHED.value, _TP.CANCELLED.value, _TP.ACCEPTED.value)
            ]
            if existing:
                dup = existing[0]
                return {
                    "status": "already_dispatched",
                    "node_id": dup.id,
                    "employee_id": employee_id,
                    "description": dup.description,
                    "node_type": NodeType.CEO_REQUEST,
                    "ceo_request": True,
                    "message": (
                        f"A CEO request ({dup.id}) is already pending. Do NOT create another. "
                        "Your task will automatically pause (HOLDING) until the CEO responds. "
                        "You should finish your current output now — the system handles the rest."
                    ),
                }

        # Add child node
        child = tree.add_child(
            parent_id=task_id,
            employee_id=employee_id,
            description=description,
            acceptance_criteria=acceptance_criteria,
            timeout_seconds=timeout_seconds,
            depends_on=depends_on,
        )
        child.project_id = current_node.project_id
        child.project_dir = project_dir

        # Auto-register dispatched employee in project team for project history
        _add_to_project_team(project_dir, employee_id)

        if employee_id == CEO_ID:
            child.node_type = NodeType.CEO_REQUEST
            # Signal vessel to auto-HOLD parent after execution (no_watchdog:
            # routes.py handles resume when CEO responds)
            current_node.hold_reason = f"ceo_request={child.id},no_watchdog=1"
            _save_tree(project_dir, tree)
            # Persist task index entry for taskboard
            from onemancompany.core.store import append_task_index_entry
            append_task_index_entry(employee_id, child.id, tree_path_str)
            # Publish WebSocket event (async from sync context — use main loop)
            import asyncio
            from onemancompany.core.events import CompanyEvent, event_bus
            from onemancompany.core.vessel import employee_manager
            coro = event_bus.publish(CompanyEvent(
                type=EventType.CEO_INBOX_UPDATED,
                payload={"node_id": child.id, "description": description},
                agent=SYSTEM_AGENT,
            ))
            main_loop = getattr(employee_manager, "_event_loop", None)
            if main_loop and main_loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, main_loop)
            else:
                logger.warning("No event loop for ceo_inbox_updated publish")
            return {
                "status": "dispatched",
                "node_id": child.id,
                "employee_id": employee_id,
                "description": description,
                "node_type": NodeType.CEO_REQUEST,
                "ceo_request": True,
                "message": (
                    "Task dispatched to CEO inbox. Your task will automatically pause (HOLDING) "
                    "until the CEO responds. You should finish your current output now — "
                    "the system handles the rest."
                ),
            }

        # --- Normal employee dispatch (existing logic) ---
        # When dispatching to a DIFFERENT employee, the parent should HOLD
        # until child tasks complete — otherwise it gets marked COMPLETED
        # immediately and never has a chance to review/accept children.
        if employee_id != current_node.employee_id and not current_node.hold_reason:
            current_node.hold_reason = f"awaiting_children,no_watchdog=1"

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
        from onemancompany.core.vessel import employee_manager
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
    project_dir, tree_path_str = _find_entry_for_task(task_id)

    if not project_dir:
        return {"status": "error", "message": "No project context."}

    from onemancompany.core.task_tree import get_tree_lock
    with get_tree_lock(tree_path_str):
        tree = _load_tree(project_dir)
        node = tree.get_node(node_id)
        if not node:
            return {"status": "error", "message": f"Node {node_id} not found."}

        # Normalize status to string for comparison (TaskNode.status is str)
        current = node.status.value if hasattr(node.status, "value") else node.status

        # Idempotent: already accepted/finished/cancelled → return success without re-transitioning
        if current == TaskPhase.ACCEPTED.value:
            return {"status": TaskPhase.ACCEPTED.value, "node_id": node_id, "notes": notes, "already_accepted": True}
        if current == TaskPhase.FINISHED.value:
            return {"status": TaskPhase.ACCEPTED.value, "node_id": node_id, "notes": notes, "already_finished": True}
        if current == TaskPhase.CANCELLED.value:
            return {"status": TaskPhase.ACCEPTED.value, "node_id": node_id, "notes": notes, "already_cancelled": True}

        # Only completed tasks can be accepted
        if current != TaskPhase.COMPLETED.value:
            return {
                "status": "error",
                "message": f"Cannot accept node {node_id}: current status is '{current}', must be 'completed' first.",
            }

        node.set_status(TaskPhase.ACCEPTED)
        node.acceptance_result = {"passed": True, "notes": notes}
        _save_tree(project_dir, tree)

        # Trigger dependency resolution for dependents
        from onemancompany.core.vessel import _trigger_dep_resolution
        _trigger_dep_resolution(project_dir, tree, node)

        return {"status": TaskPhase.ACCEPTED.value, "node_id": node_id, "notes": notes}


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

    project_dir, tree_path_str = _find_entry_for_task(task_id)

    if not project_dir:
        return {"status": "error", "message": "No project context."}

    from onemancompany.core.task_tree import get_tree_lock
    with get_tree_lock(tree_path_str):
        tree = _load_tree(project_dir)
        node = tree.get_node(node_id)
        if not node:
            return {"status": "error", "message": f"Node {node_id} not found."}

        current = node.status

        # Only completed tasks can be rejected
        if current != TaskPhase.COMPLETED.value:
            return {
                "status": "error",
                "message": f"Cannot reject node {node_id}: current status is '{current}', must be 'completed' first. Wait for the employee to finish before rejecting.",
            }

        node.acceptance_result = {"passed": False, "notes": reason}

        if retry:
            from onemancompany.core.vessel import employee_manager as em
            if node.employee_id not in em.executors:
                return {"status": "error", "message": f"No handle for employee {node.employee_id}, cannot push correction task."}

            # Reset to pending and re-schedule
            node.load_content(project_dir)  # Ensure description is loaded before reading
            node.set_status(TaskPhase.PENDING)
            node.result = ""
            node.description = (
                f"Correction task: {node.description}\n\n"
                f"Rejection reason: {reason}\n\n"
                f"Acceptance criteria:\n" + "\n".join(f"- {c}" for c in node.acceptance_criteria)
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

    project_dir, tree_path_str = _find_entry_for_task(task_id)

    if not project_dir:
        return {"status": "error", "message": "No project context."}

    from onemancompany.core.task_tree import get_tree_lock
    with get_tree_lock(tree_path_str):
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
        from onemancompany.core.vessel import employee_manager
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

    project_dir, tree_path_str = _find_entry_for_task(task_id)

    if not project_dir:
        return {"status": "error", "message": "No project context."}

    from onemancompany.core.task_tree import get_tree_lock
    with get_tree_lock(tree_path_str):
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

        return {"status": TaskPhase.CANCELLED.value, "node_id": node_id}


@tool
def set_project_name(name: str) -> dict:
    """Set the display name for the current project.

    Call this when you first receive a new CEO task to give it a descriptive name.

    Args:
        name: Short project name (2-6 words)
    """
    from onemancompany.core.vessel import _current_task_id

    task_id = _current_task_id.get()
    if not task_id:
        return {"status": "error", "message": "No agent context."}

    project_dir, tree_path_str = _find_entry_for_task(task_id)

    if not project_dir:
        return {"status": "error", "message": "No project context."}

    # Update project.yaml name field
    project_yaml = Path(project_dir) / PROJECT_YAML_FILENAME
    if project_yaml.exists():
        import yaml
        data = yaml.safe_load(project_yaml.read_text(encoding=ENCODING_UTF8)) or {}
        data["name"] = name.strip()
        project_yaml.write_text(
            yaml.dump(data, allow_unicode=True, sort_keys=False),
            encoding=ENCODING_UTF8,
        )
        return {"status": "ok", "name": name.strip()}

    return {"status": "error", "message": "Project file not found."}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

from onemancompany.core.tool_registry import tool_registry, ToolMeta

tool_registry.register(dispatch_child, ToolMeta(name="dispatch_child", category="base"))
tool_registry.register(accept_child, ToolMeta(name="accept_child", category="base"))
tool_registry.register(reject_child, ToolMeta(name="reject_child", category="base"))
tool_registry.register(unblock_child, ToolMeta(name="unblock_child", category="base"))
tool_registry.register(cancel_child, ToolMeta(name="cancel_child", category="base"))
tool_registry.register(set_project_name, ToolMeta(name="set_project_name", category="base"))
