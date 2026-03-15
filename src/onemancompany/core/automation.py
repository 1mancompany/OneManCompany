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

from onemancompany.core.config import EMPLOYEES_DIR
from onemancompany.core.interval import parse_interval as _parse_interval


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

def _automations_file(employee_id: str) -> Path:
    return EMPLOYEES_DIR / employee_id / "automations.yaml"


def _load_automations(employee_id: str) -> dict:
    path = _automations_file(employee_id)
    if not path.exists():
        return {"crons": [], "webhooks": []}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data.setdefault("crons", [])
        data.setdefault("webhooks", [])
        return data
    except Exception:
        return {"crons": [], "webhooks": []}


def _save_automations(employee_id: str, data: dict) -> None:
    path = _automations_file(employee_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Cron scheduler
# ---------------------------------------------------------------------------

_cron_tasks: dict[str, asyncio.Task] = {}  # key = "employee_id:cron_name"


async def _cron_loop(employee_id: str, cron_name: str, interval_seconds: int, task_description: str) -> None:
    """Background loop that dispatches a task at regular intervals."""
    from onemancompany.core.agent_loop import get_agent_loop

    logger.info(f"[cron] Started '{cron_name}' for {employee_id} every {interval_seconds}s")
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            loop = get_agent_loop(employee_id)
            if loop:
                agent_task = loop.push_task(f"[cron:{cron_name}] {task_description}")
                # Record dispatched task ID
                _record_dispatched_task(employee_id, cron_name, agent_task.id)
                logger.debug(f"[cron] Dispatched '{cron_name}' to {employee_id}, task_id={agent_task.id}")
            else:
                logger.warning(f"[cron] Employee {employee_id} not found, skipping '{cron_name}'")
    except asyncio.CancelledError:
        logger.info(f"[cron] Stopped '{cron_name}' for {employee_id}")
        raise


def _record_dispatched_task(employee_id: str, cron_name: str, task_id: str) -> None:
    """Record a dispatched task ID in the cron's YAML entry."""
    data = _load_automations(employee_id)
    for c in data["crons"]:
        if c.get("name") == cron_name:
            task_ids = c.setdefault("dispatched_task_ids", [])
            task_ids.append(task_id)
            # Keep last 100 to avoid unbounded growth
            if len(task_ids) > 100:
                c["dispatched_task_ids"] = task_ids[-100:]
            break
    _save_automations(employee_id, data)


def start_cron(employee_id: str, cron_name: str, interval: str, task_description: str) -> dict:
    """Start a cron job for an employee.

    Args:
        employee_id: Employee ID.
        cron_name: Unique name for this cron (per employee).
        interval: Interval string like '5m', '1h', '30s'.
        task_description: Task to dispatch each interval.
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
    task = asyncio.create_task(_cron_loop(employee_id, cron_name, seconds, task_description))
    _cron_tasks[key] = task

    # Persist
    data = _load_automations(employee_id)
    # Remove existing with same name
    data["crons"] = [c for c in data["crons"] if c.get("name") != cron_name]
    data["crons"].append({
        "name": cron_name,
        "interval": interval,
        "task_description": task_description,
        "created": datetime.now(timezone.utc).isoformat(),
    })
    _save_automations(employee_id, data)

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
            if node and node.status in (TaskPhase.PENDING.value, TaskPhase.PROCESSING.value):
                node.status = TaskPhase.CANCELLED.value
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
    for c in data["crons"]:
        if c.get("name") == cron_name:
            task_ids = c.get("dispatched_task_ids", [])
            break

    # Cancel associated tasks first
    cancelled_tasks = _cancel_cron_tasks(employee_id, task_ids)

    # Cancel the cron loop
    key = f"{employee_id}:{cron_name}"
    task = _cron_tasks.pop(key, None)
    if task and not task.done():
        task.cancel()

    # Remove from persistence
    data["crons"] = [c for c in data["crons"] if c.get("name") != cron_name]
    _save_automations(employee_id, data)

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
    for c in data["crons"]:
        key = f"{employee_id}:{c['name']}"
        task = _cron_tasks.get(key)
        result.append({
            "name": c["name"],
            "interval": c.get("interval", "?"),
            "task_description": c.get("task_description", ""),
            "running": bool(task and not task.done()),
            "dispatched_task_ids": c.get("dispatched_task_ids", []),
        })
        seen_names.add(c["name"])

    # In-memory crons not in YAML (orphaned)
    prefix = f"{employee_id}:"
    for key, task in _cron_tasks.items():
        if key.startswith(prefix) and not task.done():
            cron_name = key[len(prefix):]
            if cron_name not in seen_names:
                result.append({
                    "name": cron_name,
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
    for c in data.get("crons", []):
        all_task_ids.extend(c.get("dispatched_task_ids", []))

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
    data["crons"] = []
    _save_automations(employee_id, data)

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
        for c in data.get("crons", []):
            name = c.get("name", "")
            interval = c.get("interval", "")
            desc = c.get("task_description", "")
            if name and interval and desc:
                seconds = _parse_interval(interval)
                if seconds and seconds >= 10:
                    key = f"{employee_id}:{name}"
                    if key not in _cron_tasks or _cron_tasks[key].done():
                        task = asyncio.create_task(_cron_loop(employee_id, name, seconds, desc))
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
    data["webhooks"] = [w for w in data["webhooks"] if w.get("name") != hook_name]
    data["webhooks"].append({
        "name": hook_name,
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
    data["webhooks"] = [w for w in data["webhooks"] if w.get("name") != hook_name]
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

    loop = get_agent_loop(employee_id)
    if not loop:
        return {"status": "error", "message": f"Employee {employee_id} not found"}

    loop.push_task(f"[webhook:{hook_name}] {task_desc}")
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
        for w in data.get("webhooks", []):
            name = w.get("name", "")
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
            "name": w["name"],
            "url": f"/api/webhook/{employee_id}/{w['name']}",
            "task_template": w.get("task_template", ""),
        }
        for w in data.get("webhooks", [])
    ]


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
