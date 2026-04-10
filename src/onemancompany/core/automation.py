"""Automation — cron jobs, webhooks, and Gmail Pub/Sub for employees.

Employees can schedule recurring tasks (cron), register webhook endpoints,
and subscribe to Gmail push notifications. All triggers dispatch tasks
to the owning employee via EmployeeManager.push_task().

Data files:
  employees/{id}/automations.yaml — persisted automation configs
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml
from loguru import logger

from onemancompany.core.config import EMPLOYEES_DIR, read_text_utf, write_text_utf
from onemancompany.core.interval import parse_interval as _parse_interval

# Single-file constants
AUTOMATIONS_FILENAME = "automations.yaml"
_KEY_CRONS = "crons"
_KEY_WEBHOOKS = "webhooks"
_KEY_NAME = "name"
_KEY_DISPATCHED_TASK_IDS = "dispatched_task_ids"

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

def _automations_file(employee_id: str) -> Path:
    return EMPLOYEES_DIR / employee_id / AUTOMATIONS_FILENAME


def _load_automations(employee_id: str) -> dict:
    path = _automations_file(employee_id)
    if not path.exists():
        return {_KEY_CRONS: [], _KEY_WEBHOOKS: []}
    try:
        data = yaml.safe_load(read_text_utf(path)) or {}
        data.setdefault(_KEY_CRONS, [])
        data.setdefault(_KEY_WEBHOOKS, [])
        return data
    except Exception:
        return {_KEY_CRONS: [], _KEY_WEBHOOKS: []}


def _save_automations(employee_id: str, data: dict) -> None:
    path = _automations_file(employee_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_utf(path, yaml.dump(data, allow_unicode=True, default_flow_style=False))


# ---------------------------------------------------------------------------
# Cron scheduler
# ---------------------------------------------------------------------------

_cron_tasks: dict[str, asyncio.Task] = {}  # key = "employee_id:cron_name"


def _broadcast_cron_status(employee_id: str, cron_name: str, running: bool) -> None:
    """Publish cron_status_change event via EventBus (fire-and-forget)."""
    try:
        from onemancompany.core.events import event_bus, CompanyEvent
        from onemancompany.core.models import EventType

        coro = event_bus.publish(CompanyEvent(
            type=EventType.CRON_STATUS_CHANGE,
            payload={
                "employee_id": employee_id,
                "cron_name": cron_name,
                "running": running,
            },
            agent="SYSTEM",
        ))
        try:
            asyncio.get_running_loop()
            from onemancompany.core.async_utils import spawn_background
            spawn_background(coro)
        except RuntimeError:
            # No running event loop (called from thread or startup) — skip broadcast
            coro.close()
            logger.debug("[cron] Skipped broadcast (no event loop): {}:{}", employee_id, cron_name)
    except Exception as e:
        logger.warning("[cron] Broadcast cron_status_change failed: {}", e)


async def _cron_loop(
    employee_id: str, cron_name: str, interval_seconds: int,
    task_description: str, project_id: str = "", tree_path: str = "",
) -> None:
    """Background loop that dispatches a task at regular intervals.

    If tree_path is set, tasks are added as child nodes in the existing
    project tree (root-level). Otherwise, falls back to _push_adhoc_task.
    """
    logger.info("[cron] Started '{}' for {} every {}s (project={}, tree={})",
                cron_name, employee_id, interval_seconds, project_id or "none", bool(tree_path))
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                node_id = _dispatch_cron_task(
                    employee_id, cron_name, task_description,
                    project_id=project_id, tree_path=tree_path,
                )
                _record_dispatched_task(employee_id, cron_name, node_id)
                logger.debug("[cron] Dispatched '{}' to {}, node_id={} project={}",
                             cron_name, employee_id, node_id, project_id or "adhoc")
            except Exception as e:
                logger.error("[cron] Failed to dispatch '{}' to {}: {}", cron_name, employee_id, e)
    except asyncio.CancelledError:
        logger.info("[cron] Stopped '{}' for {}", cron_name, employee_id)
        raise


def _dispatch_cron_task(
    employee_id: str, cron_name: str, task_description: str,
    project_id: str = "", tree_path: str = "",
) -> str:
    """Dispatch a single cron task. Returns node_id.

    If tree_path points to a valid project tree, adds a child node to it.
    Otherwise, creates a standalone adhoc task.
    """
    desc = f"[cron:{cron_name}] {task_description}"

    if tree_path:
        try:
            return _add_to_project_tree(employee_id, desc, tree_path, project_id)
        except Exception as e:
            logger.warning("[cron] Failed to add to project tree, falling back to adhoc: {}", e)

    # Fallback: standalone adhoc task
    from onemancompany.api.routes import _push_adhoc_task
    node_id, _tp = _push_adhoc_task(employee_id, desc, project_id=project_id)
    return node_id


def _add_to_project_tree(
    employee_id: str, description: str, tree_path: str, project_id: str,
) -> str:
    """Add a cron task as a child node in an existing project tree."""
    from pathlib import Path
    from onemancompany.core.task_tree import get_tree, save_tree_async, get_tree_lock
    from onemancompany.core.vessel import employee_manager

    tp = Path(tree_path)
    if not tp.exists():
        raise FileNotFoundError(f"Tree not found: {tree_path}")

    with get_tree_lock(tp):
        tree = get_tree(tp)
        parent_id = tree.root_id
        if not parent_id:
            raise ValueError("Tree has no root node")

        child = tree.add_child(
            parent_id=parent_id,
            employee_id=employee_id,
            description=description,
            acceptance_criteria=[],
        )
        # Mark as ADHOC so it doesn't block project completion checks
        from onemancompany.core.task_lifecycle import NodeType
        child.node_type = NodeType.ADHOC.value
        child.task_type = "simple"  # auto-skip completed → accepted → finished
        child.project_id = project_id
        child.project_dir = str(tp.parent)

    # Save outside lock (save_tree_async acquires its own RLock internally)
    save_tree_async(tp)

    employee_manager.schedule_node(employee_id, child.id, str(tp))
    employee_manager._schedule_next(employee_id)
    return child.id


def _record_dispatched_task(employee_id: str, cron_name: str, task_id: str) -> None:
    """Record a dispatched task ID in the cron's YAML entry."""
    data = _load_automations(employee_id)
    for c in data[_KEY_CRONS]:
        if c.get(_KEY_NAME) == cron_name:
            task_ids = c.setdefault(_KEY_DISPATCHED_TASK_IDS, [])
            task_ids.append(task_id)
            # Keep last 100 to avoid unbounded growth
            if len(task_ids) > 100:
                c[_KEY_DISPATCHED_TASK_IDS] = task_ids[-100:]
            break
    _save_automations(employee_id, data)


