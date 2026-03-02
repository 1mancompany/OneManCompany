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
            "logs": self.logs[-50:],  # last 50 log entries
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

# Task history constants
MAX_HISTORY_ENTRIES = 8       # keep last N full entries before compressing
MAX_HISTORY_CHARS = 3000      # total char threshold to trigger compression
RESULT_SNIPPET_LEN = 300      # truncate each result to this length


class PersistentAgentLoop:
    """Wraps a BaseAgentRunner with a persistent while-loop and task board."""

    def __init__(self, agent_runner: BaseAgentRunner) -> None:
        self.agent = agent_runner
        self.board = AgentTaskBoard()
        self._running = False
        self._current_task: AgentTask | None = None
        self._loop_task: asyncio.Task | None = None
        # Task history for cross-task context
        self.task_history: list[dict] = []   # [{task, result, completed_at}]
        self._history_summary: str = ""       # compressed summary of older entries

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
        self._log(task, "start", f"Starting task: {task.description}")
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

            # Inject workflow requirements for project tasks
            if task.project_id:
                workflow_ctx = self._get_project_workflow_context(task)
                if workflow_ctx:
                    task_with_ctx = f"{task_with_ctx}\n\n{workflow_ctx}"

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
            self._log(task, "result", result or "")

            # 4b. Record token usage from agent
            usage = getattr(self.agent, '_last_usage', {})
            if usage:
                task.model_used = usage.get("model", "")
                task.input_tokens += usage.get("input_tokens", 0)
                task.output_tokens += usage.get("output_tokens", 0)
                task.total_tokens += usage.get("total_tokens", 0)
                from onemancompany.core.model_costs import get_model_cost
                costs = get_model_cost(task.model_used)
                task.estimated_cost_usd = (
                    task.input_tokens * costs["input"] + task.output_tokens * costs["output"]
                ) / 1_000_000

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

        # 7b. Record to task history for cross-task context
        if task.status == "completed":
            self._append_history(task)

        # Clear employee summary
        if emp:
            emp.current_task_summary = ""

        # 8. Post-task cleanup (only for top-level tasks with project_id)
        effective_project_id = task.project_id or task.original_project_id
        if effective_project_id and not task.parent_id:
            await self._post_task_cleanup(task, agent_error, effective_project_id)

    # ------------------------------------------------------------------
    # Task history management
    # ------------------------------------------------------------------

    def _append_history(self, task: AgentTask) -> None:
        """Record a completed task into the history ring."""
        self.task_history.append({
            "task": task.description[:200],
            "result": (task.result or "")[:RESULT_SNIPPET_LEN],
            "completed_at": task.completed_at or datetime.now().isoformat(),
        })
        # Fire-and-forget compression check
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._maybe_compress_history())
        except RuntimeError:
            pass

    async def _maybe_compress_history(self) -> None:
        """If history exceeds size limits, compress the oldest half into a summary."""
        total = sum(len(h["task"]) + len(h["result"]) for h in self.task_history)
        total += len(self._history_summary)
        if total <= MAX_HISTORY_CHARS or len(self.task_history) <= MAX_HISTORY_ENTRIES:
            return

        # Compress oldest half
        split = len(self.task_history) // 2
        old_entries = self.task_history[:split]
        self.task_history = self.task_history[split:]

        old_text = "\n".join(
            f"- [{h['completed_at'][:10]}] {h['task']}: {h['result']}"
            for h in old_entries
        )
        if self._history_summary:
            old_text = f"Previous summary:\n{self._history_summary}\n\nNew entries:\n{old_text}"

        try:
            llm = make_llm(self.agent.employee_id)
            resp = await llm.ainvoke(
                f"Summarize this employee's completed work into a concise paragraph (max 200 words). "
                f"Focus on key decisions, findings, and outputs:\n\n{old_text}"
            )
            self._history_summary = resp.content.strip()[:800]
        except Exception:
            # On failure, just concatenate a crude summary
            self._history_summary = (self._history_summary + "\n" + old_text)[:800]

    def get_history_context(self) -> str:
        """Build a prompt section with this agent's recent work history."""
        if not self.task_history and not self._history_summary:
            return ""
        parts = ["\n\n## Your Recent Work History:"]
        if self._history_summary:
            parts.append(f"Earlier work summary: {self._history_summary}")
        for h in self.task_history:
            parts.append(f"- [{h['completed_at'][:10]}] Task: {h['task']}\n  Result: {h['result']}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Workflow context injection
    # ------------------------------------------------------------------

    def _get_project_workflow_context(self, task: AgentTask) -> str:
        """Load relevant workflow instructions for a project task.

        Reads the project_intake_workflow and extracts the self-verification
        requirements from Phase 4 so every agent executing a project task
        knows they must verify before marking complete.
        """
        from onemancompany.core.config import load_workflows
        from onemancompany.core.workflow_engine import parse_workflow

        workflows = load_workflows()
        workflow_doc = workflows.get("project_intake_workflow", "")
        if not workflow_doc:
            return ""

        wf = parse_workflow("project_intake_workflow", workflow_doc)

        # Find Phase 4 (Execution) — extract self-verification instructions
        verification_instructions = ""
        for step in wf.steps:
            if "Execution" in step.title or "Tracking" in step.title:
                # Extract only the verification-related instructions
                for inst in step.instructions:
                    if any(kw in inst.lower() for kw in [
                        "verification", "verify", "build and run",
                        "test", "do not report", "验证", "验收",
                    ]):
                        verification_instructions += f"  - {inst}\n"
                break

        if not verification_instructions:
            # Fallback: generic verification requirement
            verification_instructions = (
                "  - For code/software: Build and run it. Fix all errors until it runs successfully.\n"
                "  - For documents/reports: Re-read and verify all claims, data, and formatting.\n"
                "  - For any deliverable: Test it as a real end-user would.\n"
                "  - Do NOT report a task as complete unless you have personally verified it works.\n"
            )

        return (
            "[MANDATORY — Self-Verification Before Completion]\n"
            "According to the company project workflow, you MUST verify your work before "
            "reporting it as complete:\n"
            f"{verification_instructions}"
            "Include verification evidence (test output, run logs, or screenshots) in your result.\n"
            "If verification fails, fix the issues and re-verify until everything works."
        )

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
        self._log(sub, "start", f"Sub-task: {sub.description}")
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

            # Accumulate subtask token usage
            usage = getattr(self.agent, '_last_usage', {})
            if usage:
                sub.model_used = usage.get("model", "")
                sub.input_tokens += usage.get("input_tokens", 0)
                sub.output_tokens += usage.get("output_tokens", 0)
                sub.total_tokens += usage.get("total_tokens", 0)
                # Bubble up to parent task
                parent = self.board.get_task(sub.parent_id) if sub.parent_id else None
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
                        self._log(task, "subtask_added", f"New sub-task: {desc}")

            self._log(task, "completion_check", "Task judged incomplete, added sub-tasks")
            return False

        except Exception as e:
            self._log(task, "error", f"Completion check failed: {e!s}")
            return True  # assume complete on error to avoid infinite loop

    async def _post_task_cleanup(self, task: AgentTask, agent_error: bool, project_id: str = "") -> None:
        """Cleanup after a top-level task completes — routes through acceptance flow."""
        from onemancompany.core.project_archive import (
            append_action, load_project,
            record_dispatch_completion, all_dispatches_complete,
        )
        from onemancompany.core.resolutions import create_resolution

        if not project_id:
            project_id = task.project_id
        if not project_id:
            return

        # --- Record cost data to project archive ---
        if task.total_tokens > 0:
            from onemancompany.core.project_archive import record_project_cost
            record_project_cost(
                project_id, self.agent.employee_id,
                task.model_used, task.input_tokens, task.output_tokens,
                task.estimated_cost_usd,
            )

        # --- Always: record timeline entry + create resolution ---
        if not project_id.startswith("_auto_") and task.result:
            summary = task.result[:MAX_SUMMARY_LEN]
            append_action(project_id, self.agent.employee_id, f"{self.agent.role} task completed", summary)

        resolution = create_resolution(project_id, task.description)
        if resolution:
            await event_bus.publish(
                CompanyEvent(type="resolution_ready", payload=resolution, agent="SYSTEM")
            )

        project = load_project(project_id)
        acceptance_criteria = project.get("acceptance_criteria", []) if project else []
        acceptance_result = project.get("acceptance_result") if project else None

        # --- CASE A: Has criteria, not yet accepted → check dispatches ---
        if acceptance_criteria and not acceptance_result:
            record_dispatch_completion(project_id, self.agent.employee_id)
            if all_dispatches_complete(project_id):
                # Push acceptance task to responsible officer
                from onemancompany.core.config import COO_ID
                officer_id = project.get("responsible_officer") or COO_ID
                self._push_acceptance_task(
                    officer_id, project_id,
                    task.project_dir or task.original_project_dir,
                    acceptance_criteria, project,
                )
            # Minimal cleanup only
            await self._minimal_cleanup(project_id)
            return

        # --- CASE B: Acceptance result exists and accepted → full cleanup + retrospective ---
        if acceptance_result and acceptance_result.get("accepted"):
            await self._full_cleanup(task, agent_error, project_id)
            return

        # --- CASE C: No criteria at all → old behavior, full cleanup + retrospective ---
        if not acceptance_criteria:
            await self._full_cleanup(task, agent_error, project_id)
            return

        # Fallback: criteria exist but rejected — just do minimal cleanup
        await self._minimal_cleanup(project_id)

    async def _full_cleanup(self, task: AgentTask, agent_error: bool, project_id: str) -> None:
        """Full cleanup: retrospective, complete project, reset employees, broadcast."""
        from onemancompany.core.project_archive import append_action, complete_project
        from onemancompany.core.resolutions import create_resolution

        # Run post-task routine (meeting reports, etc.) with project context
        from onemancompany.core.resolutions import current_project_id
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

        # Create resolution for edits accumulated during the routine phase
        routine_resolution = create_resolution(project_id, f"Routine: {task.description}")
        if routine_resolution:
            await event_bus.publish(
                CompanyEvent(type="resolution_ready", payload=routine_resolution, agent="SYSTEM")
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

    async def _minimal_cleanup(self, project_id: str) -> None:
        """Lightweight cleanup: sandbox + state broadcast (no retrospective)."""
        from onemancompany.tools.sandbox import cleanup_sandbox as _cleanup_sandbox
        await _cleanup_sandbox()

        await event_bus.publish(
            CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
        )

    def _push_acceptance_task(
        self,
        officer_id: str,
        project_id: str,
        project_dir: str,
        criteria: list[str],
        project: dict,
    ) -> None:
        """Push an acceptance review task to the responsible xxO."""
        officer_loop = get_agent_loop(officer_id)
        if not officer_loop:
            print(f"[acceptance] WARNING: No agent loop for officer {officer_id}")
            return

        criteria_text = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(criteria))
        timeline = project.get("timeline", [])
        # Format recent timeline for context (last 10 entries)
        timeline_lines = []
        for entry in timeline[-10:]:
            emp = entry.get("employee_id", "?")
            action = entry.get("action", "")
            detail = entry.get("detail", "")[:100]
            timeline_lines.append(f"  - [{emp}] {action}: {detail}")
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
        officer_loop.push_task(acceptance_task, project_id=project_id, project_dir=project_dir)
        print(f"[acceptance] Pushed acceptance task to officer {officer_id} for project {project_id}")

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


async def register_and_start_agent(employee_id: str, agent_runner: "BaseAgentRunner") -> PersistentAgentLoop:
    """Register a new agent and start its loop immediately. Used for newly hired employees."""
    loop = register_agent(employee_id, agent_runner)
    await loop.start()
    return loop


async def stop_all_loops() -> None:
    """Stop all registered agent loops. Called from lifespan shutdown."""
    for loop in agent_loops.values():
        await loop.stop()
