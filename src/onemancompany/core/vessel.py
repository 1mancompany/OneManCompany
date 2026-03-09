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
from onemancompany.core.state import TaskEntry, company_state
from onemancompany.core.vessel_config import VesselConfig

from loguru import logger

# ---------------------------------------------------------------------------
# Context variables — set during task execution so tools can access context
# ---------------------------------------------------------------------------

_current_vessel: ContextVar["Vessel | None"] = ContextVar("_current_vessel", default=None)
_current_task_id: ContextVar[str] = ContextVar("_current_task_id", default="")



# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

from onemancompany.core.task_lifecycle import TaskPhase



@dataclass
class AgentTask:
    id: str
    description: str
    status: TaskPhase = TaskPhase.PENDING
    task_type: str = "simple"  # "simple" or "project" — from TaskType enum
    parent_id: str = ""  # non-empty if this is a sub-task
    project_id: str = ""  # links to company project archive
    project_dir: str = ""  # project workspace path
    original_project_id: str = ""  # preserved across multiple dispatch_task calls
    original_project_dir: str = ""
    depends_on: list[str] = field(default_factory=list)  # dispatch_ids this task waits on
    sub_task_ids: list[str] = field(default_factory=list)
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
            "original_project_id": self.original_project_id,
            "depends_on": self.depends_on,
            "sub_task_ids": self.sub_task_ids,
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
        if parent_id:
            parent = self.get_task(parent_id)
            if parent:
                parent.sub_task_ids.append(task.id)
        return task

    def get_next_pending(self) -> AgentTask | None:
        for t in self.tasks:
            if t.status == TaskPhase.PENDING and not t.parent_id:
                return t
        return None

    def get_pending_subtasks(self, parent_id: str) -> list[AgentTask]:
        return [t for t in self.tasks if t.parent_id == parent_id and t.status == TaskPhase.PENDING]

    def cancel_by_project(self, project_id: str) -> list[AgentTask]:
        cancelled = []
        for t in self.tasks:
            if t.project_id == project_id and t.status in (TaskPhase.PENDING, TaskPhase.PROCESSING):
                t.status = TaskPhase.CANCELLED
                t.completed_at = datetime.now().isoformat()
                t.result = "Cancelled by CEO"
                cancelled.append(t)
                for sid in t.sub_task_ids:
                    sub = self.get_task(sid)
                    if sub and sub.status in (TaskPhase.PENDING, TaskPhase.PROCESSING):
                        sub.status = TaskPhase.CANCELLED
                        sub.completed_at = datetime.now().isoformat()
                        sub.result = "Parent task cancelled"
                        cancelled.append(sub)
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

    TASK_QUEUE_PATH = Path(__file__).parent.parent.parent.parent / "company" / ".task_queue.json"

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
            self.task_histories[employee_id] = []
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

    def save_task_queue(self) -> int:
        """Persist all pending/in_progress tasks to disk for restart recovery.

        Returns the number of tasks saved.
        """
        tasks_to_save: list[dict] = []
        for emp_id, board in self.boards.items():
            for task in board.tasks:
                if task.status in (TaskPhase.PENDING, TaskPhase.PROCESSING):
                    tasks_to_save.append({
                        "employee_id": emp_id,
                        "description": task.description,
                        "project_id": task.project_id,
                        "project_dir": task.project_dir,
                        "original_project_id": task.original_project_id,
                        "original_project_dir": task.original_project_dir,
                        "created_at": task.created_at,
                    })

        # Also save active_tasks from company_state for frontend continuity
        active_tasks_data = [
            {"project_id": t.project_id, "task": t.task, "employee_id": t.employee_id}
            for t in company_state.active_tasks
        ]

        data = {
            "saved_at": datetime.now().isoformat(),
            "tasks": tasks_to_save,
            "active_tasks": active_tasks_data,
        }
        try:
            self.TASK_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
            self.TASK_QUEUE_PATH.write_text(json.dumps(data, default=str), encoding="utf-8")
            logger.info("Saved {} task(s) to task queue", len(tasks_to_save))
        except Exception as e:
            logger.error("Failed to save task queue: {}", e)
        return len(tasks_to_save)

    def restore_task_queue(self) -> int:
        """Restore tasks from disk after restart. All tasks become pending.

        Returns the number of tasks restored.
        """
        if not self.TASK_QUEUE_PATH.exists():
            return 0
        try:
            raw = json.loads(self.TASK_QUEUE_PATH.read_text(encoding="utf-8"))
            tasks = raw.get("tasks", [])
            active_tasks_data = raw.get("active_tasks", [])

            restored = 0
            for t in tasks:
                emp_id = t["employee_id"]
                # Skip employees without registered executors — they can't run tasks
                if emp_id not in self.executors:
                    logger.warning("Skipping restored task for unregistered employee {}", emp_id)
                    continue
                board = self.boards.get(emp_id)
                if not board:
                    board = AgentTaskBoard()
                    self.boards[emp_id] = board
                task = board.push(
                    t["description"],
                    project_id=t.get("project_id", ""),
                    project_dir=t.get("project_dir", ""),
                )
                task.original_project_id = t.get("original_project_id", "")
                task.original_project_dir = t.get("original_project_dir", "")
                restored += 1

            # Restore active_tasks to company_state so frontend sees them
            for at in active_tasks_data:
                already = any(t.project_id == at["project_id"] for t in company_state.active_tasks)
                if not already:
                    company_state.active_tasks.append(
                        TaskEntry(
                            project_id=at["project_id"],
                            task=at.get("task", ""),
                            current_owner=at.get("employee_id", at.get("current_owner", "")),
                        )
                    )

            # Clean up the file after restore
            self.TASK_QUEUE_PATH.unlink(missing_ok=True)
            if restored:
                logger.info("Restored {} task(s) from task queue", restored)
            return restored
        except Exception as e:
            logger.error("Failed to restore task queue: {}", e)
            return 0

    def abort_project(self, project_id: str) -> int:
        """Cancel board tasks AND cancel the running asyncio.Task for a project.

        Returns the number of tasks cancelled.
        """
        total_cancelled = 0
        for emp_id, board in self.boards.items():
            cancelled = board.cancel_by_project(project_id)
            for t in cancelled:
                vessel = self.vessels.get(emp_id)
                if vessel:
                    self._log(emp_id, t, "cancelled", "Task aborted by CEO")
                    self._publish_task_update(emp_id, t)
            total_cancelled += len(cancelled)

            # Cancel the running asyncio.Task if it's working on this project
            if cancelled and emp_id in self._running_tasks:
                running = self._running_tasks[emp_id]
                if not running.done():
                    running.cancel()
                    logger.info("Cancelled running asyncio.Task for {} (project {})", emp_id, project_id)

        return total_cancelled

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
        self._set_employee_status(employee_id, STATUS_WORKING)
        self._log(employee_id, task, "start", f"Starting task: {task.description}")
        self._publish_task_update(employee_id, task)

        emp = company_state.employees.get(employee_id)
        if emp:
            emp.current_task_summary = task.description[:100]

        # 2. Set contextvars
        loop_token = _current_vessel.set(vessel)
        task_token = _current_task_id.set(task.id)

        # 3. Create company-level TaskEntry if not already tracked
        project_id = task.project_id
        project_dir = task.project_dir
        already_tracked = any(t.project_id == project_id for t in company_state.active_tasks)
        if project_id and not already_tracked:
            company_state.active_tasks.append(
                TaskEntry(
                    project_id=project_id,
                    task=task.description,
                    routed_to=role,
                    project_dir=project_dir,
                )
            )
            await event_bus.publish(
                CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
            )

        ctx_token = current_project_id.set(project_id) if project_id else None

        agent_error = False
        try:
            # 4. Build task context with injections
            task_with_ctx = task.description
            if project_dir:
                task_with_ctx = f"{task.description}\n\n[Project workspace: {project_dir} — save all outputs here]"

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

            # 7. Sub-task loop
            for iteration in range(max_subtask_iterations):
                if task.status == TaskPhase.CANCELLED:
                    break
                pending_subs = self.boards[employee_id].get_pending_subtasks(task.id)
                if not pending_subs:
                    break

                self._log(employee_id, task, "subtask_phase", f"Processing {len(pending_subs)} sub-tasks (iteration {iteration + 1})")

                for sub in pending_subs:
                    if task.status == TaskPhase.CANCELLED:
                        break
                    await self._execute_subtask(employee_id, sub, depth=1, max_retries=max_retries, retry_delays=retry_delays, max_subtask_depth=max_subtask_depth)

                if task.status == TaskPhase.CANCELLED:
                    break
                is_complete = await self._completion_check(employee_id, task)
                if is_complete:
                    break

        except asyncio.CancelledError:
            agent_error = True
            if task.status != TaskPhase.CANCELLED:
                task.status = TaskPhase.CANCELLED
            task.result = task.result or "Cancelled by CEO"
            if not task.completed_at:
                task.completed_at = datetime.now().isoformat()
            self._log(employee_id, task, "cancelled", "Task cancelled (asyncio abort)")
        except Exception as e:
            agent_error = True
            task.status = TaskPhase.FAILED
            task.result = f"Error: {e!s}"
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

        # 8. Mark completed
        if task.status not in (TaskPhase.FAILED, TaskPhase.CANCELLED):
            task.status = TaskPhase.COMPLETE
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

        # 10. Post-task cleanup
        effective_project_id = task.project_id or task.original_project_id
        if effective_project_id and not task.parent_id:
            await self._post_task_cleanup(employee_id, task, agent_error, effective_project_id)

    # ------------------------------------------------------------------
    # Sub-task execution
    # ------------------------------------------------------------------

    async def _execute_subtask(
        self, employee_id: str, sub: AgentTask, depth: int = 1,
        *, max_retries: int = MAX_RETRIES, retry_delays: list[int] = RETRY_DELAYS,
        max_subtask_depth: int = MAX_SUBTASK_DEPTH,
    ) -> None:
        if sub.status == TaskPhase.CANCELLED:
            return
        if depth > max_subtask_depth:
            sub.status = TaskPhase.FAILED
            sub.result = "Max sub-task depth exceeded"
            self._log(employee_id, sub, "error", "Max sub-task depth exceeded")
            return

        sub.status = TaskPhase.PROCESSING
        self._log(employee_id, sub, "start", f"Sub-task: {sub.description}")
        self._publish_task_update(employee_id, sub)

        vessel = self.vessels.get(employee_id)
        loop_token = _current_vessel.set(vessel)
        task_token = _current_task_id.set(sub.id)

        try:
            def _on_log(log_type: str, content: str) -> None:
                self._log(employee_id, sub, log_type, content)

            executor = self.executors.get(employee_id)
            if not executor:
                raise RuntimeError(f"No executor for {employee_id}")

            context = TaskContext(
                project_id=sub.project_id,
                work_dir=sub.project_dir,
                employee_id=employee_id,
                task_id=sub.id,
            )

            last_err: Exception | None = None
            for attempt in range(max_retries):
                try:
                    launch_result = await executor.execute(sub.description, context, on_log=_on_log)
                    last_err = None
                    break
                except Exception as run_err:
                    last_err = run_err
                    if attempt < max_retries - 1:
                        delay = retry_delays[attempt] if attempt < len(retry_delays) else retry_delays[-1]
                        self._log(employee_id, sub, "retry", f"Attempt {attempt + 1} failed: {run_err!s} — retrying in {delay}s")
                        await asyncio.sleep(delay)
            if last_err is not None:
                raise last_err

            sub.result = launch_result.output if launch_result else ""
            sub.status = TaskPhase.COMPLETE
            self._log(employee_id, sub, "result", (sub.result or "")[:300])

            # Accumulate subtask token usage
            if launch_result and launch_result.total_tokens > 0:
                sub.model_used = launch_result.model_used
                sub.input_tokens += launch_result.input_tokens
                sub.output_tokens += launch_result.output_tokens
                sub.total_tokens += launch_result.total_tokens
                parent = self.boards[employee_id].get_task(sub.parent_id) if sub.parent_id else None
                if parent:
                    parent.input_tokens += sub.input_tokens
                    parent.output_tokens += sub.output_tokens
                    parent.total_tokens += sub.total_tokens
                    from onemancompany.core.model_costs import get_model_cost
                    costs = get_model_cost(parent.model_used or sub.model_used)
                    parent.estimated_cost_usd = (
                        parent.input_tokens * costs["input"] + parent.output_tokens * costs["output"]
                    ) / 1_000_000
        except Exception as e:
            sub.status = TaskPhase.FAILED
            sub.result = f"Error: {e!s}"
            self._log(employee_id, sub, "error", f"Sub-task failed after {max_retries} attempts: {e!s}")
            traceback.print_exc()
        finally:
            _current_vessel.reset(loop_token)
            _current_task_id.reset(task_token)

        sub.completed_at = datetime.now().isoformat()
        self._publish_task_update(employee_id, sub)

    # ------------------------------------------------------------------
    # Completion check
    # ------------------------------------------------------------------

    async def _completion_check(self, employee_id: str, task: AgentTask) -> bool:
        sub_summaries = []
        for sid in task.sub_task_ids:
            sub = self.boards[employee_id].get_task(sid)
            if sub:
                sub_summaries.append(f"- [{sub.status}] {sub.description}: {sub.result[:200]}")

        if not sub_summaries:
            return True

        prompt = (
            f"You are checking if a task is complete.\n\n"
            f"Main task: {task.description}\n\n"
            f"Sub-task results:\n" + "\n".join(sub_summaries) + "\n\n"
            f"Is this main task complete? Reply with EXACTLY 'COMPLETE' or 'INCOMPLETE'.\n"
            f"If INCOMPLETE, list additional sub-tasks needed as a JSON array:\n"
            f'[{{"description": "sub-task description"}}]\n'
        )

        try:
            from onemancompany.agents.base import tracked_ainvoke
            llm = make_llm(employee_id)
            result = await tracked_ainvoke(llm, prompt,
                category="completion_check", employee_id=employee_id)
            answer = result.content.strip()

            if "COMPLETE" in answer.upper() and "INCOMPLETE" not in answer.upper():
                self._log(employee_id, task, "completion_check", "Task judged complete")
                return True

            import json
            import re
            json_match = re.search(r'\[.*\]', answer, re.DOTALL)
            if json_match:
                new_subs = json.loads(json_match.group())
                for s in new_subs[:3]:
                    desc = s.get("description", "")
                    if desc:
                        self.boards[employee_id].push(desc, parent_id=task.id)
                        self._log(employee_id, task, "subtask_added", f"New sub-task: {desc}")

            self._log(employee_id, task, "completion_check", "Task judged incomplete, added sub-tasks")
            return False

        except Exception as e:
            self._log(employee_id, task, "error", f"Completion check failed: {e!s}")
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
                "  3. dispatch_task() 给最合适的员工，附上清晰指令和 project workspace 路径。\n"
                "  4. 复杂项目用 dispatch_team_tasks() 分阶段调度多人协作。\n"
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
    # Post-task cleanup
    # ------------------------------------------------------------------

    def _dispatch_ready_subtasks(
        self, project_id: str, employee_id: str, task: AgentTask,
        failed: bool = False,
    ) -> bool:
        """Mark employee's dispatch complete (or failed); activate any ready follow-up tasks.

        If failed=True, marks the dispatch as failed and blocks all dependent dispatches,
        then notifies the responsible officer about the failure.

        Returns True if ALL dispatches for this project are now terminal.
        """
        from onemancompany.core.project_archive import (
            record_dispatch_completion, record_dispatch_failure,
            all_dispatches_complete, get_ready_dispatches, activate_dispatch,
        )

        if failed:
            blocked = record_dispatch_failure(project_id, employee_id, task.result or "Task failed")
            if blocked:
                logger.warning(
                    "Dispatch failure for {} in project {} — {} dependent tasks blocked",
                    employee_id, project_id, len(blocked),
                )
        else:
            record_dispatch_completion(project_id, employee_id)

        # Activate newly-ready dispatches (only makes sense if not a failure cascade)
        for rd in get_ready_dispatches(project_id):
            activate_dispatch(project_id, rd["dispatch_id"])
            target = self.get_handle(rd["employee_id"])
            if target:
                project_dir = task.project_dir or task.original_project_dir
                target.push_task(rd["description"], project_id=project_id, project_dir=project_dir)
        return all_dispatches_complete(project_id)

    # ------------------------------------------------------------------
    # Phase determination + dispatch table
    # ------------------------------------------------------------------

    # Internal phase keys for the dispatch table — these are string literals,
    # not TaskPhase enum values, because we need to distinguish sub-states
    # (e.g., EA_APPROVED vs ACCEPTED) that the simplified TaskPhase merges.
    _PHASE_COMPLETE = "complete"
    _PHASE_NEEDS_ACCEPTANCE = "needs_acceptance"
    _PHASE_REJECTED_BY_OFFICER = "rejected_by_officer"
    _PHASE_ACCEPTED = "accepted"
    _PHASE_EA_APPROVED = "ea_approved"
    _PHASE_EA_REJECTED = "ea_rejected"

    @staticmethod
    def _determine_project_phase(project: dict | None) -> str:
        """Derive internal phase key from project dict fields.

        Returns one of the _PHASE_* constants for the handler dispatch table.
        """
        if not project:
            return EmployeeManager._PHASE_COMPLETE

        # Simple tasks NEVER enter the acceptance/review flow — hardcoded rule
        task_type = project.get("task_type", "simple")
        if task_type != "project":
            return EmployeeManager._PHASE_COMPLETE

        criteria = project.get("acceptance_criteria", [])
        acceptance = project.get("acceptance_result")
        ea_review = project.get("ea_review_result")

        if not criteria:
            return EmployeeManager._PHASE_COMPLETE

        if not acceptance:
            return EmployeeManager._PHASE_NEEDS_ACCEPTANCE

        if not acceptance.get("accepted"):
            return EmployeeManager._PHASE_REJECTED_BY_OFFICER

        # Accepted by officer
        if not ea_review:
            return EmployeeManager._PHASE_ACCEPTED

        if ea_review.get("approved"):
            return EmployeeManager._PHASE_EA_APPROVED

        return EmployeeManager._PHASE_EA_REJECTED

    async def _post_task_cleanup(self, employee_id: str, task: AgentTask, agent_error: bool, project_id: str = "") -> None:
        from onemancompany.core.project_archive import (
            append_action, load_project,
        )
        from onemancompany.core.resolutions import create_resolution

        role = self._get_role(employee_id)

        if not project_id:
            project_id = task.project_id
        if not project_id:
            return

        if task.total_tokens > 0:
            from onemancompany.core.project_archive import record_project_cost
            record_project_cost(
                project_id, employee_id,
                task.model_used, task.input_tokens, task.output_tokens,
                task.estimated_cost_usd,
            )

        if not project_id.startswith("_auto_") and task.result:
            summary = task.result[:MAX_SUMMARY_LEN]
            append_action(project_id, employee_id, f"{role} task completed", summary)

        resolution = create_resolution(project_id, task.description)
        if resolution:
            resolution["employee_id"] = employee_id
            await event_bus.publish(
                CompanyEvent(type="resolution_ready", payload=resolution, agent="SYSTEM")
            )

        project = load_project(project_id)
        phase = self._determine_project_phase(project)

        # Phase dispatch table — keys are _PHASE_* string constants
        handlers = {
            self._PHASE_COMPLETE: self._handle_completed,
            self._PHASE_NEEDS_ACCEPTANCE: self._handle_needs_acceptance,
            self._PHASE_REJECTED_BY_OFFICER: self._handle_rejected_by_coo,
            self._PHASE_ACCEPTED: self._handle_accepted,
            self._PHASE_EA_APPROVED: self._handle_ea_approved,
            self._PHASE_EA_REJECTED: self._handle_ea_rejected,
        }

        handler = handlers.get(phase)
        if handler:
            await handler(employee_id, task, agent_error, project_id, project)
        else:
            # Unexpected phase — dispatch subtasks and do minimal cleanup
            self._dispatch_ready_subtasks(project_id, employee_id, task)
            await self._minimal_cleanup(project_id)

    # ------------------------------------------------------------------
    # Phase handlers
    # ------------------------------------------------------------------

    async def _handle_completed(
        self, employee_id: str, task: AgentTask, agent_error: bool,
        project_id: str, project: dict | None,
    ) -> None:
        """No acceptance criteria — dispatch subtasks, then full cleanup if all done."""
        all_done = self._dispatch_ready_subtasks(
            project_id, employee_id, task, failed=agent_error,
        )
        if all_done:
            await self._full_cleanup(employee_id, task, agent_error, project_id)
        else:
            await self._minimal_cleanup(project_id)

    async def _handle_needs_acceptance(
        self, employee_id: str, task: AgentTask, agent_error: bool,
        project_id: str, project: dict | None,
    ) -> None:
        """Has criteria, not yet accepted — dispatch subtasks, push acceptance if all done."""
        # Guard: if the just-completed task WAS an acceptance task but accept_project()
        # was never called (acceptance_result still None), don't re-push immediately.
        # This prevents infinite loops when the acceptance executor fails silently.
        is_acceptance_task = "验收" in (task.description or "")[:20]
        if is_acceptance_task:
            from onemancompany.core.project_archive import load_project as _reload
            fresh = _reload(project_id)
            if fresh and fresh.get("acceptance_result") is None:
                # Acceptance task ran but didn't produce a result — escalate to CEO
                fails = fresh.get("acceptance_failures", 0) + 1
                fresh["acceptance_failures"] = fails
                from onemancompany.core.project_archive import _save_project
                _save_project(project_id, fresh)
                if fails >= 3:
                    await event_bus.publish(CompanyEvent(
                        type="ceo_report",
                        payload={
                            "subject": f"验收任务执行异常 — {project.get('task', '')[:60]}",
                            "report": (
                                f"项目 {project_id} 的验收任务已连续 {fails} 次未能产生验收结果。\n"
                                f"任务输出: {(task.result or '')[:200]}\n"
                                f"可能原因: 工具调用失败、API 凭证无效等。请检查后手动重试。"
                            ),
                            "action_required": True,
                            "project_id": project_id,
                        },
                        agent="SYSTEM",
                    ))
                    await self._minimal_cleanup(project_id)
                    return
                # Allow a few retries before escalating
                await self._minimal_cleanup(project_id)
                return

        all_done = self._dispatch_ready_subtasks(
            project_id, employee_id, task, failed=agent_error,
        )
        if all_done:
            from onemancompany.core.config import COO_ID
            criteria = project.get("acceptance_criteria", [])
            officer_id = project.get("responsible_officer") or COO_ID
            # Reset acceptance_failures on fresh acceptance dispatch
            if project.get("acceptance_failures"):
                from onemancompany.core.project_archive import _save_project
                project["acceptance_failures"] = 0
                _save_project(project_id, project)
            self._push_acceptance_task(
                officer_id, project_id,
                task.project_dir or task.original_project_dir,
                criteria, project,
            )
        await self._minimal_cleanup(project_id)

    async def _handle_rejected_by_coo(
        self, employee_id: str, task: AgentTask, agent_error: bool,
        project_id: str, project: dict | None,
    ) -> None:
        """Officer rejected — push rectification, reset acceptance."""
        from onemancompany.core.config import COO_ID
        from onemancompany.core.project_archive import _save_project

        acceptance = project.get("acceptance_result", {})
        officer_id = acceptance.get("officer_id") or project.get("responsible_officer") or COO_ID
        rejection_notes = acceptance.get("notes", "")
        self._push_rectification_task(
            officer_id, project_id,
            task.project_dir or task.original_project_dir,
            project.get("acceptance_criteria", []), rejection_notes, project,
            source="officer",
        )
        project["acceptance_result"] = None
        _save_project(project_id, project)
        await self._minimal_cleanup(project_id)

    async def _handle_accepted(
        self, employee_id: str, task: AgentTask, agent_error: bool,
        project_id: str, project: dict | None,
    ) -> None:
        """Officer accepted, no EA review yet — push EA review."""
        from onemancompany.core.config import EA_ID

        self._push_ea_review_task(
            EA_ID, project_id,
            task.project_dir or task.original_project_dir,
            project.get("acceptance_criteria", []),
            project.get("acceptance_result", {}), project,
        )
        await self._minimal_cleanup(project_id)

    async def _handle_ea_approved(
        self, employee_id: str, task: AgentTask, agent_error: bool,
        project_id: str, project: dict | None,
    ) -> None:
        """EA approved — project complete. Retrospective only for PROJECT tasks and if EA flagged it."""
        task_type = (project or {}).get("task_type", "simple")
        ea_review = (project or {}).get("ea_review_result", {})
        # Simple tasks NEVER get retrospective — hardcoded rule
        needs_retro = (task_type == "project") and ea_review.get("needs_retrospective", False)
        await self._full_cleanup(employee_id, task, agent_error, project_id, run_retrospective=needs_retro)

    async def _handle_ea_rejected(
        self, employee_id: str, task: AgentTask, agent_error: bool,
        project_id: str, project: dict | None,
    ) -> None:
        """EA rejected — push rectification, reset acceptance + ea_review."""
        from onemancompany.core.config import COO_ID
        from onemancompany.core.project_archive import _save_project

        officer_id = project.get("responsible_officer") or COO_ID
        ea_notes = project.get("ea_review_result", {}).get("notes", "")
        self._push_rectification_task(
            officer_id, project_id,
            task.project_dir or task.original_project_dir,
            project.get("acceptance_criteria", []), ea_notes, project,
        )
        project["acceptance_result"] = None
        project["ea_review_result"] = None
        _save_project(project_id, project)
        await self._minimal_cleanup(project_id)

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

        company_state.active_tasks = [
            t for t in company_state.active_tasks if t.project_id != project_id
        ]

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

        self.save_task_queue()
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
        from onemancompany.core.config import EMPLOYEES_DIR, FOUNDING_IDS, get_workspace_dir
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

            # Register in active_tasks
            company_state.active_tasks.append(
                TaskEntry(
                    project_id=project_id,
                    task=task_description or task_name,
                    routed_to=task_name,
                )
            )
            await event_bus.publish(
                CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
            )

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

            # Remove from active_tasks
            company_state.active_tasks = [
                t for t in company_state.active_tasks if t.project_id != project_id
            ]

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

    async def _minimal_cleanup(self, project_id: str) -> None:
        from onemancompany.tools.sandbox import cleanup_sandbox as _cleanup_sandbox
        await _cleanup_sandbox()
        await event_bus.publish(
            CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
        )

    # ------------------------------------------------------------------
    # Acceptance / review / rectification task dispatching
    # ------------------------------------------------------------------

    def _push_acceptance_task(
        self,
        officer_id: str,
        project_id: str,
        project_dir: str,
        criteria: list[str],
        project: dict,
    ) -> None:
        handle = self.get_handle(officer_id)
        if not handle:
            print(f"[acceptance] WARNING: No handle for officer {officer_id}")
            return

        # Record dispatch for acceptance task (visible in Gantt)
        from onemancompany.core.project_archive import record_dispatch
        record_dispatch(project_id, officer_id, "项目验收", task_type="acceptance")

        criteria_text = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(criteria))
        timeline = project.get("timeline", [])
        timeline_lines = []
        for entry in timeline[-10:]:
            emp_entry = entry.get("employee_id", "?")
            action = entry.get("action", "")
            detail = entry.get("detail", "")[:100]
            timeline_lines.append(f"  - [{emp_entry}] {action}: {detail}")
        timeline_text = "\n".join(timeline_lines) if timeline_lines else "  (no entries)"

        acceptance_task = (
            f"项目验收任务（严格验收）\n\n"
            f"项目任务: {project.get('task', '')}\n\n"
            f"验收标准:\n{criteria_text}\n\n"
            f"项目记录摘要:\n{timeline_text}\n\n"
            f"⚠️ 严格验收要求（必须执行，不可跳过）:\n"
            f"1. 实际验证: 你必须亲自验证每一项交付物，而不是仅凭项目记录判断。\n"
            f"   - 代码/软件: 必须实际构建并运行，确认功能正常，无报错\n"
            f"   - 文档/报告: 必须逐条核实内容的准确性和完整性\n"
            f"   - 任何交付物: 以真实终端用户的标准测试，验证是否满足所有验收标准\n"
            f"2. 逐条对照: 将每个验收标准逐一与实际输出对比，记录通过/不通过\n"
            f"3. 质量抽查: 检查代码质量、边界情况处理、错误处理等\n"
            f"4. 验收决定:\n"
            f"   - 全部通过: 调用 accept_project(accepted=true)，附上验证证据\n"
            f"   - 任一不通过:\n"
            f"     a. 诊断根因：调查具体为什么失败（检查 workspace 文件、错误日志、工具可用性、API 凭证等）\n"
            f"     b. 分类问题：缺少工具/凭证 | 技术能力不足 | 需求不清晰 | 时间/资源不够 | 代码质量问题\n"
            f"     c. 提出可执行建议：明确下一步该怎么修复（如'需要配置 API Key'、'需要增加发布工具'）\n"
            f"     d. 调用 accept_project(accepted=false, notes='...')，notes 必须包含：不通过项、根因分析、修复建议\n"
            f"     系统将自动触发整改流程，相关员工将被要求整改后重新提交\n\n"
            f"如果需要补充或调整验收标准，请先调用 set_acceptance_criteria() 更新。\n"
            f"[Project ID: {project_id}] [Project workspace: {project_dir}]"
        )
        handle.push_task(acceptance_task, project_id=project_id, project_dir=project_dir)
        print(f"[acceptance] Pushed acceptance task to officer {officer_id} for project {project_id}")

    def _push_ea_review_task(
        self,
        ea_id: str,
        project_id: str,
        project_dir: str,
        criteria: list[str],
        acceptance_result: dict,
        project: dict,
    ) -> None:
        handle = self.get_handle(ea_id)
        if not handle:
            print(f"[ea-review] WARNING: No handle for EA {ea_id}")
            return

        # Record dispatch for EA review task (visible in Gantt)
        from onemancompany.core.project_archive import record_dispatch
        record_dispatch(project_id, ea_id, "EA质量审核", task_type="ea_review")

        criteria_text = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(criteria))
        officer_notes = acceptance_result.get("notes", "(无备注)")
        officer_id = acceptance_result.get("officer_id", "?")

        timeline = project.get("timeline", [])
        timeline_lines = []
        for entry in timeline[-10:]:
            emp_entry = entry.get("employee_id", "?")
            action = entry.get("action", "")
            detail = entry.get("detail", "")[:100]
            timeline_lines.append(f"  - [{emp_entry}] {action}: {detail}")
        timeline_text = "\n".join(timeline_lines) if timeline_lines else "  (no entries)"

        ea_review_task = (
            f"CEO质量把关任务（EA代表CEO进行最终审核）\n\n"
            f"项目任务: {project.get('task', '')}\n\n"
            f"验收标准:\n{criteria_text}\n\n"
            f"负责人({officer_id})验收意见: {officer_notes}\n\n"
            f"项目记录摘要:\n{timeline_text}\n\n"
            f"⚠️ 你作为EA，代表CEO对项目进行最终质量把关。\n"
            f"负责人已经通过了验收，但CEO需要你再次确认：\n\n"
            f"1. 逐条核对验收标准: 每一条是否真正达成？不能只看报告，要验证实际交付物\n"
            f"2. 实际验证交付物:\n"
            f"   - 代码/软件: 检查项目workspace中的文件，确认代码存在且可运行\n"
            f"   - 文档: 阅读实际文档内容，确认质量达标\n"
            f"   - 检查是否有遗漏、敷衍了事、或明显质量问题\n"
            f"3. 对照CEO原始需求: 确认交付物真正满足CEO最初提出的要求\n"
            f"4. 判断是否需要复盘(retrospective):\n"
            f"   - 项目性质的任务（开发、设计、多步骤工作）→ needs_retrospective=true\n"
            f"   - 简单操作性任务（发邮件、查询、单步执行）→ needs_retrospective=false\n"
            f"5. 审核决定:\n"
            f"   - 通过: 调用 ea_review_project(approved=true, review_notes='验证详情...', needs_retrospective=true/false)\n"
            f"   - 不通过: 调用 ea_review_project(approved=false, review_notes='具体问题...')\n"
            f"     不通过时，相关负责人将收到整改通知并重新验收\n\n"
            f"[Project ID: {project_id}] [Project workspace: {project_dir}]"
        )
        handle.push_task(ea_review_task, project_id=project_id, project_dir=project_dir)
        print(f"[ea-review] Pushed EA quality review task for project {project_id}")

    def _push_rectification_task(
        self,
        officer_id: str,
        project_id: str,
        project_dir: str,
        criteria: list[str],
        rejection_notes: str,
        project: dict,
        source: str = "ea",
    ) -> None:
        handle = self.get_handle(officer_id)
        if not handle:
            print(f"[rectification] WARNING: No handle for officer {officer_id}")
            return

        # Reset dispatches for the new rectification round.
        # Add the officer as a gate dispatch so all_dispatches_complete()
        # won't return True until the officer's own task finishes (by which
        # time all sub-dispatches are registered).
        from onemancompany.core.project_archive import (
            _resolve_and_load, _save_resolved, record_dispatch,
        )
        version, doc, key = _resolve_and_load(project_id)
        if doc:
            doc["dispatches"] = []
            _save_resolved(version, key, doc)
        record_dispatch(project_id, officer_id, "Rectification coordinator", task_type="rectification")

        criteria_text = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(criteria))

        if source == "officer":
            title = "项目整改通知（验收未通过）"
            reason_label = "验收驳回理由"
            context_text = "验收未通过，需要整改。"
        else:
            title = "项目整改通知（EA代CEO驳回）"
            reason_label = "EA驳回理由"
            context_text = "EA代表CEO认为项目交付物未达标，需要整改。"

        rectification_task = (
            f"{title}\n\n"
            f"项目任务: {project.get('task', '')}\n\n"
            f"验收标准:\n{criteria_text}\n\n"
            f"{reason_label}:\n{rejection_notes}\n\n"
            f"⚠️ {context_text}\n"
            f"请根据以上驳回理由:\n"
            f"1. 分析驳回理由中指出的问题\n"
            f"2. 判断是否需要 CEO 介入（如需要购买工具、配置凭证、调整需求等）\n"
            f"   → 如需要，调用 report_to_ceo() 反馈具体需求\n"
            f"3. 能自行解决的问题，dispatch_task() 给相关员工整改\n"
            f"4. 整改完成后重新进行验收\n\n"
            f"[Project ID: {project_id}] [Project workspace: {project_dir}]"
        )
        handle.push_task(rectification_task, project_id=project_id, project_dir=project_dir)
        print(f"[rectification] Pushed rectification task to officer {officer_id} for project {project_id}")

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