def start_cron(employee_id: str, cron_name: str, interval: str, task_description: str, project_id: str = "", tree_path: str = "") -> dict:
    """Start a cron job for an employee.

    Args:
        employee_id: Employee ID.
        cron_name: Unique name for this cron (per employee).
        interval: Interval string like '5m', '1h', '30s'.
        task_description: Task to dispatch each interval.
        project_id: If set, cron tasks are dispatched under this project.
        tree_path: If set, cron tasks are added as child nodes in this tree.
    """
    seconds = _parse_interval(interval)
    if seconds is None or seconds < 10:
        return {"status": "error", "message": f"Invalid interval: {interval} (min 10s)"}

    key = f"{employee_id}:{cron_name}"

    # Cancel existing if any
    existing = _cron_tasks.get(key)
    if existing and not existing.done():
        existing.cancel()

    # Start background task
    task = asyncio.create_task(_cron_loop(
        employee_id, cron_name, seconds, task_description,
        project_id=project_id, tree_path=tree_path,
    ))
    _cron_tasks[key] = task

    # Persist
    data = _load_automations(employee_id)
    # Remove existing with same name
    data[_KEY_CRONS] = [c for c in data[_KEY_CRONS] if c.get(_KEY_NAME) != cron_name]
    data[_KEY_CRONS].append({
        _KEY_NAME: cron_name,
        "interval": interval,
        "task_description": task_description,
        "project_id": project_id,
        "tree_path": tree_path,
        "created": datetime.now(timezone.utc).isoformat(),
    })
    _save_automations(employee_id, data)
    _broadcast_cron_status(employee_id, cron_name, True)

    return {"status": "ok", "cron_name": cron_name, "interval": interval}


