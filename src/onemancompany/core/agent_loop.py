"""Persistent agent loop with per-agent task board.

Each on-site agent (HR, COO, ...) gets a PersistentAgentLoop that:
- Lives as long as the server is running
- Maintains its own internal task board
- Processes tasks sequentially from the board
- Supports sub-task decomposition and completion checks
- Exposes task board and execution logs to the frontend
"""

from __future__ import annotations

import asyncio
import traceback
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime

from onemancompany.agents.base import BaseAgentRunner, make_llm
from onemancompany.core.config import (
    MAX_SUMMARY_LEN,
    STATUS_IDLE,
    STATUS_WORKING,
)
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.state import TaskEntry, company_state

# ---------------------------------------------------------------------------
# Context variables — set during task execution so tools can access the loop
# ---------------------------------------------------------------------------

_current_loop: ContextVar["PersistentAgentLoop | None"] = ContextVar("_current_loop", default=None)
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
    sub_task_ids: list[str] = field(default_factory=list)
    logs: list[dict] = field(default_factory=list)  # [{timestamp, type, content}]
    result: str = ""
    created_at: str = ""
    completed_at: str = ""

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
            "sub_task_ids": self.sub_task_ids,
            "logs": self.logs[-20:],  # last 20 log entries
            "result": self.result[:MAX_SUMMARY_LEN] if self.result else "",
            "created_at": self.created_at,
            "completed_at": self.completed_at,
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
        # Link to parent if this is a sub-task
        if parent_id:
            parent = self.get_task(parent_id)
            if parent:
                parent.sub_task_ids.append(task.id)
        return task

    def get_next_pending(self) -> AgentTask | None:
        """Return the oldest pending top-level task (skip cancelled)."""
        for t in self.tasks:
            if t.status == "pending" and not t.parent_id:
                return t
        return None

    def get_pending_subtasks(self, parent_id: str) -> list[AgentTask]:
        return [t for t in self.tasks if t.parent_id == parent_id and t.status == "pending"]

    def cancel_by_project(self, project_id: str) -> list[AgentTask]:
        """Cancel all tasks (and their sub-tasks) matching a project_id.

        Returns the list of cancelled tasks.
        """
        cancelled = []
        for t in self.tasks:
            if t.project_id == project_id and t.status in ("pending", "in_progress"):
                t.status = "cancelled"
                t.completed_at = datetime.now().isoformat()
                t.result = "Cancelled by CEO"
                cancelled.append(t)
                # Also cancel sub-tasks
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
# Persistent agent loop
# ---------------------------------------------------------------------------

MAX_SUBTASK_ITERATIONS = 3
MAX_SUBTASK_DEPTH = 2
MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 30]  # seconds


