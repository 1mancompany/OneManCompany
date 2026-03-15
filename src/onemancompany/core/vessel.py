"""Vessel — Employee execution system (on-demand task dispatch).

Vessel + Talent = Employee.
EmployeeManager manages the complete employee after combining vessel and talent.

Key concepts:
- Vessel: Employee execution container (formerly EmployeeHandle)
- *Executor / Launcher: Execution backend
- VesselConfig: Vessel DNA (vessel.yaml)
- VesselHarness protocols: Adapter standards (decoupling company system interactions)

Design:
  No persistent while-loop per employee — tasks execute on-demand.
  When a task is pushed, EmployeeManager creates a one-shot asyncio.Task.
  When that task completes, the next pending task is auto-scheduled.
  Between tasks, no process/coroutine is occupied.
"""

from __future__ import annotations

import asyncio
import json
import traceback
import uuid
from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from langgraph.errors import GraphRecursionError

from onemancompany.agents.base import BaseAgentRunner, make_llm
from onemancompany.core.config import (
    EMPLOYEES_DIR,
    MAX_SUMMARY_LEN,
    STATUS_IDLE,
    STATUS_WORKING,
)
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.state import company_state  # noqa: F401 — tests patch this
from onemancompany.core import store as _store
from onemancompany.core.vessel_config import VesselConfig

from loguru import logger

# ---------------------------------------------------------------------------
# ScheduleEntry — pure pointer to a TaskNode (replaces AgentTask)
# ---------------------------------------------------------------------------

@dataclass
class ScheduleEntry:
    """Pure pointer to a TaskNode. No business data."""
    node_id: str
    tree_path: str  # path to the tree YAML file


# ---------------------------------------------------------------------------
# Context variables — set during task execution so tools can access context
# ---------------------------------------------------------------------------

_current_vessel: ContextVar["Vessel | None"] = ContextVar("_current_vessel", default=None)
_current_task_id: ContextVar[str] = ContextVar("_current_task_id", default="")


# ---------------------------------------------------------------------------
# Task tree helpers (module-level for easy mocking)
# ---------------------------------------------------------------------------

def _load_project_tree(project_dir: str):
    """Get TaskTree from memory cache (loading from disk if needed)."""
    from onemancompany.core.task_tree import get_tree
    path = Path(project_dir) / "task_tree.yaml"
    if not path.exists():
        return None
    return get_tree(path)


def _save_project_tree(project_dir: str, tree):
    """Register tree in cache and save to disk.

    First call creates the file synchronously; subsequent saves are async.
    """
    from onemancompany.core.task_tree import register_tree, save_tree_async
    path = Path(project_dir) / "task_tree.yaml"
    register_tree(path, tree)
    if not path.exists():
        tree.save(path)  # sync: create file on disk
    else:
        save_tree_async(path)



# ---------------------------------------------------------------------------
# Dependency context builder
# ---------------------------------------------------------------------------

def _build_dependency_context(tree, node, project_dir: str = "") -> str:
    """Build context string from resolved dependency results."""
    if not node.depends_on:
        return ""
    sections = []
    max_per_dep = 2000 if len(node.depends_on) <= 3 else 1000
    for dep_id in node.depends_on:
        dep = tree.get_node(dep_id)
        if not dep or not dep.is_resolved:
            continue
        # Load content for reading description/result
        load_dir = dep.project_dir or project_dir
        if load_dir:
            dep.load_content(load_dir)
        result = dep.result or "(no result)"
        if len(result) > max_per_dep:
            result = "..." + result[-max_per_dep:]
        status_label = "completed" if dep.status == "accepted" else dep.status
        sections.append(f"{dep.employee_id} {status_label} \"{dep.description}\":\n{result}")
    if not sections:
        return ""
    return "=== Dependency Results ===\n" + "\n\n".join(sections) + "\n=== End Dependencies ===\n\n"


# ---------------------------------------------------------------------------
# Distance-based tree context builder
# ---------------------------------------------------------------------------

def _build_tree_context(tree, node, project_dir: str) -> str:
    """Build distance-based tree context for an employee.

    - Current node + parent: full content (load_content)
    - Grandparent+: skeleton only (id + status + preview)
    - Children needing review: full result
    - Accepted children: skeleton only
    """
    parts: list[str] = []

    # Walk up: ancestors
    ancestors: list[tuple] = []  # (node, distance)
    current = node
    dist = 0
    while current.parent_id:
        parent = tree.get_node(current.parent_id)
        if not parent:
            break
        dist += 1
        ancestors.append((parent, dist))
        current = parent

    if ancestors:
        parts.append("=== Task Chain (ancestors) ===")
        for anc, d in reversed(ancestors):
            if d <= 1:  # parent only
                anc.load_content(project_dir)
                parts.append(f"[Lv-{d}] {anc.id} ({anc.employee_id}) [{anc.status}]")
                parts.append(f"  Description: {anc.description}")
                if anc.result:
                    parts.append(f"  Result: {anc.result}")
            else:
                parts.append(f"[Lv-{d}] {anc.id} ({anc.employee_id}) [{anc.status}]")
                parts.append(f"  Preview: {anc.description_preview}")
        parts.append("")

    # Current node
    node.load_content(project_dir)
    parts.append(f"=== Current Task ({node.id}) ===")
    parts.append(f"Description: {node.description}")
    if node.result:
        parts.append(f"Result: {node.result}")
    parts.append("")

    # Children
    children = tree.get_active_children(node.id)
    if children:
        parts.append("=== Child Tasks ===")
        for child in children:
            if child.is_ceo_node:
                continue
            if child.status == "accepted":
                parts.append(f"  [ACCEPTED] {child.id} ({child.employee_id}): {child.description_preview[:100]}")
            elif child.is_done_executing:
                child.load_content(project_dir)
                parts.append(f"  [{child.status.upper()}] {child.id} ({child.employee_id}): {child.description}")
                parts.append(f"    Result: {child.result}")
            else:
                parts.append(f"  [{child.status.upper()}] {child.id} ({child.employee_id}): {child.description_preview}")
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Dependency resolution trigger (callable from sync tool context)
# ---------------------------------------------------------------------------

def _trigger_dep_resolution(project_dir: str, tree, node) -> None:
    """Schedule async dependency resolution after a node becomes terminal."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            employee_manager._resolve_dependencies(tree, node, project_dir)
        )
    except RuntimeError:
        # Called from sync tool context (e.g. accept_child) — no event loop.
        # Use the main loop via call_soon_threadsafe, same pattern as _schedule_next.
        main_loop = getattr(employee_manager, "_event_loop", None)
        if main_loop and not main_loop.is_closed():
            main_loop.call_soon_threadsafe(
                main_loop.create_task,
                employee_manager._resolve_dependencies(tree, node, project_dir),
            )
            logger.info("Scheduled dep resolution for {} via call_soon_threadsafe", node.id)
        else:
            logger.warning("No event loop available for dep resolution of {}", node.id)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("Could not schedule dep resolution: {}", e)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

from onemancompany.core.task_lifecycle import TaskPhase, TERMINAL, RESOLVED


def _history_path(employee_id: str) -> Path:
    """Return path to employee's task history file."""
    return EMPLOYEES_DIR / employee_id / "task_history.json"


def _load_task_history(employee_id: str) -> tuple[list[dict], str]:
    """Load task history and summary from disk."""
    path = _history_path(employee_id)
    if not path.exists():
        return [], ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("entries", []), data.get("summary", "")
    except Exception as e:
        logger.warning("Failed to load task history for {}: {}", employee_id, e)
        return [], ""


def _save_task_history(employee_id: str, entries: list[dict], summary: str) -> None:
    """Persist task history and summary to disk."""
    path = _history_path(employee_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "entries": entries,
            "summary": summary,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to save task history for {}: {}", employee_id, e)




def stop_cron(employee_id: str, cron_name: str) -> dict:
    """Lazy-import wrapper."""
    from onemancompany.core.automation import stop_cron as _stop
    return _stop(employee_id, cron_name)


def _parse_holding_metadata(result: str | None) -> dict | None:
    """Parse __HOLDING:key=value,... prefix from agent result.

    Returns dict of metadata if HOLDING prefix found, None otherwise.
    Only parses the first line.
    """
    if not result or not result.startswith("__HOLDING:"):
        return None
    first_line = result.split("\n", 1)[0]
    payload = first_line[len("__HOLDING:"):]
    if not payload.strip():
        return {}
    meta = {}
    for pair in payload.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            meta[k.strip()] = v.strip()
    return meta




# ---------------------------------------------------------------------------
# Execution Harness — pluggable execution backends (was: Launcher)
# ---------------------------------------------------------------------------

@dataclass
class LaunchResult:
    """Result from a single task execution."""
    output: str = ""
    model_used: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass
class TaskContext:
    """Context passed to executors alongside the task description."""
    project_id: str = ""
    work_dir: str = ""
    employee_id: str = ""
    task_id: str = ""


class Launcher(ABC):
    """Protocol for executing a single task iteration.

    Launchers are pluggable execution backends. The platform defines the protocol;
    each launcher implements it for a specific AI/execution environment.

    See also: Protocol-based ExecutionHarness in vessel_harness.py.
    """

    @abstractmethod
    async def execute(
        self,
        task_description: str,
        context: TaskContext,
        on_log: Callable[[str, str], None] | None = None,
    ) -> LaunchResult:
        ...

    def is_ready(self) -> bool:
        return True




class LangChainExecutor(Launcher):
    """Executes tasks via a LangChain react agent (company-hosted employees)."""

    def __init__(self, agent_runner: BaseAgentRunner) -> None:
        self.agent = agent_runner

    async def execute(
        self,
        task_description: str,
        context: TaskContext,
        on_log: Callable[[str, str], None] | None = None,
    ) -> LaunchResult:
        result = await self.agent.run_streamed(task_description, on_log=on_log)
        usage = getattr(self.agent, '_last_usage', {})
        return LaunchResult(
            output=result or "",
            model_used=usage.get("model", ""),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        )


class ClaudeSessionExecutor(Launcher):
    """Executes tasks via Claude CLI sessions (self-hosted employees)."""

    def __init__(self, employee_id: str) -> None:
        self.employee_id = employee_id

    async def execute(
        self,
        task_description: str,
        context: TaskContext,
        on_log: Callable[[str, str], None] | None = None,
    ) -> LaunchResult:
        from onemancompany.core.claude_session import run_claude_session

        result = await run_claude_session(
            self.employee_id,
            context.project_id or "default",
            prompt=task_description,
            work_dir=context.work_dir,
            task_id=context.task_id,
        )
        output = result.get("output", "")
        if on_log:
            on_log("result", (output or "")[:500])
        input_tokens = result.get("input_tokens", 0)
        output_tokens = result.get("output_tokens", 0)
        return LaunchResult(
            output=output or "",
            model_used=result.get("model", ""),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )


class ScriptExecutor(Launcher):
    """Executes tasks via a custom bash script (extensible).

    The script receives the task description via stdin and writes output to stdout.
    Employee directory contains launch.sh that is executed.
    """

    def __init__(self, employee_id: str, script_path: str = "") -> None:
        self.employee_id = employee_id
        self.script_path = script_path or str(EMPLOYEES_DIR / employee_id / "launch.sh")

    async def execute(
        self,
        task_description: str,
        context: TaskContext,
        on_log: Callable[[str, str], None] | None = None,
    ) -> LaunchResult:
        import os

        cwd = context.work_dir or str(EMPLOYEES_DIR / self.employee_id)
        env = {**os.environ, "TASK_PROJECT_ID": context.project_id, "TASK_WORK_DIR": context.work_dir}

        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", self.script_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=task_description.encode()),
                timeout=600,
            )
            output = stdout.decode("utf-8", errors="replace").strip()
            if proc.returncode != 0 and not output:
                err = stderr.decode("utf-8", errors="replace").strip()
                output = f"[script error] exit={proc.returncode}\n{err[:2000]}"
            if on_log:
                on_log("result", output[:500])
            return LaunchResult(output=output)
        except asyncio.TimeoutError:
            return LaunchResult(output="[script timeout] Timed out after 600s")
        except Exception as e:
            return LaunchResult(output=f"[script error] {e}")




