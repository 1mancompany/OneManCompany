"""Employee task execution — on-demand dispatch with pluggable launchers.

Replaces the persistent while-loop-per-employee pattern with:
- EmployeeManager: centralized coordinator, dispatches tasks on-demand
- Launcher protocol: pluggable execution backends (LangChain, Claude CLI, etc.)
- File-based progress log: cross-task context (ralph-inspired)

Key design:
  No persistent while-loop per employee — tasks execute on-demand.
  When a task is pushed, EmployeeManager creates a one-shot asyncio.Task.
  When that task completes, the next pending task is auto-scheduled.
  Between tasks, no process/coroutine is occupied.
"""

from __future__ import annotations

import asyncio
import traceback
import uuid
from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

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

# ---------------------------------------------------------------------------
# Context variables — set during task execution so tools can access context
# ---------------------------------------------------------------------------

_current_loop: ContextVar["EmployeeHandle | None"] = ContextVar("_current_loop", default=None)
_current_task_id: ContextVar[str] = ContextVar("_current_task_id", default="")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class AgentTask:
    id: str
    description: str
    status: str = "pending"  # pending / in_progress / completed / failed / cancelled
    parent_id: str = ""  # non-empty if this is a sub-task
    project_id: str = ""  # links to company project archive
    project_dir: str = ""  # project workspace path
    original_project_id: str = ""  # preserved across multiple dispatch_task calls
    original_project_dir: str = ""
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

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status,
            "parent_id": self.parent_id,
            "project_id": self.project_id,
            "original_project_id": self.original_project_id,
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
            if t.status == "pending" and not t.parent_id:
                return t
        return None

    def get_pending_subtasks(self, parent_id: str) -> list[AgentTask]:
        return [t for t in self.tasks if t.parent_id == parent_id and t.status == "pending"]

    def cancel_by_project(self, project_id: str) -> list[AgentTask]:
        cancelled = []
        for t in self.tasks:
            if t.project_id == project_id and t.status in ("pending", "in_progress"):
                t.status = "cancelled"
                t.completed_at = datetime.now().isoformat()
                t.result = "Cancelled by CEO"
                cancelled.append(t)
                for sid in t.sub_task_ids:
                    sub = self.get_task(sid)
                    if sub and sub.status in ("pending", "in_progress"):
                        sub.status = "cancelled"
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
# Launcher protocol — pluggable execution backends
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
    """Context passed to launchers alongside the task description."""
    project_id: str = ""
    work_dir: str = ""
    employee_id: str = ""