def _cancel_cron_tasks(employee_id: str, task_ids: list[str]) -> list[str]:
    """Cancel scheduled task nodes spawned by a cron. Returns cancelled node IDs."""
    from pathlib import Path
    from onemancompany.core.task_lifecycle import TaskPhase
    from onemancompany.core.task_tree import TaskTree
    from onemancompany.core.vessel import employee_manager

    cancelled = []
    for task_id in task_ids:
        # Search the employee's schedule for this node
        for entry in employee_manager._schedule.get(employee_id, []):
            if entry.node_id != task_id:
                continue
            tp = Path(entry.tree_path)
            if not tp.exists():
                continue
            tree = TaskTree.load(tp)
            node = tree.get_node(task_id)
            from onemancompany.core.task_lifecycle import safe_cancel
            if node and safe_cancel(node):
                node.completed_at = datetime.now(timezone.utc).isoformat()
                node.result = "Cancelled: cron stopped"
                tree.save(tp)
                employee_manager.unschedule(employee_id, task_id)
                cancelled.append(task_id)
            break

    # Cancel the running asyncio.Task if it was executing one of these tasks
    if cancelled and employee_id in employee_manager._running_tasks:
        running = employee_manager._running_tasks[employee_id]
        if not running.done():
            running.cancel()
            logger.info("Cancelled running asyncio.Task for {} (cron stop)", employee_id)

    return cancelled


def stop_cron(employee_id: str, cron_name: str) -> dict:
    """Stop a cron job and cancel its pending/running tasks."""
    # Collect dispatched task IDs before removing from YAML
    data = _load_automations(employee_id)
    task_ids: list[str] = []
    for c in data[_KEY_CRONS]:
        if c.get(_KEY_NAME) == cron_name:
            task_ids = c.get(_KEY_DISPATCHED_TASK_IDS, [])
            break

    # Cancel associated tasks first
    cancelled_tasks = _cancel_cron_tasks(employee_id, task_ids)

    # Cancel the cron loop
    key = f"{employee_id}:{cron_name}"
    task = _cron_tasks.pop(key, None)
    if task and not task.done():
        task.cancel()

    # Remove from persistence
    data[_KEY_CRONS] = [c for c in data[_KEY_CRONS] if c.get(_KEY_NAME) != cron_name]
    _save_automations(employee_id, data)
    _broadcast_cron_status(employee_id, cron_name, False)

    return {
        "status": "ok",
        "message": f"Cron '{cron_name}' stopped",
        "cancelled_tasks": cancelled_tasks,
    }


def list_crons(employee_id: str) -> list[dict]:
    """List all crons for an employee, including in-memory tasks not in YAML."""
    data = _load_automations(employee_id)
    result = []
    seen_names: set[str] = set()

    # From YAML
    for c in data[_KEY_CRONS]:
        key = f"{employee_id}:{c[_KEY_NAME]}"
        task = _cron_tasks.get(key)
        result.append({
            _KEY_NAME: c[_KEY_NAME],
            "interval": c.get("interval", "?"),
            "task_description": c.get("task_description", ""),
            "running": bool(task and not task.done()),
            _KEY_DISPATCHED_TASK_IDS: c.get(_KEY_DISPATCHED_TASK_IDS, []),
        })
        seen_names.add(c[_KEY_NAME])

    # In-memory crons not in YAML (orphaned)
    prefix = f"{employee_id}:"
    for key, task in _cron_tasks.items():
        if key.startswith(prefix) and not task.done():
            cron_name = key[len(prefix):]
            if cron_name not in seen_names:
                result.append({
                    _KEY_NAME: cron_name,
                    "interval": "?",
                    "task_description": "(in-memory, not persisted)",
                    "running": True,
                })
    return result


