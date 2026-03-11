"""Vessel — 员工躯壳执行系统 (on-demand task dispatch).

Vessel(躯壳) + Talent(灵魂) = Employee(员工)。
EmployeeManager 管理躯壳与灵魂结合后的完整员工。

Key concepts:
- Vessel: 员工执行容器（原 EmployeeHandle）
- *Executor / Launcher: 执行后端
- VesselConfig: 躯壳 DNA（vessel.yaml）
- VesselHarness protocols: 套接件标准（解耦公司系统交互）

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
from onemancompany.core.state import company_state
from onemancompany.core.vessel_config import VesselConfig

from loguru import logger

# ---------------------------------------------------------------------------
# Context variables — set during task execution so tools can access context
# ---------------------------------------------------------------------------

_current_vessel: ContextVar["Vessel | None"] = ContextVar("_current_vessel", default=None)
_current_task_id: ContextVar[str] = ContextVar("_current_task_id", default="")


# ---------------------------------------------------------------------------
# Task tree helpers (module-level for easy mocking)
# ---------------------------------------------------------------------------

def _load_project_tree(project_dir: str):
    """Load TaskTree from project directory."""
    from onemancompany.core.task_tree import TaskTree
    path = Path(project_dir) / "task_tree.yaml"
    if not path.exists():
        return None
    return TaskTree.load(path)


def _save_project_tree(project_dir: str, tree):
    """Save TaskTree to project directory."""
    path = Path(project_dir) / "task_tree.yaml"
    tree.save(path)


def _node_id_for_task(tree, task_id: str) -> str | None:
    """Look up TaskNode ID for an AgentTask ID via tree's task_id_map."""
    return tree.task_id_map.get(task_id)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

from onemancompany.core.task_lifecycle import TERMINAL_STATES, TaskPhase


def persist_task(employee_id: str, task: "AgentTask") -> None:
    """Lazy-import wrapper to avoid circular import with task_persistence."""
    from onemancompany.core.task_persistence import persist_task as _persist
    _persist(employee_id, task)


def archive_task(employee_id: str, task: "AgentTask") -> None:
    """Lazy-import wrapper to avoid circular import with task_persistence."""
    from onemancompany.core.task_persistence import archive_task as _archive
    _archive(employee_id, task)


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


def load_all_active_tasks(*, crash_recovery: bool = True) -> dict[str, list["AgentTask"]]:
    """Lazy-import wrapper to avoid circular import with task_persistence."""
    from onemancompany.core.task_persistence import load_all_active_tasks as _load
    return _load(crash_recovery=crash_recovery)


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


@dataclass
class AgentTask:
    id: str
    description: str
    status: TaskPhase = TaskPhase.PENDING
    task_type: str = "simple"  # "simple" or "project" — from TaskType enum
    parent_id: str = ""  # non-empty if this is a sub-task
    project_id: str = ""  # links to company project archive
    project_dir: str = ""  # project workspace path
    logs: list[dict] = field(default_factory=list)  # [{timestamp, type, content}]
    result: str = ""
    created_at: str = ""
    completed_at: str = ""
    # Cost tracking
    model_used: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    @property
    def is_project(self) -> bool:
        return self.task_type == "project"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status,
            "task_type": self.task_type,
            "parent_id": self.parent_id,
            "project_id": self.project_id,
            "logs": self.logs[-50:],
            "result": self.result[:MAX_SUMMARY_LEN] if self.result else "",
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "model_used": self.model_used,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


class AgentTaskBoard:
    """Per-agent task queue with sub-task tracking."""

    def __init__(self) -> None:
        self.tasks: list[AgentTask] = []

    def push(
        self,
        description: str,
        project_id: str = "",
        project_dir: str = "",
        parent_id: str = "",
    ) -> AgentTask:
        task = AgentTask(
            id=uuid.uuid4().hex[:12],
            description=description,
            project_id=project_id,
            project_dir=project_dir,
            parent_id=parent_id,
        )
        self.tasks.append(task)
        return task

    def get_next_pending(self) -> AgentTask | None:
        for t in self.tasks:
            if t.status == TaskPhase.PENDING and not t.parent_id:
                return t
        return None

    def cancel_by_project(self, project_id: str) -> list[AgentTask]:
        cancelled = []
        for t in self.tasks:
            if t.project_id == project_id and t.status in (TaskPhase.PENDING, TaskPhase.PROCESSING):
                t.status = TaskPhase.CANCELLED
                t.completed_at = datetime.now().isoformat()
                t.result = "Cancelled by CEO"
                cancelled.append(t)
        return cancelled

    def get_task(self, task_id: str) -> AgentTask | None:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def to_dict(self) -> list[dict]:
        return [t.to_dict() for t in self.tasks]


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
        emp = company_state.employees.get(self.employee_id)
        return emp.role if emp else "Employee"