class Launcher(ABC):
    """Protocol for executing a single task iteration.

    Launchers are pluggable execution backends. The platform defines the protocol;
    each launcher implements it for a specific AI/execution environment.
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


class LangChainLauncher(Launcher):
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


class ClaudeSessionLauncher(Launcher):
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

        output = await run_claude_session(
            self.employee_id,
            context.project_id or "default",
            prompt=task_description,
            work_dir=context.work_dir,
        )
        if on_log:
            on_log("result", (output or "")[:500])
        return LaunchResult(output=output or "")


class ScriptLauncher(Launcher):
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
# Employee Handle — backward-compatible view for get_agent_loop() callers
# ---------------------------------------------------------------------------

class _AgentRef:
    """Minimal agent reference for backward compat (loop.agent.employee_id)."""

    def __init__(self, employee_id: str) -> None:
        self.employee_id = employee_id

    @property
    def role(self) -> str:
        emp = company_state.employees.get(self.employee_id)
        return emp.role if emp else "Employee"


class EmployeeHandle:
    """Per-employee view into the EmployeeManager.

    Provides the same interface that PersistentAgentLoop exposed so that
    existing callers (common_tools, routes.py) don't need changes.
    """

    def __init__(self, manager: "EmployeeManager", employee_id: str) -> None:
        self.manager = manager
        self.employee_id = employee_id
        self.agent = _AgentRef(employee_id)

    @property
    def board(self) -> AgentTaskBoard:
        return self.manager.boards.get(self.employee_id, AgentTaskBoard())

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
        self.launchers: dict[str, Launcher] = {}
        self.task_histories: dict[str, list[dict]] = {}
        self._history_summaries: dict[str, str] = {}
        self._handles: dict[str, EmployeeHandle] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, employee_id: str, launcher: Launcher) -> EmployeeHandle:
        """Register an employee with a launcher. Returns an EmployeeHandle."""
        self.launchers[employee_id] = launcher
        if employee_id not in self.boards:
            self.boards[employee_id] = AgentTaskBoard()
        if employee_id not in self.task_histories:
            self.task_histories[employee_id] = []
        handle = EmployeeHandle(self, employee_id)
        self._handles[employee_id] = handle
        return handle

    def unregister(self, employee_id: str) -> None:
        self.launchers.pop(employee_id, None)
        self._handles.pop(employee_id, None)

    def get_handle(self, employee_id: str) -> EmployeeHandle | None:
        return self._handles.get(employee_id)

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
            pass

    async def _run_task(self, employee_id: str, task: AgentTask) -> None:
        """Execute a task, then schedule the next one."""
        try:
            await self._execute_task(employee_id, task)
        finally:
            self._running_tasks.pop(employee_id, None)
            self._schedule_next(employee_id)

    # ------------------------------------------------------------------
    # Task execution — core logic (ported from PersistentAgentLoop)
    # ------------------------------------------------------------------

    async def _execute_task(self, employee_id: str, task: AgentTask) -> None:
        from onemancompany.core.resolutions import current_project_id

        role = self._get_role(employee_id)
        handle = self._handles.get(employee_id)

        # 1. Mark in_progress
        task.status = "in_progress"
        self._set_employee_status(employee_id, STATUS_WORKING)
        self._log(employee_id, task, "start", f"Starting task: {task.description}")
        self._publish_task_update(employee_id, task)

        emp = company_state.employees.get(employee_id)
        if emp:
            emp.current_task_summary = task.description[:100]

        # 2. Set contextvars
        loop_token = _current_loop.set(handle)
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
            progress = _load_progress(employee_id)
            if progress:
                task_with_ctx += f"\n\n[Previous Work Learnings]\n{progress}"

            # Log callback
            def _on_log(log_type: str, content: str) -> None:
                self._log(employee_id, task, log_type, content)

            # 5. Execute via launcher with retry
            launcher = self.launchers.get(employee_id)
            if not launcher:
                raise RuntimeError(f"No launcher registered for employee {employee_id}")

            context = TaskContext(
                project_id=task.project_id,
                work_dir=project_dir,
                employee_id=employee_id,
            )

            launch_result: LaunchResult | None = None
            last_err: Exception | None = None
            for attempt in range(MAX_RETRIES):
                try:
                    launch_result = await launcher.execute(task_with_ctx, context, on_log=_on_log)
                    last_err = None
                    break
                except GraphRecursionError as rec_err:
                    last_err = rec_err
                    self._log(employee_id, task, "error", f"Agent hit recursion limit: {rec_err!s}")
                    break
                except Exception as run_err:
                    last_err = run_err
                    if attempt < MAX_RETRIES - 1:
                        delay = RETRY_DELAYS[attempt]
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
            for iteration in range(MAX_SUBTASK_ITERATIONS):
                if task.status == "cancelled":
                    break
                pending_subs = self.boards[employee_id].get_pending_subtasks(task.id)
                if not pending_subs:
                    break

                self._log(employee_id, task, "subtask_phase", f"Processing {len(pending_subs)} sub-tasks (iteration {iteration + 1})")

                for sub in pending_subs:
                    if task.status == "cancelled":
                        break
                    await self._execute_subtask(employee_id, sub, depth=1)

                if task.status == "cancelled":
                    break
                is_complete = await self._completion_check(employee_id, task)
                if is_complete:
                    break

        except Exception as e:
            agent_error = True
            task.status = "failed"
            task.result = f"Error: {e!s}"
            self._log(employee_id, task, "error", f"Task failed after {MAX_RETRIES} attempts: {e!s}")
            traceback.print_exc()
            await event_bus.publish(
                CompanyEvent(
                    type="agent_done",
                    payload={"role": role, "summary": f"Error: {e!s}"},
                    agent=role,
                )
            )
        finally:
            _current_loop.reset(loop_token)
            _current_task_id.reset(task_token)
            if ctx_token is not None:
                current_project_id.reset(ctx_token)

        # 8. Mark completed
        if task.status not in ("failed", "cancelled"):
            task.status = "completed"
        if not task.completed_at:
            task.completed_at = datetime.now().isoformat()
        self._log(employee_id, task, "end", f"Task {task.status}")
        self._publish_task_update(employee_id, task)

        # 9. Record to history + progress log
        if task.status == "completed":
            self._append_history(employee_id, task)
            summary = (task.result or "")[:200]
            _append_progress(employee_id, f"Completed: {task.description[:100]} → {summary}")

        if emp:
            emp.current_task_summary = ""

        # 10. Post-task cleanup
        effective_project_id = task.project_id or task.original_project_id
        if effective_project_id and not task.parent_id:
            await self._post_task_cleanup(employee_id, task, agent_error, effective_project_id)

    # ------------------------------------------------------------------
    # Sub-task execution
    # ------------------------------------------------------------------

    async def _execute_subtask(self, employee_id: str, sub: AgentTask, depth: int = 1) -> None:
        if sub.status == "cancelled":
            return
        if depth > MAX_SUBTASK_DEPTH:
            sub.status = "failed"
            sub.result = "Max sub-task depth exceeded"
            self._log(employee_id, sub, "error", "Max sub-task depth exceeded")
            return

        sub.status = "in_progress"
        self._log(employee_id, sub, "start", f"Sub-task: {sub.description}")
        self._publish_task_update(employee_id, sub)

        handle = self._handles.get(employee_id)
        loop_token = _current_loop.set(handle)
        task_token = _current_task_id.set(sub.id)

        try:
            def _on_log(log_type: str, content: str) -> None:
                self._log(employee_id, sub, log_type, content)

            launcher = self.launchers.get(employee_id)
            if not launcher:
                raise RuntimeError(f"No launcher for {employee_id}")

            context = TaskContext(
                project_id=sub.project_id,
                work_dir=sub.project_dir,
                employee_id=employee_id,
            )

            last_err: Exception | None = None
            for attempt in range(MAX_RETRIES):
                try:
                    launch_result = await launcher.execute(sub.description, context, on_log=_on_log)
                    last_err = None
                    break
                except Exception as run_err:
                    last_err = run_err
                    if attempt < MAX_RETRIES - 1:
                        delay = RETRY_DELAYS[attempt]
                        self._log(employee_id, sub, "retry", f"Attempt {attempt + 1} failed: {run_err!s} — retrying in {delay}s")
                        await asyncio.sleep(delay)
            if last_err is not None:
                raise last_err

            sub.result = launch_result.output if launch_result else ""
            sub.status = "completed"
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
            sub.status = "failed"
            sub.result = f"Error: {e!s}"
            self._log(employee_id, sub, "error", f"Sub-task failed after {MAX_RETRIES} attempts: {e!s}")
            traceback.print_exc()
        finally:
            _current_loop.reset(loop_token)
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
            pass

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
            load_named_project, load_iteration, list_project_files,
        )

        slug = project_id
        current_iter = ""
        if _is_iteration(project_id):
            found = _find_project_for_iteration(project_id)
            if not found:
                return ""
            slug = found
            current_iter = project_id
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
            parts.append(f'\nUse read_file("{ws_path}/{{filename}}") to read file contents.')

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
                "  1. Identify which employees can handle this work (use list_colleagues()).\n"
                "  2. dispatch_task() to the best-suited employee with clear instructions.\n"
                "  3. Include the project workspace path in dispatch so deliverables are saved correctly.\n"
                "  4. If no suitable employee exists, dispatch to HR to hire one.\n"
                "  5. Only execute yourself if absolutely no one else can do it.\n"
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
            "Save all outputs to the project workspace using save_to_project().\n"
            "Include a brief verification note in your result.\n"
            "Do NOT re-read files you already read. Do NOT loop — verify once, then finish."
        )

    # ------------------------------------------------------------------
    # Post-task cleanup (ported from PersistentAgentLoop)
    # ------------------------------------------------------------------

    async def _post_task_cleanup(self, employee_id: str, task: AgentTask, agent_error: bool, project_id: str = "") -> None:
        from onemancompany.core.project_archive import (
            append_action, load_project,
            record_dispatch_completion, all_dispatches_complete,
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
            await event_bus.publish(
                CompanyEvent(type="resolution_ready", payload=resolution, agent="SYSTEM")
            )

        project = load_project(project_id)
        acceptance_criteria = project.get("acceptance_criteria", []) if project else []
        acceptance_result = project.get("acceptance_result") if project else None
        ea_review_result = project.get("ea_review_result") if project else None

        # CASE A: Has criteria, not yet accepted
        if acceptance_criteria and not acceptance_result:
            record_dispatch_completion(project_id, employee_id)
            if all_dispatches_complete(project_id):
                from onemancompany.core.config import COO_ID
                officer_id = project.get("responsible_officer") or COO_ID
                self._push_acceptance_task(
                    officer_id, project_id,
                    task.project_dir or task.original_project_dir,
                    acceptance_criteria, project,
                )
            await self._minimal_cleanup(project_id)
            return

        # CASE B: Officer accepted → check EA review gate
        if acceptance_result and acceptance_result.get("accepted"):
            if not ea_review_result:
                from onemancompany.core.config import EA_ID
                self._push_ea_review_task(
                    EA_ID, project_id,
                    task.project_dir or task.original_project_dir,
                    acceptance_criteria, acceptance_result, project,
                )
                await self._minimal_cleanup(project_id)
                return

            if ea_review_result.get("approved"):
                await self._full_cleanup(employee_id, task, agent_error, project_id)
                return

            from onemancompany.core.config import COO_ID
            officer_id = project.get("responsible_officer") or COO_ID
            ea_notes = ea_review_result.get("notes", "")
            self._push_rectification_task(
                officer_id, project_id,
                task.project_dir or task.original_project_dir,
                acceptance_criteria, ea_notes, project,
            )
            from onemancompany.core.project_archive import _save_project
            project["acceptance_result"] = None
            project["ea_review_result"] = None
            _save_project(project_id, project)
            await self._minimal_cleanup(project_id)
            return

        # CASE C: No criteria
        if not acceptance_criteria:
            record_dispatch_completion(project_id, employee_id)
            if all_dispatches_complete(project_id):
                await self._full_cleanup(employee_id, task, agent_error, project_id)
            else:
                await self._minimal_cleanup(project_id)
            return

        record_dispatch_completion(project_id, employee_id)
        await self._minimal_cleanup(project_id)

    async def _full_cleanup(self, employee_id: str, task: AgentTask, agent_error: bool, project_id: str) -> None:
        from onemancompany.core.project_archive import append_action, complete_project
        from onemancompany.core.resolutions import create_resolution, current_project_id

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
            await event_bus.publish(
                CompanyEvent(type="resolution_ready", payload=routine_resolution, agent="SYSTEM")
            )

        from onemancompany.tools.sandbox import cleanup_sandbox as _cleanup_sandbox
        await _cleanup_sandbox()

        from onemancompany.core.config import FOUNDING_LEVEL
        for emp in company_state.employees.values():
            if emp.level < FOUNDING_LEVEL:
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

        await event_bus.publish(
            CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
        )

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
            f"   - 任一不通过: 调用 accept_project(accepted=false)，详细列出不通过项和具体问题，"
            f"相关员工将被要求整改后重新提交\n\n"
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
            f"4. 审核决定:\n"
            f"   - 通过: 调用 ea_review_project(approved=true, review_notes='验证详情...')\n"
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
        ea_rejection_notes: str,
        project: dict,
    ) -> None:
        handle = self.get_handle(officer_id)
        if not handle:
            print(f"[rectification] WARNING: No handle for officer {officer_id}")
            return

        criteria_text = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(criteria))

        rectification_task = (
            f"项目整改通知（EA代CEO驳回）\n\n"
            f"项目任务: {project.get('task', '')}\n\n"
            f"验收标准:\n{criteria_text}\n\n"
            f"EA驳回理由:\n{ea_rejection_notes}\n\n"
            f"⚠️ EA代表CEO认为项目交付物未达标，需要整改。\n"
            f"请根据以上驳回理由:\n"
            f"1. 分析具体哪些问题需要修复\n"
            f"2. 将整改任务 dispatch_task() 给相关员工执行\n"
            f"3. 整改完成后重新进行验收\n\n"
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
            pass

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
            pass


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

employee_manager = EmployeeManager()


# ---------------------------------------------------------------------------
# Backward-compatible API
# ---------------------------------------------------------------------------

# Legacy registry alias (used by some imports)
agent_loops: dict[str, EmployeeHandle] = employee_manager._handles


def register_agent(employee_id: str, agent_runner: BaseAgentRunner) -> EmployeeHandle:
    """Register a company-hosted employee with a LangChain agent."""
    launcher = LangChainLauncher(agent_runner)
    return employee_manager.register(employee_id, launcher)


def register_self_hosted(employee_id: str) -> EmployeeHandle:
    """Register a self-hosted employee (Claude CLI sessions)."""
    launcher = ClaudeSessionLauncher(employee_id)
    return employee_manager.register(employee_id, launcher)


def get_agent_loop(employee_id: str) -> EmployeeHandle | None:
    """Get an employee's handle (backward compat for PersistentAgentLoop callers)."""
    return employee_manager.get_handle(employee_id)


async def start_all_loops() -> None:
    """No-op — tasks are dispatched on-demand, no persistent loops to start."""
    pass


async def stop_all_loops() -> None:
    """Cancel any running task executions."""
    tasks = list(employee_manager._running_tasks.values())
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    employee_manager._running_tasks.clear()


async def register_and_start_agent(employee_id: str, agent_runner: BaseAgentRunner) -> EmployeeHandle:
    """Register a new agent (no persistent loop to start)."""
    return register_agent(employee_id, agent_runner)