# ---------------------------------------------------------------------------
# Vessel — employee execution container (was: EmployeeHandle)
# ---------------------------------------------------------------------------

class _VesselRef:
    """Minimal agent reference for backward compat (vessel.agent.employee_id)."""

    def __init__(self, employee_id: str) -> None:
        self.employee_id = employee_id

    @property
    def role(self) -> str:
        from onemancompany.core.store import load_employee
        emp_data = load_employee(self.employee_id)
        return (emp_data or {}).get("role", "Employee")




class Vessel:
    """Per-employee view into the EmployeeManager.

    Per-employee view providing task management and history access.
    """

    def __init__(self, manager: "EmployeeManager", employee_id: str) -> None:
        self.manager = manager
        self.employee_id = employee_id
        self.agent = _VesselRef(employee_id)

    @property
    def task_history(self) -> list[dict]:
        return self.manager.task_histories.get(self.employee_id, [])

    def push_task(
        self,
        description: str,
        project_id: str = "",
        project_dir: str = "",
        node_id: str = "",
        tree_path: str = "",
    ) -> str:
        return self.manager.push_task(
            self.employee_id, description,
            project_id=project_id, project_dir=project_dir,
            node_id=node_id, tree_path=tree_path,
        )

    def get_history_context(self) -> str:
        return self.manager.get_history_context(self.employee_id)

    def get_task(self, task_id: str):
        """Look up a TaskNode by ID (delegates to EmployeeManager)."""
        return self.manager.get_task(task_id)




# ---------------------------------------------------------------------------
# Progress log — file-based cross-task context (ralph-inspired)
# ---------------------------------------------------------------------------

PROGRESS_LOG_MAX_LINES = 30


def _append_progress(employee_id: str, entry: str) -> None:
    """Append an entry to the employee's progress log (persistent across tasks)."""
    path = EMPLOYEES_DIR / employee_id / "progress.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat()[:19]}] {entry}\n")


def _load_progress(employee_id: str, max_lines: int = PROGRESS_LOG_MAX_LINES) -> str:
    """Load recent entries from the employee's progress log."""
    path = EMPLOYEES_DIR / employee_id / "progress.log"
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        return "\n".join(lines[-max_lines:])
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Employee Manager — centralized task coordinator
# ---------------------------------------------------------------------------

MAX_SUBTASK_ITERATIONS = 3
MAX_SUBTASK_DEPTH = 2
MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 30]

# Task history constants
MAX_HISTORY_ENTRIES = 8
MAX_HISTORY_CHARS = 3000
RESULT_SNIPPET_LEN = 300


