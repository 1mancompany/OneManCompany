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

from onemancompany.core.async_utils import spawn_background
import json
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
    ENCODING_UTF8,
    LAUNCH_SH_FILENAME,
    MAX_HOLD_SECONDS,
    MAX_SUMMARY_LEN,
    PF_NAME,
    PF_NICKNAME,
    PF_ROLE,
    PROGRESS_LOG_FILENAME,
    SOUL_FILENAME,
    STATUS_IDLE,
    STATUS_WORKING,
    SYSTEM_AGENT,
    TASK_TREE_FILENAME,
    TL_FIELD_ACTION,
    TL_FIELD_DETAIL,
    TL_FIELD_EMPLOYEE_ID,
    TL_FIELD_TIME,
    read_text_utf,
    write_text_utf,
)
from onemancompany.core.project_archive import ITER_STATUS_FAILED, PA_TOKEN_USAGE
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.models import EventType
from onemancompany.core.state import company_state  # noqa: F401 — tests patch this
from onemancompany.core import store as _store
from onemancompany.core.vessel_config import VesselConfig

from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# EXECUTION_LOG_FILENAME removed — per-employee summary log no longer written
TASK_HISTORY_FILENAME = "task_history.json"
PROGRESS_LOG_MAX_LINES = 30
# EXECUTION_LOG_MAX_SIZE removed — per-employee summary log no longer written
MAX_SUBTASK_ITERATIONS = 3
MAX_SUBTASK_DEPTH = 2
MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 30]
MAX_HISTORY_ENTRIES = 8
MAX_HISTORY_CHARS = 3000

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
    path = Path(project_dir) / TASK_TREE_FILENAME
    if not path.exists():
        return None
    return get_tree(path)


def _save_project_tree(project_dir: str, tree):
    """Register tree in cache and save to disk.

    First call creates the file synchronously; subsequent saves are async.
    """
    from onemancompany.core.task_tree import register_tree, save_tree_async
    path = Path(project_dir) / TASK_TREE_FILENAME
    register_tree(path, tree)
    if not path.exists():
        tree.save(path)  # sync: create file on disk
    else:
        save_tree_async(path)


# ---------------------------------------------------------------------------
# Stall detection — detect unfulfilled action promises in agent output
# ---------------------------------------------------------------------------

import re as _re

# Patterns that indicate the agent is promising future actions it hasn't taken.
# These are checked ONLY when the task completes WITHOUT dispatching children.
_PROMISE_PATTERNS = _re.compile(
    r"(?:"
    # Chinese future-action phrases
    r"我将|接下来|下一步|现在开始|马上开始|即将开始|准备开始"
    r"|我会(?:立即|马上|开始)"
    r"|下面我(?:来|将|要)"
    # English future-action phrases
    r"|I will (?:now |start |begin )|I'll (?:now |start |begin )"
    r"|Let me (?:start|begin|proceed)"
    r"|Next,? I'?(?:ll| will)"
    r"|I'?m going to (?:start|begin)"
    r")",
    _re.IGNORECASE,
)


def detect_unfulfilled_promises(output: str | None) -> bool:
    """Check if agent output contains unfulfilled action promises.

    Returns True if the output contains phrases indicating the agent
    plans to take action (but hasn't, since this is checked at completion).
    """
    if not output:
        return False
    return bool(_PROMISE_PATTERNS.search(output))


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
        status_label = "completed" if dep.status == TaskPhase.ACCEPTED else dep.status
        sections.append(f"{dep.employee_id} {status_label} \"{dep.description}\":\n{result}")
    if not sections:
        return ""
    return "=== Dependency Results ===\n" + "\n\n".join(sections) + "\n=== End Dependencies ===\n\n"


# ---------------------------------------------------------------------------
# Shared role identity builder — single source of truth for all employee types
# ---------------------------------------------------------------------------

# Roles that get "coordinator" archetype (plan/delegate/review).
# All other roles get "executor" archetype (produce deliverables).
# Update this set when adding new management-level roles.
MANAGER_ROLES = {"PM", "Project Manager", "Manager", "Team Lead", "Director"}
LEVEL_LABELS = {1: "Junior", 2: "Mid-level", 3: "Senior"}


def _load_archetype_templates() -> tuple[str, str]:
    """Load manager/executor archetype templates from SOP file.

    Returns (manager_template, executor_template). Falls back to minimal defaults.
    """
    from onemancompany.core.config import load_workflows
    workflows = load_workflows()
    content = workflows.get("role_archetype_templates", "")
    if not content:
        return (
            "You are a coordinator — plan, delegate, and ensure quality.",
            "You are an executor — produce high-quality deliverables that meet acceptance criteria.",
        )
    # Split by archetype sections
    manager_block = ""
    executor_block = ""
    current = None
    for line in content.splitlines():
        if "Manager Archetype" in line:
            current = "manager"
            continue
        elif "Executor Archetype" in line:
            current = "executor"
            continue
        if current == "manager":
            manager_block += line + "\n"
        elif current == "executor":
            executor_block += line + "\n"
    return (manager_block.strip(), executor_block.strip())


def build_role_identity(employee_id: str) -> str:
    """Generate standardized role identity block from employee profile.

    Returns empty string for founding employees (they define their own via role_guide.md).
    Called by:
      - BaseAgentRunner._get_role_identity_section() → system prompt (LangChain)
      - EmployeeManager._build_company_context_block() → task prompt (Claude CLI / Script)
    """
    from onemancompany.core.config import (
        FOUNDING_IDS, PF_NAME, PF_NICKNAME, PF_ROLE, PF_DEPARTMENT, PF_LEVEL,
        EMPLOYEES_DIR, load_employee_profile_yaml, read_text_utf,
    )
    if employee_id in FOUNDING_IDS:
        return ""

    # Check for role_guide.md first (per-employee override)
    guide_path = EMPLOYEES_DIR / employee_id / "role_guide.md"
    if guide_path.exists():
        return read_text_utf(guide_path)

    profile = load_employee_profile_yaml(employee_id)
    name = profile.get(PF_NAME, "Employee")
    nickname = profile.get(PF_NICKNAME, "")
    role = profile.get(PF_ROLE, "Employee")
    department = profile.get(PF_DEPARTMENT, "")
    level = profile.get(PF_LEVEL, 1)

    level_label = LEVEL_LABELS.get(level, f"Lv.{level}")
    dept_str = f" in {department}" if department else ""
    nick_str = f" ({nickname})" if nickname else ""
    is_manager = role in MANAGER_ROLES

    manager_tmpl, executor_tmpl = _load_archetype_templates()

    header = (
        f"## Who You Are — Identity\n"
        f"You are {name}{nick_str}, a {level_label} {role}{dept_str}.\n"
    )
    if is_manager:
        return header + manager_tmpl
    return header + executor_tmpl


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
    if node.directives:
        parts.append("")
        parts.append("=== Directives from upstream ===")
        for d in node.directives:
            from_id = d.get("from", "unknown")
            text = d.get("directive", "")
            parts.append(f"[{from_id}]: {text}")
        parts.append("=== End directives ===")
    if node.result:
        parts.append(f"Result: {node.result}")
    parts.append("")

    # Children
    children = tree.get_active_children(node.id)
    if children:
        parts.append("=== Child Tasks ===")
        for child in children:
            # Include CEO_REQUEST results (CEO replies) — critical for multi-turn context
            if child.is_ceo_node and child.is_done_executing:
                child.load_content(project_dir)
                if child.result:
                    parts.append(f"  [CEO REPLY] {child.id}: {child.result}")
                continue
            if child.is_ceo_node:
                continue
            if child.status == TaskPhase.ACCEPTED:
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
    return EMPLOYEES_DIR / employee_id / TASK_HISTORY_FILENAME


def _load_task_history(employee_id: str) -> tuple[list[dict], str]:
    """Load task history and summary from disk."""
    path = _history_path(employee_id)
    if not path.exists():
        return [], ""
    try:
        data = json.loads(read_text_utf(path))
        return data.get("entries", []), data.get("summary", "")
    except Exception as e:
        logger.warning("Failed to load task history for {}: {}", employee_id, e)
        return [], ""