class PersistentAgentLoop:
    """Wraps a BaseAgentRunner with a persistent while-loop and task board."""

    def __init__(self, agent_runner: BaseAgentRunner) -> None:
        self.agent = agent_runner
        self.board = AgentTaskBoard()
        self._running = False
        self._current_task: AgentTask | None = None
        self._loop_task: asyncio.Task | None = None

    def push_task(
        self,
        description: str,
        project_id: str = "",
        project_dir: str = "",
    ) -> AgentTask:
        """Public API: add a task to this agent's board."""
        task = self.board.push(description, project_id=project_id, project_dir=project_dir)
        self._publish_task_update(task)
        return task

    async def start(self) -> None:
        """Launch the persistent loop as an asyncio.Task."""
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Gracefully stop the loop."""
        self._running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

    async def _loop(self) -> None:
        """Main while loop: poll board, execute tasks, idle sleep."""
        while self._running:
            task = self.board.get_next_pending()
            if task:
                await self._execute_task(task)
            else:
                self.agent._set_status(STATUS_IDLE)
                await asyncio.sleep(2)

    async def _execute_task(self, task: AgentTask) -> None:
        """Execute a task with sub-task support and completion checking."""
        from onemancompany.core.resolutions import current_project_id

        # 1. Mark in_progress
        task.status = "in_progress"
        self._current_task = task
        self.agent._set_status(STATUS_WORKING)
        self._log(task, "start", f"Starting task: {task.description[:100]}")
        self._publish_task_update(task)

        # Update employee current_task_summary
        emp = company_state.employees.get(self.agent.employee_id)
        if emp:
            emp.current_task_summary = task.description[:100]

        # 2. Set contextvars
        loop_token = _current_loop.set(self)
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
                    routed_to=self.agent.role,
                    project_dir=project_dir,
                )
            )
            await event_bus.publish(
                CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
            )

        # Set project_id context for file edit collection
        ctx_token = current_project_id.set(project_id) if project_id else None

        agent_error = False
        try:
            # 4. Call agent.run() with retry on transient errors
            task_with_ctx = task.description
            if project_dir:
                task_with_ctx = f"{task.description}\n\n[Project workspace: {project_dir} — save all outputs here]"

            # Log callback for streaming LLM steps into the task log
            def _on_log(log_type: str, content: str) -> None:
                self._log(task, log_type, content)

            result = None
            last_err = None
            for attempt in range(MAX_RETRIES):
                try:
                    result = await self.agent.run_streamed(task_with_ctx, on_log=_on_log)
                    last_err = None
                    break
                except Exception as run_err:
                    last_err = run_err
                    if attempt < MAX_RETRIES - 1:
                        delay = RETRY_DELAYS[attempt]
                        self._log(task, "retry", f"Attempt {attempt + 1} failed: {run_err!s} — retrying in {delay}s")
                        await asyncio.sleep(delay)
                    # else: fall through, last_err will be raised below

            if last_err is not None:
                raise last_err

            task.result = result or ""
            self._log(task, "result", (result or "")[:500])

            # 5. Sub-task loop
            for iteration in range(MAX_SUBTASK_ITERATIONS):
                if task.status == "cancelled":
                    break
                pending_subs = self.board.get_pending_subtasks(task.id)
                if not pending_subs:
                    break

                self._log(task, "subtask_phase", f"Processing {len(pending_subs)} sub-tasks (iteration {iteration + 1})")

                # Execute sub-tasks
                for sub in pending_subs:
                    if task.status == "cancelled":
                        break
                    await self._execute_subtask(sub, depth=1)

                # 6. Completion check
                if task.status == "cancelled":
                    break
                is_complete = await self._completion_check(task)
                if is_complete:
                    break

        except Exception as e:
            agent_error = True
            task.status = "failed"
            task.result = f"Error: {e!s}"
            self._log(task, "error", f"Task failed after {MAX_RETRIES} attempts: {e!s}")
            traceback.print_exc()
            await event_bus.publish(
                CompanyEvent(
                    type="agent_done",
                    payload={"role": self.agent.role, "summary": f"Error: {e!s}"},
                    agent=self.agent.role,
                )
            )
        finally:
            # Reset contextvars
            _current_loop.reset(loop_token)
            _current_task_id.reset(task_token)
            if ctx_token is not None:
                current_project_id.reset(ctx_token)

        # 7. Mark completed (if not already failed/cancelled)
        if task.status not in ("failed", "cancelled"):
            task.status = "completed"
        if not task.completed_at:
            task.completed_at = datetime.now().isoformat()
        self._current_task = None
        self._log(task, "end", f"Task {task.status}")
        self._publish_task_update(task)

        # Clear employee summary
        if emp:
            emp.current_task_summary = ""

        # 8. Post-task cleanup (only for top-level tasks with project_id)
        if task.project_id and not task.parent_id:
            await self._post_task_cleanup(task, agent_error)

    async def _execute_subtask(self, sub: AgentTask, depth: int = 1) -> None:
        """Execute a sub-task (depth-limited)."""
        if sub.status == "cancelled":
            return
        if depth > MAX_SUBTASK_DEPTH:
            sub.status = "failed"
            sub.result = "Max sub-task depth exceeded"
            self._log(sub, "error", "Max sub-task depth exceeded")
            return

        sub.status = "in_progress"
        self._log(sub, "start", f"Sub-task: {sub.description[:100]}")
        self._publish_task_update(sub)

        loop_token = _current_loop.set(self)
        task_token = _current_task_id.set(sub.id)

        try:
            def _on_log(log_type: str, content: str) -> None:
                self._log(sub, log_type, content)

            last_err = None
            for attempt in range(MAX_RETRIES):
                try:
                    result = await self.agent.run_streamed(sub.description, on_log=_on_log)
                    last_err = None
                    break
                except Exception as run_err:
                    last_err = run_err
                    if attempt < MAX_RETRIES - 1:
                        delay = RETRY_DELAYS[attempt]
                        self._log(sub, "retry", f"Attempt {attempt + 1} failed: {run_err!s} — retrying in {delay}s")
                        await asyncio.sleep(delay)
            if last_err is not None:
                raise last_err
            sub.result = result or ""
            sub.status = "completed"
            self._log(sub, "result", (result or "")[:300])
        except Exception as e:
            sub.status = "failed"
            sub.result = f"Error: {e!s}"
            self._log(sub, "error", f"Sub-task failed after {MAX_RETRIES} attempts: {e!s}")
            traceback.print_exc()
        finally:
            _current_loop.reset(loop_token)
            _current_task_id.reset(task_token)

        sub.completed_at = datetime.now().isoformat()
        self._publish_task_update(sub)

    async def _completion_check(self, task: AgentTask) -> bool:
        """Ask LLM if the main task is done given sub-task results."""
        # Gather sub-task results
        sub_summaries = []
        for sid in task.sub_task_ids:
            sub = self.board.get_task(sid)
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
            llm = make_llm(self.agent.employee_id)
            result = await llm.ainvoke(prompt)
            answer = result.content.strip()

            if "COMPLETE" in answer.upper() and "INCOMPLETE" not in answer.upper():
                self._log(task, "completion_check", "Task judged complete")
                return True

            # Parse additional sub-tasks
            import json
            import re
            json_match = re.search(r'\[.*\]', answer, re.DOTALL)
            if json_match:
                new_subs = json.loads(json_match.group())
                for s in new_subs[:3]:  # max 3 new sub-tasks
                    desc = s.get("description", "")
                    if desc:
                        self.board.push(desc, parent_id=task.id)
                        self._log(task, "subtask_added", f"New sub-task: {desc[:100]}")

            self._log(task, "completion_check", "Task judged incomplete, added sub-tasks")
            return False

        except Exception as e:
            self._log(task, "error", f"Completion check failed: {e!s}")
            return True  # assume complete on error to avoid infinite loop

    async def _post_task_cleanup(self, task: AgentTask, agent_error: bool) -> None:
        """Cleanup after a top-level task completes (absorbed from _run_agent_safe)."""
        from onemancompany.core.project_archive import append_action, complete_project
        from onemancompany.core.resolutions import create_resolution

        project_id = task.project_id
        if not project_id:
            return

        # Record agent output in project timeline
        if not project_id.startswith("_auto_") and task.result:
            summary = task.result[:MAX_SUMMARY_LEN]
            append_action(project_id, self.agent.role.lower(), f"{self.agent.role} task completed", summary)

        # Create a resolution if any file edits were accumulated
        resolution = create_resolution(project_id, task.description)
        if resolution:
            await event_bus.publish(
                CompanyEvent(type="resolution_ready", payload=resolution, agent="SYSTEM")
            )

        # Run post-task routine
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

        # Cleanup sandbox
        from onemancompany.tools.sandbox import cleanup_sandbox as _cleanup_sandbox
        await _cleanup_sandbox()

        # Reset all non-founding employees to idle
        from onemancompany.core.config import FOUNDING_LEVEL
        for emp in company_state.employees.values():
            if emp.level < FOUNDING_LEVEL:
                emp.status = STATUS_IDLE

        # Remove from company-level active_tasks
        company_state.active_tasks = [
            t for t in company_state.active_tasks if t.project_id != project_id
        ]

        # Complete project archive
        if not project_id.startswith("_auto_"):
            label = task.description or "Task completed"
            if agent_error:
                label = f"{label} (with errors)"
            complete_project(project_id, label)

        # Flush deferred reloads
        from onemancompany.core.state import flush_pending_reload
        flush_result = flush_pending_reload()
        if flush_result:
            updated = flush_result.get("employees_updated", [])
            added = flush_result.get("employees_added", [])
            if updated or added:
                print(f"[hot-reload] Post-task flush: {len(updated)} updated, {len(added)} added")

        # Broadcast state
        await event_bus.publish(
            CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
        )

    def _log(self, task: AgentTask, log_type: str, content: str) -> None:
        """Append a log entry to the task + publish agent_log event."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": log_type,
            "content": content,
        }
        task.logs.append(entry)

        # Fire-and-forget publish
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(event_bus.publish(
                CompanyEvent(
                    type="agent_log",
                    payload={
                        "employee_id": self.agent.employee_id,
                        "task_id": task.id,
                        "log": entry,
                    },
                    agent=self.agent.role,
                )
            ))
        except RuntimeError:
            pass  # no running event loop

    def _publish_task_update(self, task: AgentTask) -> None:
        """Publish an agent_task_update event."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(event_bus.publish(
                CompanyEvent(
                    type="agent_task_update",
                    payload={
                        "employee_id": self.agent.employee_id,
                        "task": task.to_dict(),
                    },
                    agent=self.agent.role,
                )
            ))
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------

agent_loops: dict[str, PersistentAgentLoop] = {}


def register_agent(employee_id: str, agent_runner: BaseAgentRunner) -> PersistentAgentLoop:
    loop = PersistentAgentLoop(agent_runner)
    agent_loops[employee_id] = loop
    return loop


def get_agent_loop(employee_id: str) -> PersistentAgentLoop | None:
    return agent_loops.get(employee_id)


async def start_all_loops() -> None:
    """Start all registered agent loops. Called from lifespan startup."""
    for loop in agent_loops.values():
        await loop.start()


async def stop_all_loops() -> None:
    """Stop all registered agent loops. Called from lifespan shutdown."""
    for loop in agent_loops.values():
        await loop.stop()
