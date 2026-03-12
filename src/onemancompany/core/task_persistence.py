"""Task persistence — per-employee YAML-based write-through task storage.

Each employee's active tasks live in employees/{id}/tasks/{task_id}.yaml.
Terminal tasks are moved to employees/{id}/tasks/archive/{task_id}.yaml.

This is a pure I/O module — no async, no agent logic.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from loguru import logger

from onemancompany.core.config import EMPLOYEES_DIR
from onemancompany.core.task_lifecycle import TaskPhase
from onemancompany.core.vessel import AgentTask


# ---------------------------------------------------------------------------
# Serialize / deserialize
# ---------------------------------------------------------------------------

def _task_to_dict(task: AgentTask) -> dict:
    """Serialize an AgentTask to a plain dict suitable for YAML storage."""
    return {
        "id": task.id,
        "description": task.description,
        "status": task.status.value if isinstance(task.status, TaskPhase) else str(task.status),
        "task_type": task.task_type,
        "parent_id": task.parent_id,
        "project_id": task.project_id,
        "project_dir": task.project_dir,
        "logs": task.logs,
        "result": task.result,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
        "model_used": task.model_used,
        "input_tokens": task.input_tokens,
        "output_tokens": task.output_tokens,
        "total_tokens": task.total_tokens,
        "estimated_cost_usd": task.estimated_cost_usd,
    }


def _dict_to_task(data: dict) -> AgentTask:
    """Deserialize a dict (from YAML) back into an AgentTask.

    Unknown status values default to PENDING.
    """
    # Parse status — default to PENDING for unknown values
    raw_status = data.get("status", "pending")
    try:
        status = TaskPhase(raw_status)
    except ValueError:
        logger.warning("Unknown task status '{}', defaulting to PENDING", raw_status)
        status = TaskPhase.PENDING

    return AgentTask(
        id=data["id"],
        description=data["description"],
        status=status,
        task_type=data.get("task_type", "simple"),
        parent_id=data.get("parent_id", ""),
        project_id=data.get("project_id", ""),
        project_dir=data.get("project_dir", ""),
        logs=data.get("logs", []),
        result=data.get("result", ""),
        created_at=data.get("created_at", ""),
        completed_at=data.get("completed_at", ""),
        model_used=data.get("model_used", ""),
        input_tokens=data.get("input_tokens", 0),
        output_tokens=data.get("output_tokens", 0),
        total_tokens=data.get("total_tokens", 0),
        estimated_cost_usd=data.get("estimated_cost_usd", 0.0),
    )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _tasks_dir(employee_id: str) -> Path:
    """Return the tasks directory for an employee: employees/{id}/tasks/."""
    return EMPLOYEES_DIR / employee_id / "tasks"


# ---------------------------------------------------------------------------
# Persist / archive / load
# ---------------------------------------------------------------------------

def persist_task(employee_id: str, task: AgentTask) -> None:
    """Write-through: serialize task to employees/{id}/tasks/{task_id}.yaml."""
    tasks_path = _tasks_dir(employee_id)
    tasks_path.mkdir(parents=True, exist_ok=True)
    file_path = tasks_path / f"{task.id}.yaml"
    data = _task_to_dict(task)
    with open(file_path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
    logger.debug("Persisted task {} for employee {}", task.id, employee_id)


def archive_task(employee_id: str, task: AgentTask) -> None:
    """Move a task file from tasks/ to tasks/archive/. Noop if file doesn't exist."""
    tasks_path = _tasks_dir(employee_id)
    src = tasks_path / f"{task.id}.yaml"
    if not src.exists():
        logger.debug("No task file to archive: {}", src)
        return
    archive_dir = tasks_path / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    dst = archive_dir / f"{task.id}.yaml"
    shutil.move(str(src), str(dst))
    logger.debug("Archived task {} for employee {}", task.id, employee_id)


def load_active_tasks(employee_id: str, *, crash_recovery: bool = True) -> list[AgentTask]:
    """Load all non-archived tasks for an employee.

    Args:
        employee_id: Employee whose tasks to load.
        crash_recovery: If True (default, for server startup), resets
            PROCESSING → PENDING so interrupted tasks get retried.
            Set to False for read-only queries (API, hot-reload) to
            avoid resetting tasks that are actively running.

    Skips corrupt or incomplete YAML files with a warning.
    """
    tasks_path = _tasks_dir(employee_id)
    if not tasks_path.exists():
        return []

    tasks: list[AgentTask] = []
    for yaml_file in sorted(tasks_path.iterdir()):
        if not yaml_file.is_file() or yaml_file.suffix != ".yaml":
            continue
        try:
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict) or "id" not in data or "description" not in data:
                logger.warning("Skipping incomplete task file: {}", yaml_file)
                continue
            task = _dict_to_task(data)
            # HOLDING tasks stay HOLDING — their reply pollers will be restarted by vessel
            # Crash recovery: reset PROCESSING → PENDING (only on startup)
            if crash_recovery and task.status == TaskPhase.PROCESSING:
                task.status = TaskPhase.PENDING
                logger.info("Reset task {} from PROCESSING to PENDING (crash recovery)", task.id)
            tasks.append(task)
        except Exception as e:
            logger.warning("Skipping corrupt task file {}: {}", yaml_file, e)
            continue
    return tasks


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
    from onemancompany.core.task_tree import TaskTree
    from onemancompany.core.task_lifecycle import TaskPhase  # noqa: F811 (lazy re-import OK)

    # 1. Scan all task_tree.yaml files under projects_dir
    if projects_dir.exists():
        for tree_path in projects_dir.rglob("task_tree.yaml"):
            try:
                tree = TaskTree.load(tree_path)
            except Exception:
                logger.warning("Skipping corrupt tree file: {}", tree_path)
                continue

            modified = False
            for node in tree._nodes.values():
                if node.status == TaskPhase.PROCESSING.value:
                    node.status = TaskPhase.PENDING.value
                    modified = True

            if modified:
                tree.save(tree_path)

            for node in tree._nodes.values():
                if node.status == TaskPhase.PENDING.value and tree.all_deps_resolved(node.id):
                    employee_manager.schedule_node(
                        node.employee_id, node.id, str(tree_path),
                    )

    # 2. Scan system task trees
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


def load_all_active_tasks(*, crash_recovery: bool = True) -> dict[str, list[AgentTask]]:
    """Scan all employee directories and load active tasks.

    Returns {employee_id: [AgentTask, ...]} for employees that have tasks.
    Skips employee dirs without a tasks/ subdirectory.

    Args:
        crash_recovery: Passed through to load_active_tasks. Only True on
            server startup; False for read-only queries.
    """
    if not EMPLOYEES_DIR.exists():
        return {}

    result: dict[str, list[AgentTask]] = {}
    for emp_dir in sorted(EMPLOYEES_DIR.iterdir()):
        if not emp_dir.is_dir():
            continue
        employee_id = emp_dir.name
        tasks = load_active_tasks(employee_id, crash_recovery=crash_recovery)
        if tasks:
            result[employee_id] = tasks
    return result