class EmployeeManager:
    """Central coordinator for all employee task execution.

    Replaces the per-employee PersistentAgentLoop pattern.
    Tasks are dispatched on-demand — no idle polling loops.
    """

    def __init__(self) -> None:
        self.executors: dict[str, Launcher] = {}
        self.vessels: dict[str, Vessel] = {}
        self.configs: dict[str, VesselConfig] = {}
        self.task_histories: dict[str, list[dict]] = {}
        self._history_summaries: dict[str, str] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._system_tasks: dict[str, asyncio.Task] = {}  # system operation tracking
        self._deferred_schedule: set[str] = set()
        self._hooks: dict[str, dict[str, Callable]] = {}
        self._event_loop: asyncio.AbstractEventLoop | None = None  # set by drain_pending
        self._restart_pending: bool = False
        # ScheduleEntry-based scheduling (replaces boards for new code paths)
        self._schedule: dict[str, list[ScheduleEntry]] = {}  # employee_id → scheduled nodes
        self._task_logs: dict[str, list[dict]] = {}  # node_id → temporary log buffer

    # ------------------------------------------------------------------
    # ScheduleEntry-based node scheduling
    # ------------------------------------------------------------------

    def schedule_node(self, employee_id: str, node_id: str, tree_path: str) -> None:
        """Add a node to the employee's schedule."""
        # Always persist to task index for taskboard visibility
        from onemancompany.core.store import append_task_index_entry
        append_task_index_entry(employee_id, node_id, tree_path)

        if employee_id not in self.executors:
            logger.debug("Skipping schedule_node for {} (no executor, e.g. CEO)", employee_id)
            return
        entry = ScheduleEntry(node_id=node_id, tree_path=tree_path)
        self._schedule.setdefault(employee_id, []).append(entry)

    def unschedule(self, employee_id: str, node_id: str) -> None:
        """Remove a completed/failed node from schedule."""
        entries = self._schedule.get(employee_id, [])
        self._schedule[employee_id] = [e for e in entries if e.node_id != node_id]

    def get_next_scheduled(self, employee_id: str) -> ScheduleEntry | None:
        """Find next scheduled node that is PENDING with deps resolved."""
        from onemancompany.core.task_tree import get_tree
        for entry in self._schedule.get(employee_id, []):
            tree_path = Path(entry.tree_path)
            if not tree_path.exists():
                continue
            tree = get_tree(tree_path)
            node = tree.get_node(entry.node_id)
            if node and TaskPhase(node.status) == TaskPhase.PENDING and tree.all_deps_resolved(node.id):
                return entry
        return None

    def get_task(self, task_id: str):
        """Look up a TaskNode by its ID across all scheduled trees."""
        from onemancompany.core.task_tree import get_tree
        for entries in self._schedule.values():
            for entry in entries:
                tree_path = Path(entry.tree_path)
                if not tree_path.exists():
                    continue
                tree = get_tree(tree_path)
                node = tree.get_node(task_id)
                if node:
                    return node
        return None

    # Backward-compat aliases (properties so they stay in sync)
    @property
    def launchers(self) -> dict[str, Launcher]:
        return self.executors

    @property
    def _handles(self) -> dict[str, Vessel]:
        return self.vessels

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, employee_id: str, launcher: Launcher, config: VesselConfig | None = None) -> Vessel:
        """Register an employee with a launcher. Returns a Vessel."""
        self.executors[employee_id] = launcher
        if config is not None:
            self.configs[employee_id] = config
        if employee_id not in self.task_histories:
            entries, summary = _load_task_history(employee_id)
            self.task_histories[employee_id] = entries
            if summary:
                self._history_summaries[employee_id] = summary
        vessel = Vessel(self, employee_id)
        self.vessels[employee_id] = vessel
        return vessel

    def register_hooks(self, employee_id: str, hooks: dict[str, Callable]) -> None:
        """Register lifecycle hooks (pre_task, post_task) for an employee."""
        self._hooks[employee_id] = hooks

    def unregister(self, employee_id: str) -> None:
        self.executors.pop(employee_id, None)
        self.vessels.pop(employee_id, None)
        self.configs.pop(employee_id, None)
        self._hooks.pop(employee_id, None)

    def get_handle(self, employee_id: str) -> Vessel | None:
        return self.vessels.get(employee_id)

    # ------------------------------------------------------------------
    # Task dispatch (public API)
    # ------------------------------------------------------------------

    def push_task(
        self,
        employee_id: str,
        description: str,
        project_id: str = "",
        project_dir: str = "",
        node_id: str = "",
        tree_path: str = "",
    ) -> str:
        """Push a task for an employee. Returns node_id.

        The TaskNode should already exist in the tree (created by tree_tools
        dispatch_child or routes.py). This method just schedules it.
        """
        if node_id and tree_path:
            self.schedule_node(employee_id, node_id, tree_path)
        self._schedule_next(employee_id)
        return node_id

    # ------------------------------------------------------------------
    # Scheduling — on-demand, no idle polling
    # ------------------------------------------------------------------

    def _schedule_next(self, employee_id: str) -> None:
        """If no task is running for this employee, start the next scheduled one."""
        if employee_id in self._running_tasks:
            return
        entry = self.get_next_scheduled(employee_id)
        if not entry:
            # Also check deferred schedule
            if employee_id in self._deferred_schedule:
                self._deferred_schedule.discard(employee_id)
            self._set_employee_status(employee_id, STATUS_IDLE)
            return
        try:
            loop = asyncio.get_running_loop()
            self._running_tasks[employee_id] = loop.create_task(
                self._run_task(employee_id, entry)
            )
        except RuntimeError:
            if self._event_loop and not self._event_loop.is_closed():
                self._event_loop.call_soon_threadsafe(self._create_run_task, employee_id, entry)
                logger.info("Scheduled deferred task for {} via call_soon_threadsafe", employee_id)
            else:
                self._deferred_schedule.add(employee_id)
                logger.warning("No event loop to schedule task for {}, deferred", employee_id)

    def _create_run_task(self, employee_id: str, entry: ScheduleEntry) -> None:
        """Create an asyncio.Task for _run_task. Must be called from the event loop thread."""
        if employee_id in self._running_tasks:
            return
        loop = asyncio.get_running_loop()
        self._running_tasks[employee_id] = loop.create_task(
            self._run_task(employee_id, entry)
        )

    def drain_pending(self) -> None:
        """Schedule any pending tasks that were deferred (no event loop at push time).

        Called by start_all_loops() and can be called manually to unstick tasks.
        """
        # Stash the event loop for future deferred scheduling
        try:
            self._event_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("drain_pending called without a running event loop")
        # Drain deferred set
        deferred = list(self._deferred_schedule)
        self._deferred_schedule.clear()
        for emp_id in deferred:
            self._schedule_next(emp_id)
        # Also scan all scheduled entries for any orphaned pending tasks
        for emp_id in list(self._schedule.keys()):
            if emp_id not in self._running_tasks and self.get_next_scheduled(emp_id):
                self._schedule_next(emp_id)

    def is_idle(self, exclude: str = "") -> bool:
        """Return True if no tasks (employee or system) are running.

        Args:
            exclude: Employee ID to exclude from the check (used when called
                     from within that employee's _execute_task, where the
                     employee hasn't been popped from _running_tasks yet).
        """
        has_system = len(self._system_tasks) > 0
        if not exclude:
            return len(self._running_tasks) == 0 and not has_system
        return all(k == exclude for k in self._running_tasks) and not has_system

    def restore_persisted_tasks(self) -> int:
        """Restore tasks from tree files on disk via recover_schedule_from_trees.

        Returns the number of nodes scheduled.
        """
        from onemancompany.core.config import PROJECTS_DIR
        from onemancompany.core.task_persistence import recover_schedule_from_trees

        recover_schedule_from_trees(self, PROJECTS_DIR, EMPLOYEES_DIR)
        total = sum(len(entries) for entries in self._schedule.values())
        if total:
            logger.info("Restored {} scheduled node(s) from trees", total)
        self._restart_holding_pollers()
        return total

    def _restart_holding_pollers(self) -> int:
        """Restart watchdog crons for all HOLDING nodes in scheduled trees."""
        from onemancompany.core.task_tree import get_tree

        count = 0
        for emp_id, entries in self._schedule.items():
            for entry in entries:
                try:
                    tree = get_tree(entry.tree_path)
                    node = tree.get_node(entry.node_id)
                    if node and node.status == TaskPhase.HOLDING.value:
                        load_dir = node.project_dir or str(Path(entry.tree_path).parent)
                        node.load_content(load_dir)
                        meta = _parse_holding_metadata(node.result or "")
                        if meta:
                            self._setup_holding_watchdog_by_id(emp_id, entry.node_id, node.created_at, meta)
                            count += 1
                except Exception as e:
                    logger.warning("Failed to check holding status for node {}: {}", entry.node_id, e)
        if count:
            logger.info("Restarted {} holding watchdog(s)", count)
        return count

    def abort_project(self, project_id: str) -> int:
        """Cancel all tasks for a project. Returns count cancelled."""
        from onemancompany.core.task_tree import get_tree, save_tree_async

        count = 0
        for emp_id, entries in list(self._schedule.items()):
            for entry in list(entries):
                try:
                    tree = get_tree(entry.tree_path)
                    node = tree.get_node(entry.node_id)
                    if not node or node.project_id != project_id:
                        continue
                    if node.status in (TaskPhase.PENDING.value, TaskPhase.PROCESSING.value, TaskPhase.HOLDING.value):
                        # Force status — may not follow normal transitions
                        node.status = TaskPhase.CANCELLED.value
                        node.completed_at = datetime.now().isoformat()
                        node.result = "Cancelled by CEO"
                        save_tree_async(entry.tree_path)
                        self._log_node(emp_id, entry.node_id, "cancelled", "Task aborted by CEO")
                        self._publish_node_update(emp_id, node)
                        self.unschedule(emp_id, entry.node_id)
                        count += 1

                        # Stop associated crons
                        from onemancompany.core.automation import stop_cron as _stop_cron
                        for cron_prefix in (f"reply_{entry.node_id}", f"holding_{entry.node_id}"):
                            try:
                                _stop_cron(emp_id, cron_prefix)
                            except Exception as exc:
                                logger.debug("Could not stop cron {}/{}: {}", emp_id, cron_prefix, exc)
                except Exception as e:
                    logger.error("Failed to cancel node {} for project {}: {}", entry.node_id, project_id, e)

            # Cancel running asyncio.Task if it's working on this project
            if emp_id in self._running_tasks:
                running = self._running_tasks[emp_id]
                if not running.done():
                    running.cancel()
                    logger.info("Cancelled running asyncio.Task for {} (project {})", emp_id, project_id)

        return count

    def abort_employee(self, employee_id: str) -> int:
        """Cancel all tasks for an employee. Returns count cancelled."""
        from onemancompany.core.task_tree import get_tree, save_tree_async
        from onemancompany.core.automation import stop_all_crons_for_employee

        count = 0
        _cancelable = {TaskPhase.PENDING.value, TaskPhase.PROCESSING.value, TaskPhase.HOLDING.value}

        # 1. Clear schedule and cancel nodes
        entries = list(self._schedule.get(employee_id, []))
        self._schedule[employee_id] = []

        # 2. Clear deferred schedule
        self._deferred_schedule.discard(employee_id)

        # 3. Cancel running asyncio.Task
        running = self._running_tasks.pop(employee_id, None)
        if running and not running.done():
            running.cancel()
            logger.info("Cancelled running asyncio.Task for {}", employee_id)

        # 4. Cancel non-terminal nodes in trees
        seen_trees: set[str] = set()
        for entry in entries:
            try:
                tree = get_tree(entry.tree_path)
                node = tree.get_node(entry.node_id)
                if node and node.status in _cancelable:
                    # Force status — may not follow normal transitions
                    node.status = TaskPhase.CANCELLED.value
                    node.completed_at = datetime.now().isoformat()
                    node.result = f"Cancelled: employee {employee_id} aborted"
                    count += 1
                    self._publish_node_update(employee_id, node)
                seen_trees.add(entry.tree_path)
            except Exception as e:
                logger.error("Failed to cancel node {} for {}: {}", entry.node_id, employee_id, e)

        for tp in seen_trees:
            save_tree_async(tp)

        # 5. Stop crons
        stop_all_crons_for_employee(employee_id)

        # 6. Reset status
        if employee_id in company_state.employees:
            company_state.employees[employee_id].status = STATUS_IDLE
            company_state.employees[employee_id].current_task = None

        return count

    async def abort_all(self) -> int:
        """Cancel all tasks for all employees. Returns total count cancelled."""
        from onemancompany.core.automation import stop_all_automations
        from onemancompany.core.claude_session import stop_all_daemons

        total = 0
        for emp_id in list(self._schedule.keys()):
            total += self.abort_employee(emp_id)

        # Also abort employees with running tasks but empty schedules
        for emp_id in list(self._running_tasks.keys()):
            total += self.abort_employee(emp_id)

        await stop_all_automations()
        await stop_all_daemons()

        return total

    async def _run_task(self, employee_id: str, entry: ScheduleEntry) -> None:
        """Execute a task, then schedule the next one."""
        try:
            await self._execute_task(employee_id, entry)
        finally:
            self._running_tasks.pop(employee_id, None)
            self._schedule_next(employee_id)
            if self._restart_pending and self.is_idle():
                logger.info("All tasks complete (post-schedule) — triggering deferred graceful restart")
                await self._trigger_graceful_restart()

    # ------------------------------------------------------------------
    # Task execution — core logic
    # ------------------------------------------------------------------

    async def _execute_task(self, employee_id: str, entry: ScheduleEntry) -> None:
        from onemancompany.core.task_tree import get_tree, save_tree_async

        tree = get_tree(entry.tree_path)
        node = tree.get_node(entry.node_id)
        if not node:
            logger.error("Node {} not found in tree {}", entry.node_id, entry.tree_path)
            self.unschedule(employee_id, entry.node_id)
            return

        role = self._get_role(employee_id)
        vessel = self.vessels.get(employee_id)
        cfg = self.configs.get(employee_id)
        max_retries = cfg.limits.max_retries if cfg else MAX_RETRIES
        retry_delays = cfg.limits.retry_delays if cfg else RETRY_DELAYS

        # 1. Mark PROCESSING
        node.set_status(TaskPhase.PROCESSING)
        save_tree_async(entry.tree_path)
        self._set_employee_status(employee_id, STATUS_WORKING)
        self._log_node(employee_id, entry.node_id, "start", f"Starting task: {node.description_preview}")
        self._publish_node_update(employee_id, node)

        await _store.save_employee_runtime(employee_id, current_task_summary=node.description_preview[:100])

        # 2. Set contextvars
        loop_token = _current_vessel.set(vessel)
        task_token = _current_task_id.set(entry.node_id)

        project_id = node.project_id
        project_dir = node.project_dir
        agent_error = False
        try:
            # 4. Build task context with injections
            _effective_dir = project_dir or str(Path(entry.tree_path).parent)
            node.load_content(_effective_dir)

            # Tree context includes current node description + ancestors + children
            tree_ctx = _build_tree_context(tree, node, _effective_dir)
            task_with_ctx = tree_ctx if tree_ctx else node.description

            # Inject dependency context if this node has depends_on
            dep_ctx = _build_dependency_context(tree, node, _effective_dir)
            if dep_ctx:
                task_with_ctx = dep_ctx + task_with_ctx

            if project_id:
                identity = self._build_project_identity(project_id)
                if identity:
                    task_with_ctx = f"{identity}\n\n{task_with_ctx}"

            if project_dir:
                task_with_ctx += f"\n\n[Project workspace: {project_dir} — save all outputs here]"

            if project_id:
                proj_ctx = self._get_project_history_context(project_id)
                if proj_ctx:
                    task_with_ctx = f"{task_with_ctx}\n\n{proj_ctx}"

            if project_id:
                workflow_ctx = self._get_project_workflow_context(employee_id, project_id)
                if workflow_ctx:
                    task_with_ctx = f"{task_with_ctx}\n\n{workflow_ctx}"

            inject_progress = cfg.context.inject_progress_log if cfg else True
            if inject_progress:
                progress = _load_progress(employee_id)
                if progress:
                    task_with_ctx += f"\n\n[Previous Work Learnings]\n{progress}"

            def _on_log(log_type: str, content: str) -> None:
                self._log_node(employee_id, entry.node_id, log_type, content)

            # 5. Execute via launcher with retry
            executor = self.executors.get(employee_id)
            if not executor:
                raise RuntimeError(f"No executor registered for employee {employee_id}")

            context = TaskContext(
                project_id=project_id,
                work_dir=project_dir,
                employee_id=employee_id,
                task_id=entry.node_id,
            )

            # Pre-task hook
            hooks = self._hooks.get(employee_id, {})
            pre_task = hooks.get("pre_task")
            if pre_task:
                try:
                    result = pre_task(task_with_ctx, context)
                    if isinstance(result, str):
                        task_with_ctx = result
                except Exception:
                    logger.warning("Pre-task hook failed for %s", employee_id)

            # Set timeout from node
            if node.timeout_seconds:
                from onemancompany.core.subprocess_executor import SubprocessExecutor
                if isinstance(executor, SubprocessExecutor):
                    executor.timeout_seconds = node.timeout_seconds

            launch_result: LaunchResult | None = None
            last_err: Exception | None = None
            for attempt in range(max_retries):
                try:
                    launch_result = await executor.execute(task_with_ctx, context, on_log=_on_log)
                    last_err = None
                    break
                except GraphRecursionError as rec_err:
                    last_err = rec_err
                    self._log_node(employee_id, entry.node_id, "error", f"Agent hit recursion limit: {rec_err!s}")
                    break
                except Exception as run_err:
                    last_err = run_err
                    if attempt < max_retries - 1:
                        delay = retry_delays[attempt] if attempt < len(retry_delays) else retry_delays[-1]
                        self._log_node(employee_id, entry.node_id, "retry", f"Attempt {attempt + 1} failed: {run_err!s} — retrying in {delay}s")
                        await asyncio.sleep(delay)

            if last_err is not None:
                raise last_err

            node.result = launch_result.output if launch_result else ""
            self._log_node(employee_id, entry.node_id, "result", node.result or "")

            # Record token usage to node
            if launch_result and launch_result.total_tokens > 0:
                node.model_used = launch_result.model_used
                node.input_tokens += launch_result.input_tokens
                node.output_tokens += launch_result.output_tokens
                from onemancompany.core.model_costs import get_model_cost
                costs = get_model_cost(node.model_used)
                node.cost_usd = (
                    node.input_tokens * costs["input"] + node.output_tokens * costs["output"]
                ) / 1_000_000

        except asyncio.CancelledError:
            agent_error = True
            node.status = TaskPhase.CANCELLED.value
            node.result = node.result or "Cancelled by CEO"
            if not node.completed_at:
                node.completed_at = datetime.now().isoformat()
            self._log_node(employee_id, entry.node_id, "cancelled", "Task cancelled")
        except TimeoutError as te:
            agent_error = True
            node.set_status(TaskPhase.FAILED)
            node.result = str(te)
            if not node.completed_at:
                node.completed_at = datetime.now().isoformat()
            self._log_node(employee_id, entry.node_id, "timeout", f"Task timed out: {te!s}")
        except Exception as e:
            agent_error = True
            node.set_status(TaskPhase.FAILED)
            node.result = f"Error: {e!s}"
            self._log_node(employee_id, entry.node_id, "error", f"Task failed: {e!s}")
            traceback.print_exc()
        finally:
            _current_vessel.reset(loop_token)
            _current_task_id.reset(task_token)

        # 8. Mark completed (or HOLDING)
        # (No stale-read issue: tree is in-memory cache, all tools modify the same object)
        if node.status not in (TaskPhase.FAILED.value, TaskPhase.CANCELLED.value):
            holding_meta = _parse_holding_metadata(node.result or "")
            if holding_meta is not None:
                node.set_status(TaskPhase.HOLDING)
                save_tree_async(entry.tree_path)
                self._setup_holding_watchdog_by_id(employee_id, entry.node_id, node.created_at, holding_meta)
                self._log_node(employee_id, entry.node_id, "holding", f"Task entered HOLDING: {holding_meta}")
            else:
                node.set_status(TaskPhase.COMPLETED)
                # System nodes auto-skip review: they don't need to be reviewed themselves
                if node.node_type in ("review", "ceo_request"):
                    node.set_status(TaskPhase.ACCEPTED)
                    node.set_status(TaskPhase.FINISHED)
                save_tree_async(entry.tree_path)

        if node.status != TaskPhase.HOLDING.value:
            if not node.completed_at:
                node.completed_at = datetime.now().isoformat()
            save_tree_async(entry.tree_path)
            self._log_node(employee_id, entry.node_id, "end", f"Task {node.status}")
            self._publish_node_update(employee_id, node)

            # Record to history + progress
            if node.status in (TaskPhase.COMPLETED.value, TaskPhase.ACCEPTED.value, TaskPhase.FINISHED.value):
                self._append_history_from_node(employee_id, node)
                summary = (node.result or "")[:200]
                _append_progress(employee_id, f"Completed: {node.description[:100]} → {summary}")

            # Post-task hook
            post_task_hook = self._hooks.get(employee_id, {}).get("post_task")
            if post_task_hook:
                try:
                    post_task_hook(node, node.result or "")
                except Exception:
                    logger.warning("Post-task hook failed for %s", employee_id)

            await _store.save_employee_runtime(employee_id, current_task_summary="")

            # Task tree callback
            if project_dir:
                try:
                    await self._on_child_complete(employee_id, entry, project_id=project_id)
                except Exception as e:
                    logger.error("Task tree callback failed for {}: {}", employee_id, e)

                # Trigger dependency resolution for nodes waiting on this one
                tree = get_tree(entry.tree_path)
                _trigger_dep_resolution(project_dir, tree, node)

            # Post-task cleanup (cost, resolution, etc.)
            if project_id:
                if node.input_tokens + node.output_tokens > 0:
                    from onemancompany.core.project_archive import record_project_cost
                    record_project_cost(project_id, employee_id, node.model_used, node.input_tokens, node.output_tokens, node.cost_usd)
                if not project_id.startswith("_auto_") and node.result:
                    from onemancompany.core.project_archive import append_action
                    summary = node.result[:MAX_SUMMARY_LEN]
                    append_action(project_id, employee_id, f"{role} task completed", summary)

            # Unschedule completed node
            self.unschedule(employee_id, entry.node_id)
        else:
            self._publish_node_update(employee_id, node)

    # ------------------------------------------------------------------
    # HOLDING helpers
    # ------------------------------------------------------------------

    # _setup_holding_watchdog removed — use _setup_holding_watchdog_by_id directly

    def _setup_holding_watchdog_by_id(
        self, employee_id: str, task_id: str, created_at: str, holding_meta: dict,
    ) -> None:
        """Start a watchdog cron for a HOLDING task, by task/node ID."""
        from onemancompany.core.automation import start_cron as _start_cron

        thread_id = holding_meta.get("thread_id", "")
        if thread_id:
            # Specific Gmail reply poller
            interval = holding_meta.get("interval", "1m")
            cron_name = f"reply_{task_id}"
            task_desc = f"[reply_poll] Check Gmail thread {thread_id} for task {task_id}"
        else:
            # Generic holding watchdog — employee checks if condition is resolved
            interval = holding_meta.get("interval", "5m")
            meta_summary = ", ".join(f"{k}={v}" for k, v in holding_meta.items() if k != "interval")
            cron_name = f"holding_{task_id}"
            holding_since = created_at or datetime.now().isoformat()
            task_desc = (
                f"[holding_check] You have a HOLDING task (task_id={task_id}) waiting for an external condition to be met."
                f" Metadata: {meta_summary}. Waiting since: {holding_since}."
                f"\n\nPlease follow this procedure:"
                f"\n1. Check if the condition has been met. If completed, call resume_held_task(task_id='{task_id}', result='Condition met: <specific result>')."
                f"\n2. If waiting for more than 10 minutes but less than 30 minutes, try a different approach (resend request, use alternative contact, try alternative solutions, etc.)."
                f"\n3. If waiting for more than 30 minutes, escalate to supervisor (use dispatch_child or describe the situation in the result),"
                f" and call resume_held_task(task_id='{task_id}', result='Timeout escalation: <reason for waiting and methods already tried>') to end the wait."
                f"\n4. If not yet timed out and condition not met, no action needed."
            )

        result = _start_cron(employee_id, cron_name, interval, task_desc)
        if result.get("status") != "ok":
            logger.error("Failed to start holding watchdog for {}: {}", task_id, result)

    async def resume_held_task(self, employee_id: str, task_id: str, result: str) -> bool:
        """Resume a HOLDING task with the provided result.

        Transitions HOLDING → COMPLETE, stops the reply poller cron,
        saves to tree, and triggers task tree callbacks.

        Returns True if task was found and resumed, False otherwise.
        """
        from onemancompany.core.task_tree import get_tree, save_tree_async

        # Search schedule for the node
        for entry in self._schedule.get(employee_id, []):
            if entry.node_id == task_id:
                tree = get_tree(entry.tree_path)
                node = tree.get_node(task_id)
                if not node or node.status != TaskPhase.HOLDING.value:
                    return False

                stop_cron(employee_id, f"reply_{task_id}")
                stop_cron(employee_id, f"holding_{task_id}")

                node.load_content(Path(entry.tree_path).parent)
                node.result = result
                node.set_status(TaskPhase.COMPLETED)
                node.completed_at = datetime.now().isoformat()

                # System nodes auto-skip review
                if node.node_type in ("review", "ceo_request"):
                    node.set_status(TaskPhase.ACCEPTED)
                    node.set_status(TaskPhase.FINISHED)

                save_tree_async(entry.tree_path)

                final_status = node.status
                self._log_node(employee_id, task_id, "resumed", f"HOLDING → {final_status} with result: {result[:200]}")
                self._publish_node_update(employee_id, node)

                self._append_history_from_node(employee_id, node)
                summary = (node.result or "")[:200]
                _append_progress(employee_id, f"Completed (resumed): {node.description_preview[:100]} → {summary}")

                if node.project_dir:
                    try:
                        await self._on_child_complete(employee_id, entry, project_id=node.project_id)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.error("Task tree callback failed for {}: {}", employee_id, e)

                    # Trigger dependency resolution for nodes waiting on this one
                    tree = get_tree(entry.tree_path)
                    _trigger_dep_resolution(node.project_dir, tree, node)

                self.unschedule(employee_id, task_id)
                self._schedule_next(employee_id)
                return True

        return False

    # ------------------------------------------------------------------
    # Task history management
    # ------------------------------------------------------------------

    def _append_history_from_node(self, employee_id: str, node) -> None:
        """Append task history from a TaskNode."""
        history = self.task_histories.setdefault(employee_id, [])
        history.append({
            "task": node.description[:200],
            "result": (node.result or "")[:RESULT_SNIPPET_LEN],
            "completed_at": node.completed_at or datetime.now().isoformat(),
        })
        _save_task_history(employee_id, history, self._history_summaries.get(employee_id, ""))
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._maybe_compress_history(employee_id))
        except RuntimeError:
            logger.debug("No event loop for history compression of %s", employee_id)

    async def _maybe_compress_history(self, employee_id: str) -> None:
        history = self.task_histories.get(employee_id, [])
        summary = self._history_summaries.get(employee_id, "")
        total = sum(len(h["task"]) + len(h["result"]) for h in history) + len(summary)
        if total <= MAX_HISTORY_CHARS or len(history) <= MAX_HISTORY_ENTRIES:
            return

        split = len(history) // 2
        old_entries = history[:split]
        self.task_histories[employee_id] = history[split:]

        old_text = "\n".join(
            f"- [{h['completed_at'][:10]}] {h['task']}: {h['result']}"
            for h in old_entries
        )
        if summary:
            old_text = f"Previous summary:\n{summary}\n\nNew entries:\n{old_text}"

        try:
            from onemancompany.agents.base import tracked_ainvoke
            llm = make_llm(employee_id)
            resp = await tracked_ainvoke(llm,
                f"Summarize this employee's completed work into a concise paragraph (max 200 words). "
                f"Focus on key decisions, findings, and outputs:\n\n{old_text}",
                category="history_compress", employee_id=employee_id)
            self._history_summaries[employee_id] = resp.content.strip()[:800]
        except Exception:
            self._history_summaries[employee_id] = (summary + "\n" + old_text)[:800]

        # Write-through compressed history to disk
        _save_task_history(
            employee_id,
            self.task_histories[employee_id],
            self._history_summaries.get(employee_id, ""),
        )

    def get_history_context(self, employee_id: str) -> str:
        history = self.task_histories.get(employee_id, [])
        summary = self._history_summaries.get(employee_id, "")
        if not history and not summary:
            return ""
        parts = ["\n\n## Your Recent Work History:"]
        if summary:
            parts.append(f"Earlier work summary: {summary}")
        for h in history:
            parts.append(f"- [{h['completed_at'][:10]}] Task: {h['task']}\n  Result: {h['result']}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Project history context
    # ------------------------------------------------------------------

    _CTX_MAX_ITERATIONS = 5
    _CTX_MAX_OUTPUT_CHARS = 2000
    _CTX_MAX_TIMELINE_ENTRIES = 15
    _CTX_TIMELINE_DETAIL_CHARS = 300

    def _build_project_identity(self, project_id: str) -> str:
        """Build a prominent project identity header for task context."""
        from onemancompany.core.project_archive import (
            _is_iteration, _find_project_for_iteration,
            _split_qualified_iter, load_named_project,
        )

        parts: list[str] = []
        if _is_iteration(project_id):
            slug = _find_project_for_iteration(project_id)
            if slug:
                proj = load_named_project(slug)
                proj_name = proj.get("name", slug) if proj else slug
                _, bare_iter = _split_qualified_iter(project_id)
                parts.append(f"⚙ Current project: {proj_name} ({bare_iter})")
                parts.append(f"  Project ID: {project_id}")
        else:
            proj = load_named_project(project_id)
            if proj:
                proj_name = proj.get("name", project_id)
                parts.append(f"⚙ Current project: {proj_name}")
                parts.append(f"  Project ID: {project_id}")

        if not parts:
            return ""
        return "\n".join(parts)
    _CTX_TASK_DESC_CHARS = 200
    _CTX_MAX_WORKSPACE_FILES = 30
    _CTX_MAX_CRITERIA = 5

    def _get_project_history_context(self, project_id: str) -> str:
        from onemancompany.core.project_archive import (
            _is_iteration, _find_project_for_iteration,
            _split_qualified_iter,
            load_named_project, load_iteration, list_project_files,
        )

        slug = project_id
        current_iter = ""
        if _is_iteration(project_id):
            found = _find_project_for_iteration(project_id)
            if not found:
                return ""
            slug = found
            _, bare_iter = _split_qualified_iter(project_id)
            current_iter = bare_iter

        proj = load_named_project(slug)
        if not proj:
            return ""

        iterations = proj.get("iterations", [])
        prev_iters = [i for i in iterations if i != current_iter]
        files = list_project_files(slug)
        if not prev_iters and not files:
            return ""

        proj_name = proj.get("name", slug)
        proj_status = proj.get("status", "active")

        total_budget = 0.0
        total_spent = 0.0
        for it_id in iterations:
            it = load_iteration(slug, it_id)
            if not it:
                continue
            cost = it.get("cost", {})
            total_budget = max(total_budget, cost.get("budget_estimate_usd", 0.0))
            total_spent += cost.get("actual_cost_usd", 0.0)

        parts: list[str] = []

        parts.append("═══ Project Context ═══")
        parts.append(f"Project: {proj_name} | Status: {proj_status}")
        if total_budget > 0:
            pct = (total_spent / total_budget * 100) if total_budget else 0
            parts.append(f"Budget: ${total_budget:.2f} | Spent: ${total_spent:.4f} ({pct:.1f}%)")
        elif total_spent > 0:
            parts.append(f"Spent: ${total_spent:.4f}")

        for it_id in prev_iters[-self._CTX_MAX_ITERATIONS:]:
            it = load_iteration(slug, it_id)
            if not it:
                continue

            status = it.get("status", "unknown")
            parts.append(f"\n── {it_id} [{status}] ──")

            task_desc = (it.get("task") or "")[:self._CTX_TASK_DESC_CHARS]
            if task_desc:
                parts.append(f"Task: {task_desc}")

            criteria = it.get("acceptance_criteria", [])
            if criteria:
                parts.append("Criteria:")
                for i, c in enumerate(criteria[:self._CTX_MAX_CRITERIA], 1):
                    parts.append(f"  {i}. {c}")

            timeline = it.get("timeline", [])
            if timeline:
                total_entries = len(timeline)
                if total_entries <= self._CTX_MAX_TIMELINE_ENTRIES:
                    shown = timeline
                    omitted = 0
                else:
                    shown = timeline[:10] + timeline[-5:]
                    omitted = total_entries - 15

                parts.append(f"Log ({total_entries} entries):")
                for j, entry in enumerate(shown):
                    ts = entry.get("time", "")
                    time_short = ts[11:19] if len(ts) >= 19 else ts[:8]
                    emp_entry = entry.get("employee_id", "?")
                    action = entry.get("action", "")
                    detail = (entry.get("detail") or "")[:self._CTX_TIMELINE_DETAIL_CHARS]
                    line = f"  [{time_short}] {emp_entry} — {action}"
                    if detail:
                        line += f": {detail}"
                    parts.append(line)
                    if j == 9 and omitted > 0:
                        parts.append(f"  ... ({omitted} entries omitted) ...")

            output = (it.get("output") or "")[:self._CTX_MAX_OUTPUT_CHARS]
            if output:
                parts.append(f"Output:\n{output}")

            cost = it.get("cost", {})
            iter_cost = cost.get("actual_cost_usd", 0.0)
            iter_budget = cost.get("budget_estimate_usd", 0.0)
            tokens = cost.get("token_usage", {})
            tok_in = tokens.get("input", 0)
            tok_out = tokens.get("output", 0)
            if iter_cost > 0 or tok_in > 0:
                cost_parts = [f"Cost: ${iter_cost:.4f}"]
                if iter_budget > 0:
                    cost_parts.append(f"Budget: ${iter_budget:.2f}")
                if tok_in or tok_out:
                    cost_parts.append(f"Tokens: {tok_in:,} in / {tok_out:,} out")
                parts.append(" | ".join(cost_parts))

            parts.append("────────────────────────")

        if files:
            shown_files = files[:self._CTX_MAX_WORKSPACE_FILES]
            parts.append(f"\nWorkspace files ({len(files)}):")
            for f in shown_files:
                parts.append(f"  {f}")
            if len(files) > self._CTX_MAX_WORKSPACE_FILES:
                parts.append(f"  ... and {len(files) - self._CTX_MAX_WORKSPACE_FILES} more")
            from onemancompany.core.project_archive import get_project_workspace
            ws_path = get_project_workspace(slug)
            parts.append(f'\nUse read("{ws_path}/{{filename}}") to read file contents.')

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Workflow context injection
    # ------------------------------------------------------------------

    def _get_project_workflow_context(self, employee_id: str, project_id_or_task=None) -> str:
        from onemancompany.core.config import load_workflows, FOUNDING_LEVEL
        from onemancompany.core.workflow_engine import parse_workflow

        emp_data = _store.load_employee(employee_id) or {}
        role = emp_data.get("role", "Employee").upper()
        is_manager = role in ("COO", "CSO", "EA", "HR")

        if is_manager and role in ("COO", "CSO"):
            return (
                "[Manager Execution Guide]\n"
                "As a manager receiving a project task:\n"
                "  1. list_colleagues() to understand all available team members and their skills.\n"
                "  2. Leverage the team fully: PM handles project management/research/docs, Engineer handles development, each to their strengths.\n"
                "  3. dispatch_child() to the most suitable employee with clear instructions and acceptance criteria.\n"
                "  4. For complex projects, use dispatch_child() to distribute multiple subtasks (parallel execution).\n"
                "  5. If no suitable employee exists, dispatch to HR for hiring.\n"
                "  6. You can bring people into the project at any stage (not just initial assignment), including review, remediation, diagnosis, etc.\n"
                "  7. Only do the work yourself when nobody else can.\n"
                "Do NOT loop or re-analyze — dispatch quickly and move on."
            )

        workflows = load_workflows()
        workflow_doc = workflows.get("project_intake_workflow", "")
        verification_instructions = ""

        if workflow_doc:
            wf = parse_workflow("project_intake_workflow", workflow_doc)
            for step in wf.steps:
                if "Execution" in step.title or "Tracking" in step.title:
                    for inst in step.instructions:
                        if any(kw in inst.lower() for kw in [
                            "verification", "verify", "build and run",
                            "test", "do not report", "validate", "acceptance",
                        ]):
                            verification_instructions += f"  - {inst}\n"
                    break

        if not verification_instructions:
            from onemancompany.tools.sandbox import is_sandbox_enabled as _sb_enabled
            if _sb_enabled():
                verification_instructions = (
                    "  - For code/software: Use sandbox_execute_code to run it once. Fix errors if any.\n"
                    "  - For documents/reports: Proofread your output once before submitting.\n"
                )
            else:
                verification_instructions = (
                    "  - For code/software: Review your code carefully for errors.\n"
                    "  - For documents/reports: Proofread your output once before submitting.\n"
                )

        return (
            "[Self-Verification Before Completion]\n"
            "After producing your deliverable, verify once:\n"
            f"{verification_instructions}"
            "Save all outputs to the project workspace using write().\n"
            "Include a brief verification note in your result.\n"
            "Do NOT re-read files you already read. Do NOT loop — verify once, then finish."
        )

    # ------------------------------------------------------------------
    # Task tree child-completion callback
    # ------------------------------------------------------------------

    async def _on_child_complete(self, employee_id: str, entry: ScheduleEntry, project_id: str = "") -> None:
        """Update TaskTree node when a task completes, wake parent if all siblings done."""
        from onemancompany.core.task_tree import get_tree_lock
        lock = get_tree_lock(entry.tree_path)
        with lock:
            await self._on_child_complete_inner(employee_id, entry, project_id)

    async def _on_child_complete_inner(self, employee_id: str, entry: ScheduleEntry, project_id: str = "") -> None:
        """Inner implementation of _on_child_complete, called under tree lock.

        TaskNode is the SSOT — status/result/tokens are already on the node
        (set by _execute_task). This method only needs to propagate upward.
        """
        from onemancompany.core.task_tree import get_tree, save_tree_async

        tree_file = Path(entry.tree_path)
        if not tree_file.exists():
            logger.debug("Tree file {} not found, skipping child complete", entry.tree_path)
            return
        tree = get_tree(tree_file)
        node = tree.get_node(entry.node_id)
        if not node:
            logger.debug("Node {} not found in tree {}", entry.node_id, entry.tree_path)
            return

        # Trigger 3: root node failed → project failed
        is_root = not node.parent_id
        task_failed = node.status == TaskPhase.FAILED.value
        if is_root and task_failed:
            await _store.save_project_status(project_id, "failed")
            return

        # Root node or child of CEO node completed — request CEO confirmation
        parent_node = tree.get_node(node.parent_id) if node.parent_id else None
        is_child_of_ceo = parent_node and parent_node.is_ceo_node if parent_node else False

        if is_root or is_child_of_ceo:
            logger.info("EA node {} completed (root or child of CEO) — requesting CEO confirmation", entry.node_id)
            if is_child_of_ceo:
                if parent_node.status != TaskPhase.COMPLETED.value:
                    if parent_node.status == TaskPhase.PENDING.value:
                        parent_node.set_status(TaskPhase.PROCESSING)
                    parent_node.set_status(TaskPhase.COMPLETED)
                save_tree_async(entry.tree_path)
            await self._request_ceo_confirmation(
                employee_id, node, tree, entry, project_id
            )
            return

        parent_node = tree.get_node(node.parent_id)
        if not parent_node:
            return

        # Skip if parent is already resolved (failed/cancelled/accepted/finished)
        if TaskPhase(parent_node.status) in RESOLVED:
            logger.debug("Parent {} is {} — skipping review spawn", parent_node.id, parent_node.status)
            return

        # Check all children of parent — are they all done executing?
        children = tree.get_active_children(parent_node.id)
        if not tree.all_children_done(parent_node.id):
            return

        # Skip if there's already a pending/processing review node for this parent
        # (prevents infinite review loop when review node itself completes)
        for child in children:
            if child.node_type == "review":
                if child.status in (TaskPhase.PENDING.value, TaskPhase.PROCESSING.value):
                    logger.debug("Review node {} already active for parent {} — skipping", child.id, parent_node.id)
                    return

        # If all children that need review are already accepted, auto-complete the parent
        non_review_children = [c for c in children if c.node_type != "review"]
        if non_review_children and all(c.status == TaskPhase.ACCEPTED.value for c in non_review_children):
            logger.info("All non-review children of {} are accepted — auto-completing parent", parent_node.id)
            if parent_node.status == TaskPhase.COMPLETED.value:
                return  # Already completed (e.g. from a previous review cycle)
            if parent_node.status == TaskPhase.HOLDING.value:
                parent_node.set_status(TaskPhase.PROCESSING)
            parent_node.set_status(TaskPhase.COMPLETED)
            parent_node.result = "All child tasks accepted."
            save_tree_async(entry.tree_path)
            self._publish_node_update(parent_node.employee_id, parent_node)
            return

        # Build review prompt for parent employee
        project_dir = node.project_dir or str(Path(entry.tree_path).parent)
        needs_review = []
        already_accepted = []
        for child in children:
            if child.is_ceo_node:
                continue
            if child.status == "accepted":
                already_accepted.append(child)
            else:
                needs_review.append(child)

        lines = []
        if already_accepted and needs_review:
            lines.append("The following subtasks have passed review and do not need re-review:")
            for child in already_accepted:
                lines.append(f"  \u2713 ({child.employee_id}): {child.description_preview[:80]}")
            lines.append("")

        if needs_review:
            lines.append("The following subtasks need review:")
            lines.append("")
            for i, child in enumerate(needs_review, 1):
                child.load_content(project_dir)
                criteria_str = ", ".join(child.acceptance_criteria) if child.acceptance_criteria else "None"
                lines.append(f"Subtask {i} ({child.employee_id}): {child.description}")
                lines.append(f"  Acceptance criteria: {criteria_str}")
                lines.append(f"  Execution result: \"{child.result}\"")
                lines.append(f"  Status: {child.status}")
                if child.acceptance_result and not child.acceptance_result.get("passed"):
                    lines.append(f"  \u26a0 This task was previously rejected: {child.acceptance_result.get('notes', '')}")
                lines.append("")
        else:
            lines.append("All subtasks have passed review.")

        lines.append("Please call accept_child(node_id, notes) or reject_child(node_id, reason) for unreviewed subtasks.")
        lines.append("If additional tasks are needed, call dispatch_child().")
        lines.append("Once all are handled, your task will auto-complete and report upward.")

        review_prompt = "\n".join(lines)

        # --- Circuit breaker: check review round count ---
        from onemancompany.core.config import MAX_REVIEW_ROUNDS, CEO_ID
        review_count = sum(
            1 for c in children
            if c.node_type == "review" and c.employee_id == parent_node.employee_id
        )
        if review_count >= MAX_REVIEW_ROUNDS:
            logger.warning(
                "Review circuit breaker: {} rounds for parent {} — escalating to CEO",
                review_count, parent_node.id,
            )
            parent_node.set_status(TaskPhase.HOLDING)
            save_tree_async(entry.tree_path)

            # Build escalation summary
            last_notes = ""
            for sibling in reversed(children):
                if sibling.acceptance_result and not sibling.acceptance_result.get("passed"):
                    last_notes = sibling.acceptance_result.get("notes", "")
                    break

            escalation_desc = (
                f"Review deadlock: Task {parent_node.id} ({parent_node.description_preview}) "
                f"has gone through {review_count} review rounds without convergence.\n"
                f"Last round disagreement: {last_notes[:300]}\n"
                f"Please intervene: you can accept the current result, cancel the task, or provide specific guidance."
            )
            ceo_node = tree.add_child(
                parent_id=parent_node.id,
                employee_id=CEO_ID,
                description=escalation_desc,
                acceptance_criteria=[],
            )
            ceo_node.node_type = "ceo_request"
            ceo_node.project_id = project_id
            ceo_node.project_dir = project_dir
            save_tree_async(entry.tree_path)

            # Publish inbox event
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(event_bus.publish(CompanyEvent(
                    type="ceo_inbox_updated",
                    payload={"node_id": ceo_node.id, "description": escalation_desc},
                    agent="SYSTEM",
                )))
            except RuntimeError:
                logger.debug("No event loop for circuit breaker CEO escalation publish")
            return

        # Create a review node in the tree and schedule it
        review_node = tree.add_child(
            parent_id=parent_node.id,
            employee_id=parent_node.employee_id,
            description=review_prompt,
            acceptance_criteria=[],
        )
        review_node.node_type = "review"
        review_node.project_id = project_id
        review_node.project_dir = project_dir
        save_tree_async(entry.tree_path)

        self.schedule_node(parent_node.employee_id, review_node.id, entry.tree_path)
        logger.info("All children done for parent {} — scheduled review node to {}", parent_node.id, parent_node.employee_id)

        if parent_node.employee_id not in self._running_tasks:
            self._schedule_next(parent_node.employee_id)

    # ------------------------------------------------------------------
    # Dependency resolution — unlock dependents when a node becomes terminal
    # ------------------------------------------------------------------

    async def _resolve_dependencies(self, tree, completed_node, project_dir: str) -> None:
        """Check if completing this node unlocks any dependent tasks."""
        project_id = completed_node.project_id or tree.project_id
        tree_path = str(Path(project_dir) / "task_tree.yaml")
        from onemancompany.core.task_tree import get_tree_lock
        lock = get_tree_lock(tree_path)
        with lock:
            dependents = tree.find_dependents(completed_node.id)
            if not dependents:
                return

            dirty = False
            to_schedule: list[str] = []  # employee_ids to schedule
            cascade_cancelled: list = []  # nodes that were cascade-cancelled

            for dep_node in dependents:
                if dep_node.status != "pending":
                    continue

                if tree.has_failed_deps(dep_node.id):
                    # Check if the dep was cancelled — cascade cancel instead of blocking
                    cancelled_deps = [
                        d for d_id in dep_node.depends_on
                        if (d := tree.get_node(d_id)) and d.status == TaskPhase.CANCELLED.value
                    ]
                    if cancelled_deps:
                        # Cascade cancel: dep was cancelled, so this node should be too
                        dep_node.set_status(TaskPhase.CANCELLED)
                        dep_node.result = (
                            f"Cascade cancelled: dependency "
                            f"\"{cancelled_deps[0].description_preview[:80]}\" was cancelled"
                        )
                        dep_node.completed_at = datetime.now().isoformat()
                        dirty = True
                        cascade_cancelled.append(dep_node)
                        logger.info(
                            "Cascade-cancelled {} because dep {} was cancelled",
                            dep_node.id, cancelled_deps[0].id,
                        )
                        continue

                    dep_node.set_status(TaskPhase.BLOCKED)
                    dirty = True
                    # Notify parent about blocked task
                    parent = tree.get_node(dep_node.parent_id)
                    if parent:
                        msg = (
                            f"Task \"{dep_node.description_preview}\" is BLOCKED because dependency "
                            f"\"{completed_node.description_preview}\" failed. Please handle via "
                            f"reject_child (retry), unblock_child, or cancel_child."
                        )
                        notify_node = tree.add_child(
                            parent_id=parent.id,
                            employee_id=parent.employee_id,
                            description=msg,
                            acceptance_criteria=[],
                        )
                        notify_node.project_dir = project_dir
                        notify_node.project_id = project_id
                        dirty = True
                        self.schedule_node(parent.employee_id, notify_node.id, tree_path)
                        to_schedule.append(parent.employee_id)
                    continue

                if tree.all_deps_resolved(dep_node.id):
                    # Schedule the dependent node (dependency context injected at execution time)
                    dep_node.project_dir = project_dir
                    dirty = True
                    self.schedule_node(dep_node.employee_id, dep_node.id, tree_path)
                    to_schedule.append(dep_node.employee_id)

            if dirty:
                _save_project_tree(project_dir, tree)

            # Recursively resolve dependents of cascade-cancelled nodes
            for cancelled_node in cascade_cancelled:
                await self._resolve_dependencies(tree, cancelled_node, project_dir)

            # Check if all tree nodes are now terminal or blocked → project failed
            all_stuck = all(
                n.status in ("blocked", "failed", "cancelled", "accepted", "finished")
                for n in tree._nodes.values()
                if n.id != tree.root_id
            )
            if all_stuck and any(
                n.status in ("blocked", "failed") for n in tree._nodes.values()
            ):
                await _store.save_project_status(project_id, "failed")

            for emp_id in to_schedule:
                if emp_id not in self._running_tasks:
                    self._schedule_next(emp_id)

    async def _request_ceo_confirmation(
        self,
        employee_id: str,
        node,
        tree,
        entry: ScheduleEntry,
        project_id: str,
    ) -> None:
        """Publish project completion notification and proceed with cleanup.

        The old blocking wait for CEO response has been removed.
        CEO reviews project completions via the CEO Inbox (dispatch_child to CEO 00001).
        This now auto-approves and runs cleanup immediately.
        """
        # Build completion summary from all children (skip CEO info nodes)
        _pdir = node.project_dir or str(Path(entry.tree_path).parent)
        children = [c for c in tree.get_children(node.id) if not c.is_ceo_node]
        lines = [f"Project Completion Report — {node.description_preview[:100]}", ""]
        for i, child in enumerate(children, 1):
            status_icon = "✓" if child.status == "accepted" else "●"
            lines.append(f"{status_icon} Subtask {i} ({child.employee_id}): {child.description_preview[:80]}")
            child.load_content(_pdir)
            lines.append(f"  Result: {(child.result or 'None')[:200]}")
            lines.append("")
        summary = "\n".join(lines)

        payload = {
            "subject": f"Project Completion Confirmation: {node.description_preview[:60]}",
            "report": summary,
            "employee_id": employee_id,
            "project_id": project_id,
            "timestamp": datetime.now().isoformat(),
        }
        emp_data = _store.load_employee(employee_id)
        if emp_data:
            payload["employee_name"] = emp_data.get("name", "")
        await event_bus.publish(CompanyEvent(type="ceo_report", payload=payload, agent="SYSTEM"))

        # Auto-approve: proceed directly with cleanup
        is_system_node = node.node_type in ("review", "ceo_request", "ceo_prompt")
        await self._full_cleanup(
            employee_id, node, agent_error=False,
            project_id=project_id,
            run_retrospective=not is_system_node,
        )

    async def _full_cleanup(
        self, employee_id: str, node, agent_error: bool,
        project_id: str, run_retrospective: bool = False,
    ) -> None:
        from onemancompany.core.project_archive import append_action, complete_project

        if run_retrospective:
            try:
                from onemancompany.core.routine import run_post_task_routine
                await run_post_task_routine(node.description, project_id=project_id)
            except Exception as e:
                traceback.print_exc()
                if not project_id.startswith("_auto_"):
                    append_action(project_id, "routine", "Routine error", str(e)[:MAX_SUMMARY_LEN])
                await event_bus.publish(
                    CompanyEvent(
                        type="agent_done",
                        payload={"role": "ROUTINE", "summary": f"Routine error: {e!s}"},
                        agent="ROUTINE",
                    )
                )

        await self._update_soul(employee_id, node)

        from onemancompany.tools.sandbox import cleanup_sandbox as _cleanup_sandbox
        await _cleanup_sandbox()

        all_emps = _store.load_all_employees()
        for eid in all_emps:
            if eid not in self._running_tasks:
                await _store.save_employee_runtime(eid, status=STATUS_IDLE)

        if not project_id.startswith("_auto_"):
            label = node.description or "Task completed"
            if agent_error:
                label = f"{label} (with errors)"
            complete_project(project_id, label)
            status = "failed" if agent_error else "completed"
            await _store.save_project_status(
                project_id, status, completed_at=datetime.now().isoformat()
            )

        from onemancompany.core.state import flush_pending_reload
        flush_result = flush_pending_reload()
        if flush_result:
            updated = flush_result.get("employees_updated", [])
            added = flush_result.get("employees_added", [])
            if updated or added:
                print(f"[hot-reload] Post-task flush: {len(updated)} updated, {len(added)} added")

        role = self._get_role(employee_id)
        summary = (node.result or node.description or "Task completed")[:MAX_SUMMARY_LEN]
        if agent_error:
            summary = f"(with errors) {summary}"
        await event_bus.publish(
            CompanyEvent(
                type="agent_done",
                payload={"role": role, "summary": summary, "employee_id": employee_id, "project_id": project_id},
                agent=role,
            )
        )

        await event_bus.publish(
            CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
        )

        if self._restart_pending and self.is_idle(exclude=employee_id):
            logger.info("All tasks complete — triggering deferred graceful restart")
            await self._trigger_graceful_restart()

    async def _trigger_graceful_restart(self) -> None:
        """Execute a graceful restart: save state, then os.execv."""
        import os
        import sys
        from onemancompany.main import _save_ephemeral_state, _pending_code_changes

        _save_ephemeral_state()
        _pending_code_changes.clear()

        await event_bus.publish(
            CompanyEvent(
                type="backend_restart_scheduled",
                payload={"reason": "Code changes applied", "immediate": True},
                agent="SYSTEM",
            )
        )
        # Brief delay to let the WebSocket message reach clients
        await asyncio.sleep(0.5)

        logger.info("Graceful restart: os.execv")
        os.execv(sys.executable, [sys.executable, "-m", "onemancompany.main"])

    # ------------------------------------------------------------------
    # SOUL.md self-update
    # ------------------------------------------------------------------

    async def _update_soul(self, employee_id: str, node) -> None:
        """Ask the employee to update their SOUL.md after a task completes."""
        from onemancompany.core.config import FOUNDING_IDS, get_workspace_dir
        from onemancompany.agents.base import make_llm, tracked_ainvoke
        from langchain_core.messages import HumanMessage, SystemMessage

        if employee_id in FOUNDING_IDS:
            return
        node_result = getattr(node, "result", "") or ""
        node_desc = getattr(node, "description", "") or ""
        if not node_result:
            return

        soul_path = get_workspace_dir(employee_id) / "SOUL.md"
        soul_path.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if soul_path.exists():
            try:
                existing = soul_path.read_text(encoding="utf-8")
            except Exception as exc:
                logger.debug("Failed to read SOUL.md for {}: {}", employee_id, exc)

        emp_data = _store.load_employee(employee_id)
        if not emp_data:
            return

        try:
            llm = make_llm(employee_id)
            prompt = (
                f"You are {emp_data.get('name', '')} ({emp_data.get('nickname', '')}), {emp_data.get('role', '')}.\n"
                f"You just completed a task: {node_desc[:500]}\n"
                f"Task result summary: {node_result[:1000]}\n\n"
                f"Your current SOUL.md (your personal knowledge file):\n"
                f"---\n{existing or '(empty — this is your first entry)'}\n---\n\n"
                f"Update your SOUL.md with any lessons learned, patterns discovered, "
                f"or knowledge gained from this task. Keep it concise and useful for future you.\n"
                f"Output ONLY the complete updated SOUL.md content, nothing else."
            )
            result = await tracked_ainvoke(
                llm,
                [
                    SystemMessage(content="You maintain a personal knowledge file. Be concise, focus on actionable insights."),
                    HumanMessage(content=prompt),
                ],
                category="soul_update",
                employee_id=employee_id,
            )
            new_content = result.content.strip()
            if new_content and len(new_content) > 10:
                soul_path.write_text(new_content, encoding="utf-8")
                logger.info(f"[soul] Updated SOUL.md for employee {employee_id}")
        except Exception as e:
            logger.debug(f"[soul] Failed to update SOUL.md for {employee_id}: {e}")

    # ------------------------------------------------------------------
    # System task runner — for non-employee operations
    # ------------------------------------------------------------------

    def schedule_system_task(
        self,
        coro,
        task_name: str,
        task_description: str = "",
        project_id: str = "",
    ) -> str:
        """Schedule a system-level operation (routine, all-hands, approved actions).

        Unlike employee tasks, system tasks:
        - Are tracked in active_tasks for frontend visibility
        - Do NOT trigger post-task routine / retrospective
        - Do NOT complete a project lifecycle
        - Do NOT reset employee statuses
        - DO check for graceful restart when finished
        - DO create resolutions if file edits are accumulated
        - DO clean up sandbox

        Returns the auto-generated system task ID.
        """
        if not project_id:
            project_id = f"_sys_{uuid.uuid4().hex[:8]}"

        async def _run() -> None:
            try:
                await coro
            except Exception as e:
                traceback.print_exc()
                await event_bus.publish(
                    CompanyEvent(
                        type="agent_done",
                        payload={"role": task_name, "summary": f"Error: {e!s}"},
                        agent=task_name,
                    )
                )

            # Sandbox cleanup
            from onemancompany.tools.sandbox import cleanup_sandbox as _cleanup_sandbox
            await _cleanup_sandbox()

            # Broadcast updated state
            await event_bus.publish(
                CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
            )

        async def _wrapper() -> None:
            try:
                await _run()
            finally:
                self._system_tasks.pop(project_id, None)
                # Check for graceful restart
                if self._restart_pending and self.is_idle():
                    logger.info("System tasks complete — triggering deferred graceful restart")
                    await self._trigger_graceful_restart()

        loop = self._event_loop or asyncio.get_event_loop()
        t = loop.create_task(_wrapper())
        self._system_tasks[project_id] = t
        return project_id

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_role(self, employee_id: str) -> str:
        emp_data = _store.load_employee(employee_id)
        return (emp_data or {}).get("role", "Employee")

    def _set_employee_status(self, employee_id: str, status: str) -> None:
        try:
            asyncio.create_task(_store.save_employee_runtime(employee_id, status=status))
        except RuntimeError:
            logger.debug("No event loop for runtime persist of {}", employee_id)

    def _log_node(self, employee_id: str, node_id: str, log_type: str, content: str) -> None:
        """Log an event for a node (ScheduleEntry path)."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": log_type,
            "content": content,
        }
        self._task_logs.setdefault(node_id, []).append(entry)
        self._publish_log_event(employee_id, node_id, entry)

    def _publish_log_event(self, employee_id: str, task_id: str, entry: dict) -> None:
        """Publish a log event via event bus."""
        try:
            role = self._get_role(employee_id)
            loop = asyncio.get_running_loop()
            loop.create_task(event_bus.publish(
                CompanyEvent(
                    type="agent_log",
                    payload={
                        "employee_id": employee_id,
                        "task_id": task_id,
                        "log": entry,
                    },
                    agent=role,
                )
            ))
        except RuntimeError:
            logger.debug("No event loop for log publish (%s)", employee_id)

    def _publish_node_update(self, employee_id: str, node) -> None:
        """Publish a task update event for a TaskNode (ScheduleEntry path)."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(event_bus.publish(
                CompanyEvent(
                    type="agent_task_update",
                    payload={
                        "employee_id": employee_id,
                        "task": node.to_dict(),
                    },
                    agent=self._get_role(employee_id),
                )
            ))
        except RuntimeError:
            logger.debug("No event loop for node update publish (%s)", employee_id)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