def stop_all_crons_for_employee(employee_id: str) -> dict:
    """Stop all cron jobs for an employee, cancelling associated tasks first."""
    stopped = []
    all_cancelled_tasks: list[str] = []

    # Collect all dispatched task IDs from YAML
    data = _load_automations(employee_id)
    all_task_ids: list[str] = []
    for c in data.get(_KEY_CRONS, []):
        all_task_ids.extend(c.get(_KEY_DISPATCHED_TASK_IDS, []))

    # Cancel associated tasks first
    if all_task_ids:
        all_cancelled_tasks = _cancel_cron_tasks(employee_id, all_task_ids)

    # Cancel all in-memory cron loops
    prefix = f"{employee_id}:"
    keys_to_remove = [k for k in _cron_tasks if k.startswith(prefix)]
    for key in keys_to_remove:
        task = _cron_tasks.pop(key)
        if not task.done():
            task.cancel()
            stopped.append(key[len(prefix):])

    # Clear YAML
    data[_KEY_CRONS] = []
    _save_automations(employee_id, data)

    for cron_name in stopped:
        _broadcast_cron_status(employee_id, cron_name, False)

    return {
        "status": "ok",
        "stopped": stopped,
        "count": len(stopped),
        "cancelled_tasks": all_cancelled_tasks,
    }


def restore_all_crons() -> int:
    """Restore all persisted crons on server startup. Returns count."""
    count = 0
    if not EMPLOYEES_DIR.exists():
        return count
    for emp_dir in sorted(EMPLOYEES_DIR.iterdir()):
        if not emp_dir.is_dir():
            continue
        employee_id = emp_dir.name
        data = _load_automations(employee_id)
        for c in data.get(_KEY_CRONS, []):
            name = c.get(_KEY_NAME, "")
            interval = c.get("interval", "")
            desc = c.get("task_description", "")
            if name and interval and desc:
                seconds = _parse_interval(interval)
                if seconds and seconds >= 10:
                    pid = c.get("project_id", "")
                    tp = c.get("tree_path", "")
                    key = f"{employee_id}:{name}"
                    if key not in _cron_tasks or _cron_tasks[key].done():
                        task = asyncio.create_task(_cron_loop(
                            employee_id, name, seconds, desc,
                            project_id=pid, tree_path=tp,
                        ))
                        _cron_tasks[key] = task
                        count += 1
    return count


# ---------------------------------------------------------------------------
# Webhook registry
# ---------------------------------------------------------------------------

# In-memory registry: key = "employee_id:hook_name"
_webhook_registry: dict[str, dict] = {}


def register_webhook(employee_id: str, hook_name: str, task_template: str = "") -> dict:
    """Register a webhook endpoint for an employee.

    The webhook will be available at: POST /api/webhook/{employee_id}/{hook_name}
    When triggered, it dispatches a task to the employee with the webhook payload.

    Args:
        employee_id: Employee ID.
        hook_name: Unique webhook name (URL-safe).
        task_template: Task description template. Use {payload} for the webhook body.
    """
    key = f"{employee_id}:{hook_name}"
    _webhook_registry[key] = {
        "employee_id": employee_id,
        "hook_name": hook_name,
        "task_template": task_template or "Webhook '{hook_name}' triggered with payload: {payload}",
    }

    # Persist
    data = _load_automations(employee_id)
    data[_KEY_WEBHOOKS] = [w for w in data[_KEY_WEBHOOKS] if w.get(_KEY_NAME) != hook_name]
    data[_KEY_WEBHOOKS].append({
        _KEY_NAME: hook_name,
        "task_template": task_template or "Webhook '{hook_name}' triggered with payload: {payload}",
        "created": datetime.now(timezone.utc).isoformat(),
    })
    _save_automations(employee_id, data)

    return {
        "status": "ok",
        "hook_name": hook_name,
        "url": f"/api/webhook/{employee_id}/{hook_name}",
    }