class Vessel:
    """Per-employee view into the EmployeeManager.

    Per-employee view providing task management and history access.
    """

    def __init__(self, manager: "EmployeeManager", employee_id: str) -> None:
        self.manager = manager
        self.employee_id = employee_id
        self.agent = _VesselRef(employee_id)

    @property
    def board(self) -> AgentTaskBoard:
        return self.manager.boards.get(self.employee_id, AgentTaskBoard())

    @property
    def _current_task(self) -> AgentTask | None:
        """Return the currently running task for this employee, if any."""
        if self.employee_id not in self.manager._running_tasks:
            return None
        board = self.manager.boards.get(self.employee_id)
        if not board:
            return None
        for task in board.tasks:
            if task.status == TaskPhase.PROCESSING:
                return task
        return None

    @property
    def task_history(self) -> list[dict]:
        return self.manager.task_histories.get(self.employee_id, [])

    def push_task(
        self,
        description: str,
        project_id: str = "",
        project_dir: str = "",
    ) -> AgentTask:
        return self.manager.push_task(
            self.employee_id, description,
            project_id=project_id, project_dir=project_dir,
        )

    def get_history_context(self) -> str:
        return self.manager.get_history_context(self.employee_id)




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
        self.boards: dict[str, AgentTaskBoard] = {}
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
        if employee_id not in self.boards:
            self.boards[employee_id] = AgentTaskBoard()
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
    ) -> AgentTask:
        """Push a task to an employee's board and trigger execution."""
        board = self.boards.get(employee_id)
        if not board:
            board = AgentTaskBoard()
            self.boards[employee_id] = board
        task = board.push(description, project_id=project_id, project_dir=project_dir)
        persist_task(employee_id, task)
        self._publish_task_update(employee_id, task)
        self._schedule_next(employee_id)
        return task

    # ------------------------------------------------------------------
    # Scheduling — on-demand, no idle polling
    # ------------------------------------------------------------------

    def _schedule_next(self, employee_id: str) -> None:
        """If no task is running for this employee, start the next pending one."""
        if employee_id in self._running_tasks:
            return
        board = self.boards.get(employee_id)
        if not board:
            return
        task = board.get_next_pending()
        if not task:
            self._set_employee_status(employee_id, STATUS_IDLE)
            return
        try:
            loop = asyncio.get_running_loop()
            self._running_tasks[employee_id] = loop.create_task(
                self._run_task(employee_id, task)
            )
        except RuntimeError:
            # No running loop in current context (e.g. sync LangChain tool call).
            # Use the stashed event loop to schedule via call_soon_threadsafe.
            if self._event_loop and not self._event_loop.is_closed():
                self._event_loop.call_soon_threadsafe(self._create_run_task, employee_id, task)
                logger.info("Scheduled deferred task for {} via call_soon_threadsafe", employee_id)
            else:
                self._deferred_schedule.add(employee_id)
                logger.warning("No event loop to schedule task for {}, deferred", employee_id)

    def _create_run_task(self, employee_id: str, task: AgentTask) -> None:
        """Create an asyncio.Task for _run_task. Must be called from the event loop thread."""
        if employee_id in self._running_tasks:
            return
        loop = asyncio.get_running_loop()
        self._running_tasks[employee_id] = loop.create_task(
            self._run_task(employee_id, task)
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
        # Also scan all boards for any orphaned pending tasks
        for emp_id, board in self.boards.items():
            if emp_id not in self._running_tasks and board.get_next_pending():
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
        """Restore tasks from per-employee task files on disk.

        Returns the number of tasks restored.
        """
        all_tasks = load_all_active_tasks()
        restored = 0
        for emp_id, tasks in all_tasks.items():
            if emp_id not in self.executors:
                logger.warning("Skipping restored tasks for unregistered employee {}", emp_id)
                continue
            board = self.boards.get(emp_id)
            if not board:
                board = AgentTaskBoard()
                self.boards[emp_id] = board
            for task in tasks:
                board.tasks.append(task)
                restored += 1
        if restored:
            logger.info("Restored {} task(s) from disk", restored)
        self._restart_holding_pollers()
        return restored

    def _restart_holding_pollers(self) -> int:
        """Restart watchdog crons for all HOLDING tasks."""
        count = 0
        for emp_id, board in self.boards.items():
            for task in board.tasks:
                if task.status == TaskPhase.HOLDING:
                    meta = _parse_holding_metadata(task.result or "")
                    if meta:
                        self._setup_holding_watchdog(emp_id, task, meta)
                        count += 1
        if count:
            logger.info("Restarted {} holding watchdog(s)", count)
        return count

    def abort_project(self, project_id: str) -> list:
        """Cancel board tasks AND cancel the running asyncio.Task for a project.

        Returns list of cancelled AgentTask objects.
        """
        all_cancelled: list = []
        for emp_id, board in self.boards.items():
            cancelled = board.cancel_by_project(project_id)
            for t in cancelled:
                vessel = self.vessels.get(emp_id)
                if vessel:
                    self._log(emp_id, t, "cancelled", "Task aborted by CEO")
                    self._publish_task_update(emp_id, t)
                persist_task(emp_id, t)
                archive_task(emp_id, t)
            all_cancelled.extend(cancelled)

            # Cancel the running asyncio.Task if it's working on this project
            if cancelled and emp_id in self._running_tasks:
                running = self._running_tasks[emp_id]
                if not running.done():
                    running.cancel()
                    logger.info("Cancelled running asyncio.Task for {} (project {})", emp_id, project_id)

        return all_cancelled

    async def _run_task(self, employee_id: str, task: AgentTask) -> None:
        """Execute a task, then schedule the next one."""
        try:
            await self._execute_task(employee_id, task)
        finally:
            self._running_tasks.pop(employee_id, None)
            self._schedule_next(employee_id)
            # After scheduling next (which may or may not start a new task),
            # check if a graceful restart is pending and we're now truly idle.
            if self._restart_pending and self.is_idle():
                logger.info("All tasks complete (post-schedule) — triggering deferred graceful restart")
                await self._trigger_graceful_restart()

    # ------------------------------------------------------------------
    # Task execution — core logic
    # ------------------------------------------------------------------

    async def _execute_task(self, employee_id: str, task: AgentTask) -> None:
        from onemancompany.core.resolutions import current_project_id

        role = self._get_role(employee_id)
        vessel = self.vessels.get(employee_id)

        # Get per-employee limits from VesselConfig
        cfg = self.configs.get(employee_id)
        max_retries = cfg.limits.max_retries if cfg else MAX_RETRIES
        retry_delays = cfg.limits.retry_delays if cfg else RETRY_DELAYS
        max_subtask_iterations = cfg.limits.max_subtask_iterations if cfg else MAX_SUBTASK_ITERATIONS
        max_subtask_depth = cfg.limits.max_subtask_depth if cfg else MAX_SUBTASK_DEPTH

        # 1. Mark in_progress
        task.status = TaskPhase.PROCESSING
        persist_task(employee_id, task)
        self._set_employee_status(employee_id, STATUS_WORKING)
        self._log(employee_id, task, "start", f"Starting task: {task.description}")
        self._publish_task_update(employee_id, task)

        emp = company_state.employees.get(employee_id)
        if emp:
            emp.current_task_summary = task.description[:100]

        # 2. Set contextvars
        loop_token = _current_vessel.set(vessel)
        task_token = _current_task_id.set(task.id)

        # 3. Task is already persisted via task_persistence — no in-memory tracking needed
        project_id = task.project_id
        project_dir = task.project_dir

        ctx_token = current_project_id.set(project_id) if project_id else None

        agent_error = False
        try:
            # 4. Build task context with injections
            task_with_ctx = task.description

            # Inject project identity header so employees always know which project
            if task.project_id:
                identity = self._build_project_identity(task.project_id)
                if identity:
                    task_with_ctx = f"{identity}\n\n{task_with_ctx}"

            if project_dir:
                task_with_ctx += f"\n\n[Project workspace: {project_dir} — save all outputs here]"

            if task.project_id:
                proj_ctx = self._get_project_history_context(task.project_id)
                if proj_ctx:
                    task_with_ctx = f"{task_with_ctx}\n\n{proj_ctx}"

            if task.project_id:
                workflow_ctx = self._get_project_workflow_context(employee_id, task)
                if workflow_ctx:
                    task_with_ctx = f"{task_with_ctx}\n\n{workflow_ctx}"

            # Inject progress log (ralph-inspired cross-task context)
            inject_progress = cfg.context.inject_progress_log if cfg else True
            if inject_progress:
                progress = _load_progress(employee_id)
                if progress:
                    task_with_ctx += f"\n\n[Previous Work Learnings]\n{progress}"

            # Log callback
            def _on_log(log_type: str, content: str) -> None:
                self._log(employee_id, task, log_type, content)

            # 5. Execute via launcher with retry
            executor = self.executors.get(employee_id)
            if not executor:
                raise RuntimeError(f"No executor registered for employee {employee_id}")

            context = TaskContext(
                project_id=task.project_id,
                work_dir=project_dir,
                employee_id=employee_id,
                task_id=task.id,
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

            # Set timeout from task tree node if available
            if task.project_dir:
                _tree = _load_project_tree(task.project_dir)
                if _tree:
                    _nid = _node_id_for_task(_tree, task.id)
                    if _nid:
                        _node = _tree.get_node(_nid)
                        if _node and _node.timeout_seconds:
                            from onemancompany.core.subprocess_executor import SubprocessExecutor
                            if isinstance(executor, SubprocessExecutor):
                                executor.timeout_seconds = _node.timeout_seconds

            launch_result: LaunchResult | None = None
            last_err: Exception | None = None
            for attempt in range(max_retries):
                try:
                    launch_result = await executor.execute(task_with_ctx, context, on_log=_on_log)
                    last_err = None
                    break
                except GraphRecursionError as rec_err:
                    last_err = rec_err
                    self._log(employee_id, task, "error", f"Agent hit recursion limit: {rec_err!s}")
                    break
                except Exception as run_err:
                    last_err = run_err
                    if attempt < max_retries - 1:
                        delay = retry_delays[attempt] if attempt < len(retry_delays) else retry_delays[-1]
                        self._log(employee_id, task, "retry", f"Attempt {attempt + 1} failed: {run_err!s} — retrying in {delay}s")
                        await asyncio.sleep(delay)

            if last_err is not None:
                raise last_err

            task.result = launch_result.output if launch_result else ""
            self._log(employee_id, task, "result", task.result or "")

            # 6. Record token usage
            if launch_result and launch_result.total_tokens > 0:
                task.model_used = launch_result.model_used
                task.input_tokens += launch_result.input_tokens
                task.output_tokens += launch_result.output_tokens
                task.total_tokens += launch_result.total_tokens
                from onemancompany.core.model_costs import get_model_cost
                costs = get_model_cost(task.model_used)
                task.estimated_cost_usd = (
                    task.input_tokens * costs["input"] + task.output_tokens * costs["output"]
                ) / 1_000_000

        except asyncio.CancelledError:
            agent_error = True
            if task.status != TaskPhase.CANCELLED:
                task.status = TaskPhase.CANCELLED
            task.result = task.result or "Cancelled by CEO"
            if not task.completed_at:
                task.completed_at = datetime.now().isoformat()
            persist_task(employee_id, task)
            self._log(employee_id, task, "cancelled", "Task cancelled (asyncio abort)")
        except TimeoutError as te:
            agent_error = True
            task.status = TaskPhase.FAILED
            task.result = str(te)
            if not task.completed_at:
                task.completed_at = datetime.now().isoformat()
            persist_task(employee_id, task)
            self._log(employee_id, task, "timeout", f"Task timed out: {te!s}")
            await event_bus.publish(
                CompanyEvent(
                    type="agent_done",
                    payload={"role": role, "summary": f"Timeout: {te!s}"},
                    agent=role,
                )
            )
        except Exception as e:
            agent_error = True
            task.status = TaskPhase.FAILED
            task.result = f"Error: {e!s}"
            persist_task(employee_id, task)
            self._log(employee_id, task, "error", f"Task failed after {max_retries} attempts: {e!s}")
            traceback.print_exc()
            await event_bus.publish(
                CompanyEvent(
                    type="agent_done",
                    payload={"role": role, "summary": f"Error: {e!s}"},
                    agent=role,
                )
            )
        finally:
            _current_vessel.reset(loop_token)
            _current_task_id.reset(task_token)
            if ctx_token is not None:
                current_project_id.reset(ctx_token)

        # 8. Mark completed (or HOLDING)
        if task.status not in (TaskPhase.FAILED, TaskPhase.CANCELLED):
            holding_meta = _parse_holding_metadata(task.result or "")
            if holding_meta is not None:
                task.status = TaskPhase.HOLDING
                persist_task(employee_id, task)
                # Start holding watchdog cron — employee checks status every 5 min
                self._setup_holding_watchdog(employee_id, task, holding_meta)
                self._log(employee_id, task, "holding", f"Task entered HOLDING: {holding_meta}")
            else:
                task.status = TaskPhase.COMPLETE
                persist_task(employee_id, task)

        if task.status != TaskPhase.HOLDING:
            # Normal completion path
            if not task.completed_at:
                task.completed_at = datetime.now().isoformat()
            self._log(employee_id, task, "end", f"Task {task.status}")
            self._publish_task_update(employee_id, task)

            # 9. Record to history + progress log
            if task.status == TaskPhase.COMPLETE:
                self._append_history(employee_id, task)
                summary = (task.result or "")[:200]
                _append_progress(employee_id, f"Completed: {task.description[:100]} → {summary}")

            # Post-task hook
            post_task_hook = self._hooks.get(employee_id, {}).get("post_task")
            if post_task_hook:
                try:
                    post_task_hook(task, task.result or "")
                except Exception:
                    logger.warning("Post-task hook failed for %s", employee_id)

            if emp:
                emp.current_task_summary = ""

            # Task tree: update node status and wake parent if all siblings done
            if task.project_dir:
                try:
                    await self._on_child_complete(employee_id, task, project_id=task.project_id)
                except Exception as e:
                    logger.error("Task tree callback failed for {}: {}", employee_id, e)

            # 10. Post-task cleanup
            effective_project_id = task.project_id
            if effective_project_id:
                # Record cost to project
                if task.total_tokens > 0:
                    from onemancompany.core.project_archive import record_project_cost
                    record_project_cost(
                        effective_project_id, employee_id,
                        task.model_used, task.input_tokens, task.output_tokens,
                        task.estimated_cost_usd,
                    )
                # Record action
                if not effective_project_id.startswith("_auto_") and task.result:
                    from onemancompany.core.project_archive import append_action
                    role = self._get_role(employee_id)
                    summary = task.result[:MAX_SUMMARY_LEN]
                    append_action(effective_project_id, employee_id, f"{role} task completed", summary)
                # Resolution
                from onemancompany.core.resolutions import create_resolution
                resolution = create_resolution(effective_project_id, task.description)
                if resolution:
                    resolution["employee_id"] = employee_id
                    await event_bus.publish(
                        CompanyEvent(type="resolution_ready", payload=resolution, agent="SYSTEM")
                    )

            # 11. Archive task at terminal state
            if task.status in TERMINAL_STATES:
                archive_task(employee_id, task)
        else:
            # HOLDING — just publish the status update
            self._publish_task_update(employee_id, task)

    # ------------------------------------------------------------------
    # HOLDING helpers
    # ------------------------------------------------------------------

    def _setup_holding_watchdog(self, employee_id: str, task: AgentTask, holding_meta: dict) -> None:
        """Start a watchdog cron for a HOLDING task.

        The cron dispatches a check task to the employee periodically.
        For thread_id-based holds, it checks Gmail. For all other holds
        (e.g. batch_id), it asks the employee to verify the condition.
        """
        from onemancompany.core.automation import start_cron as _start_cron

        thread_id = holding_meta.get("thread_id", "")
        if thread_id:
            # Specific Gmail reply poller
            interval = holding_meta.get("interval", "1m")
            cron_name = f"reply_{task.id}"
            task_desc = f"[reply_poll] Check Gmail thread {thread_id} for task {task.id}"
        else:
            # Generic holding watchdog — employee checks if condition is resolved
            interval = holding_meta.get("interval", "5m")
            meta_summary = ", ".join(f"{k}={v}" for k, v in holding_meta.items() if k != "interval")
            cron_name = f"holding_{task.id}"
            holding_since = task.created_at or datetime.now().isoformat()
            task_desc = (
                f"[holding_check] 你有一个 HOLDING 任务 (task_id={task.id}) 等待外部条件完成。"
                f" 元数据: {meta_summary}。开始等待时间: {holding_since}。"
                f"\n\n请按以下流程处理："
                f"\n1. 检查该条件是否已满足。如果已完成，调用 resume_held_task(task_id='{task.id}', result='条件已满足: <具体结果>')。"
                f"\n2. 如果等待已超过 10 分钟但未超过 30 分钟，尝试换一种方式推进（重新发送请求、换联系方式、尝试替代方案等）。"
                f"\n3. 如果等待已超过 30 分钟，上报给上级（用 dispatch_child 或直接在结果中说明情况），"
                f"并调用 resume_held_task(task_id='{task.id}', result='超时上报: <等待原因和已尝试的方法>') 结束等待。"
                f"\n4. 如果尚未超时且条件未满足，无需操作。"
            )

        result = _start_cron(employee_id, cron_name, interval, task_desc)
        if result.get("status") != "ok":
            logger.error("Failed to start holding watchdog for {}: {}", task.id, result)

    async def resume_held_task(self, employee_id: str, task_id: str, result: str) -> bool:
        """Resume a HOLDING task with the provided result.

        Transitions HOLDING → COMPLETE, stops the reply poller cron,
        persists the task, archives it, and triggers task tree callbacks.

        Returns True if task was found and resumed, False otherwise.
        """
        board = self.boards.get(employee_id)
        if not board:
            return False

        task: AgentTask | None = None
        for t in board.tasks:
            if t.id == task_id:
                task = t
                break
        if not task or task.status != TaskPhase.HOLDING:
            return False

        # Stop holding watchdog cron (reply poller or generic watchdog)
        stop_cron(employee_id, f"reply_{task_id}")
        stop_cron(employee_id, f"holding_{task_id}")

        # Transition to COMPLETE
        task.result = result
        task.status = TaskPhase.COMPLETE
        task.completed_at = datetime.now().isoformat()
        persist_task(employee_id, task)

        self._log(employee_id, task, "resumed", f"HOLDING → COMPLETE with result: {result[:200]}")
        self._publish_task_update(employee_id, task)

        # Record to history + progress log
        self._append_history(employee_id, task)
        summary = (task.result or "")[:200]
        _append_progress(employee_id, f"Completed (resumed): {task.description[:100]} → {summary}")

        # Task tree callback
        if task.project_dir:
            try:
                await self._on_child_complete(employee_id, task, project_id=task.project_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Task tree callback failed for {}: {}", employee_id, e)

        # Archive
        if task.status in TERMINAL_STATES:
            archive_task(employee_id, task)

        return True

    # ------------------------------------------------------------------
    # Task history management
    # ------------------------------------------------------------------

    def _append_history(self, employee_id: str, task: AgentTask) -> None:
        history = self.task_histories.setdefault(employee_id, [])
        history.append({
            "task": task.description[:200],
            "result": (task.result or "")[:RESULT_SNIPPET_LEN],
            "completed_at": task.completed_at or datetime.now().isoformat(),
        })
        # Write-through to disk
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
            _is_v1, _is_iteration, _find_project_for_iteration,
            _split_qualified_iter, load_named_project, load_iteration,
        )

        parts: list[str] = []
        if _is_iteration(project_id):
            slug = _find_project_for_iteration(project_id)
            if slug:
                proj = load_named_project(slug)
                proj_name = proj.get("name", slug) if proj else slug
                _, bare_iter = _split_qualified_iter(project_id)
                parts.append(f"⚙ 当前项目: {proj_name} ({bare_iter})")
                parts.append(f"  Project ID: {project_id}")
        elif _is_v1(project_id):
            # v1 timestamped project — load task from project.yaml
            from onemancompany.core.project_archive import list_projects
            for p in list_projects():
                if p.get("project_id") == project_id:
                    task_summary = (p.get("task") or "")[:80]
                    parts.append(f"⚙ 当前任务: {task_summary}")
                    parts.append(f"  Project ID: {project_id}")
                    break
        else:
            proj = load_named_project(project_id)
            if proj:
                proj_name = proj.get("name", project_id)
                parts.append(f"⚙ 当前项目: {proj_name}")
                parts.append(f"  Project ID: {project_id}")

        if not parts:
            return ""
        return "\n".join(parts)
    _CTX_TASK_DESC_CHARS = 200
    _CTX_MAX_WORKSPACE_FILES = 30
    _CTX_MAX_CRITERIA = 5

    def _get_project_history_context(self, project_id: str) -> str:
        from onemancompany.core.project_archive import (
            _is_v1, _is_iteration, _find_project_for_iteration,
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
        elif _is_v1(project_id) or project_id.startswith("_auto_"):
            return ""

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

    def _get_project_workflow_context(self, employee_id: str, task: AgentTask) -> str:
        from onemancompany.core.config import load_workflows, FOUNDING_LEVEL
        from onemancompany.core.workflow_engine import parse_workflow

        emp = company_state.employees.get(employee_id)
        role = (emp.role if emp else "Employee").upper()
        is_manager = role in ("COO", "CSO", "EA", "HR")

        if is_manager and role in ("COO", "CSO"):
            return (
                "[Manager Execution Guide]\n"
                "As a manager receiving a project task:\n"
                "  1. list_colleagues() 了解所有可用团队成员及其技能。\n"
                "  2. 充分利用团队：PM可做项目管理/调研/文档，Engineer做开发，各司其职。\n"
                "  3. dispatch_child() 给最合适的员工，附上清晰指令和验收标准。\n"
                "  4. 复杂项目用 dispatch_child() 分发多个子任务（并行执行）。\n"
                "  5. 如果没有合适员工，dispatch 给 HR 招聘。\n"
                "  6. 你可以在任何阶段拉人加入项目（不仅限于初始分工），包括验收、整改、诊断等。\n"
                "  7. 只在没人能做的情况下才自己动手。\n"
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
                            "test", "do not report", "验证", "验收",
                        ]):
                            verification_instructions += f"  - {inst}\n"
                    break

        if not verification_instructions:
            verification_instructions = (
                "  - For code/software: Use sandbox_execute_code to run it once. Fix errors if any.\n"
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

    async def _on_child_complete(self, employee_id: str, task: AgentTask, project_id: str = "") -> None:
        """Update TaskTree node when a task completes, wake parent if all siblings done."""
        tree = _load_project_tree(task.project_dir)
        if tree is None:
            return

        node_id = _node_id_for_task(tree, task.id)
        if not node_id:
            logger.debug("No tree node mapped for task {} (employee {})", task.id, employee_id)
            return

        node = tree.get_node(node_id)
        if not node:
            logger.warning("Tree node {} not found for task {}", node_id, task.id)
            return

        # Update node with task results
        node.status = "completed"
        node.result = task.result or ""
        node.input_tokens = task.input_tokens
        node.output_tokens = task.output_tokens
        node.cost_usd = task.estimated_cost_usd
        node.completed_at = datetime.now().isoformat()

        _save_project_tree(task.project_dir, tree)

        # Root node or child of CEO node completed — request CEO confirmation
        is_root = not node.parent_id
        parent_node = tree.get_node(node.parent_id) if node.parent_id else None
        is_child_of_ceo = parent_node and parent_node.is_ceo_node if parent_node else False

        if is_root or is_child_of_ceo:
            logger.info("EA node {} completed (root or child of CEO) — requesting CEO confirmation", node_id)
            # Update CEO root node status
            if is_child_of_ceo:
                parent_node.status = "completed"
                _save_project_tree(task.project_dir, tree)
            effective_project_id = project_id or task.project_id
            await self._request_ceo_confirmation(
                employee_id, task, tree, node, effective_project_id
            )
            return

        parent_node = tree.get_node(node.parent_id)
        if not parent_node:
            return

        # Check all children of parent — are they all terminal?
        children = tree.get_active_children(parent_node.id)
        all_terminal = all(c.is_terminal or c.status == "completed" for c in children)
        if not all_terminal:
            return

        # Build review prompt for parent employee
        # Separate children needing review from already-accepted
        # Skip CEO nodes (informational only, not reviewable)
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
            lines.append("以下子任务已通过验收，无需重复审核：")
            for child in already_accepted:
                lines.append(f"  \u2713 ({child.employee_id}): {child.description[:80]}")
            lines.append("")

        if needs_review:
            lines.append("以下子任务需要审核：")
            lines.append("")
            for i, child in enumerate(needs_review, 1):
                criteria_str = ", ".join(child.acceptance_criteria) if child.acceptance_criteria else "无"
                lines.append(f"子任务 {i} ({child.employee_id}): {child.description}")
                lines.append(f"  验收标准: {criteria_str}")
                lines.append(f"  执行结果: \"{child.result}\"")
                lines.append(f"  状态: {child.status}")
                if child.acceptance_result and not child.acceptance_result.get("passed"):
                    lines.append(f"  \u26a0 此任务曾被拒绝: {child.acceptance_result.get('notes', '')}")
                lines.append("")
        else:
            lines.append("所有子任务已通过验收。")

        lines.append("请对未验收的子任务调用 accept_child(node_id, notes) 或 reject_child(node_id, reason)。")
        lines.append("如需追加任务，调用 dispatch_child()。")
        lines.append("全部处理完毕后，你的任务将自动完成并向上汇报。")

        review_prompt = "\n".join(lines)

        board = self.boards.setdefault(parent_node.employee_id, AgentTaskBoard())
        review_task = board.push(
            description=review_prompt,
            project_id=project_id,
            project_dir=task.project_dir,
        )
        persist_task(parent_node.employee_id, review_task)
        logger.info("All children done for parent {} — pushed review task to {}", parent_node.id, parent_node.employee_id)

        # Schedule parent if not already running
        if parent_node.employee_id not in self._running_tasks:
            self._schedule_next(parent_node.employee_id)

    async def _request_ceo_confirmation(
        self,
        employee_id: str,
        task: AgentTask,
        tree,
        root_node,
        project_id: str,
    ) -> None:
        """Send project completion report to CEO and wait for confirmation.

        CEO approve -> _full_cleanup(run_retrospective=True)
        CEO revise  -> push revision task to root employee
        """
        from onemancompany.agents.common_tools import _ceo_pending, _ceo_wait_key

        # Build completion summary from all children (skip CEO info nodes)
        children = [c for c in tree.get_children(root_node.id) if not c.is_ceo_node]
        lines = [f"项目完成汇报 — {task.description[:100]}", ""]
        for i, child in enumerate(children, 1):
            status_icon = "✓" if child.status == "accepted" else "●"
            lines.append(f"{status_icon} 子任务 {i} ({child.employee_id}): {child.description[:80]}")
            lines.append(f"  结果: {(child.result or '无')[:200]}")
            lines.append("")
        summary = "\n".join(lines)

        # Publish CEO report event
        payload = {
            "subject": f"项目完成确认: {task.description[:60]}",
            "report": summary,
            "action_required": True,
            "employee_id": employee_id,
            "project_id": project_id,
            "timestamp": datetime.now().isoformat(),
        }
        emp = company_state.employees.get(employee_id)
        if emp:
            payload["employee_name"] = emp.name
        await event_bus.publish(CompanyEvent(type="ceo_report", payload=payload, agent="SYSTEM"))

        # Block until CEO responds
        key = _ceo_wait_key(employee_id, project_id)
        entry = {"event": asyncio.Event(), "response": {}, "meta": payload}
        _ceo_pending[key] = entry
        try:
            await asyncio.wait_for(entry["event"].wait(), timeout=600)
            ceo_response = entry.get("response", {})
            action = ceo_response.get("action", "approve")
            message = ceo_response.get("message", "")
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            action = "approve"
            message = "CEO未在10分钟内响应，自动确认"
            logger.warning("CEO confirmation timed out for project {}, auto-approving", project_id)
        finally:
            _ceo_pending.pop(key, None)

        if action == "revise" and message:
            # CEO wants revision — push task back to root employee
            revision_desc = (
                f"CEO要求修改:\n{message}\n\n"
                f"原任务: {task.description[:200]}"
            )
            board = self.boards.setdefault(employee_id, AgentTaskBoard())
            revision_task = board.push(
                description=revision_desc,
                project_id=project_id,
                project_dir=task.project_dir,
            )
            persist_task(employee_id, revision_task)
            self._schedule_next(employee_id)
            logger.info("CEO requested revision for project {} — pushed to {}", project_id, employee_id)
        else:
            # CEO approved — run full cleanup with retrospective for project tasks
            is_project = task.task_type == "project"
            await self._full_cleanup(
                employee_id, task, agent_error=False,
                project_id=project_id,
                run_retrospective=is_project,
            )

    async def _full_cleanup(
        self, employee_id: str, task: AgentTask, agent_error: bool,
        project_id: str, run_retrospective: bool = False,
    ) -> None:
        from onemancompany.core.project_archive import append_action, complete_project
        from onemancompany.core.resolutions import create_resolution, current_project_id

        # Retrospective only runs after project acceptance + rectification completes.
        # Simple tasks without acceptance criteria do NOT trigger retrospective.
        if run_retrospective:
            routine_ctx = current_project_id.set(project_id)
            try:
                from onemancompany.core.routine import run_post_task_routine
                await run_post_task_routine(task.description, project_id=project_id)
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
            finally:
                current_project_id.reset(routine_ctx)

            routine_resolution = create_resolution(project_id, f"Routine: {task.description}")
            if routine_resolution:
                routine_resolution["employee_id"] = employee_id
                await event_bus.publish(
                    CompanyEvent(type="resolution_ready", payload=routine_resolution, agent="SYSTEM")
                )

        # Trigger SOUL.md self-update for the task executor
        await self._update_soul(employee_id, task)

        from onemancompany.tools.sandbox import cleanup_sandbox as _cleanup_sandbox
        await _cleanup_sandbox()

        # Only reset employees that are NOT currently running a task.
        # The old code reset ALL non-founding employees to IDLE, which would
        # clobber the status of employees working on other projects.
        for eid, emp in company_state.employees.items():
            if eid not in self._running_tasks:
                emp.status = STATUS_IDLE

        if not project_id.startswith("_auto_"):
            label = task.description or "Task completed"
            if agent_error:
                label = f"{label} (with errors)"
            complete_project(project_id, label)

        from onemancompany.core.state import flush_pending_reload
        flush_result = flush_pending_reload()
        if flush_result:
            updated = flush_result.get("employees_updated", [])
            added = flush_result.get("employees_added", [])
            if updated or added:
                print(f"[hot-reload] Post-task flush: {len(updated)} updated, {len(added)} added")

        # Notify CEO that the task is done
        role = self._get_role(employee_id)
        summary = (task.result or task.description or "Task completed")[:MAX_SUMMARY_LEN]
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

        # Check if a graceful restart is pending and we're now idle.
        # Exclude current employee because _running_tasks hasn't popped yet
        # (we're still inside _execute_task, called before _run_task's finally).
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

    async def _update_soul(self, employee_id: str, task: AgentTask) -> None:
        """Ask the employee to update their SOUL.md after a task completes.

        Runs as a lightweight LLM call — reads existing SOUL.md, asks the agent
        to update it with lessons from the completed task, writes back.
        """
        from onemancompany.core.config import FOUNDING_IDS, get_workspace_dir
        from onemancompany.agents.base import make_llm, tracked_ainvoke
        from langchain_core.messages import HumanMessage, SystemMessage

        # Skip for founding employees and system tasks
        if employee_id in FOUNDING_IDS:
            return
        if not task.result:
            return

        soul_path = get_workspace_dir(employee_id) / "SOUL.md"
        soul_path.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if soul_path.exists():
            try:
                existing = soul_path.read_text(encoding="utf-8")
            except Exception as exc:
                logger.debug("Failed to read SOUL.md for {}: {}", employee_id, exc)

        emp = company_state.employees.get(employee_id)
        if not emp:
            return

        try:
            llm = make_llm(employee_id)
            prompt = (
                f"You are {emp.name} ({emp.nickname}), {emp.role}.\n"
                f"You just completed a task: {task.description[:500]}\n"
                f"Task result summary: {task.result[:1000]}\n\n"
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
            from onemancompany.core.resolutions import create_resolution, current_project_id

            ctx_token = current_project_id.set(project_id)
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
            finally:
                current_project_id.reset(ctx_token)

            # Create resolution if file edits were accumulated
            resolution = create_resolution(project_id, task_description)
            if resolution:
                await event_bus.publish(
                    CompanyEvent(type="resolution_ready", payload=resolution, agent="SYSTEM")
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
        emp = company_state.employees.get(employee_id)
        return emp.role if emp else "Employee"

    def _set_employee_status(self, employee_id: str, status: str) -> None:
        emp = company_state.employees.get(employee_id)
        if emp:
            emp.status = status

    def _log(self, employee_id: str, task: AgentTask, log_type: str, content: str) -> None:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": log_type,
            "content": content,
        }
        task.logs.append(entry)
        try:
            role = self._get_role(employee_id)
            loop = asyncio.get_running_loop()
            loop.create_task(event_bus.publish(
                CompanyEvent(
                    type="agent_log",
                    payload={
                        "employee_id": employee_id,
                        "task_id": task.id,
                        "log": entry,
                    },
                    agent=role,
                )
            ))
        except RuntimeError:
            logger.debug("No event loop for log publish (%s)", employee_id)

    def _publish_task_update(self, employee_id: str, task: AgentTask) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(event_bus.publish(
                CompanyEvent(
                    type="agent_task_update",
                    payload={
                        "employee_id": employee_id,
                        "task": task.to_dict(),
                    },
                    agent=self._get_role(employee_id),
                )
            ))
        except RuntimeError:
            logger.debug("No event loop for task update publish (%s)", employee_id)


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
# Backward-compat aliases (old names → new names)
# ---------------------------------------------------------------------------
EmployeeHandle = Vessel
_AgentRef = _VesselRef
_current_loop = _current_vessel
LangChainLauncher = LangChainExecutor
ClaudeSessionLauncher = ClaudeSessionExecutor
ScriptLauncher = ScriptExecutor
agent_loops = employee_manager.vessels