employee_manager = EmployeeManager()


# ---------------------------------------------------------------------------
# Backward-compatible API
# ---------------------------------------------------------------------------



def register_agent(
    employee_id: str,
    agent_runner: BaseAgentRunner,
    config: "VesselConfig | None" = None,
) -> Vessel:
    """Register a company-hosted employee with a LangChain agent."""
    executor = LangChainExecutor(agent_runner)
    return employee_manager.register(employee_id, executor, config=config)


def register_self_hosted(
    employee_id: str,
    config: "VesselConfig | None" = None,
) -> Vessel:
    """Register a self-hosted employee (Claude CLI sessions)."""
    executor = ClaudeSessionExecutor(employee_id)
    return employee_manager.register(employee_id, executor, config=config)


def get_agent_loop(employee_id: str) -> Vessel | None:
    """Get an employee's vessel (backward compat for PersistentAgentLoop callers)."""
    return employee_manager.get_handle(employee_id)


async def start_all_loops() -> None:
    """Drain any deferred/orphaned pending tasks now that the event loop is running."""
    employee_manager.drain_pending()


async def stop_all_loops() -> None:
    """Cancel any running task executions."""
    tasks = list(employee_manager._running_tasks.values())
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    employee_manager._running_tasks.clear()


async def register_and_start_agent(employee_id: str, agent_runner: BaseAgentRunner) -> Vessel:
    """Register a new agent (no persistent loop to start)."""
    return register_agent(employee_id, agent_runner)