def _save_task_history(employee_id: str, entries: list[dict], summary: str) -> None:
    """Persist task history and summary to disk."""
    path = _history_path(employee_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_utf(path, json.dumps({
            "entries": entries,
            "summary": summary,
        }, ensure_ascii=False, indent=2))
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
    error: str | None = None  # structured error; None = no error
    model_used: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None  # provider-reported cost; None = use catalog price


class ExecutionError(Exception):
    """Raised when a task execution fails in a way the caller should handle."""


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
            cost_usd=usage.get("cost_usd"),
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
        error: str | None = None
        if output and output.startswith("[claude-daemon error]"):
            error = output
            output = ""
        if on_log:
            on_log("error" if error else "result", error or output or "")
        input_tokens = result.get("input_tokens", 0)
        output_tokens = result.get("output_tokens", 0)
        return LaunchResult(
            output=output or "",
            error=error,
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
        self.script_path = script_path or str(EMPLOYEES_DIR / employee_id / LAUNCH_SH_FILENAME)

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
            output = stdout.decode(ENCODING_UTF8, errors="replace").strip()
            if proc.returncode != 0 and not output:
                err = stderr.decode(ENCODING_UTF8, errors="replace").strip()
                error_msg = f"[script error] exit={proc.returncode}\n{err[:2000]}"
                if on_log:
                    on_log("error", error_msg)
                return LaunchResult(error=error_msg)
            if on_log:
                on_log("result", output)
            return LaunchResult(output=output)
        except asyncio.TimeoutError:
            return LaunchResult(error="[script timeout] Timed out after 600s")
        except Exception as e:
            return LaunchResult(error=f"[script error] {e}")




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
        return (emp_data or {}).get(PF_ROLE, "Employee")




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

_PROGRESS_LINE_MAX = 1000  # per-line cap for progress log (agent context, not CEO-facing)


def _append_progress(employee_id: str, entry: str) -> None:
    """Append an entry to the employee's progress log (persistent across tasks)."""
    path = EMPLOYEES_DIR / employee_id / PROGRESS_LOG_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    capped = entry[:_PROGRESS_LINE_MAX]
    with open(path, "a", encoding=ENCODING_UTF8) as f:
        f.write(f"[{datetime.now().isoformat()[:19]}] {capped}\n")


# _append_execution_log removed — node-level execution.log (JSONL) is the single source of truth.
# Per-employee summary logs are no longer written. See _append_node_execution_log.


def _append_node_execution_log(project_dir: str, node_id: str, log_type: str, content: str | dict) -> None:
    """Append full-content log entry to node-level execution log (JSONL).

    content can be a string (backward compat) or a dict with structured tool data.
    For dict content, the JSONL disk write uses content["content"] (string) to keep
    the trace viewer's single-source-of-truth format unchanged.
    """
    if not project_dir:
        return
    import json as _json
    # Extract string for JSONL disk write; structured dicts go to WS only
    content_str = content["content"] if isinstance(content, dict) else content
    log_dir = Path(project_dir) / "nodes" / node_id
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "execution.log"
    try:
        entry = _json.dumps({
            "ts": datetime.now().isoformat(),
            "type": log_type,
            "content": content_str,
        }, ensure_ascii=False) + "\n"
        with open(path, "a", encoding=ENCODING_UTF8) as f:
            f.write(entry)
    except Exception as exc:
        logger.debug("Failed to write node execution log: {}", exc)


def _trunc(s: str | None, limit: int = 3000) -> str:
    """Truncate string for debug logging."""
    text = s or ""
    return text[:limit] + ("..." if len(text) > limit else "")


def _result_preview(result: str, max_lines: int = 3, max_chars: int = 500) -> str:
    """Extract a multi-line preview of a task result for CEO consumption."""
    text = result.strip()
    if not text:
        return ""
    lines = text.split("\n")[:max_lines]
    preview = "\n    ".join(lines)
    return preview[:max_chars]


def _collect_work_results(tree, project_dir: str) -> list:
    """Collect all completed/failed work nodes from the entire tree.

    Returns task-type nodes that have results, excluding system and CEO nodes.
    This recurses the full tree rather than only looking at direct children.
    """
    from onemancompany.core.task_lifecycle import SYSTEM_NODE_TYPES, TaskPhase

    done_statuses = {
        TaskPhase.COMPLETED.value, TaskPhase.ACCEPTED.value,
        TaskPhase.FINISHED.value, TaskPhase.FAILED.value,
    }
    results = []
    for node in tree.all_nodes():
        if node.node_type in SYSTEM_NODE_TYPES or node.is_ceo_node:
            continue
        if node.status not in done_statuses:
            continue
        node.load_content(project_dir)
        if node.result and node.result.strip():
            results.append(node)
    return results


def _list_deliverables(project_dir: str) -> list[str]:
    """List non-system files in project directory as deliverables."""
    _skip = {"nodes", "task_tree.yaml", "llm_traces.jsonl", "iteration.yaml", ".DS_Store"}
    pdir = Path(project_dir)
    if not pdir.exists():
        return []
    files = []
    for f in sorted(pdir.iterdir()):
        if f.name in _skip or f.name.startswith("."):
            continue
        if f.is_file():
            files.append(f.name)
    return files


async def _summarize_project_for_ceo(
    project_name: str,
    work_nodes: list,
    deliverables: list[str],
) -> str:
    """Have EA write a concise project completion summary for the CEO.

    Falls back to a simple result listing if the LLM call fails.
    """
    from onemancompany.agents.base import tracked_ainvoke
    from onemancompany.core.config import EA_ID
    from onemancompany.core.task_lifecycle import TaskPhase

    # Build raw results context for EA
    raw_parts: list[str] = []
    for node in work_nodes:
        status = "succeeded" if node.status != TaskPhase.FAILED.value else "FAILED"
        title = node.title or node.description_preview[:80]
        result = (node.result or "").strip()[:1000]
        raw_parts.append(f"[{node.employee_id}] {title} ({status}):\n{result}")

    if not raw_parts:
        return ""

    raw_context = "\n\n---\n\n".join(raw_parts)
    deliverables_ctx = "\n".join(f"  - {f}" for f in deliverables) if deliverables else "(none)"

    prompt = (
        f"You are the Executive Assistant. Write a concise project completion report "
        f"for the CEO.\n\n"
        f"Project: {project_name}\n\n"
        f"Deliverables:\n{deliverables_ctx}\n\n"
        f"Raw task results:\n{raw_context}\n\n"
        f"Instructions:\n"
        f"- Summarize what was accomplished in 3-5 sentences\n"
        f"- Highlight key deliverables and their quality\n"
        f"- Note any failures or issues that need CEO attention\n"
        f"- Use the project's language (Chinese if results are in Chinese)\n"
        f"- Do NOT include greetings or signatures\n"
        f"- Be direct and factual"
    )

    try:
        llm = make_llm(EA_ID)
        result = await tracked_ainvoke(llm, prompt, category="project_summary", employee_id=EA_ID)
        summary = result.content.strip()
        if summary:
            logger.debug("[PROJECT SUMMARY] EA generated {}-char summary for {}", len(summary), project_name)
            return summary
    except Exception as e:
        logger.warning("[PROJECT SUMMARY] EA summary failed for {}: {}", project_name, e)

    # Fallback: simple result listing with previews
    fallback_lines = ["Work summary:"]
    for node in work_nodes:
        status_icon = "✓" if node.status != TaskPhase.FAILED.value else "✗"
        title = node.title or node.description_preview[:60]
        preview = _result_preview(node.result or "")
        if preview:
            fallback_lines.append(f"  {status_icon} [{node.employee_id}] {title}:")
            fallback_lines.append(f"    {preview}")
        else:
            fallback_lines.append(f"  {status_icon} [{node.employee_id}] {title}")
    return "\n".join(fallback_lines)


def _load_progress(employee_id: str, max_lines: int = PROGRESS_LOG_MAX_LINES) -> str:
    """Load recent entries from the employee's progress log."""
    path = EMPLOYEES_DIR / employee_id / PROGRESS_LOG_FILENAME
    if not path.exists():
        return ""
    try:
        lines = read_text_utf(path).strip().split("\n")
        return "\n".join(lines[-max_lines:])
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Employee Manager — centralized task coordinator
# ---------------------------------------------------------------------------

from onemancompany.core.task_lifecycle import SKIP_COMPLETION_TYPES, SYSTEM_NODE_TYPES, NodeType, is_system_project_id


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
        self._current_entries: dict[str, ScheduleEntry] = {}  # currently executing entry per employee
        self._system_tasks: dict[str, asyncio.Task] = {}  # system operation tracking
        self._deferred_schedule: set[str] = set()
        self._hooks: dict[str, dict[str, Callable]] = {}  # bridge cache only — execution goes through skill_hooks
        self._event_loop: asyncio.AbstractEventLoop | None = None  # set by drain_pending
        self._restart_pending: bool = False
        # ScheduleEntry-based scheduling (replaces boards for new code paths)
        self._schedule: dict[str, list[ScheduleEntry]] = {}  # employee_id → scheduled nodes
        # _task_logs removed — node-level execution.log (JSONL) is the single source of truth
        # Tree completion event queue — serializes all child-complete callbacks
        self._completion_queue: asyncio.Queue | None = None
        self._completion_consumer: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # ScheduleEntry-based node scheduling
    # ------------------------------------------------------------------

    def schedule_node(self, employee_id: str, node_id: str, tree_path: str) -> None:
        """Add a node to the employee's schedule (idempotent — skips duplicates)."""
        # Always persist to task index for taskboard visibility
        from onemancompany.core.store import append_task_index_entry
        append_task_index_entry(employee_id, node_id, tree_path)

        if employee_id not in self.executors:
            logger.warning(
                "[SCHEDULE] schedule_node for {} but no executor registered yet — "
                "task {} saved to index, will be recovered on register()",
                employee_id, node_id,
            )
            return
        entries = self._schedule.setdefault(employee_id, [])
        if any(e.node_id == node_id for e in entries):
            logger.debug("[SCHEDULE] node {} already in schedule for {}, skipping", node_id, employee_id)
            return
        entries.append(ScheduleEntry(node_id=node_id, tree_path=tree_path))

    def unschedule(self, employee_id: str, node_id: str) -> None:
        """Remove a completed/failed node from schedule."""
        entries = self._schedule.get(employee_id, [])
        self._schedule[employee_id] = [e for e in entries if e.node_id != node_id]

    def cleanup_orphaned_schedule(self) -> int:
        """Remove schedule entries pointing to missing trees or resolved/terminal nodes.

        Returns the number of entries removed.
        """
        from onemancompany.core.task_lifecycle import RESOLVED, TERMINAL
        from onemancompany.core.task_tree import get_tree

        removed = 0
        for emp_id in list(self._schedule.keys()):
            entries = self._schedule.get(emp_id, [])
            keep = []
            for entry in entries:
                tree_path = Path(entry.tree_path)
                if not tree_path.exists():
                    logger.debug("[cleanup_schedule] Removing orphan: tree {} gone", tree_path)
                    removed += 1
                    continue
                try:
                    tree = get_tree(tree_path)
                    node = tree.get_node(entry.node_id)
                except Exception:
                    logger.debug("[cleanup_schedule] Removing orphan: corrupt tree {}", tree_path)
                    removed += 1
                    continue
                if not node:
                    logger.debug("[cleanup_schedule] Removing orphan: node {} not in tree", entry.node_id)
                    removed += 1
                    continue
                if TaskPhase(node.status) in TERMINAL:
                    logger.debug("[cleanup_schedule] Removing terminal node {} ({})", entry.node_id, node.status)
                    removed += 1
                    continue
                keep.append(entry)
            self._schedule[emp_id] = keep
        if removed:
            logger.info("[cleanup_schedule] Removed {} orphaned schedule entries", removed)
        return removed

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

        # Recover orphaned tasks: schedule_node() may have been called before
        # the executor was registered, adding tasks to task_index on disk but
        # not to the in-memory _schedule.  Re-add them now.
        self._recover_orphaned_tasks(employee_id)

        return vessel

    def _recover_orphaned_tasks(self, employee_id: str) -> None:
        """Re-add PENDING tasks from task_index that are missing from _schedule."""
        from onemancompany.core.store import load_task_index
        from onemancompany.core.task_tree import get_tree

        index_entries = load_task_index(employee_id)
        scheduled_ids = {e.node_id for e in self._schedule.get(employee_id, [])}
        recovered = 0
        for entry in index_entries:
            node_id = entry.get("node_id")
            tree_path = entry.get("tree_path")
            if not node_id or not tree_path or node_id in scheduled_ids:
                continue
            # Only recover tasks that are still PENDING
            tp = Path(tree_path)
            if not tp.exists():
                continue
            tree = get_tree(tp)
            node = tree.get_node(node_id)
            if not node or TaskPhase(node.status) != TaskPhase.PENDING:
                continue
            self._schedule.setdefault(employee_id, []).append(
                ScheduleEntry(node_id=node_id, tree_path=tree_path)
            )
            recovered += 1
        if recovered:
            logger.info("[REGISTER] Recovered {} orphaned PENDING tasks for {}", recovered, employee_id)
            self._schedule_next(employee_id)

    def register_hooks(self, employee_id: str, hooks: dict[str, Callable]) -> None:
        """Register lifecycle hooks (pre_task, post_task) for an employee.

        Bridges legacy vessel.yaml hooks into the unified skill_hooks system.
        Wraps sync callables as async callbacks with compatible signatures.
        """
        self._hooks[employee_id] = hooks
        from onemancompany.core.skill_hooks import register_callback_hook, clear_hooks, load_hooks_from_skills, HookEvent
        clear_hooks(employee_id)  # wipe all, then re-register both sources
        load_hooks_from_skills(employee_id)  # re-load SKILL.md hooks first
        if hooks.get("pre_task"):
            _pre = hooks["pre_task"]
            async def _pre_wrap(hook_input, _fn=_pre):
                try:
                    result = _fn(hook_input.get("task_description", ""), None)
                    return {"additionalContext": result if isinstance(result, str) else ""}
                except Exception as e:
                    logger.warning("Legacy pre_task hook failed: {}", e)
                    return {}
            register_callback_hook(employee_id, HookEvent.TASK_START, _pre_wrap, skill_name="_vessel")
        if hooks.get("post_task"):
            _post = hooks["post_task"]
            async def _post_wrap(hook_input, _fn=_post):
                try:
                    _fn(None, hook_input.get("task_description", ""))
                except Exception as e:
                    logger.warning("Legacy post_task hook failed: {}", e)
                return {}
            register_callback_hook(employee_id, HookEvent.TASK_COMPLETE, _post_wrap, skill_name="_vessel")

    def unregister(self, employee_id: str) -> None:
        self.executors.pop(employee_id, None)
        self.vessels.pop(employee_id, None)
        self.configs.pop(employee_id, None)
        self._hooks.pop(employee_id, None)
        from onemancompany.core.skill_hooks import clear_hooks
        clear_hooks(employee_id)

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
            logger.debug("[SCHEDULE] employee={} already has running task, skip", employee_id)
            return
        entry = self.get_next_scheduled(employee_id)
        if not entry:
            # Also check deferred schedule
            if employee_id in self._deferred_schedule:
                self._deferred_schedule.discard(employee_id)
            logger.debug("[SCHEDULE] employee={} no pending tasks → IDLE", employee_id)
            self._set_employee_status(employee_id, STATUS_IDLE)
            self._publish_dispatch_status(employee_id, status="idle")
            return
        try:
            logger.debug("[SCHEDULE] employee={} starting node={}", employee_id, entry.node_id)
            self._publish_dispatch_status(employee_id, status="dispatched", entry=entry)
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
                            if not meta.get("no_watchdog"):
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
                    from onemancompany.core.task_lifecycle import safe_cancel
                    if safe_cancel(node):
                        logger.debug("[TASK LIFECYCLE] employee={} node={} → CANCELLED (project abort)", emp_id, entry.node_id)
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

            # Cancel running asyncio.Task only if it's actually working on this project
            current_entry = self._current_entries.get(emp_id)
            if current_entry and emp_id in self._running_tasks:
                try:
                    cur_tree = get_tree(current_entry.tree_path)
                    cur_node = cur_tree.get_node(current_entry.node_id)
                    if cur_node and cur_node.project_id == project_id:
                        running = self._running_tasks[emp_id]
                        if not running.done():
                            running.cancel()
                            logger.info("Cancelled running asyncio.Task for {} (project {})", emp_id, project_id)
                except Exception as exc:
                    logger.debug("Could not check running task for {}: {}", emp_id, exc)

        return count

    def abort_employee(self, employee_id: str) -> int:
        """Cancel all tasks for an employee. Returns count cancelled."""
        from onemancompany.core.task_tree import get_tree, save_tree_async
        from onemancompany.core.automation import stop_all_crons_for_employee

        count = 0

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
                from onemancompany.core.task_lifecycle import safe_cancel as _safe_cancel
                if node and _safe_cancel(node):
                    logger.debug("[TASK LIFECYCLE] employee={} node={} → CANCELLED (employee abort)", employee_id, entry.node_id)
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
        self._current_entries[employee_id] = entry
        try:
            await self._execute_task(employee_id, entry)
        finally:
            logger.debug("[TASK LIFECYCLE] employee={} node={} _run_task finally block — cleaning up",
                         employee_id, entry.node_id)
            self._current_entries.pop(employee_id, None)
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

        # 1. Mark PROCESSING (skip if already PROCESSING — can happen when
        #    child failure handler re-dispatches a node that was already running)
        if node.status != TaskPhase.PROCESSING.value:
            node.set_status(TaskPhase.PROCESSING)
        logger.debug("[TASK LIFECYCLE] employee={} node={} → PROCESSING", employee_id, entry.node_id)
        save_tree_async(entry.tree_path)
        self._set_employee_status(employee_id, STATUS_WORKING)

        # Clear watchdog nudge flag so it can re-nudge if project stalls again
        if node.project_id:
            from onemancompany.core.system_cron import clear_watchdog_nudge
            clear_watchdog_nudge(node.project_id)
        desc = node.description or node.description_preview
        self._log_node(employee_id, entry.node_id, "start", f"Starting task: {desc}")
        self._publish_node_update(employee_id, node)
        self._push_to_conversation(node, f"▶ {node.title or desc}")

        await _store.save_employee_runtime(employee_id, current_task_summary=node.description_preview[:100])

        # 2. Set contextvars
        loop_token = _current_vessel.set(vessel)
        task_token = _current_task_id.set(entry.node_id)

        project_id = node.project_id
        project_dir = node.project_dir
        agent_error = False
        try:
            # 4. Build task context with injections
            # _effective_dir: guaranteed non-empty workspace path for this task.
            # Falls back to tree_path parent when node.project_dir is unset
            # (e.g. root/EA nodes created without explicit project_dir).
            _effective_dir = project_dir or str(Path(entry.tree_path).parent)
            node.load_content(_effective_dir)

            # Backfill node.project_dir so child dispatches inherit the correct workspace.
            # Persist immediately so restarts / snapshot-restore also pick it up.
            if not project_dir:
                node.project_dir = _effective_dir
                project_dir = _effective_dir
                logger.debug("[TASK] Backfilled project_dir for node {} → {}",
                             entry.node_id, _effective_dir)
                save_tree_async(entry.tree_path)

            # CEO_REQUEST nodes (confirm/inbox) get clean description only —
            # no tree context, no SOPs, no progress log. The confirm message
            # built by _on_child_complete already contains the full summary.
            is_ceo_request = node.node_type in (NodeType.CEO_REQUEST, NodeType.CEO_REQUEST.value)
            if is_ceo_request:
                task_with_ctx = node.description
            else:
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

                # Product context — inject if project is linked to a product
                if project_id:
                    from onemancompany.core.project_archive import load_named_project as _load_named_proj
                    _proj_doc = _load_named_proj(project_id)
                    _product_id = _proj_doc.get("product_id", "") if _proj_doc else ""
                    if _product_id:
                        from onemancompany.core.product import build_product_context, find_slug_by_product_id
                        _product_slug = find_slug_by_product_id(_product_id)
                        if _product_slug:
                            _prod_ctx = build_product_context(_product_slug)
                            if _prod_ctx:
                                task_with_ctx = f"{_prod_ctx}\n\n{task_with_ctx}"

                if _effective_dir:
                    task_with_ctx += f"\n\n[Project workspace: {_effective_dir} — save all outputs here]"

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

                # Company context: culture, SOPs, guidance, work principles
                company_ctx = self._build_company_context_block(employee_id)
                if company_ctx:
                    task_with_ctx = f"{company_ctx}\n\n{task_with_ctx}"

            # Debug: print full task prompt (without history)
            logger.debug("[TASK PROMPT] employee={} node={} project={}:\n{}",
                         employee_id, entry.node_id, project_id or "none",
                         _trunc(task_with_ctx))

            def _on_log(log_type: str, content: str | dict) -> None:
                self._log_node(employee_id, entry.node_id, log_type, content)
                # Also write to project-level LLM trace JSONL
                if project_id:
                    from datetime import timezone as _tz
                    from onemancompany.core.claude_session import write_llm_trace
                    # Normalize on_log types to trace schema
                    _role_map = {"llm_input": "user", "llm_output": "assistant",
                                 "tool_call": "assistant", "tool_result": "tool", "result": "system"}
                    _type_map = {"llm_input": "prompt", "llm_output": "text",
                                 "tool_call": "tool_use", "tool_result": "tool_result", "result": "result"}
                    # Extract string content for trace (dict has .content key)
                    trace_content = content["content"] if isinstance(content, dict) else content
                    write_llm_trace(project_id, {
                        "ts": datetime.now(_tz.utc).isoformat(),
                        "employee_id": employee_id,
                        "source": "vessel",
                        "role": _role_map.get(log_type, "system"),
                        "type": _type_map.get(log_type, log_type),
                        "content": trace_content,
                    })

            # 5. Execute via launcher with retry
            executor = self.executors.get(employee_id)
            if not executor:
                raise RuntimeError(f"No executor registered for employee {employee_id}")

            context = TaskContext(
                project_id=project_id,
                work_dir=_effective_dir,
                employee_id=employee_id,
                task_id=entry.node_id,
            )

            # Task start hooks (unified skill_hooks system)
            from onemancompany.core.skill_hooks import run_hooks, collect_context, HookEvent
            try:
                _start_results = await run_hooks(
                    employee_id, HookEvent.TASK_START,
                    task_id=entry.node_id, task_description=task_with_ctx,
                )
                _extra = collect_context(_start_results)
                if _extra:
                    task_with_ctx = f"{task_with_ctx}\n\n[Hook context]\n{_extra}"
            except Exception:
                logger.warning("Task start hooks failed for {}", employee_id)

            # Universal timeout — asyncio.wait_for wraps ALL executor types.
            task_timeout = node.timeout_seconds or 3600
            # For SubprocessExecutor: set its internal timeout slightly longer
            # so the outer wait_for fires first. If the outer cancellation
            # somehow fails, the inner timeout still kills the subprocess.
            from onemancompany.core.subprocess_executor import SubprocessExecutor
            if isinstance(executor, SubprocessExecutor):
                executor.timeout_seconds = task_timeout + 30

            launch_result: LaunchResult | None = None
            last_err: Exception | None = None
            for attempt in range(max_retries):
                try:
                    launch_result = await asyncio.wait_for(
                        executor.execute(task_with_ctx, context, on_log=_on_log),
                        timeout=task_timeout,
                    )
                    last_err = None
                    break
                except GraphRecursionError as rec_err:
                    last_err = rec_err
                    self._log_node(employee_id, entry.node_id, "error", f"Agent hit recursion limit: {rec_err!s}")
                    break
                except TimeoutError:
                    raise  # Don't retry task-level timeout — LLM request_timeout handles per-call retries
                except Exception as run_err:
                    last_err = run_err
                    if attempt < max_retries - 1:
                        delay = retry_delays[attempt] if attempt < len(retry_delays) else retry_delays[-1]
                        self._log_node(employee_id, entry.node_id, "retry", f"Attempt {attempt + 1} failed: {run_err!s} — retrying in {delay}s")
                        await asyncio.sleep(delay)

            if last_err is not None:
                raise last_err

            # Check for executor-reported errors
            if launch_result and launch_result.error:
                raise ExecutionError(launch_result.error)

            node.result = launch_result.output if launch_result else ""
            logger.debug("[TASK RESPONSE] employee={} node={}:\n{}",
                         employee_id, entry.node_id, _trunc(node.result))
            self._log_node(employee_id, entry.node_id, "result", node.result or "")

            # Record token usage to node
            logger.debug("[COST] employee={} node={} launch_result tokens: input={} output={} total={} model={}",
                         employee_id, entry.node_id,
                         launch_result.input_tokens if launch_result else 0,
                         launch_result.output_tokens if launch_result else 0,
                         launch_result.total_tokens if launch_result else 0,
                         launch_result.model_used if launch_result else "")
            if launch_result and launch_result.total_tokens > 0:
                node.model_used = launch_result.model_used
                node.input_tokens += launch_result.input_tokens
                node.output_tokens += launch_result.output_tokens
                # Prefer provider-reported cost, fallback to catalog price
                if launch_result.cost_usd is not None:
                    node.cost_usd += launch_result.cost_usd
                else:
                    from onemancompany.core.model_costs import get_model_cost
                    costs = get_model_cost(node.model_used)
                    node.cost_usd = (
                        node.input_tokens * costs["input"] + node.output_tokens * costs["output"]
                    ) / 1_000_000

        except asyncio.CancelledError:
            agent_error = True
            from onemancompany.core.task_lifecycle import safe_cancel as _sc
            _sc(node)
            logger.debug("[TASK LIFECYCLE] employee={} node={} → CANCELLED", employee_id, entry.node_id)
            node.result = node.result or "Cancelled by CEO"
            if not node.completed_at:
                node.completed_at = datetime.now().isoformat()
            self._log_node(employee_id, entry.node_id, "cancelled", "Task cancelled")
            save_tree_async(entry.tree_path)
            self._publish_node_update(employee_id, node)
            # Cascade-cancel downstream dependents
            if project_dir:
                tree = get_tree(entry.tree_path)
                _trigger_dep_resolution(project_dir, tree, node)
                # Notify parent that this child is done (cancelled) so it can
                # resume from HOLDING instead of waiting forever.
                try:
                    await asyncio.shield(
                        self._on_child_complete(employee_id, entry, project_id=project_id)
                    )
                except Exception as _e:
                    logger.debug("[TASK LIFECYCLE] _on_child_complete after cancel failed: {}", _e)
            raise
        except TimeoutError as te:
            agent_error = True
            node.set_status(TaskPhase.FAILED)
            logger.debug("[TASK LIFECYCLE] employee={} node={} → FAILED (timeout)", employee_id, entry.node_id)
            node.result = f"Timeout: task exceeded {node.timeout_seconds or 3600}s limit"
            if not node.completed_at:
                node.completed_at = datetime.now().isoformat()
            self._log_node(employee_id, entry.node_id, "timeout", f"Task timed out after {node.timeout_seconds or 3600}s")
            self._push_to_conversation(node, f"\u2717 Timeout ({node.timeout_seconds or 3600}s)")
            _append_progress(employee_id, f"Failed: {node.description_preview} \u2014 timeout")
        except Exception as e:
            agent_error = True
            node.set_status(TaskPhase.FAILED)
            logger.debug("[TASK LIFECYCLE] employee={} node={} → FAILED (error: {})", employee_id, entry.node_id, e)
            node.result = f"Error: {e!s}"
            if not node.completed_at:
                node.completed_at = datetime.now().isoformat()
            self._log_node(employee_id, entry.node_id, "error", f"Task failed: {e!s}")
            self._push_to_conversation(node, f"\u2717 {str(e)[:500]}")
            _append_progress(employee_id, f"Failed: {node.description_preview} \u2014 {e!s}")
            logger.exception("Unhandled error")
            # Task error hooks
            try:
                await run_hooks(
                    employee_id, HookEvent.TASK_ERROR,
                    task_id=entry.node_id, error_message=str(e),
                )
            except Exception as hook_err:
                logger.debug("Task error hook failed (suppressed): {}", hook_err)
        finally:
            _current_vessel.reset(loop_token)
            _current_task_id.reset(task_token)

        # 7b. Collect verification evidence from execution log → store as file
        if project_dir and not agent_error:
            try:
                from onemancompany.core.task_verification import collect_evidence
                evidence = collect_evidence(project_dir, entry.node_id)
                if evidence.tools_called:
                    ev_path = Path(project_dir) / "nodes" / entry.node_id / "verification.json"
                    ev_path.parent.mkdir(parents=True, exist_ok=True)
                    ev_path.write_text(json.dumps(evidence.to_dict(), ensure_ascii=False), encoding=ENCODING_UTF8)
                    if evidence.has_unresolved_errors:
                        logger.info(
                            "[VERIFICATION] employee={} node={}: {} unresolved error(s)",
                            employee_id, entry.node_id, len(evidence.unresolved_errors),
                        )
            except Exception as e:
                logger.debug("[VERIFICATION] Failed to collect evidence: {}", e)

        # 8. Mark completed (or HOLDING)
        # (No stale-read issue: tree is in-memory cache, all tools modify the same object)
        logger.debug("[TASK LIFECYCLE] employee={} node={} status_before_completion={}",
                     employee_id, entry.node_id, node.status)
        if node.status not in (TaskPhase.FAILED.value, TaskPhase.CANCELLED.value,
                               TaskPhase.FINISHED.value, TaskPhase.ACCEPTED.value):
            holding_meta = _parse_holding_metadata(node.result or "")

            # Generic auto-HOLDING: tools set node.hold_reason to request HOLDING
            # after execution. Inject __HOLDING: prefix so it's serializable + restart-safe.
            if holding_meta is None and node.hold_reason:
                original = node.result or ""
                node.result = f"__HOLDING:{node.hold_reason}\n{original}"
                holding_meta = _parse_holding_metadata(node.result)
                self._log_node(
                    employee_id, entry.node_id, "auto_holding",
                    f"Tool-requested HOLDING: {node.hold_reason}",
                )

            # System nodes (REVIEW, WATCHDOG_NUDGE) never HOLD — always auto-finish
            if holding_meta is not None and node.node_type not in SYSTEM_NODE_TYPES:
                logger.debug("[TASK LIFECYCLE] employee={} node={} → HOLDING meta={}",
                             employee_id, entry.node_id, holding_meta)
                node.set_status(TaskPhase.HOLDING)
                node.hold_started_at = datetime.now().isoformat()
                save_tree_async(entry.tree_path)
                # Auto-resume HOLDING (e.g. ceo_request): skip watchdog when the
                # resume is handled by another code path (routes.py, etc.)
                if not holding_meta.get("no_watchdog"):
                    self._setup_holding_watchdog_by_id(employee_id, entry.node_id, node.created_at, holding_meta)
                self._log_node(employee_id, entry.node_id, "holding", f"Task entered HOLDING: {holding_meta}")
            else:
                node.set_status(TaskPhase.COMPLETED)
                logger.debug("[TASK LIFECYCLE] employee={} node={} → COMPLETED (type={})",
                             employee_id, entry.node_id, node.node_type)
                # System nodes auto-skip review: they don't need to be reviewed themselves
                if node.node_type in SYSTEM_NODE_TYPES:
                    node.set_status(TaskPhase.ACCEPTED)
                    node.set_status(TaskPhase.FINISHED)
                    logger.debug("[TASK LIFECYCLE] employee={} node={} → auto FINISHED (system node)",
                                 employee_id, entry.node_id)

                # Stall detection: agent said "I will do X" but dispatched no children
                if (node.node_type not in SYSTEM_NODE_TYPES
                        and not node.children_ids
                        and detect_unfulfilled_promises(node.result)):
                    logger.warning(
                        "[STALL] employee={} node={}: output contains action promises "
                        "but no subtasks were dispatched. Agent may have stalled.",
                        employee_id, entry.node_id,
                    )
                    self._push_to_conversation(
                        node,
                        "⚠️ Agent claimed it would execute follow-up work but did not "
                        "create any tasks. It may have stalled. Please review and re-dispatch.",
                    )

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
                summary = node.result or ""
                _append_progress(employee_id, f"Completed: {node.description_preview} → {summary}")
                # Push full result to CEO session
                result_text = (node.result or "").strip()
                if result_text:
                    self._push_to_conversation(node, f"✓ {result_text}")

            # Task complete hooks (unified skill_hooks system)
            try:
                await run_hooks(
                    employee_id, HookEvent.TASK_COMPLETE,
                    task_id=entry.node_id, task_description=node.result or "",
                )
            except Exception:
                logger.warning("Task complete hooks failed for {}", employee_id)

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
                if not is_system_project_id(project_id) and node.result:
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

    def find_holding_task(self, employee_id: str, match_text: str) -> str | None:
        """Find a HOLDING task whose result contains match_text. Returns node_id or None."""
        from onemancompany.core.task_tree import get_tree
        for entry in self._schedule.get(employee_id, []):
            tp = Path(entry.tree_path)
            if not tp.exists():
                continue
            tree = get_tree(tp)
            node = tree.get_node(entry.node_id)
            if node and node.status == TaskPhase.HOLDING and node.result and match_text in node.result:
                return entry.node_id
        return None

    def _check_holding_timeout(self, tree_path: str, node_id: str) -> bool:
        """Check if a HOLDING node has exceeded MAX_HOLD_SECONDS.

        If timed out: transitions to FAILED, stops watchdog crons, saves tree.
        Returns True if the node was timed out, False otherwise.
        """
        from onemancompany.core.task_tree import get_tree, save_tree_async

        tree = get_tree(tree_path)
        node = tree.get_node(node_id)
        if not node:
            logger.debug("[HOLDING TIMEOUT] node {} not found in tree {}", node_id, tree_path)
            return False
        if node.status != TaskPhase.HOLDING.value:
            return False
        if not node.hold_started_at:
            return False

        # Skip timeout for holds that require human action (CEO/hiring)
        hold_reason = node.hold_reason or ""
        if "no_watchdog" in hold_reason or "batch_id" in hold_reason:
            return False

        try:
            started = datetime.fromisoformat(node.hold_started_at)
        except (ValueError, TypeError):
            logger.warning("[HOLDING TIMEOUT] invalid hold_started_at={!r} for node {}", node.hold_started_at, node_id)
            return False

        elapsed = (datetime.now() - started).total_seconds()
        if elapsed <= MAX_HOLD_SECONDS:
            return False

        # Timed out — auto-fail
        logger.info(
            "[HOLDING TIMEOUT] node={} employee={} elapsed={:.0f}s > MAX_HOLD_SECONDS={} — auto-failing",
            node_id, node.employee_id, elapsed, MAX_HOLD_SECONDS,
        )
        node.set_status(TaskPhase.FAILED)
        node.load_content(Path(tree_path).parent)
        original_result = node.result or ""
        node.result = f"HOLDING timeout ({elapsed:.0f}s > {MAX_HOLD_SECONDS}s). {original_result}"
        save_tree_async(tree_path)

        # Stop associated crons
        stop_cron(node.employee_id, f"reply_{node_id}")
        stop_cron(node.employee_id, f"holding_{node_id}")

        return True

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
                logger.debug("[TASK LIFECYCLE] employee={} node={} HOLDING → COMPLETED (resumed)", employee_id, task_id)
                node.completed_at = datetime.now().isoformat()

                # System nodes auto-skip review
                if node.node_type in SYSTEM_NODE_TYPES:
                    node.set_status(TaskPhase.ACCEPTED)
                    node.set_status(TaskPhase.FINISHED)
                    logger.debug("[TASK LIFECYCLE] employee={} node={} → auto FINISHED (system node, resumed)", employee_id, task_id)

                save_tree_async(entry.tree_path)

                final_status = node.status
                self._log_node(employee_id, task_id, "resumed", f"HOLDING → {final_status} with result: {result}")
                self._publish_node_update(employee_id, node)

                self._append_history_from_node(employee_id, node)
                summary = node.result or ""
                _append_progress(employee_id, f"Completed (resumed): {node.description_preview} → {summary}")

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
            "task": (node.description or "")[:2000],
            "result": (node.result or "")[:2000],
            "completed_at": node.completed_at or datetime.now().isoformat(),
        })
        _save_task_history(employee_id, history, self._history_summaries.get(employee_id, ""))
        try:
            from onemancompany.core.async_utils import spawn_background
            spawn_background(self._maybe_compress_history(employee_id))
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
                logger.warning("_get_project_history_context: iteration {} has no matching project", project_id)
                return ""
            slug = found
            _, bare_iter = _split_qualified_iter(project_id)
            current_iter = bare_iter

        proj = load_named_project(slug)
        if not proj:
            logger.warning("_get_project_history_context: project '{}' not found in store", slug)
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
                    ts = entry.get(TL_FIELD_TIME, "")
                    time_short = ts[11:19] if len(ts) >= 19 else ts[:8]
                    emp_entry = entry.get(TL_FIELD_EMPLOYEE_ID, "?")
                    action = entry.get(TL_FIELD_ACTION, "")
                    detail = (entry.get(TL_FIELD_DETAIL) or "")[:self._CTX_TIMELINE_DETAIL_CHARS]
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
            tokens = cost.get(PA_TOKEN_USAGE, {})
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
            from onemancompany.core.project_archive import get_project_dir
            ws_path = get_project_dir(slug)
            parts.append(f'\nUse read("{ws_path}/{{filename}}") to read file contents.')

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Company context injection (culture, SOPs, guidance, work principles)
    # ------------------------------------------------------------------

    def _build_company_context_block(self, employee_id: str) -> str:
        """Build unified company context block injected into every task.

        This ensures ALL employee types (LangChain, Claude CLI, Script)
        receive the same company context regardless of executor.

        Role identity is injected here ONLY for non-LangChain employees
        (Claude CLI, Script). LangChain employees get identity via their
        agent class's ``_get_role_identity_section()`` in the system prompt.
        """
        parts: list[str] = []

        # 0. Role identity — only for non-LangChain executors
        #    LangChain employees already have identity in system prompt.
        executor = self.executors.get(employee_id)
        if not isinstance(executor, LangChainExecutor):
            identity = build_role_identity(employee_id)
            if identity:
                parts.append(identity)

        # 1. Company culture
        culture_items = _store.load_culture()
        if culture_items:
            rules = "\n".join(
                f"  {i + 1}. {item.get('content', '')}"
                for i, item in enumerate(culture_items)
            )
            parts.append(f"## Company Culture\n{rules}")

        # 2. SOPs — title + first line only; agent can read() full content
        from onemancompany.core.config import load_workflows, SOP_DIR, WORKFLOWS_DIR, HR_SOP_DIR
        workflows = load_workflows()
        if workflows:
            sop_lines = []
            for name, content in workflows.items():
                first_line = ""
                for line in content.splitlines():
                    stripped = line.strip().lstrip("#").strip()
                    if stripped:
                        first_line = stripped
                        break
                # Determine the file path for read()
                sop_path = SOP_DIR / f"{name}.md"
                if not sop_path.exists():
                    sop_path = HR_SOP_DIR / f"{name}.md"
                if not sop_path.exists():
                    sop_path = WORKFLOWS_DIR / f"{name}.md"
                sop_lines.append(f"  - {name}: {first_line}  [read(\"{sop_path}\")]")
            parts.append(
                "## SOPs & Workflows (use read() for full content)\n"
                + "\n".join(sop_lines)
            )

        # 3. CEO guidance (1-on-1 notes)
        notes = _store.load_employee_guidance(employee_id)
        if notes:
            guidance = "\n".join(f"  - {n}" for n in notes)
            parts.append(f"## CEO Guidance\n{guidance}")

        # 4. Work principles
        principles = _store.load_employee_work_principles(employee_id)
        wp_path = EMPLOYEES_DIR / employee_id / "work_principles.md"
        if principles and principles.strip():
            parts.append(f"## Your Work Principles\nFile: {wp_path}\n{principles.strip()}")
        else:
            parts.append(f"## Your Work Principles\nFile: {wp_path}\n(not yet written)")

        # 5. Talent-provided prompt (CLAUDE.md or talent_persona.md from onboarding)
        #    CLAUDE.md takes priority; fall back to talent_persona.md for LangChain talents.
        claude_md_path = EMPLOYEES_DIR / employee_id / "CLAUDE.md"
        talent_persona_path = EMPLOYEES_DIR / employee_id / "prompts" / "talent_persona.md"
        persona_content = ""
        if claude_md_path.exists():
            persona_content = read_text_utf(claude_md_path).strip()
        elif talent_persona_path.exists():
            persona_content = read_text_utf(talent_persona_path).strip()
        if persona_content:
            parts.append(f"## Your Persona\n{persona_content}")

        if not parts:
            return ""
        return "[Company Context]\n" + "\n\n".join(parts) + "\n[/Company Context]"

    # ------------------------------------------------------------------
    # Workflow context injection
    # ------------------------------------------------------------------

    def _get_project_workflow_context(self, employee_id: str, project_id_or_task=None) -> str:
        from onemancompany.core.config import load_workflows, FOUNDING_LEVEL
        from onemancompany.core.workflow_engine import parse_workflow

        emp_data = _store.load_employee(employee_id) or {}
        role = emp_data.get(PF_ROLE, "Employee").upper()
        is_manager = role in ("COO", "CSO", "EA", "HR")

        if is_manager and role in ("COO", "CSO"):
            return (
                "[Manager Execution Guide]\n"
                "As a manager receiving a project task, follow this flow:\n"
                "  1. **Check SOPs**: Your task prompt lists available SOPs & Workflows. "
                "Read the relevant SOP (e.g. project intake, execution) via read() BEFORE acting.\n"
                "  2. **Assess workforce**: list_colleagues() to review current team and skills.\n"
                "  3. **Staff up**: If gaps exist, request_hiring() first. Hire before starting.\n"
                "  4. **Assemble team & align**: pull_meeting() with all team members — "
                "discuss goals, acceptance criteria, work breakdown.\n"
                "  5. **Break down & dispatch**: dispatch_child() with clear acceptance criteria "
                "and depends_on for sequential tasks.\n"
                "  6. **Accept/reject**: Review each child deliverable via actual file inspection, "
                "then accept_child() or reject_child().\n"
                "You are a coordinator — plan, delegate, verify. Do NOT produce deliverables yourself.\n"
                "Do NOT loop or re-analyze — follow the SOP steps and move on."
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
            # Try loading from SOP file
            sop_content = workflows.get("self_verification_sop", "")
            if sop_content:
                return "[Self-Verification Before Completion]\n" + sop_content
            # Minimal fallback
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

    def _ensure_completion_queue(self) -> None:
        """Lazily create the completion queue and its consumer task."""
        if self._completion_queue is not None:
            return
        self._completion_queue = asyncio.Queue()
        self._completion_consumer = asyncio.ensure_future(self._completion_consumer_loop())

    async def _completion_consumer_loop(self) -> None:
        """Serial consumer for tree completion events.

        All child-complete callbacks are funnelled through this single consumer
        so that tree mutations (派生 new children, status changes, review spawning)
        are fully serialised — no concurrent modification races.
        """
        while True:
            employee_id, entry, project_id, done_event = await self._completion_queue.get()
            try:
                from onemancompany.core.task_tree import get_tree_lock
                lock = get_tree_lock(entry.tree_path)
                with lock:
                    await asyncio.wait_for(
                        self._on_child_complete_inner(employee_id, entry, project_id),
                        timeout=60.0,
                    )
            except asyncio.TimeoutError:
                logger.error(
                    "Completion consumer TIMEOUT (60s) for node {} tree={} — "
                    "skipping to unblock queue. Tree may be in inconsistent state; "
                    "restart will re-evaluate via recover_schedule_from_trees.",
                    entry.node_id, entry.tree_path,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Completion consumer error for node {}: {}", entry.node_id, e)
            finally:
                done_event.set()
                self._completion_queue.task_done()

    async def _on_child_complete(self, employee_id: str, entry: ScheduleEntry, project_id: str = "") -> None:
        """Enqueue a child-complete event and wait for serial processing."""
        self._ensure_completion_queue()
        done_event = asyncio.Event()
        await self._completion_queue.put((employee_id, entry, project_id, done_event))
        await done_event.wait()

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
        logger.debug("[ON_CHILD_COMPLETE] employee={} node={} status={} is_root={} parent_id={}",
                     employee_id, entry.node_id, node.status, is_root, node.parent_id)
        if is_root and task_failed:
            logger.debug("[ON_CHILD_COMPLETE] root node {} failed → project {} marked failed", entry.node_id, project_id)
            await _store.save_project_status(project_id, ITER_STATUS_FAILED)
            return

        # --- Propagate upward: review / auto-complete parent ---
        # CEO prompt nodes are containers — they don't need review or auto-complete.
        # Their child (EA) completing is handled by the project completion check below.
        parent_node = tree.get_node(node.parent_id) if node.parent_id else None
        if parent_node and parent_node.is_ceo_node:
            logger.debug("[ON_CHILD_COMPLETE] parent {} is CEO node — skipping review/auto-complete", parent_node.id)
            parent_node = None  # Skip propagation, fall through to project completion check

        # --- Auto-accept orphaned COMPLETED children after REVIEW finishes ---
        # MUST run BEFORE Gate 1/Gate 2 to prevent review spawn loop:
        # without this, a finished review triggers Gate 2 which sees COMPLETED
        # children and spawns another review, creating an infinite loop.
        if node.node_type in SYSTEM_NODE_TYPES and parent_node:
            completed_siblings = [
                c for c in tree.get_active_children(parent_node.id)
                if c.node_type not in SYSTEM_NODE_TYPES
                and c.status == TaskPhase.COMPLETED.value
            ]
            if completed_siblings:
                has_active_review = any(
                    c for c in tree.get_active_children(parent_node.id)
                    if c.node_type == NodeType.REVIEW
                    and c.status in (TaskPhase.PENDING.value, TaskPhase.PROCESSING.value)
                )
                if not has_active_review:
                    for c in completed_siblings:
                        c.set_status(TaskPhase.ACCEPTED)
                        c.acceptance_result = {"passed": True, "notes": "Auto-accepted: review completed without explicit accept/reject."}
                        c.set_status(TaskPhase.FINISHED)
                        logger.info("[ON_CHILD_COMPLETE] Auto-accepted orphaned COMPLETED node {} (review finished without tool call)", c.id)
                    save_tree_async(entry.tree_path)
                    # Trigger dep resolution for each auto-accepted node so
                    # downstream tasks waiting on them get unblocked.
                    _pdir = parent_node.project_dir or str(Path(entry.tree_path).parent)
                    for c in completed_siblings:
                        _trigger_dep_resolution(_pdir, tree, c)
                    # Re-check gates below with updated statuses (children now FINISHED).

        # --- Auto-accept orphaned node whose parent is already RESOLVED ---
        # When a node completes but its parent was already promoted (e.g. review
        # finished before its dispatched child), the node is orphaned at COMPLETED.
        # Auto-accept it so is_subtree_resolved() sees it as resolved.
        if parent_node and TaskPhase(parent_node.status) in RESOLVED:
            if node.status == TaskPhase.COMPLETED.value:
                node.set_status(TaskPhase.ACCEPTED)
                node.acceptance_result = {"passed": True, "notes": "Auto-accepted: parent already resolved."}
                node.set_status(TaskPhase.FINISHED)
                logger.info(
                    "[ON_CHILD_COMPLETE] Auto-accepted orphaned node {} (parent {} already {})",
                    node.id, parent_node.id, parent_node.status,
                )
                save_tree_async(entry.tree_path)
                _pdir = node.project_dir or str(Path(entry.tree_path).parent)
                _trigger_dep_resolution(_pdir, tree, node)

        if parent_node and TaskPhase(parent_node.status) not in RESOLVED:
            children = tree.get_active_children(parent_node.id)
            non_review_children = [c for c in children if c.node_type not in SYSTEM_NODE_TYPES]

            # Gate 1: all substantive children ACCEPTED/FINISHED → auto-complete parent upward
            # Excludes FAILED/CANCELLED — those need parent review to decide how to handle.
            _SUCCESS_RESOLVED = frozenset({TaskPhase.ACCEPTED, TaskPhase.FINISHED})
            if non_review_children and all(TaskPhase(c.status) in _SUCCESS_RESOLVED for c in non_review_children):
                if parent_node.status != TaskPhase.COMPLETED.value:
                    logger.info("All non-review children of {} are resolved — auto-completing parent", parent_node.id)
                    if parent_node.status in (TaskPhase.PENDING.value, TaskPhase.HOLDING.value):
                        parent_node.set_status(TaskPhase.PROCESSING)
                        logger.debug("[TASK LIFECYCLE] parent={} → PROCESSING (auto-complete prep)", parent_node.id)
                    parent_node.set_status(TaskPhase.COMPLETED)
                    logger.debug("[TASK LIFECYCLE] parent={} → COMPLETED (all children resolved)", parent_node.id)
                    # Propagate CEO response if a CEO_REQUEST child has a result
                    ceo_responses = [
                        c.result for c in children
                        if c.node_type == NodeType.CEO_REQUEST.value and c.result
                    ]
                    parent_node.result = ceo_responses[0] if ceo_responses else "All child tasks accepted."
                    save_tree_async(entry.tree_path)
                    self._publish_node_update(parent_node.employee_id, parent_node)
                if parent_node.status == TaskPhase.COMPLETED.value:
                    parent_node.set_status(TaskPhase.ACCEPTED)
                    # Clear stale acceptance_result from prior rejection (if any)
                    parent_node.acceptance_result = {"passed": True, "notes": "Auto-accepted: all child tasks resolved."}
                    logger.info("[TASK LIFECYCLE] parent={} → ACCEPTED (all children resolved, auto-promoting)", parent_node.id)
                    parent_node.set_status(TaskPhase.FINISHED)
                    logger.debug("[TASK LIFECYCLE] parent={} → FINISHED", parent_node.id)
                    save_tree_async(entry.tree_path)
                    self._publish_node_update(parent_node.employee_id, parent_node)
                    # Recursively propagate upward (includes project completion check)
                    parent_entry = ScheduleEntry(node_id=parent_node.id, tree_path=entry.tree_path)
                    await self._on_child_complete_inner(
                        parent_node.employee_id, parent_entry, project_id
                    )
                    return  # recursive call handles project completion check

            # Gate 2: incremental review — any child COMPLETED triggers immediate
            # review so it can be accepted individually.  This prevents dep-chain
            # deadlocks (A→B): B stays PENDING until A is ACCEPTED, so we cannot
            # wait for all children to finish before reviewing.
            else:
                needs_review = any(
                    c for c in non_review_children
                    if c.status == TaskPhase.COMPLETED.value
                )
                has_active_review = any(
                    c for c in children
                    if c.node_type == NodeType.REVIEW
                    and c.status in (TaskPhase.PENDING.value, TaskPhase.PROCESSING.value)
                )
                # Check for failed children when parent is HOLDING — resume parent
                # so it can react (retry via reject_child, reassign, or escalate).
                has_failed_child = any(
                    c for c in non_review_children
                    if c.status == TaskPhase.FAILED.value
                )
                # Check if the CURRENT completing node was cancelled — resume parent
                # so it can reassess (e.g. cancelled CEO_REQUEST should unblock parent).
                # Only trigger on the node that just completed, NOT on stale cancelled
                # siblings — otherwise WATCHDOG_NUDGE completions re-trigger the cancelled
                # branch in an infinite loop.
                has_cancelled_child = node.status == TaskPhase.CANCELLED.value
                if has_failed_child and parent_node.status in (TaskPhase.HOLDING.value, TaskPhase.PROCESSING.value):
                    failed_children = [
                        c for c in non_review_children
                        if c.status == TaskPhase.FAILED.value
                    ]
                    failure_summary = "; ".join(
                        f"[{c.employee_id}] {c.description_preview}: {c.result or 'no details'}"
                        for c in failed_children
                    )
                    resume_desc = (
                        f"[Child Task Failure] The following child tasks have failed:\n\n"
                        f"{failure_summary}\n\n"
                        f"Options:\n"
                        f"- reject_child(node_id, reason, retry=True) to reassign the task\n"
                        f"- reject_child(node_id, reason, retry=False) to abandon the task\n"
                        f"- dispatch_child to assign to a different employee\n"
                        f"- If the project cannot continue, explain why"
                    )
                    was_processing = parent_node.status == TaskPhase.PROCESSING.value
                    logger.info(
                        "[ON_CHILD_COMPLETE] child {} FAILED — resuming {} parent {} with failure context",
                        node.id, parent_node.status, parent_node.id,
                    )
                    if was_processing:
                        # Parent is actively running — cancel its execution so it can be re-dispatched
                        running = self._running_tasks.pop(parent_node.employee_id, None)
                        if running and not running.done():
                            running.cancel()
                            logger.debug("[TASK LIFECYCLE] parent={} cancelled running task (child failed)", parent_node.id)
                    # Transition parent to PROCESSING — skip if already PROCESSING (idempotent)
                    if parent_node.status != TaskPhase.PROCESSING.value:
                        parent_node.set_status(TaskPhase.PROCESSING)
                        logger.debug("[TASK LIFECYCLE] parent={} → PROCESSING (child failed, resuming)", parent_node.id)
                    else:
                        logger.debug("[TASK LIFECYCLE] parent={} already PROCESSING (child failed, re-dispatching)", parent_node.id)
                    # Inject failure context into parent's description for re-execution
                    notify_node = tree.add_child(
                        parent_id=parent_node.id,
                        employee_id=parent_node.employee_id,
                        description=resume_desc,
                        acceptance_criteria=[],
                    )
                    notify_node.node_type = NodeType.WATCHDOG_NUDGE
                    notify_node.project_id = project_id
                    notify_node.project_dir = parent_node.project_dir or str(Path(entry.tree_path).parent)
                    save_tree_async(entry.tree_path)
                    self.schedule_node(parent_node.employee_id, notify_node.id, entry.tree_path)
                    self._schedule_next(parent_node.employee_id)

                elif has_cancelled_child and parent_node.status in (TaskPhase.HOLDING.value, TaskPhase.PROCESSING.value):
                    cancelled_children = [
                        c for c in children
                        if c.status == TaskPhase.CANCELLED.value
                    ]
                    cancel_summary = "; ".join(
                        f"[{c.employee_id}] {c.description_preview}: {c.result or 'cancelled'}"
                        for c in cancelled_children
                    )
                    resume_desc = (
                        f"[Child Task Cancelled] The following child tasks have been cancelled:\n\n"
                        f"{cancel_summary}\n\n"
                        f"Options:\n"
                        f"- dispatch_child to reassign to a different employee\n"
                        f"- Continue with remaining child tasks\n"
                        f"- If the project cannot continue, explain why"
                    )
                    was_processing = parent_node.status == TaskPhase.PROCESSING.value
                    logger.info(
                        "[ON_CHILD_COMPLETE] child {} CANCELLED — resuming {} parent {} with cancellation context",
                        node.id, parent_node.status, parent_node.id,
                    )
                    if was_processing:
                        # Parent is actively running — cancel its execution so it can be re-dispatched
                        running = self._running_tasks.pop(parent_node.employee_id, None)
                        if running and not running.done():
                            running.cancel()
                            logger.debug("[TASK LIFECYCLE] parent={} cancelled running task (child cancelled)", parent_node.id)
                    # Transition parent to PROCESSING — skip if already PROCESSING (idempotent)
                    if parent_node.status != TaskPhase.PROCESSING.value:
                        parent_node.set_status(TaskPhase.PROCESSING)
                        logger.debug("[TASK LIFECYCLE] parent={} → PROCESSING (child cancelled, resuming)", parent_node.id)
                    else:
                        logger.debug("[TASK LIFECYCLE] parent={} already PROCESSING (child cancelled, re-dispatching)", parent_node.id)
                    notify_node = tree.add_child(
                        parent_id=parent_node.id,
                        employee_id=parent_node.employee_id,
                        description=resume_desc,
                        acceptance_criteria=[],
                    )
                    notify_node.node_type = NodeType.WATCHDOG_NUDGE
                    notify_node.project_id = project_id
                    notify_node.project_dir = parent_node.project_dir or str(Path(entry.tree_path).parent)
                    save_tree_async(entry.tree_path)
                    self.schedule_node(parent_node.employee_id, notify_node.id, entry.tree_path)
                    self._schedule_next(parent_node.employee_id)

                elif needs_review and not has_active_review:
                    logger.info(
                        "[ON_CHILD_COMPLETE] child {} completed — triggering incremental review for parent {}",
                        node.id, parent_node.id,
                    )
                    await self._spawn_review_or_escalate(
                        tree, node, parent_node, children, entry, project_id
                    )
                else:
                    logger.debug("[ON_CHILD_COMPLETE] parent={} — waiting (needs_review={}, active_review={})",
                                 parent_node.id, needs_review, has_active_review)

        # --- CEO confirm node completed → trigger cleanup ---
        # When a CEO_REQUEST confirm node (created below) finishes, run
        # _full_cleanup to archive the project and optionally run retrospective.
        from onemancompany.core.config import CEO_ID as _CEO_ID, EA_ID
        if (node.node_type in (NodeType.CEO_REQUEST, NodeType.CEO_REQUEST.value)
            and node.employee_id == _CEO_ID
            and tree.is_project_complete()):
            ea_node = tree.get_ea_node()
            if ea_node:
                # Advance CEO_PROMPT root: COMPLETED → ACCEPTED → FINISHED
                ea_parent = tree.get_node(ea_node.parent_id) if ea_node.parent_id else None
                if ea_parent and ea_parent.is_ceo_node:
                    if ea_parent.status == TaskPhase.COMPLETED.value:
                        ea_parent.set_status(TaskPhase.ACCEPTED)
                        ea_parent.acceptance_result = {"passed": True, "notes": f"CEO confirmed: {node.result or 'approved'}"}
                        ea_parent.set_status(TaskPhase.FINISHED)
                        logger.info("[TASK LIFECYCLE] CEO root {} → FINISHED (CEO confirmed)", ea_parent.id)
                        save_tree_async(entry.tree_path)

                is_system_node = ea_node.node_type in SYSTEM_NODE_TYPES
                run_retro = not is_system_node and tree.mode != "simple"
                await self._full_cleanup(
                    ea_node.employee_id, ea_node, agent_error=False,
                    project_id=project_id, run_retrospective=run_retro,
                )
                return  # cleanup done, no need to continue

        # --- Bottom-up project completion check ---
        # After any status change, check if the entire project tree is resolved.
        # EA done executing + all child subtrees RESOLVED → trigger CEO confirmation.
        # Skip non-project node types (see _SKIP_COMPLETION_TYPES).
        if node.node_type not in SKIP_COMPLETION_TYPES and tree.is_project_complete():
            # Find the EA node relevant to this completion:
            # For followup tasks, use the followup's EA child (not the original EA).
            ea_node = tree.get_ea_node()  # default: first EA
            # Walk up from completing node to find the closest EA ancestor
            _walk = node
            while _walk:
                if _walk.employee_id == EA_ID and _walk.node_type == NodeType.TASK:
                    ea_node = _walk
                    break
                _walk = tree.get_node(_walk.parent_id) if _walk.parent_id else None

            logger.info(
                "[PROJECT COMPLETE] EA node {} done + all subtrees resolved — scheduling CEO confirmation",
                ea_node.id,
            )
            # Advance CEO parent node if present
            ea_parent = tree.get_node(ea_node.parent_id) if ea_node.parent_id else None
            if ea_parent and ea_parent.is_ceo_node:
                if ea_parent.status != TaskPhase.COMPLETED.value:
                    if ea_parent.status == TaskPhase.PENDING.value:
                        ea_parent.set_status(TaskPhase.PROCESSING)
                    ea_parent.set_status(TaskPhase.COMPLETED)
                    logger.debug("[TASK LIFECYCLE] CEO parent={} → COMPLETED", ea_parent.id)

            # Guard: don't create duplicate confirm nodes for THIS specific EA.
            # Only block if there's an UNRESOLVED confirm node (pending/processing).
            # Resolved ones (finished/cancelled) should not block re-completion
            # after CEO gives new instructions.
            from onemancompany.core.task_lifecycle import has_unresolved_ceo_request
            if has_unresolved_ceo_request(tree.get_children(ea_node.id), _CEO_ID):
                logger.debug("[PROJECT COMPLETE] Unresolved confirm node already exists for EA {} — skipping", ea_node.id)
            else:
                # Build completion summary for CEO
                _pdir = ea_node.project_dir or str(Path(entry.tree_path).parent)

                # Project name from project.yaml (not EA description)
                _base_pid = project_id.split("/")[0] if project_id else ""
                from onemancompany.core.project_archive import load_project as _load_proj
                _proj_doc = _load_proj(_base_pid) if _base_pid else None
                project_name = (_proj_doc or {}).get("name", "") or ea_node.description_preview[:80]

                # Recursively collect all work results from the tree
                work_nodes = _collect_work_results(tree, _pdir)
                succeeded = sum(
                    1 for n in work_nodes
                    if n.status in (TaskPhase.ACCEPTED.value, TaskPhase.FINISHED.value, TaskPhase.COMPLETED.value)
                )
                failed = sum(1 for n in work_nodes if n.status == TaskPhase.FAILED.value)
                total = succeeded + failed

                # Deliverable files in project directory
                deliverables = _list_deliverables(_pdir)

                # Calculate project cost
                _proj_cost = 0.0
                for wn in work_nodes:
                    _proj_cost += getattr(wn, "cost_usd", 0.0) or 0.0

                # Calculate elapsed time
                _ea_created = getattr(ea_node, "created_at", "")
                _elapsed = ""
                if _ea_created:
                    try:
                        from datetime import datetime as _dt
                        _start = _dt.fromisoformat(_ea_created.replace("Z", "+00:00"))
                        _elapsed_s = (datetime.now(_start.tzinfo or None) - _start).total_seconds()
                        if _elapsed_s < 60:
                            _elapsed = f"{int(_elapsed_s)}s"
                        elif _elapsed_s < 3600:
                            _elapsed = f"{int(_elapsed_s // 60)}m"
                        else:
                            _elapsed = f"{_elapsed_s / 3600:.1f}h"
                    except Exception as _te:
                        logger.debug("[PROJECT COMPLETE] elapsed time calc failed: {}", _te)

                # EA-written summary of work results for CEO
                ea_summary = await _summarize_project_for_ceo(
                    project_name, work_nodes, deliverables,
                )

                # Build clear text message for the conversation
                lines = [f"✅ Project Complete: {project_name}", ""]
                lines.append(f"📊 Results: {succeeded}/{total} tasks succeeded" + (f", {failed} failed" if failed else ""))
                if _elapsed:
                    lines.append(f"⏱ Time: {_elapsed}")
                if _proj_cost > 0:
                    lines.append(f"💰 Cost: ${_proj_cost:.2f}")
                if deliverables:
                    lines.append(f"\n📁 Deliverables ({len(deliverables)} files):")
                    for fname in deliverables[:10]:
                        lines.append(f"  • {fname}")
                    if len(deliverables) > 10:
                        lines.append(f"  ... and {len(deliverables) - 10} more")
                if ea_summary:
                    lines.append(f"\n📝 Summary:\n{ea_summary}")
                lines.append("\n👉 Reply to confirm completion, or describe changes for a new iteration.")
                confirm_desc = "\n".join(lines)

                # Push structured completion card to project conversation
                from onemancompany.core.conversation import get_conversation_service as _get_conv_svc
                _conv_svc = _get_conv_svc()
                try:
                    _proj_conv = await _conv_svc.get_or_create_project_conversation(
                        project_id, []
                    )
                    await _conv_svc.push_system_message(
                        _proj_conv.id, confirm_desc, source_employee="project_complete",
                    )
                except Exception as _e:
                    logger.warning("[vessel] failed to push completion card to conversation: {}", _e)

                # Create confirm node — short prompt only (full details already in the completion card above)
                _confirm_prompt = f"✅ {project_name} is complete. Reply to confirm, or describe changes for a new iteration."
                confirm_node = tree.add_child(
                    parent_id=ea_node.id,
                    employee_id=_CEO_ID,
                    description=_confirm_prompt,
                    acceptance_criteria=[],
                )
                confirm_node.node_type = NodeType.CEO_REQUEST
                confirm_node.project_id = project_id
                confirm_node.project_dir = _pdir
                save_tree_async(entry.tree_path)

                self.schedule_node(_CEO_ID, confirm_node.id, entry.tree_path)
                self._schedule_next(_CEO_ID)

    async def _spawn_review_or_escalate(
        self, tree, node, parent_node, children, entry: ScheduleEntry, project_id: str
    ) -> None:
        """Build review prompt and schedule a review node, or escalate to CEO."""
        from onemancompany.core.task_tree import save_tree_async

        _SKIP_REVIEW_TYPES = {NodeType.REVIEW, NodeType.WATCHDOG_NUDGE}
        project_dir = node.project_dir or str(Path(entry.tree_path).parent)
        needs_review = []
        already_accepted = []
        for child in children:
            if child.is_ceo_node or child.node_type in _SKIP_REVIEW_TYPES:
                continue
            if child.status == TaskPhase.ACCEPTED:
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
                # Inject verification evidence from file
                ev_path = Path(project_dir) / "nodes" / child.id / "verification.json"
                if ev_path.exists():
                    try:
                        ev_data = json.loads(ev_path.read_text(encoding=ENCODING_UTF8))
                        from onemancompany.core.task_verification import VerificationEvidence
                        ev = VerificationEvidence(**ev_data)
                        lines.append(f"  {ev.to_review_block()}")
                    except Exception as e:
                        logger.debug("[VERIFICATION] Failed to read evidence file: {}", e)
                lines.append("")
        else:
            lines.append("All subtasks have passed review.")

        # Show active sibling tasks so reviewer doesn't dispatch duplicates
        active_siblings = [
            c for c in children
            if c.node_type not in _SKIP_REVIEW_TYPES
            and c.status in (TaskPhase.PENDING.value, TaskPhase.PROCESSING.value, TaskPhase.HOLDING.value)
        ]
        if active_siblings:
            lines.append("The following sibling tasks are currently in progress (DO NOT dispatch duplicates):")
            for sib in active_siblings:
                lines.append(f"  ⏳ ({sib.employee_id}): {sib.description_preview[:80]} [status={sib.status}]")
            lines.append("")

        lines.append("Please call accept_child(node_id, notes) or reject_child(node_id, reason) for unreviewed subtasks.")
        lines.append("IMPORTANT: Each subtask can only be accepted OR rejected ONCE. Once accepted, it CANNOT be rejected later. Review carefully before deciding.")
        lines.append("IMPORTANT: Do NOT call dispatch_child() to create new tasks during review. Your job is ONLY to review and accept/reject existing subtasks.")
        lines.append("Once all are handled, your task will auto-complete and report upward.")

        review_prompt = "\n".join(lines)

        # --- Circuit breaker: check review round count ---
        from onemancompany.core.config import MAX_REVIEW_ROUNDS, CEO_ID
        review_count = sum(
            1 for c in children
            if c.node_type == NodeType.REVIEW and c.employee_id == parent_node.employee_id
        )
        if review_count >= MAX_REVIEW_ROUNDS:
            logger.warning(
                "Review circuit breaker: {} rounds for parent {} — escalating to CEO",
                review_count, parent_node.id,
            )
            # Check if CEO escalation already exists to prevent infinite loop
            from onemancompany.core.task_lifecycle import has_unresolved_ceo_request
            if has_unresolved_ceo_request(children, CEO_ID):
                logger.debug(
                    "[CIRCUIT BREAKER] CEO escalation already exists for parent {} — skipping duplicate",
                    parent_node.id,
                )
                return

            if parent_node.status != TaskPhase.HOLDING:
                parent_node.set_status(TaskPhase.HOLDING)
                logger.debug("[TASK LIFECYCLE] parent={} → HOLDING (review circuit breaker, {} rounds)", parent_node.id, review_count)
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
            ceo_node.node_type = NodeType.CEO_REQUEST
            ceo_node.project_id = project_id
            ceo_node.project_dir = project_dir
            save_tree_async(entry.tree_path)

            # Schedule via CeoExecutor (creates pending interaction in ConversationService)
            self.schedule_node(CEO_ID, ceo_node.id, entry.tree_path)
            self._schedule_next(CEO_ID)
            return

        # Create a review node in the tree and schedule it
        review_node = tree.add_child(
            parent_id=parent_node.id,
            employee_id=parent_node.employee_id,
            description=review_prompt,
            acceptance_criteria=[],
        )
        review_node.node_type = NodeType.REVIEW
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
        tree_path = str(Path(project_dir) / TASK_TREE_FILENAME)
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
                if dep_node.status != TaskPhase.PENDING.value:
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
                        logger.debug("[TASK LIFECYCLE] node={} → CANCELLED (cascade from {})", dep_node.id, cancelled_deps[0].id)
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
                    logger.debug("[TASK LIFECYCLE] node={} → BLOCKED (dep {} failed)", dep_node.id, completed_node.id)
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
                n.status in (TaskPhase.BLOCKED, TaskPhase.FAILED, TaskPhase.CANCELLED, TaskPhase.ACCEPTED, TaskPhase.FINISHED)
                for n in tree._nodes.values()
                if n.id != tree.root_id
            )
            if all_stuck and any(
                n.status in (TaskPhase.BLOCKED, TaskPhase.FAILED) for n in tree._nodes.values()
            ):
                await _store.save_project_status(project_id, ITER_STATUS_FAILED)

            for emp_id in to_schedule:
                if emp_id not in self._running_tasks:
                    self._schedule_next(emp_id)


    async def _full_cleanup(
        self, employee_id: str, node, agent_error: bool,
        project_id: str, run_retrospective: bool = False,
    ) -> None:
        from onemancompany.core.project_archive import append_action, complete_project

        if run_retrospective:
            try:
                from onemancompany.core.routine import run_post_task_routine
                # Extract actual participants from the task tree so only
                # employees who worked on the project join the retrospective.
                _retro_participants = None
                tree_dir = node.project_dir or ""
                if tree_dir:
                    try:
                        from onemancompany.core.task_tree import TaskTree
                        _tree = TaskTree.load(Path(tree_dir) / "task_tree.yaml")
                        _retro_participants = list({
                            n.employee_id for n in _tree.nodes.values()
                            if n.employee_id
                        })
                        logger.debug(
                            "[cleanup] Retrospective participants from tree: {}",
                            _retro_participants,
                        )
                    except Exception as _tree_err:
                        logger.debug("[cleanup] Could not load tree for participants: {}", _tree_err)
                await run_post_task_routine(
                    node.description,
                    participants=_retro_participants,
                    project_id=project_id,
                )
            except Exception as e:
                logger.exception("Unhandled error")
                if not is_system_project_id(project_id):
                    append_action(project_id, "routine", "Routine error", str(e)[:MAX_SUMMARY_LEN])
                await event_bus.publish(
                    CompanyEvent(
                        type=EventType.AGENT_DONE,
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

        if not is_system_project_id(project_id):
            label = node.description or "Task completed"
            if agent_error:
                label = f"{label} (with errors)"
            if agent_error:
                await _store.save_project_status(project_id, ITER_STATUS_FAILED)
            else:
                complete_project(project_id, label)

        # --- Resource cleanup: evict tree cache, task logs, Claude sessions ---
        self._release_project_resources(employee_id, node, project_id)

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

        # Resolve product_slug + resolved_issue_ids for product trigger pipeline
        _product_slug = ""
        _resolved_issue_ids: list[str] = []
        if project_id:
            from onemancompany.core.project_archive import load_project as _lp
            _proj = _lp(project_id)
            _pid = _proj.get("product_id", "") if _proj else ""
            if _pid:
                from onemancompany.core.product import find_slug_by_product_id
                _product_slug = find_slug_by_product_id(_pid) or ""
            if _product_slug:
                from onemancompany.core import product as _prod
                _all_issues = _prod.list_issues(_product_slug)
                _resolved_issue_ids = [
                    i["id"] for i in _all_issues
                    if project_id in i.get("linked_task_ids", [])
                ]

        await event_bus.publish(
            CompanyEvent(
                type=EventType.AGENT_DONE,
                payload={
                    "role": role,
                    "summary": summary,
                    "employee_id": employee_id,
                    "project_id": project_id,
                    "product_slug": _product_slug,
                    "resolved_issue_ids": _resolved_issue_ids,
                },
                agent=role,
            )
        )

        await event_bus.publish(
            CompanyEvent(type=EventType.STATE_SNAPSHOT, payload={}, agent=SYSTEM_AGENT)
        )

        if self._restart_pending and self.is_idle(exclude=employee_id):
            logger.info("All tasks complete — triggering deferred graceful restart")
            await self._trigger_graceful_restart()

    def _release_project_resources(
        self, employee_id: str, node, project_id: str,
    ) -> None:
        """Release in-memory resources held by a completed project.

        Called at the end of _full_cleanup to prevent resource accumulation:
        - Evict TaskTree from cache (and its lock)
        - Stop Claude daemon and remove session lock for self-hosted employees
        """
        # 1. Evict tree from cache (frees TaskTree object + lock)
        tree_dir = node.project_dir or ""
        if tree_dir:
            try:
                from onemancompany.core.task_tree import evict_tree
                tree_path = Path(tree_dir) / TASK_TREE_FILENAME
                evict_tree(tree_path)
                logger.debug("[cleanup] evicted tree cache for {}", tree_path)
            except Exception as e:
                logger.debug("[cleanup] tree evict failed: {}", e)

        # 3. Release in-memory Claude session lock (daemon + session record
        #    are preserved so follow-up tasks can --resume the conversation)
        executor = self.executors.get(employee_id)
        if isinstance(executor, ClaudeSessionExecutor):
            try:
                from onemancompany.core.claude_session import _remove_session_lock
                _remove_session_lock(employee_id, project_id)
            except Exception as e:
                logger.debug("[cleanup] session lock cleanup failed: {}", e)

    async def _trigger_graceful_restart(self) -> None:
        """Execute a graceful restart: save state, then os.execv."""
        import os
        import sys
        from onemancompany.main import _save_ephemeral_state, _pending_code_changes

        _save_ephemeral_state()
        _pending_code_changes.clear()

        # Cancel and await all fire-and-forget background tasks so they don't
        # get silently killed by os.execv.
        from onemancompany.core.async_utils import _background_tasks
        if _background_tasks:
            logger.info("Graceful restart: waiting for {} background task(s) to finish", len(_background_tasks))
            # Give them a chance to finish naturally (e.g. ongoing hires)
            try:
                await asyncio.wait_for(
                    asyncio.gather(*list(_background_tasks), return_exceptions=True),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                logger.warning("Graceful restart: {} background task(s) still running after 30s, cancelling",
                               len(_background_tasks))
                for t in list(_background_tasks):
                    t.cancel()
                await asyncio.gather(*list(_background_tasks), return_exceptions=True)

        await event_bus.publish(
            CompanyEvent(
                type=EventType.BACKEND_RESTART_SCHEDULED,
                payload={"reason": "Code changes applied", "immediate": True},
                agent=SYSTEM_AGENT,
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

        soul_path = get_workspace_dir(employee_id) / SOUL_FILENAME
        soul_path.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if soul_path.exists():
            try:
                existing = read_text_utf(soul_path)
            except Exception as exc:
                logger.debug("Failed to read SOUL.md for {}: {}", employee_id, exc)

        emp_data = _store.load_employee(employee_id)
        if not emp_data:
            return

        try:
            llm = make_llm(employee_id)
            prompt = (
                f"You are {emp_data.get(PF_NAME, '')} ({emp_data.get(PF_NICKNAME, '')}), {emp_data.get(PF_ROLE, '')}.\n"
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
                write_text_utf(soul_path, new_content)
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
                logger.exception("Unhandled error")
                await event_bus.publish(
                    CompanyEvent(
                        type=EventType.AGENT_DONE,
                        payload={"role": task_name, "summary": f"Error: {e!s}"},
                        agent=task_name,
                    )
                )

            # Sandbox cleanup
            from onemancompany.tools.sandbox import cleanup_sandbox as _cleanup_sandbox
            await _cleanup_sandbox()

            # Broadcast updated state
            await event_bus.publish(
                CompanyEvent(type=EventType.STATE_SNAPSHOT, payload={}, agent=SYSTEM_AGENT)
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
        if not emp_data:
            logger.warning("_get_role: employee {} not found in store, defaulting to 'Employee'", employee_id)
        return (emp_data or {}).get(PF_ROLE, "Employee")

    def _set_employee_status(self, employee_id: str, status: str) -> None:
        try:
            spawn_background(_store.save_employee_runtime(employee_id, status=status))
        except RuntimeError:
            logger.warning("No event loop for runtime persist of {}", employee_id)

    def _publish_dispatch_status(
        self, employee_id: str, *, status: str, entry: ScheduleEntry | None = None
    ) -> None:
        """Fire-and-forget publish of DISPATCH_STATUS_CHANGE event."""
        payload: dict = {"employee_id": employee_id, "status": status}
        if entry is not None:
            payload["node_id"] = entry.node_id
            # Resolve project_id from the task tree
            from onemancompany.core.task_tree import get_tree
            tree = get_tree(entry.tree_path)
            node = tree.get_node(entry.node_id) if tree else None
            payload["project_id"] = (node.project_id if node else "") or ""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(event_bus.publish(
                CompanyEvent(type=EventType.DISPATCH_STATUS_CHANGE, payload=payload)
            ))
        except RuntimeError:
            logger.debug("No event loop for dispatch_status_change of {}", employee_id)

    def _log_node(self, employee_id: str, node_id: str, log_type: str, content: str | dict) -> None:
        """Log an event for a node.

        content can be a string (backward compat) or a dict with structured tool data.
        For dict content, the JSONL disk write uses content["content"] (string).
        The WebSocket event gets the full structured dict for rich frontend rendering.
        """
        # Extract string for disk write, keep structured for WS
        content_str = content["content"] if isinstance(content, dict) else content

        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": log_type,
            "content": content if isinstance(content, dict) else content_str,
        }

        # 1. Disk (SSOT): node-level execution log (JSONL) — always string
        project_id = ""
        current_entry = self._current_entries.get(employee_id)
        if current_entry:
            from onemancompany.core.task_tree import get_tree
            tree = get_tree(current_entry.tree_path)
            node = tree.get_node(current_entry.node_id) if tree else None
            _project_dir = (node.project_dir if node else "") or str(Path(current_entry.tree_path).parent)
            project_id = (node.project_id if node else "") or ""
            _append_node_execution_log(_project_dir, node_id, log_type, content_str)
        else:
            logger.warning("[_log_node] No _current_entries for {} — log not written to disk (node={})", employee_id, node_id)
        # 2. WebSocket: real-time push to frontend (pass project_id to avoid duplicate tree lookup)
        self._publish_log_event(employee_id, node_id, entry, project_id=project_id)

    def _publish_log_event(self, employee_id: str, task_id: str, entry: dict, *, project_id: str = "") -> None:
        """Publish a log event via event bus."""
        try:
            role = self._get_role(employee_id)
            loop = asyncio.get_running_loop()
            loop.create_task(event_bus.publish(
                CompanyEvent(
                    type=EventType.AGENT_LOG,
                    payload={
                        "employee_id": employee_id,
                        "task_id": task_id,
                        "project_id": project_id,
                        "log": entry,
                    },
                    agent=role,
                )
            ))
        except RuntimeError:
            logger.warning("No event loop for log publish ({})", employee_id)

    def _push_to_conversation(self, node, message: str) -> None:
        """Push a progress message to the appropriate conversation.

        Routes to project conversation (if project_id exists) or
        1-on-1 conversation (for cron/adhoc tasks without project context).
        """
        from onemancompany.core.config import CEO_ID

        if node.employee_id == CEO_ID or node.is_ceo_node:
            return

        try:
            from onemancompany.core.conversation import get_conversation_service
            from onemancompany.core.async_utils import spawn_background

            service = get_conversation_service()
            project_id = node.project_id

            async def _push():
                # Real projects get project conversations; system tasks go to 1-on-1
                if project_id and not is_system_project_id(project_id):
                    conv = await service.get_or_create_project_conversation(
                        project_id, [node.employee_id]
                    )
                else:
                    conv = await service.get_or_create_oneonone(node.employee_id)
                await service.push_system_message(conv.id, message, source_employee=node.employee_id)

            spawn_background(_push())
        except Exception as e:
            logger.warning("[conversation_push] Failed for node {}: {}", node.id, e)

    def _publish_node_update(self, employee_id: str, node) -> None:
        """Publish a task update event for a TaskNode (ScheduleEntry path)."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(event_bus.publish(
                CompanyEvent(
                    type=EventType.AGENT_TASK_UPDATE,
                    payload={
                        "employee_id": employee_id,
                        "task": node.to_dict(),
                    },
                    agent=self._get_role(employee_id),
                )
            ))
        except RuntimeError:
            logger.warning("No event loop for node update publish ({})", employee_id)


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


def _ensure_work_principles(employee_id: str, emp_dir, cfg=None) -> None:
    """Ensure work_principles.md exists. Founding employees skip onboarding,
    so this creates a default file if missing."""
    from onemancompany.core.store import WORK_PRINCIPLES_FILENAME

    if not emp_dir.exists():
        return
    wp_path = emp_dir / WORK_PRINCIPLES_FILENAME
    if wp_path.exists():
        return
    name = cfg.name if cfg else employee_id
    nickname = cfg.nickname if cfg else ""
    role = cfg.role if cfg else "Employee"
    dept = cfg.department if cfg else ""
    label = f"{name} ({nickname})" if nickname else name
    from onemancompany.core.config import write_text_utf
    write_text_utf(wp_path,
        f"# {label} Work Principles\n\n"
        f"**Department**: {dept}\n"
        f"**Role**: {role}\n\n"
        f"## Core Principles\n"
        f"1. Complete assigned work diligently and maintain professional standards\n"
        f"2. Actively collaborate with the team and communicate progress promptly\n"
        f"3. Continuously learn and improve professional skills\n"
        f"4. Follow company rules and guidelines\n")
    logger.info("[startup] Created default work_principles.md for {}", employee_id)


def register_founding_employee(
    employee_id: str,
    agent_cls: type,
    emp_cfgs: dict,
    employees_dir,
) -> Vessel:
    """Register a founding employee with the appropriate executor based on hosting mode.

    hosting → executor mapping:
      company   → LangChainExecutor (with agent-specific class)
      self      → ClaudeSessionExecutor
      openclaw  → SubprocessExecutor (launch.sh)
    """
    from onemancompany.core.vessel_config import load_vessel_config

    cfg = emp_cfgs.get(employee_id)
    emp_dir = employees_dir / employee_id
    vessel_cfg = load_vessel_config(emp_dir) if emp_dir.exists() else None

    # Ensure work_principles.md exists (founding employees skip onboarding)
    _ensure_work_principles(employee_id, emp_dir, cfg)

    hosting = (cfg.hosting if cfg else "company").strip().lower()
    executor = _create_executor_for_hosting(hosting, employee_id, agent_cls, emp_dir)
    vessel = employee_manager.register(employee_id, executor, config=vessel_cfg)
    logger.info(
        "[startup] Registered {} ({}) — {} executor",
        cfg.name if cfg else employee_id, employee_id,
        type(executor).__name__,
    )
    return vessel


def _create_executor_for_hosting(
    hosting: str,
    employee_id: str,
    agent_cls: type | None,
    emp_dir,
) -> Launcher:
    """Create the appropriate executor for a hosting mode string."""
    from onemancompany.core.config import LAUNCH_SH_FILENAME

    if hosting == "self":
        return ClaudeSessionExecutor(employee_id)
    elif hosting == "openclaw":
        from onemancompany.core.subprocess_executor import SubprocessExecutor
        script_path = str(emp_dir / LAUNCH_SH_FILENAME)
        return SubprocessExecutor(employee_id, script_path=script_path)
    else:
        # Default: company → langchain
        runner = agent_cls() if agent_cls else None
        if runner is None:
            from onemancompany.agents.base import EmployeeAgent
            runner = EmployeeAgent(employee_id)
        return LangChainExecutor(runner)


async def switch_hosting(
    employee_id: str,
    new_hosting: str,
    agent_cls: type | None = None,
) -> str:
    """Hot-swap an employee's executor by changing hosting mode.

    Requires employee to be idle (not running any tasks).
    Returns the new executor class name.
    """
    from onemancompany.core.config import EMPLOYEES_DIR, employee_configs

    if employee_id in employee_manager._running_tasks:
        raise RuntimeError(f"Employee {employee_id} is currently running a task, cannot switch")
    if employee_id in employee_manager._system_tasks:
        raise RuntimeError(f"Employee {employee_id} has a system task running, cannot switch")

    new_hosting = new_hosting.strip().lower()
    if new_hosting not in ("company", "self", "openclaw"):
        raise ValueError(f"Invalid hosting: {new_hosting}. Must be company, self, or openclaw.")

    emp_dir = EMPLOYEES_DIR / employee_id
    executor = _create_executor_for_hosting(new_hosting, employee_id, agent_cls, emp_dir)

    # Re-register: unregister first to clean up, then register new executor
    old_config = employee_manager.configs.get(employee_id)
    employee_manager.unregister(employee_id)
    employee_manager.register(employee_id, executor, config=old_config)

    # Update in-memory config
    cfg = employee_configs.get(employee_id)
    if cfg:
        cfg.hosting = new_hosting

    logger.info(
        "[hosting] Switched {} to {} ({})",
        employee_id, new_hosting, type(executor).__name__,
    )
    return type(executor).__name__


def get_agent_loop(employee_id: str) -> Vessel | None:
    """Get an employee's vessel (backward compat for PersistentAgentLoop callers)."""
    return employee_manager.get_handle(employee_id)


async def start_all_loops() -> None:
    """Drain any deferred/orphaned pending tasks now that the event loop is running."""
    employee_manager.drain_pending()


async def stop_all_loops() -> None:
    """Cancel any running task executions and background consumers."""
    tasks = list(employee_manager._running_tasks.values())
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    employee_manager._running_tasks.clear()

    # Cancel completion consumer
    if employee_manager._completion_consumer and not employee_manager._completion_consumer.done():
        employee_manager._completion_consumer.cancel()
        try:
            await employee_manager._completion_consumer
        except asyncio.CancelledError:
            logger.debug("Completion consumer cancelled during shutdown")
        employee_manager._completion_consumer = None
        employee_manager._completion_queue = None

    # Cancel ConversationService auto-reply timers
    from onemancompany.core.conversation import get_conversation_service
    get_conversation_service().cancel_all_timers()

    # _task_logs removed — logs are on disk (nodes/{node_id}/execution.log)


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
        tree_path = project_dir / TASK_TREE_FILENAME
        if not tree_path.exists():
            continue
        try:
            tree = TaskTree.load(tree_path)
        except Exception:
            logger.debug("Failed to load task tree at {}", tree_path)
            continue

        for node in tree.all_nodes():
            if node.status != TaskPhase.COMPLETED.value:
                continue
            # Skip system nodes (review/ceo_request auto-finish)
            if node.node_type in SYSTEM_NODE_TYPES:
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
                "description": node.description or "",
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
