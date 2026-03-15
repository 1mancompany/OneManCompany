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

from onemancompany.core.interval import parse_interval


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
                "scope": "system",
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

REVIEW_REMINDER_THRESHOLD_SECONDS = 300  # 5 minutes


@system_cron("heartbeat", interval="1m", description="员工 API 连接检测")
async def heartbeat_check() -> list | None:
    from onemancompany.core.heartbeat import run_heartbeat_cycle
    from onemancompany.core.events import CompanyEvent

    changed = await run_heartbeat_cycle()
    if changed:
        return [CompanyEvent(type="state_snapshot", payload={}, agent="HEARTBEAT")]
    return None


@system_cron("review_reminder", interval="5m", description="审批超时提醒")
async def review_reminder_check() -> list | None:
    from onemancompany.core.vessel import scan_overdue_reviews
    from onemancompany.core.events import CompanyEvent

    overdue = scan_overdue_reviews(threshold_seconds=REVIEW_REMINDER_THRESHOLD_SECONDS)
    if overdue:
        return [CompanyEvent(
            type="review_reminder",
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
