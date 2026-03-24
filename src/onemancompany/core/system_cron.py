"""System Cron Registry — decorator-based periodic task management.

System crons are infrastructure-level periodic tasks (heartbeat, review
reminders, config reload). They differ from employee crons (automation.py)
in that they run async functions directly (zero token cost) rather than
pushing tasks to AI agents.

Usage:
    @system_cron("heartbeat", interval="1m", description="API connection checks")
    async def heartbeat_check() -> list[CompanyEvent] | None:
        ...

All handlers are co-located in this module. The singleton `system_cron_manager`
manages lifecycle. Wire it in main.py lifespan:

    system_cron_manager.start_all()   # startup
    system_cron_manager.stop_all()    # shutdown
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Coroutine, Literal, TypedDict

from loguru import logger

from onemancompany.core.config import SYSTEM_SENDER
from onemancompany.core.interval import parse_interval
from onemancompany.core.models import EventType

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REVIEW_REMINDER_THRESHOLD_SECONDS = 300  # 5 minutes


@dataclass
class SystemCronDef:
    name: str
    default_interval: str
    description: str
    handler: Callable[[], Coroutine[Any, Any, list | None]]
    current_interval: str = ""
    current_interval_seconds: int = 0
    last_run: datetime | None = None
    run_count: int = 0
    last_error: str | None = None

    def __post_init__(self):
        if not self.current_interval:
            self.current_interval = self.default_interval
            self.current_interval_seconds = parse_interval(self.default_interval) or 60


class CronInfo(TypedDict):
    name: str
    interval: str
    description: str
    running: bool
    scope: Literal["system", "employee"]
    employee_id: str | None
    last_run: str | None
    run_count: int | None


_registry: dict[str, SystemCronDef] = {}


def system_cron(
    name: str,
    *,
    interval: str,
    description: str,
    registry: dict[str, SystemCronDef] | None = None,
):
    """Decorator to register a system cron handler."""
    target = registry if registry is not None else _registry
    seconds = parse_interval(interval)
    if seconds is None:
        raise ValueError(f"Invalid interval: {interval!r}")

    def decorator(fn):
        target[name] = SystemCronDef(
            name=name,
            default_interval=interval,
            description=description,
            handler=fn,
        )
        return fn
    return decorator


class SystemCronManager:
    """Manages lifecycle of all system cron tasks."""

    def __init__(self, registry: dict[str, SystemCronDef] | None = None):
        self._registry = registry if registry is not None else _registry
        self._tasks: dict[str, asyncio.Task] = {}

    def start_all(self) -> None:
        """Start all registered system crons."""
        for name in self._registry:
            if name not in self._tasks or self._tasks[name].done():
                self.start(name)
        logger.info("System crons started: {}", list(self._registry.keys()))

    async def stop_all(self) -> None:
        """Stop all running system crons."""
        tasks_to_await = list(self._tasks.values())
        for name in list(self._tasks.keys()):
            self.stop(name)
        if tasks_to_await:
            await asyncio.gather(*tasks_to_await, return_exceptions=True)
        self._tasks.clear()
        logger.info("All system crons stopped")

    def start(self, name: str) -> dict:
        """Start a single system cron by name."""
        defn = self._registry.get(name)
        if not defn:
            return {"status": "error", "message": f"Unknown system cron: {name}"}
        existing = self._tasks.get(name)
        if existing and not existing.done():
            existing.cancel()
        task = asyncio.create_task(self._loop(defn), name=f"system_cron:{name}")
        self._tasks[name] = task
        return {"status": "ok", "name": name}

    def stop(self, name: str) -> dict:
        """Stop a single system cron by name."""
        task = self._tasks.pop(name, None)
        if task and not task.done():
            task.cancel()
        return {"status": "ok", "name": name}

    def update_interval(self, name: str, new_interval: str) -> dict:
        """Update interval for a system cron, restarting it if running."""
        defn = self._registry.get(name)
        if not defn:
            return {"status": "error", "message": f"Unknown system cron: {name}"}
        seconds = parse_interval(new_interval)
        if seconds is None:
            return {"status": "error", "message": f"Invalid interval: {new_interval}"}
        defn.current_interval = new_interval
        defn.current_interval_seconds = seconds
        if name in self._tasks and not self._tasks[name].done():
            self.stop(name)
            self.start(name)
        return {"status": "ok", "name": name, "interval": new_interval}

    def get_all(self) -> list[CronInfo]:
        """Return info for all registered system crons."""
        result: list[CronInfo] = []
        for name, defn in self._registry.items():
            task = self._tasks.get(name)
            running = bool(task and not task.done())
            result.append({
                "name": defn.name,
                "interval": defn.current_interval,
                "description": defn.description,
                "running": running,
                "scope": SYSTEM_SENDER,
                "employee_id": None,
                "last_run": defn.last_run.isoformat() if defn.last_run else None,
                "run_count": defn.run_count,
            })
        return result

    async def _loop(self, cron_def: SystemCronDef) -> None:
        """Main loop for a single system cron."""
        from onemancompany.core.events import event_bus

        logger.info("[system_cron] Started '{}' every {}", cron_def.name, cron_def.current_interval)
        try:
            while True:
                await asyncio.sleep(cron_def.current_interval_seconds)
                try:
                    events = await cron_def.handler()
                    cron_def.last_run = datetime.now()
                    cron_def.run_count += 1
                    cron_def.last_error = None
                    if events:
                        for event in events:
                            await event_bus.publish(event)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error("[system_cron] '{}' error: {}", cron_def.name, e)
                    cron_def.last_error = str(e)
                    cron_def.last_run = datetime.now()
                    cron_def.run_count += 1
        except asyncio.CancelledError:
            logger.info("[system_cron] Stopped '{}'", cron_def.name)
            raise


system_cron_manager = SystemCronManager()


# ---------------------------------------------------------------------------
# Handler implementations
# ---------------------------------------------------------------------------

@system_cron("heartbeat", interval="1m", description="员工 API 连接检测")
async def heartbeat_check() -> list | None:
    from onemancompany.core.heartbeat import run_heartbeat_cycle
    from onemancompany.core.events import CompanyEvent

    changed = await run_heartbeat_cycle()
    if changed:
        return [CompanyEvent(type=EventType.STATE_SNAPSHOT, payload={}, agent="HEARTBEAT")]
    return None


@system_cron("review_reminder", interval="5m", description="审批超时提醒")
async def review_reminder_check() -> list | None:
    from onemancompany.core.vessel import scan_overdue_reviews
    from onemancompany.core.events import CompanyEvent

    overdue = scan_overdue_reviews(threshold_seconds=REVIEW_REMINDER_THRESHOLD_SECONDS)
    if overdue:
        return [CompanyEvent(
            type=EventType.REVIEW_REMINDER,
            payload={"overdue_nodes": overdue},
            agent="REVIEW_REMINDER",
        )]
    return None


@system_cron("config_reload", interval="30s", description="磁盘配置定期重载")
async def config_reload_check() -> list | None:
    from onemancompany.core.state import is_idle, reload_all_from_disk

    if is_idle():
        result = reload_all_from_disk()
        updated = result.get("employees_updated", [])
        added = result.get("employees_added", [])
        if updated or added:
            logger.info("[config_reload] {} updated, {} added", len(updated), len(added))
    return None


# ---------------------------------------------------------------------------
# Talent Market keepalive — maintain MCP connection
# ---------------------------------------------------------------------------


@system_cron("talent_market_keepalive", interval="15s", description="Talent Market MCP 连接保活")
async def talent_market_keepalive() -> list | None:
    """Ping the Talent Market MCP server; reconnect if the session is dead."""
    from onemancompany.agents.recruitment import talent_market, start_talent_market

    if not talent_market.connected:
        return None

    try:
        await talent_market._session.send_ping()
        logger.debug("[talent_market_keepalive] ping OK")
    except Exception as e:
        logger.warning("[talent_market_keepalive] ping failed ({}), reconnecting...", e)
        try:
            await talent_market._reconnect()
            logger.info("[talent_market_keepalive] reconnected successfully")
        except Exception as e2:
            logger.error("[talent_market_keepalive] reconnect failed: {}", e2)
    return None


# ---------------------------------------------------------------------------
# Project progress watchdog — prevent projects from getting stuck
# ---------------------------------------------------------------------------

# Track projects we've already nudged (cleared when EA picks up the task)
_watchdog_nudged: set[str] = set()


@system_cron("project_progress_watchdog", interval="1m", description="项目进度看门狗 — 防止项目卡死")
async def project_progress_watchdog() -> list | None:
    """Scan active projects; nudge EA to continue any that are stuck.

    A project is "stuck" when:
    - No node is currently in ``processing`` state (nobody is working on it)
    - Not all active-branch nodes have reached a terminal state
    - The project hasn't been nudged yet since the last check

    When stuck, a new task node is added under the EA node asking it to
    review the task tree and drive the project forward.
    """
    from onemancompany.core.config import EA_ID, PROJECTS_DIR, TASK_TREE_FILENAME
    from onemancompany.core.task_lifecycle import TaskPhase, RESOLVED, NodeType
    from onemancompany.core.task_tree import get_tree, get_tree_lock, save_tree_async
    from onemancompany.core.vessel import employee_manager

    if not PROJECTS_DIR.exists():
        return None

    nudged_projects: list[str] = []

    for tree_path in PROJECTS_DIR.rglob(TASK_TREE_FILENAME):
        tree_path_str = str(tree_path)
        try:
            tree = get_tree(tree_path_str)
        except Exception as e:
            logger.debug("[watchdog] Skipping corrupt tree: {} — {}", tree_path, e)
            continue

        project_id = tree.project_id
        if not project_id:
            continue

        # Skip archived projects
        from onemancompany.core.project_archive import load_named_project, PROJECT_STATUS_ARCHIVED
        named_pid = project_id.split("/")[0] if "/" in project_id else project_id
        named_proj = load_named_project(named_pid)
        if named_proj and named_proj.get("status") == PROJECT_STATUS_ARCHIVED:
            continue

        # Skip projects we've already nudged (waiting for EA to pick it up)
        if project_id in _watchdog_nudged:
            continue

        # Only look at active-branch nodes (exclude root ceo_prompt)
        active_nodes = [
            n for n in tree.all_nodes()
            if n.branch_active and n.id != tree.root_id
        ]
        if not active_nodes:
            continue

        # Skip if any node is currently being processed — someone is working
        if any(n.status == TaskPhase.PROCESSING.value for n in active_nodes):
            continue

        # Skip if all active nodes are resolved (project is done)
        all_resolved = all(
            TaskPhase(n.status) in RESOLVED for n in active_nodes
        )
        if all_resolved:
            continue

        # --- Project is stuck — nudge EA ---
        ea_node = tree.get_ea_node()
        if not ea_node:
            logger.debug("[watchdog] No EA node found for project {}", project_id)
            continue

        # Build a summary of the current tree state for EA
        status_summary = _build_tree_status_summary(tree)

        project_abs_path = str(tree_path.parent.resolve())
        nudge_desc = (
            f"[项目进度看门狗] 项目 {project_id} 存在未完成的任务节点且当前无人在执行。\n"
            f"项目路径: {project_abs_path}\n\n"
            f"请查看以下任务树状态，尝试继续推进项目完成：\n\n"
            f"{status_summary}\n\n"
            f"请根据当前状态采取适当行动：\n"
            f"- 如有 completed 状态的子任务，请 accept_child 或 reject_child\n"
            f"- 如有 failed/blocked 的任务，请决定重试或跳过\n"
            f"- 如需新增任务，请 dispatch_child\n"
            f"- 如项目已无法继续，请说明原因"
        )

        lock = get_tree_lock(tree_path_str)
        with lock:
            nudge_node = tree.add_child(
                parent_id=tree.root_id,
                employee_id=EA_ID,
                description=nudge_desc,
                acceptance_criteria=[],
            )
            nudge_node.node_type = NodeType.WATCHDOG_NUDGE
            nudge_node.project_id = project_id
            nudge_node.project_dir = ea_node.project_dir or str(tree_path.parent)
            save_tree_async(tree_path_str)

        employee_manager.push_task(
            EA_ID, description="", node_id=nudge_node.id, tree_path=tree_path_str,
        )

        _watchdog_nudged.add(project_id)
        nudged_projects.append(project_id)
        logger.info("[watchdog] Nudged EA to continue stuck project {}", project_id)

    if nudged_projects:
        from onemancompany.core.events import CompanyEvent

        return [CompanyEvent(
            type=EventType.STATE_SNAPSHOT,
            payload={"watchdog_nudged": nudged_projects},
            agent="PROJECT_WATCHDOG",
        )]
    return None


def clear_watchdog_nudge(project_id: str) -> None:
    """Clear the nudge flag for a project (call when EA starts working on it)."""
    _watchdog_nudged.discard(project_id)


def _build_tree_status_summary(tree) -> str:
    """Build a concise status summary of all active nodes in the tree."""
    lines = []
    active_nodes = [
        n for n in tree.all_nodes()
        if n.branch_active and n.id != tree.root_id
    ]
    # Group by status
    by_status: dict[str, list] = {}
    for n in active_nodes:
        by_status.setdefault(n.status, []).append(n)

    from onemancompany.core.task_lifecycle import TaskPhase
    for status in [p.value for p in TaskPhase]:
        nodes = by_status.get(status, [])
        if not nodes:
            continue
        lines.append(f"【{status}】({len(nodes)}个):")
        for n in nodes[:5]:  # Cap at 5 per status to keep prompt manageable
            preview = n.description_preview or n.id
            lines.append(f"  - [{n.employee_id}] {preview[:100]}")
        if len(nodes) > 5:
            lines.append(f"  ... 还有 {len(nodes) - 5} 个")

    return "\n".join(lines)