# ---------------------------------------------------------------------------
# Review reminder — scan for nodes stuck at "completed" awaiting review
# ---------------------------------------------------------------------------

def scan_overdue_reviews(threshold_seconds: int = 300) -> list[dict]:
    """Scan all active project trees for nodes stuck at 'completed' past threshold.

    Returns list of dicts with info about each overdue node:
      {node_id, employee_id, reviewer_id, description, completed_at, waiting_seconds, project_id}
    """
    from onemancompany.core.config import PROJECTS_DIR
    from onemancompany.core.task_tree import TaskTree

    overdue: list[dict] = []
    if not PROJECTS_DIR.exists():
        return overdue

    now = datetime.now()

    for project_dir in sorted(PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        tree_path = project_dir / "task_tree.yaml"
        if not tree_path.exists():
            continue
        try:
            tree = TaskTree.load(tree_path)
        except Exception:
            logger.debug("Failed to load task tree at {}", tree_path)
            continue

        for node in tree.all_nodes():
            if node.status != "completed":
                continue
            # Skip system nodes (review/ceo_request auto-finish)
            if node.node_type in ("review", "ceo_request"):
                continue
            if not node.completed_at:
                continue

            try:
                completed_dt = datetime.fromisoformat(node.completed_at)
            except (ValueError, TypeError):
                logger.debug("Invalid completed_at '{}' on node {}", node.completed_at, node.id)
                continue

            elapsed = (now - completed_dt).total_seconds()
            if elapsed < threshold_seconds:
                continue

            # Find the reviewer (parent node's employee)
            reviewer_id = ""
            if node.parent_id:
                parent = tree.get_node(node.parent_id)
                if parent:
                    reviewer_id = parent.employee_id

            overdue.append({
                "node_id": node.id,
                "employee_id": node.employee_id,
                "reviewer_id": reviewer_id,
                "description": (node.description or "")[:200],
                "completed_at": node.completed_at,
                "waiting_seconds": int(elapsed),
                "project_id": node.project_id or project_dir.name,
            })

    return overdue


# ---------------------------------------------------------------------------
# Backward-compat aliases (old names → new names)
# ---------------------------------------------------------------------------
EmployeeHandle = Vessel
_AgentRef = _VesselRef
_current_loop = _current_vessel
LangChainLauncher = LangChainExecutor
ClaudeSessionLauncher = ClaudeSessionExecutor
ScriptLauncher = ScriptExecutor
agent_loops = employee_manager.vessels