def unregister_webhook(employee_id: str, hook_name: str) -> dict:
    """Remove a webhook."""
    key = f"{employee_id}:{hook_name}"
    _webhook_registry.pop(key, None)

    data = _load_automations(employee_id)
    data[_KEY_WEBHOOKS] = [w for w in data[_KEY_WEBHOOKS] if w.get(_KEY_NAME) != hook_name]
    _save_automations(employee_id, data)

    return {"status": "ok", "message": f"Webhook '{hook_name}' removed"}


async def handle_webhook(employee_id: str, hook_name: str, payload: dict) -> dict:
    """Handle an incoming webhook call — dispatch task to employee."""
    from onemancompany.core.agent_loop import get_agent_loop

    key = f"{employee_id}:{hook_name}"
    config = _webhook_registry.get(key)
    if not config:
        return {"status": "error", "message": f"Webhook '{hook_name}' not registered for {employee_id}"}

    template = config["task_template"]
    payload_str = json.dumps(payload, ensure_ascii=False)[:2000]
    task_desc = template.format(hook_name=hook_name, payload=payload_str)

    try:
        from onemancompany.api.routes import _push_adhoc_task
        _push_adhoc_task(employee_id, f"[webhook:{hook_name}] {task_desc}")
    except Exception as e:
        return {"status": "error", "message": f"Failed to dispatch: {e}"}
    return {"status": "ok", "message": f"Task dispatched to {employee_id}"}


def restore_all_webhooks() -> int:
    """Restore all persisted webhooks on server startup. Returns count."""
    count = 0
    if not EMPLOYEES_DIR.exists():
        return count
    for emp_dir in sorted(EMPLOYEES_DIR.iterdir()):
        if not emp_dir.is_dir():
            continue
        employee_id = emp_dir.name
        data = _load_automations(employee_id)
        for w in data.get(_KEY_WEBHOOKS, []):
            name = w.get(_KEY_NAME, "")
            template = w.get("task_template", "")
            if name:
                key = f"{employee_id}:{name}"
                _webhook_registry[key] = {
                    "employee_id": employee_id,
                    "hook_name": name,
                    "task_template": template,
                }
                count += 1
    return count


def list_webhooks(employee_id: str) -> list[dict]:
    """List all webhooks for an employee."""
    data = _load_automations(employee_id)
    return [
        {
            _KEY_NAME: w[_KEY_NAME],
            "url": f"/api/webhook/{employee_id}/{w[_KEY_NAME]}",
            "task_template": w.get("task_template", ""),
        }
        for w in data.get(_KEY_WEBHOOKS, [])
    ]


# ---------------------------------------------------------------------------
# Unified query
# ---------------------------------------------------------------------------

def list_all_crons() -> list[dict]:
    """Unified query: system crons + all employee crons."""
    from onemancompany.core.system_cron import system_cron_manager

    result = system_cron_manager.get_all()

    if not EMPLOYEES_DIR.exists():
        return result

    for emp_dir in sorted(EMPLOYEES_DIR.iterdir()):
        if not emp_dir.is_dir():
            continue
        employee_id = emp_dir.name
        for cron in list_crons(employee_id):
            result.append({
                "name": cron["name"],
                "interval": cron.get("interval", "?"),
                "description": cron.get("task_description", ""),
                "running": cron.get("running", False),
                "scope": "employee",
                "employee_id": employee_id,
                "last_run": None,
                "run_count": None,
            })

    return result


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

async def stop_all_automations() -> int:
    """Cancel all running cron tasks. Called on server shutdown."""
    count = 0
    for _key, task in list(_cron_tasks.items()):
        if not task.done():
            task.cancel()
            count += 1
    _cron_tasks.clear()
    _webhook_registry.clear()
    return count
