"""FastAPI routes — REST endpoints + WebSocket."""

from __future__ import annotations

import asyncio
import re
import uuid as _uuid
from datetime import datetime
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from loguru import logger

from onemancompany.agents.base import tracked_ainvoke
from onemancompany.core.async_utils import spawn_background
from onemancompany.api.websocket import ws_manager
from onemancompany.core.config import (
    CEO_ID,
    COMPANY_DIR,
    COO_ID,
    CSO_ID,
    DATA_ROOT,
    DOT_ENV_FILENAME,
    EA_ID,
    ENCODING_UTF8,
    HR_ID,
    MANIFEST_FILENAME,
    MAX_SUMMARY_LEN,
    PF_CURRENT_TASK_SUMMARY,
    PF_NAME,
    PF_NICKNAME,
    PF_SPRITE,
    STATUS_IDLE,
    STATUS_WORKING,
    SYSTEM_AGENT,
    SYSTEM_SENDER,
    TASK_TREE_FILENAME,
    TL_ACTION_EMPLOYEE_FEEDBACK,
    TL_ACTION_IMPROVEMENT,
    TL_ACTION_SELF_EVAL,
    TL_ACTION_SENIOR_REVIEW,
    TL_FIELD_ACTION,
    TL_FIELD_DETAIL,
    TL_FIELD_EMPLOYEE_ID,
)
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.models import AuthMethod, DecisionStatus, EventType, HostingMode
from onemancompany.core.project_archive import ITER_STATUS_CANCELLED, ITER_STATUS_COMPLETED, ITER_STATUS_FAILED
from onemancompany.core.task_lifecycle import NodeType, TaskPhase
from onemancompany.agents.recruitment import HireRequest, InterviewRequest, InterviewResponse
from onemancompany.core.state import company_state
from onemancompany.core import store as _store
from onemancompany.core.store import load_employee as _load_emp, load_all_employees as _load_all
from onemancompany.core.ceo_conversation import (
    ConversationSession, get_session, register_session, unregister_session,
    load_messages, append_message,
)
from onemancompany.core.errors import ErrorCode, classify_exception

# ---------------------------------------------------------------------------
# Single-file constants
# ---------------------------------------------------------------------------
ONBOARDING_STEP_ORDER = ["assigning_id", "copying_skills", "registering_agent", "completed"]

ANTHROPIC_OAUTH_CLIENT_ID = "8ccecd22-59d4-4db0-a971-530cf734fd17"
ANTHROPIC_AUTH_URL = "https://api.anthropic.com/authorize"
ANTHROPIC_TOKEN_URL = "https://api.anthropic.com/token"
ANTHROPIC_REDIRECT_URI = "http://localhost:8000/api/oauth/callback"
ANTHROPIC_CREATE_KEY_URL = "https://api.anthropic.com/api/oauth/claude_cli/create_api_key"

_TALENT_REQUIRED_FIELDS = ["hosting"]

_MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB per file

# ---------------------------------------------------------------------------
# LLM invocation with retry (canonical impl in core/llm_utils.py)
# ---------------------------------------------------------------------------
from onemancompany.core.llm_utils import llm_invoke_with_retry as _llm_invoke_with_retry  # noqa: E402

router = APIRouter()

MAX_UPLOAD_FILE_SIZE = 50 * 1024 * 1024  # 50 MB per file
MAX_UPLOAD_FILE_COUNT = 20


def _sanitize_filename(raw: str | None) -> str:
    """Extract safe filename, stripping path traversal components."""
    if not raw:
        return f"upload_{_uuid.uuid4().hex[:8]}"
    safe = PurePosixPath(raw).name
    return safe or f"upload_{_uuid.uuid4().hex[:8]}"


def _save_file_deduped(upload_dir: Path, filename: str, content: bytes) -> Path:
    """Save file to *upload_dir*, appending counter if name already exists."""
    dest = upload_dir / filename
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        counter = 1
        while dest.exists():
            dest = upload_dir / f"{stem}_{counter}{suffix}"
            counter += 1
    dest.write_bytes(content)
    return dest


def _get_employee_manager():
    """Lazy import to avoid circular dependency."""
    from onemancompany.core.vessel import employee_manager
    return employee_manager


def _push_adhoc_task(
    employee_id: str,
    description: str,
    project_id: str = "",
    project_dir: str = "",
) -> str:
    """Create a system task tree with a single node and schedule it.

    Used for ad-hoc tasks that don't belong to an existing project tree
    (CEO responses, meeting bookings, HR reviews, CSO notifications, etc.).
    Returns the node_id.
    """
    from pathlib import Path
    from onemancompany.core.task_tree import TaskTree, register_tree, save_tree_async
    from onemancompany.core.config import EMPLOYEES_DIR
    from onemancompany.core.vessel import employee_manager

    # Create a one-node system tree under the employee's tasks directory
    sys_project_id = project_id or f"_sys_{_uuid.uuid4().hex[:8]}"
    tree = TaskTree(project_id=sys_project_id)
    root = tree.create_root(employee_id=employee_id, description=description)
    root.node_type = NodeType.ADHOC
    root.project_id = sys_project_id
    if project_dir:
        root.project_dir = project_dir

    # Persist under employee's tasks dir
    tasks_dir = EMPLOYEES_DIR / employee_id / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    tree_path = tasks_dir / f"{root.id}_tree.yaml"
    register_tree(tree_path, tree)
    save_tree_async(tree_path)  # initial sync save to create the file

    employee_manager.schedule_node(employee_id, root.id, str(tree_path))
    employee_manager._schedule_next(employee_id)
    return root.id, str(tree_path)


def _require_employee(employee_id: str) -> dict:
    """Get employee data from disk or raise 404."""
    data = _load_emp(employee_id)
    if not data:
        raise HTTPException(status_code=404, detail="Employee not found")
    data["id"] = employee_id
    return data


def _scan_employee_projects(employee_id: str, projects_dir: str = "") -> list[dict]:
    """Scan all project.yaml files for projects where employee_id is in team."""
    from pathlib import Path
    from onemancompany.core.config import PROJECTS_DIR
    import yaml

    base = Path(projects_dir) if projects_dir else PROJECTS_DIR
    results = []
    if not base.exists():
        return results

    for pdir in base.iterdir():
        if not pdir.is_dir():
            continue
        pyaml = pdir / "project.yaml"
        if not pyaml.exists():
            continue
        try:
            data = yaml.safe_load(pyaml.read_text(encoding=ENCODING_UTF8)) or {}
        except Exception:
            logger.warning("Failed to parse {}", pyaml)
            continue
        team = data.get("team", [])
        for member in team:
            if member.get("employee_id") == employee_id:
                results.append({
                    "project_id": pdir.name,
                    "task": data.get("task", ""),
                    "status": data.get("status", ""),
                    "role_in_project": member.get("role", ""),
                    "joined_at": member.get("joined_at", ""),
                })
                break

    return results


def _rebuild_employee_agent(employee_id: str) -> bool:
    """Rebuild an employee's LLM agent after config changes (model/provider/api-key).

    Returns True if the agent was rebuilt, False if no agent loop found.
    """
    from onemancompany.core.agent_loop import get_agent_loop
    loop = get_agent_loop(employee_id)
    if not (loop and loop.agent):
        return False
    if not (hasattr(loop.agent, '_agent') and loop.agent._agent):
        return False
    from onemancompany.agents.base import make_llm
    from langgraph.prebuilt import create_react_agent
    from onemancompany.core.tool_registry import tool_registry
    new_llm = make_llm(employee_id)
    loop.agent._agent = create_react_agent(model=new_llm, tools=tool_registry.get_proxied_tools_for(employee_id))
    return True


# --- Auth Onboarding Endpoints ---

@router.get("/api/auth/providers")
async def get_auth_providers() -> list[dict]:
    """Return AUTH_CHOICE_GROUPS for the provider selection UI."""
    from onemancompany.core.auth_choices import get_auth_groups_json

    return get_auth_groups_json()


@router.post("/api/auth/verify")
async def verify_auth(body: dict) -> dict:
    """Verify provider connectivity with a minimal chat request."""
    from onemancompany.core.auth_verify import probe_chat

    provider = body.get("provider", "")
    api_key = body.get("api_key", "")
    model = body.get("model", "")
    base_url = body.get("base_url", "")
    chat_class = body.get("chat_class", "")

    if not provider or not api_key or not model:
        return {"ok": False, "error": "provider, api_key, and model are required"}

    ok, error = await probe_chat(
        provider, api_key, model,
        base_url=base_url,
        chat_class=chat_class,
    )
    return {"ok": ok, "error": error} if not ok else {"ok": True}


@router.post("/api/auth/apply")
async def apply_auth(body: dict) -> dict:
    """Apply an auth choice (persist key/config)."""
    from onemancompany.core.auth_apply import apply_auth_choice

    return await apply_auth_choice(
        choice_value=body.get("choice", ""),
        scope=body.get("scope", ""),
        api_key=body.get("api_key", ""),
        model=body.get("model", ""),
        employee_id=body.get("employee_id", ""),
        base_url=body.get("base_url", ""),
        chat_class=body.get("chat_class", ""),
    )


@router.post("/api/admin/reload")
async def admin_reload() -> dict:
    """Manual soft-reload: re-read all disk data into company_state."""
    from onemancompany.core.state import reload_all_from_disk

    changes = reload_all_from_disk()
    return {"status": "reloaded", **changes}


@router.get("/api/admin/pending-code-changes")
async def admin_pending_code_changes() -> dict:
    """Return accumulated code file changes pending CEO apply."""
    from onemancompany.main import _pending_code_changes

    files = sorted(_pending_code_changes)
    return {"count": len(files), "changed_files": files}


@router.post("/api/admin/apply-code-update")
async def admin_apply_code_update() -> dict:
    """CEO triggers a graceful process restart to pick up code changes.

    If idle, restarts immediately. If tasks are running, defers until complete.
    """
    from onemancompany.core.vessel import employee_manager

    if employee_manager.is_idle():
        # No tasks running — restart now
        await employee_manager._trigger_graceful_restart()
        return {"status": "restarting"}  # won't actually reach client
    else:
        # Tasks running — defer restart
        employee_manager._restart_pending = True
        return {"status": DecisionStatus.DEFERRED.value, "message": "Restart scheduled after current tasks complete"}


@router.post("/api/admin/clear-tasks")
async def admin_clear_tasks() -> dict:
    """Clear all scheduled tasks from EmployeeManager and reset employee statuses to idle."""
    from onemancompany.core.vessel import employee_manager

    # Count and clear all scheduled entries
    cleared = sum(len(entries) for entries in employee_manager._schedule.values())
    employee_manager._schedule.clear()

    # Cancel all running asyncio tasks
    for emp_id, running in list(employee_manager._running_tasks.items()):
        if not running.done():
            running.cancel()
    employee_manager._running_tasks.clear()

    all_emps = _store.load_all_employees()
    for eid in all_emps:
        await _store.save_employee_runtime(eid, status=STATUS_IDLE)
    await event_bus.publish(
        CompanyEvent(type=EventType.STATE_SNAPSHOT, payload={}, agent=SYSTEM_AGENT)
    )
    return {"status": "cleared", "tasks_removed": cleared}


@router.get("/api/bootstrap")
async def get_bootstrap() -> dict:
    """Single bootstrap endpoint — replaces 6 parallel fetches with 1 call.

    Returns employees, task-queue (lightweight), rooms, tools, activity-log,
    and state metadata. Uses async I/O to read from disk in parallel via thread pool.
    """
    from onemancompany.core.project_archive import list_projects
    from onemancompany.core.store import (
        aload_activity_log,
        aload_all_employees,
        aload_overhead,
        aload_tools,
    )
    from onemancompany.core.vessel import employee_manager as _em

    from importlib.metadata import version as _pkg_version
    try:
        app_version = _pkg_version("onemancompany")
    except Exception:
        app_version = "dev"

    # Parallel async disk reads
    employees_raw, tools, activity_log, overhead = await asyncio.gather(
        aload_all_employees(),
        aload_tools(),
        aload_activity_log(),
        aload_overhead(),
    )

    # Build employee list (same logic as /api/employees)
    employees = []
    for emp_id, data in employees_raw.items():
        if emp_id == CEO_ID:
            continue
        runtime = data.pop("runtime", {})
        data["id"] = emp_id
        data["employee_number"] = emp_id
        disk_status = runtime.get("status", STATUS_IDLE)
        if emp_id in _em._running_tasks and disk_status != STATUS_WORKING:
            data["status"] = STATUS_WORKING
        else:
            data["status"] = disk_status
        data["is_listening"] = runtime.get("is_listening", False)
        data[PF_CURRENT_TASK_SUMMARY] = runtime.get(PF_CURRENT_TASK_SUMMARY, "")
        data["api_online"] = runtime.get("api_online", True)
        data["needs_setup"] = runtime.get("needs_setup", False)
        employees.append(data)

    # Lightweight task queue — skip expensive _tree_summary on bootstrap
    loop = asyncio.get_event_loop()
    all_projects = await loop.run_in_executor(None, list_projects)
    tasks = []
    for p in all_projects:
        if p.get("is_named"):
            continue
        if p.get("status") == "archived":
            continue
        status = _normalize_project_status(p.get("status", ""))
        tasks.append({
            "project_id": p["project_id"],
            "task": p.get("task", ""),
            "routed_to": p.get("routed_to", ""),
            "current_owner": p.get("current_owner", ""),
            "status": status,
            "created_at": p.get("created_at", ""),
            "completed_at": p.get("completed_at", ""),
            "result": "",
            "tree": None,  # Lazy — loaded on demand via /api/task-queue
        })

    rooms = [r.to_dict() for r in company_state.meeting_rooms.values()]

    return {
        "employees": employees,
        "tasks": tasks,
        "rooms": rooms,
        "tools": tools,
        "activity_log": activity_log[-50:],
        "version": app_version,
        "office_layout": company_state.office_layout,
        "company_tokens": overhead.get("company_tokens", 0),
    }


@router.get("/api/state")
async def get_state() -> dict:
    """Legacy full-state endpoint — assembles state from disk via store."""
    from onemancompany.core.state import get_active_tasks
    from onemancompany.core.store import (
        load_activity_log,
        load_all_employees,
        load_culture,
        load_ex_employees,
        load_overhead,
        load_rooms,
        load_sales_tasks,
    )
    employees = load_all_employees()
    ex_employees = load_ex_employees()
    overhead = load_overhead()
    from importlib.metadata import version as _pkg_version
    try:
        app_version = _pkg_version("onemancompany")
    except Exception:
        app_version = "dev"

    return {
        "version": app_version,
        "employees": list(employees.values()),
        "ex_employees": list(ex_employees.values()),
        "tools": [t.to_dict() for t in company_state.tools.values()],
        "meeting_rooms": [r.to_dict() for r in company_state.meeting_rooms.values()],
        "ceo_tasks": company_state.ceo_tasks[-10:],
        "active_tasks": [t.to_dict() for t in get_active_tasks()],
        "activity_log": load_activity_log()[-20:],
        "company_culture": load_culture(),
        "office_layout": company_state.office_layout,
        "sales_tasks": load_sales_tasks(),
        "company_tokens": overhead.get("company_tokens", 0),
    }


@router.post("/api/ceo/task")
async def ceo_submit_task(
    task: str = Form(""),
    project_id: str = Form(""),
    project_name: str = Form(""),
    mode: str = Form("standard"),
    files: list[UploadFile] = File(default=[]),
) -> dict:
    """CEO submits a task with optional files, routed to EA via persistent loop."""
    from pathlib import Path
    from onemancompany.core.agent_loop import get_agent_loop
    from onemancompany.core.project_archive import (
        async_create_project_from_task,
        create_iteration,
        create_named_project,
        get_project_dir,
    )

    if not task:
        return {"error": "Empty task"}

    if mode not in ("simple", "standard"):
        mode = "standard"

    company_state.ceo_tasks.append(task)
    await _store.append_activity({"type": "ceo_task", "task": task})

    if project_id:
        iter_id = create_iteration(project_id, task, "pending")
        pid = project_id
    elif project_name:
        pid = create_named_project(project_name)
        iter_id = create_iteration(pid, task, "pending")
    else:
        pid, iter_id = await async_create_project_from_task(task, "pending")

    pdir = get_project_dir(pid)

    # Save uploaded files to project attachments directory
    attachments: list[dict] = []
    if files:
        if len(files) > MAX_UPLOAD_FILE_COUNT:
            return {"error": f"Too many files (max {MAX_UPLOAD_FILE_COUNT})"}
        attach_dir = Path(pdir) / "attachments"
        attach_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            content = await f.read()
            if len(content) > MAX_UPLOAD_FILE_SIZE:
                return {"error": f"File too large: {f.filename} (max {MAX_UPLOAD_FILE_SIZE // 1024 // 1024}MB)"}
            safe_name = _sanitize_filename(f.filename)
            dest = _save_file_deduped(attach_dir, safe_name, content)
            attachments.append({"filename": safe_name, "path": str(dest)})
        logger.debug("Saved {} attachment(s) to {}", len(attachments), attach_dir)

    await event_bus.publish(
        CompanyEvent(type=EventType.CEO_TASK_SUBMITTED, payload={"task": task}, agent="CEO")
    )
    await event_bus.publish(
        CompanyEvent(type=EventType.STATE_SNAPSHOT, payload={}, agent=SYSTEM_AGENT)
    )

    ctx_id = f"{pid}/{iter_id}" if iter_id else pid

    # Build attachment info string for EA
    attach_info = ""
    if attachments:
        lines = [f"- Attachment: {a['filename']} (saved at {a['path']})" for a in attachments]
        attach_info = "\n\nCEO attached the following files:\n" + "\n".join(lines)

    loop = get_agent_loop(EA_ID)
    if loop:
        ea_task = (
            f"CEO has assigned a new task. Please analyze and dispatch to the appropriate owner:\n\n"
            f"Task: {task}{attach_info}\n\n"
            f"[Project ID: {ctx_id}] [Project workspace: {pdir}]"
        )
        # Initialize task tree: CEO node as root, EA as child
        try:
            from onemancompany.core.task_tree import TaskTree, evict_tree
            from onemancompany.core.vessel import _save_project_tree
            from onemancompany.core.agent_loop import employee_manager

            tree_path = Path(pdir) / TASK_TREE_FILENAME

            # For new iterations on existing projects: archive old tree
            if iter_id and tree_path.exists():
                # Find previous iteration ID from project.yaml
                from onemancompany.core.project_archive import load_named_project
                meta = load_named_project(project_id) if project_id else {}
                iters = meta.get("iterations", [])
                if len(iters) >= 2:
                    prev_iter = iters[-2]  # second to last is the previous
                    archive_name = f"task_tree_{prev_iter}.yaml"
                    archive_path = tree_path.parent / archive_name
                    if not archive_path.exists():
                        import shutil
                        shutil.copy2(str(tree_path), str(archive_path))
                        logger.info("Archived previous tree to {}", archive_name)
                # Evict old tree from memory cache
                evict_tree(tree_path)

            tree = TaskTree(project_id=ctx_id, mode=mode)
            # CEO root node — records original prompt
            ceo_root = tree.create_root(employee_id=CEO_ID, description=task)
            ceo_root.node_type = NodeType.CEO_PROMPT
            ceo_root.set_status(TaskPhase.PROCESSING)
            # EA node as child of CEO
            ea_node = tree.add_child(
                parent_id=ceo_root.id,
                employee_id=EA_ID,
                description=ea_task,
                acceptance_criteria=[],
            )
            _save_project_tree(pdir, tree)
            # Register CEO and EA in project team for project history
            from onemancompany.agents.tree_tools import _add_to_project_team
            _add_to_project_team(pdir, CEO_ID)
            _add_to_project_team(pdir, EA_ID)
            # Schedule EA node for execution
            employee_manager.schedule_node(EA_ID, ea_node.id, str(tree_path))
            employee_manager._schedule_next(EA_ID)
        except Exception as e:
            logger.error("Failed to initialize task tree: {}", e)
    else:
        logger.error("EA agent not registered in EmployeeManager — cannot dispatch task")
        raise HTTPException(status_code=503, detail="EA agent not available")
    return {
        "routed_to": "EA",
        "status": "processing",
        "project_id": pid,
        "iteration_id": iter_id,
        "project_dir": pdir,
    }


@router.post("/api/task/{project_id}/followup")
async def task_followup(project_id: str, body: dict) -> dict:
    """CEO adds follow-up instructions to an existing task, dispatched to EA with context."""
    from datetime import datetime as _dt

    from onemancompany.core.agent_loop import get_agent_loop
    from onemancompany.core.project_archive import get_project_dir, append_action
    from onemancompany.core.task_tree import TaskTree, get_tree, save_tree_async
    from onemancompany.core.vessel import _save_project_tree

    instructions = body.get("instructions", "").strip()
    if not instructions:
        return {"error": "Empty instructions"}

    # Load project from filesystem (persistent, not in-memory)
    from pathlib import Path
    from onemancompany.core.project_archive import _resolve_and_load

    pdir = str(get_project_dir(project_id))
    if not pdir:
        raise HTTPException(status_code=404, detail="Project directory not found")

    _ver, doc, _key = _resolve_and_load(project_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Project not found")

    original_task = doc.get("task", "")

    # Load task tree for context
    tree_path = Path(pdir) / TASK_TREE_FILENAME
    previous_result = ""
    if tree_path.exists():
        tree = get_tree(tree_path, project_id=project_id)
        root = tree.get_node(tree.root_id)
        if root:
            root.load_content(tree_path.parent)
            if root.result:
                previous_result = root.result

    # Build follow-up task for EA
    context_parts = [
        f"CEO has added follow-up instructions to a completed task:\n",
        f"Original task: {original_task}\n",
    ]
    if previous_result:
        # Truncate very long results
        prev = previous_result[:2000]
        if len(previous_result) > 2000:
            prev += "...(truncated)"
        context_parts.append(f"Previous task result:\n{prev}\n")
    context_parts.append(f"CEO follow-up instructions: {instructions}\n")
    context_parts.append(
        f"\nPlease continue execution based on the CEO's follow-up instructions and previous task context."
        f" Use dispatch_child() if subtasks are needed.\n\n"
        f"[Project ID: {project_id}] [Project workspace: {pdir}]"
    )
    followup_task = "\n".join(context_parts)

    # Append to existing tree (or create new if none exists)
    tree_path = Path(pdir) / TASK_TREE_FILENAME
    if tree_path.exists():
        tree = get_tree(tree_path, project_id=project_id)
    else:
        tree = TaskTree(project_id=project_id)

    ea_loop = get_agent_loop(EA_ID)
    if not ea_loop:
        raise HTTPException(status_code=503, detail="EA agent not available")

    schedule_node_id = ""  # will be set to the EA node to schedule

    if tree.root_id:
        # Add a new subtree from CEO root — old subtree stays intact
        root = tree.get_node(tree.root_id)

        # Record the followup instruction as a CEO node under root
        followup_node = tree.add_child(
            parent_id=tree.root_id,
            employee_id=CEO_ID,
            description=instructions,
            acceptance_criteria=[],
        )
        followup_node.node_type = NodeType.CEO_FOLLOWUP
        followup_node.status = TaskPhase.ACCEPTED.value

        # Create a new EA node under the followup node for execution
        ea_child = tree.add_child(
            parent_id=followup_node.id,
            employee_id=EA_ID,
            description=followup_task,
            acceptance_criteria=[],
        )
        schedule_node_id = ea_child.id

        # Keep CEO root in PROCESSING while new subtree runs
        if root and root.node_type == NodeType.CEO_PROMPT:
            root.status = TaskPhase.PROCESSING.value
    else:
        # No root yet — create CEO root + EA child
        ceo_root = tree.create_root(employee_id=CEO_ID, description=instructions)
        ceo_root.node_type = NodeType.CEO_PROMPT
        ceo_root.set_status(TaskPhase.PROCESSING)
        ea_child = tree.add_child(
            parent_id=ceo_root.id,
            employee_id=EA_ID,
            description=instructions,
            acceptance_criteria=[],
        )
        schedule_node_id = ea_child.id

    _save_project_tree(pdir, tree)

    # Schedule the EA node for execution
    if schedule_node_id:
        tree_path = str(Path(pdir) / TASK_TREE_FILENAME)
        from onemancompany.core.agent_loop import employee_manager
        employee_manager.schedule_node(EA_ID, schedule_node_id, tree_path)
        employee_manager._schedule_next(EA_ID)

    # Update project.yaml status back to in_progress
    doc["status"] = "in_progress"
    doc["completed_at"] = None
    from onemancompany.core.project_archive import _save_resolved
    _save_resolved(_ver, _key, doc)

    # Log the follow-up
    append_action(project_id, "ceo", "follow-up instructions", instructions[:200])

    await event_bus.publish(
        CompanyEvent(type=EventType.STATE_SNAPSHOT, payload={}, agent=SYSTEM_AGENT)
    )

    return {"status": "ok", "project_id": project_id}


@router.post("/api/oneonone/chat")
async def oneonone_chat(body: dict) -> dict:
    """Per-message 1-on-1 chat.

    All registered employees (regardless of executor type) go through
    _push_adhoc_task so the agent can use its native tools and skills.

    For unregistered employees (no vessel), falls back to a plain LLM call.
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    from onemancompany.agents.base import make_llm

    employee_id = body.get("employee_id", "")
    message = body.get("message", "")
    history = body.get("history", [])
    attachments = body.get("attachments", [])

    if not employee_id or not message:
        return {"error": "Missing employee_id or message"}

    emp_data = _load_emp(employee_id)
    if not emp_data:
        return {"error": f"Employee '{employee_id}' not found"}

    # Build attachment info string for prompt injection
    attach_info = ""
    if attachments:
        lines = [f"- Attachment: {a.get('filename', 'file')} (saved at {a.get('path', '')})" for a in attachments]
        attach_info = "\n\nCEO attached the following files:\n" + "\n".join(lines)

    # On first message (empty history), mark employee as in meeting
    if not history and emp_data:
        await _store.save_employee_runtime(employee_id, is_listening=True)
        await event_bus.publish(
            CompanyEvent(
                type=EventType.GUIDANCE_START,
                payload={"employee_id": employee_id, "name": emp_data.get("name", "")},
                agent="CEO",
            )
        )

    # --- Unified agent path: directly invoke executor (bypass task queue) ---
    response_text: str | None = None

    from onemancompany.core.vessel import employee_manager, TaskContext

    executor = employee_manager.executors.get(employee_id)
    if executor:
        context = ""
        if history:
            context = "Here is the conversation history with CEO:\n" + "\n".join(
                f"{'CEO' if e.get('role') == 'ceo' else 'You'}: {e['content']}"
                for e in history
            ) + "\n\n"
        task_desc = (
            f"[1-on-1 Meeting] CEO says to you:\n{context}"
            f"CEO: {message}{attach_info}\n\n"
            f"Please respond to the CEO. If the CEO asks you to perform an action (e.g., hiring, searching candidates), use your tools to complete it."
        )
        try:
            ctx = TaskContext(employee_id=employee_id)
            result = await executor.execute(task_desc, ctx)
            response_text = result.output or "(Processing complete)"
        except Exception as exc:
            logger.error("1-on-1 direct execute failed for {}: {}", employee_id, exc)
            response_text = f"(Execution error: {exc})"
    else:
        # --- Fallback: plain LLM for employees without a vessel ---
        from onemancompany.agents.base import get_employee_skills_prompt, get_employee_tools_prompt, get_employee_talent_persona

        skills_list = emp_data.get("skills", [])
        skills_str = ", ".join(skills_list) if skills_list else "general"
        persona_section = get_employee_talent_persona(employee_id)
        work_principles = emp_data.get("work_principles", "")
        principles_section = f"\nYour work principles:\n{work_principles}" if work_principles else ""
        culture_items = _store.load_culture()
        culture_section = ""
        if culture_items:
            rules = "\n".join(f"  {i+1}. {item.get('content', '')}" for i, item in enumerate(culture_items))
            culture_section = f"\nCompany culture:\n{rules}"

        skills_section = get_employee_skills_prompt(employee_id)
        tools_section = get_employee_tools_prompt(employee_id)

        system_prompt = (
            f"You are {emp_data.get('name', '')} ({emp_data.get('nickname', '')}), a {emp_data.get('role', '')} in {emp_data.get('department', '')}. "
            f"Skills: {skills_str}. "
            f"You are in a private 1-on-1 meeting with the CEO. "
            f"Respond naturally, 2-4 sentences. Be yourself — share thoughts honestly."
            f"{persona_section}{principles_section}{culture_section}"
            f"{skills_section}{tools_section}"
        )

        # Convert history to LangChain messages
        messages = [SystemMessage(content=system_prompt)]
        for entry in history:
            if entry.get("role") == "ceo":
                messages.append(HumanMessage(content=entry["content"]))
            elif entry.get("role") == "employee":
                messages.append(AIMessage(content=entry["content"]))
        messages.append(HumanMessage(content=message))

        llm = make_llm(employee_id)
        result = await _llm_invoke_with_retry(llm, messages, category="oneonone", employee_id=employee_id)

        content = result.content
        # Normalize content — some models return list of content blocks
        if isinstance(content, list):
            content = "\n".join(
                c.get("text", str(c)) if isinstance(c, dict) else str(c)
                for c in content
            )
        response_text = content or ""

    # Persist both CEO message and employee reply to disk
    await _store.append_oneonone(employee_id, {"role": "ceo", "content": message})
    await _store.append_oneonone(employee_id, {"role": "employee", "content": response_text})

    return {"response": response_text}


@router.post("/api/oneonone/end")
async def oneonone_end(body: dict) -> dict:
    """End meeting. LLM reflects on transcript, updates work principles, and saves 1-1 note."""
    from datetime import datetime

    from langchain_core.messages import HumanMessage, SystemMessage

    from onemancompany.agents.base import make_llm
    employee_id = body.get("employee_id", "")
    history = body.get("history", [])

    if not employee_id:
        return {"error": "Missing employee_id"}

    emp_data = _load_emp(employee_id)
    if not emp_data:
        return {"error": f"Employee '{employee_id}' not found"}

    emp_name = emp_data.get("name", "")
    emp_nickname = emp_data.get("nickname", "")
    emp_role = emp_data.get("role", "")
    emp_dept = emp_data.get("department", "")

    principles_updated = False
    note_saved = False

    if history:
        # Build transcript
        transcript_lines = []
        for entry in history:
            speaker = "CEO" if entry.get("role") == "ceo" else emp_name
            transcript_lines.append(f"{speaker}: {entry['content']}")
        transcript = "\n".join(transcript_lines)

        current_principles = emp_data.get("work_principles", "") or "(No work principles yet)"

        # Combined prompt: reflect on principles AND generate 1-1 summary
        reflection_prompt = (
            f"You are {emp_name} ({emp_nickname}, {emp_role}, Department: {emp_dept}).\n\n"
            f"You just had a 1-on-1 meeting with the CEO. Here is the conversation transcript:\n\n"
            f"{transcript}\n\n"
            f"Your current work principles:\n{current_principles}\n\n"
            f"Do TWO things:\n\n"
            f"1. PRINCIPLES: Did the CEO convey any actionable guidance, directives, or expectations "
            f"that should be incorporated into your work principles?\n"
            f"   If YES — output UPDATED: followed by the complete updated work principles in Markdown.\n"
            f"   If NO — output NO_UPDATE\n\n"
            f"2. SUMMARY: Write a concise 1-1 meeting note (2-4 sentences) summarizing the key "
            f"discussion points, decisions, and any action items from this conversation. "
            f"Include the date. Format: SUMMARY: followed by the note text.\n\n"
            f"Output format (both sections required):\n"
            f"UPDATED: ... or NO_UPDATE\n"
            f"SUMMARY: ..."
        )

        try:
            llm = make_llm(employee_id)
            result = await _llm_invoke_with_retry(llm, [
                SystemMessage(content="You are an employee reflecting on a meeting with the CEO."),
                HumanMessage(content=reflection_prompt),
            ], category="oneonone", employee_id=employee_id)
            response_text = result.content.strip()
        except Exception as e:
            logger.error("oneonone_end reflection failed for {} after retries: {}", employee_id, e)
            # Still end the meeting even if reflection fails
            await _store.save_employee_runtime(employee_id, is_listening=False)
            await event_bus.publish(
                CompanyEvent(
                    type=EventType.GUIDANCE_END,
                    payload={"employee_id": employee_id, "name": emp_name,
                             "principles_updated": False, "note_saved": False},
                    agent="CEO",
                )
            )
            return {"status": "ended", "employee_id": employee_id,
                    "principles_updated": False, "note_saved": False,
                    "warning": f"Reflection failed: {e!s}"}

        # Parse principles update
        if "UPDATED:" in response_text and "NO_UPDATE" not in response_text.split("SUMMARY:")[0]:
            # Extract the UPDATED section (between UPDATED: and SUMMARY:)
            updated_start = response_text.index("UPDATED:") + len("UPDATED:")
            summary_start = response_text.find("SUMMARY:")
            if summary_start > updated_start:
                new_principles = response_text[updated_start:summary_start].strip()
            else:
                new_principles = response_text[updated_start:].strip()
            if new_principles:
                # Persist via store
                await _store.save_work_principles(employee_id, new_principles)
                principles_updated = True

        # Parse and save 1-1 summary as guidance note
        if "SUMMARY:" in response_text:
            summary_start = response_text.index("SUMMARY:") + len("SUMMARY:")
            summary_text = response_text[summary_start:].strip()
            if summary_text:
                date_str = datetime.now().strftime("%Y-%m-%d")
                note = f"**{date_str} 1-1 Meeting**\n{summary_text}"
                # Persist guidance via store
                existing_notes = _store.load_employee_guidance(employee_id)
                existing_notes.append(note)
                await _store.save_guidance(employee_id, existing_notes)
                note_saved = True

    # End the meeting
    await _store.save_employee_runtime(employee_id, is_listening=False)
    await event_bus.publish(
        CompanyEvent(
            type=EventType.GUIDANCE_END,
            payload={
                "employee_id": employee_id,
                "name": emp_name,
                "principles_updated": principles_updated,
                "note_saved": note_saved,
            },
            agent="CEO",
        )
    )

    return {
        "status": "ended",
        "employee_id": employee_id,
        "principles_updated": principles_updated,
        "note_saved": note_saved,
    }


@router.get("/api/meeting_rooms")
async def get_meeting_rooms() -> dict:
    """Get all meeting rooms and their booking status."""
    return {
        "meeting_rooms": [r.to_dict() for r in company_state.meeting_rooms.values()]
    }


@router.post("/api/meeting/book")
async def book_meeting(body: dict) -> dict:
    """Book a meeting room (routes to COO agent for approval)."""
    from onemancompany.core.agent_loop import get_agent_loop

    employee_id = body.get("employee_id", "")
    participants = body.get("participants", [])
    purpose = body.get("purpose", "")

    if not employee_id:
        return {"error": "Missing employee_id"}

    task = (
        f"Employee {employee_id} requests to book a meeting room. "
        f"Participants: {', '.join(participants) if participants else 'none'}. "
        f"Purpose: {purpose or 'not specified'}. "
        f"Please check availability and process this request."
    )
    loop = get_agent_loop(COO_ID)
    if loop:
        _push_adhoc_task(COO_ID, task)
    else:
        logger.error("COO agent not registered in EmployeeManager — cannot process meeting request")
        return {"error": "COO agent not available"}
    return {"status": "processing", "message": "COO is processing the meeting room request"}


@router.post("/api/meeting/release")
async def release_meeting(body: dict) -> dict:
    """Release a meeting room directly."""
    room_id = body.get("room_id", "")
    if not room_id:
        return {"error": "Missing room_id"}

    room = company_state.meeting_rooms.get(room_id)
    if not room:
        return {"error": f"Meeting room '{room_id}' not found"}
    if not room.is_booked:
        return {"error": f"Meeting room '{room.name}' is not booked"}

    old_participants = room.participants.copy()
    room.is_booked = False
    room.booked_by = ""
    room.participants = []
    await _store.save_room(room_id, {
        "is_booked": False,
        "booked_by": "",
        "participants": [],
    })
    await _store.append_activity({
        "type": "meeting_released",
        "room": room.name,
        "participants": old_participants,
    })
    await event_bus.publish(
        CompanyEvent(
            type=EventType.MEETING_RELEASED,
            payload={"room_id": room_id, "room_name": room.name},
            agent="COO",
        )
    )
    return {"status": "released", "room_name": room.name}


@router.post("/api/hr/review")
async def trigger_hr_review() -> dict:
    from onemancompany.core.agent_loop import get_agent_loop

    loop = get_agent_loop(HR_ID)
    if loop:
        # Build review task description inline (same logic as run_quarterly_review)
        from onemancompany.core.config import CEO_LEVEL, TASKS_PER_QUARTER
        from onemancompany.core.state import LEVEL_NAMES

        reviewable, not_ready = [], []
        for eid, edata in _load_all().items():
            # Skip CEO (human user, level 5) — not subject to quarterly review
            if edata.get("level", 1) >= CEO_LEVEL:
                continue
            perf_hist = edata.get("performance_history", [])
            hist_str = ", ".join(
                f"Q{i+1}={h['score']}" for i, h in enumerate(perf_hist)
            ) or "no history"
            cqt = edata.get("current_quarter_tasks", 0)
            elevel = edata.get("level", 1)
            info = (
                f"- {edata.get('name', '')} (nickname: {edata.get('nickname', '')}, ID: {eid}, "
                f"Title: {edata.get('title', '')}, Lv.{elevel} {LEVEL_NAMES.get(elevel, '')}, "
                f"Q tasks: {cqt}/3, "
                f"Performance history: [{hist_str}])"
            )
            if cqt >= TASKS_PER_QUARTER:
                reviewable.append(info)
            else:
                not_ready.append(info)

        parts = []
        if reviewable:
            parts.append("The following employees completed 3 tasks this quarter and are ready for review:\n" + "\n".join(reviewable))
        if not_ready:
            parts.append("The following employees have not completed 3 tasks yet:\n" + "\n".join(not_ready))

        review_task = (
            "Run a quarterly performance review.\n\n"
            + "\n\n".join(parts)
            + "\n\nFor each reviewable employee, give a score of 3.25, 3.5, or 3.75.\n"
            "After the review, check for open positions and hire one new candidate."
        )
        _push_adhoc_task(HR_ID, review_task)
    else:
        logger.error("HR agent not registered in EmployeeManager — cannot run review")
        return {"error": "HR agent not available"}
    return {"status": "HR review started"}


@router.post("/api/routine/start")
async def start_routine(body: dict) -> dict:
    """Trigger the post-task company routine (review meeting + operations review)."""
    from onemancompany.core.routine import run_post_task_routine

    task_summary = body.get("task_summary", "Routine task completed")
    participants = body.get("participants")  # None = all employees
    em = _get_employee_manager()
    em.schedule_system_task(
        run_post_task_routine(task_summary, participants),
        "ROUTINE",
        task_description=f"Post-task routine: {task_summary[:50]}",
    )
    return {"status": "routine_started"}


@router.post("/api/routine/approve")
async def approve_routine_actions(body: dict) -> dict:
    """CEO approves selected action items from a meeting report."""
    from onemancompany.core.routine import execute_approved_actions

    report_id = body.get("report_id", "")
    approved_indices = body.get("approved_indices", [])
    if not report_id:
        return {"error": "Missing report_id"}

    em = _get_employee_manager()
    em.schedule_system_task(
        execute_approved_actions(report_id, approved_indices),
        "ROUTINE",
        task_description="Execute approved actions",
    )
    return {"status": "executing_approved_actions"}


@router.post("/api/routine/all_hands")
async def start_all_hands(body: dict) -> dict:
    """CEO convenes an all-hands meeting. All employees absorb the meeting spirit."""
    from onemancompany.core.routine import run_all_hands_meeting

    message = body.get("message", "")
    if not message:
        return {"error": "Missing CEO message"}

    em = _get_employee_manager()
    em.schedule_system_task(
        run_all_hands_meeting(message),
        "ROUTINE",
        task_description=f"All-hands meeting: {message[:50]}",
    )
    return {"status": "all_hands_started"}


@router.post("/api/meeting/start")
async def meeting_start(body: dict) -> dict:
    """Start a CEO meeting (all_hands or discussion)."""
    from onemancompany.core.routine import start_ceo_meeting

    meeting_type = body.get("type", "")
    if meeting_type not in ("all_hands", "discussion"):
        return {"error": "Invalid meeting type. Must be 'all_hands' or 'discussion'."}

    return await start_ceo_meeting(meeting_type)


@router.post("/api/meeting/chat")
async def meeting_chat(body: dict) -> dict:
    """CEO sends a message in the active meeting."""
    from onemancompany.core.routine import ceo_meeting_chat

    message = body.get("message", "")
    if not message:
        return {"error": "Missing message"}

    return await ceo_meeting_chat(message)


@router.post("/api/meeting/end")
async def meeting_end() -> dict:
    """End the active CEO meeting. EA summarizes action points."""
    from onemancompany.core.routine import end_ceo_meeting

    return await end_ceo_meeting()


@router.get("/api/workflows")
async def list_workflows() -> dict:
    """List all company workflow documents."""
    from onemancompany.core.config import load_workflows

    workflows = load_workflows()
    return {
        "workflows": [
            {"name": name, "preview": content[:100]}
            for name, content in workflows.items()
        ]
    }


@router.get("/api/workflows/{name}")
async def get_workflow(name: str) -> dict:
    """Get the full content of a specific workflow document."""
    from onemancompany.core.config import load_workflows

    workflows = load_workflows()
    content = workflows.get(name)
    if content is None:
        return {"error": f"Workflow '{name}' not found"}
    return {"name": name, "content": content}


@router.put("/api/workflows/{name}")
async def update_workflow(name: str, body: dict) -> dict:
    """Update (or create) a workflow document. CEO edits the company rules."""
    from onemancompany.core.config import save_workflow

    content = body.get("content", "")
    if not content:
        return {"error": "Missing content"}
    save_workflow(name, content)

    await event_bus.publish(
        CompanyEvent(
            type=EventType.WORKFLOW_UPDATED,
            payload={"name": name},
            agent="CEO",
        )
    )
    return {"status": "saved", "name": name}


@router.get("/api/models")
async def list_available_models() -> dict:
    """Fetch available models from OpenRouter API."""
    import httpx

    from onemancompany.core.config import settings

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.openrouter_base_url}/models",
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                models = [
                    {
                        "id": m["id"],
                        "name": m.get("name", m["id"]),
                        "context_length": m.get("context_length", 0),
                    }
                    for m in data.get("data", [])
                ]
                return {"models": models}
            return {"models": [], "error": f"OpenRouter returned {resp.status_code}"}
    except Exception as e:
        return {"models": [], "error": str(e)}


@router.get("/api/employee/{employee_id}")
async def get_employee_detail(employee_id: str) -> dict:
    """Get full employee details including work principles, model config, and manifest."""
    from onemancompany.core.config import employee_configs, load_manifest

    emp = _require_employee(employee_id)

    cfg = employee_configs.get(employee_id)
    llm_model = cfg.llm_model if cfg else ""
    api_provider = cfg.api_provider if cfg else "openrouter"
    api_key = cfg.api_key if cfg else ""

    result = dict(emp)  # emp is already a dict from _require_employee
    result["llm_model"] = llm_model
    result["api_provider"] = api_provider
    result["api_key_set"] = bool(api_key)
    result["api_key_preview"] = ("..." + api_key[-4:]) if len(api_key) >= 4 else ""
    result["hosting"] = cfg.hosting if cfg else HostingMode.COMPANY.value
    result["auth_method"] = cfg.auth_method if cfg else "api_key"
    # Self-hosted employees manage their own auth via Claude CLI — always considered logged in
    if cfg and cfg.hosting == HostingMode.SELF:
        result["oauth_logged_in"] = True
    else:
        result["oauth_logged_in"] = bool(cfg.api_key) if cfg and cfg.auth_method == AuthMethod.OAUTH else False
    result["tool_permissions"] = list(cfg.tool_permissions) if cfg and cfg.tool_permissions else []

    # Include manifest if available
    manifest = load_manifest(employee_id)
    if manifest:
        result["manifest"] = manifest
        # For secret fields, indicate whether they are set in env
        import os as _os
        settings = manifest.get("settings", {})
        for section in (settings.get("sections", []) if isinstance(settings, dict) else []):
            for field in section.get("fields", []):
                if field.get("type") == "secret" and field["key"] != "api_key":
                    env_key = field["key"].upper()
                    val = _os.environ.get(env_key, "")
                    result[f"{field['key']}_set"] = bool(val)
                    if len(val) >= 4:
                        result[f"{field['key']}_preview"] = "..." + val[-4:]

    # Include custom settings (target_email, polling_interval, etc.)
    from onemancompany.core.config import load_custom_settings
    custom = load_custom_settings(employee_id)
    result.update(custom)

    if cfg and cfg.hosting == HostingMode.SELF:
        from onemancompany.core.claude_session import list_sessions
        result["sessions"] = list_sessions(employee_id)

    return result


@router.post("/api/employee/{employee_id}/fire")
async def fire_employee(employee_id: str, body: dict) -> dict:
    """Fire an employee directly (CEO action). Cannot fire founding employees."""
    from onemancompany.agents.termination import execute_fire

    reason = body.get("reason", "CEO decision")
    try:
        result = await execute_fire(employee_id, reason)
    except Exception as e:
        logger.error("fire_employee failed for {}: {}", employee_id, e)
        return {"error": str(e)}
    return result


@router.get("/api/employee/{employee_id}/manifest")
async def get_employee_manifest(employee_id: str) -> dict:
    """Get the manifest.json for an employee."""
    from onemancompany.core.config import load_manifest

    manifest = load_manifest(employee_id)
    if not manifest:
        return {"error": "No manifest found"}
    return manifest


@router.get("/api/employee/{employee_id}/okrs")
async def get_employee_okrs(employee_id: str) -> dict:
    """Get OKRs for an employee."""
    emp_data = _require_employee(employee_id)
    return {"employee_id": employee_id, "okrs": emp_data.get("okrs", [])}


@router.put("/api/employee/{employee_id}/okrs")
async def update_employee_okrs(employee_id: str, body: dict) -> dict:
    """Update OKRs for an employee."""
    _require_employee(employee_id)

    okrs = body.get("okrs", [])
    # Persist via store
    await _store.save_employee(employee_id, {"okrs": okrs})

    await event_bus.publish(CompanyEvent(
        type=EventType.OKR_UPDATED,
        payload={"employee_id": employee_id, "okrs": okrs},
        agent="CEO",
    ))

    return {"employee_id": employee_id, "okrs": okrs}


@router.get("/api/employee/{employee_id}/taskboard")
async def get_employee_taskboard(employee_id: str, status: str = "") -> dict:
    """Get tasks for an employee. Optional ?status= filter: active/completed/failed."""
    from pathlib import Path
    from onemancompany.core.store import load_task_index, append_task_index_entry
    from onemancompany.core.vessel import employee_manager
    from onemancompany.core.task_tree import get_tree

    # Merge disk index + in-memory schedule (for entries not yet indexed)
    index_entries = load_task_index(employee_id)
    indexed_ids = {e.get("node_id") for e in index_entries}

    # Backfill any scheduled entries missing from the index
    for sched in employee_manager._schedule.get(employee_id, []):
        if sched.node_id not in indexed_ids and Path(sched.tree_path).exists():
            append_task_index_entry(employee_id, sched.node_id, sched.tree_path)
            index_entries.append({"node_id": sched.node_id, "tree_path": sched.tree_path})

    tasks = []
    seen_ids: set[str] = set()
    for entry in reversed(index_entries):  # newest first
        node_id = entry.get("node_id", "")
        tree_path = entry.get("tree_path", "")
        if not node_id or not tree_path or node_id in seen_ids:
            continue
        seen_ids.add(node_id)
        tp = Path(tree_path)
        if not tp.exists():
            continue
        try:
            tree = get_tree(tp)
            node = tree.get_node(node_id)
            if node:
                tasks.append(node.to_dict())
        except Exception as e:
            logger.warning("Failed to load task tree {}: {}", tree_path, e)

    # Always compute counts from full list before filtering
    _ACTIVE = {"pending", "processing", "holding"}
    _DONE = {"completed", "accepted", "finished"}
    _FAILED = {"failed", "blocked", "cancelled"}
    all_statuses = [t.get("status", "") for t in tasks]
    counts = {
        "active": sum(1 for s in all_statuses if s in _ACTIVE),
        "completed": sum(1 for s in all_statuses if s in _DONE),
        "failed": sum(1 for s in all_statuses if s in _FAILED),
        "total": len(tasks),
    }

    # Apply filter after counting
    if status == "active":
        tasks = [t for t in tasks if t.get("status") in _ACTIVE]
    elif status == "completed":
        tasks = [t for t in tasks if t.get("status") in _DONE]
    elif status == "failed":
        tasks = [t for t in tasks if t.get("status") in _FAILED]

    return {"tasks": tasks, "counts": counts}


@router.get("/api/node/{node_id}/logs")
async def get_node_logs(node_id: str, project_dir: str = "", tail: int = 100) -> dict:
    """Get execution logs for a task node — single source of truth.

    Reads from {project_dir}/nodes/{node_id}/execution.log (JSONL).
    If project_dir not provided, searches task_index across employees.
    """
    tail = max(1, min(tail, 500))
    pdir = project_dir or _find_node_project_dir(node_id)
    if not pdir:
        return {"logs": [], "node_id": node_id}
    return {"logs": _read_node_log(pdir, node_id, tail), "node_id": node_id}


@router.get("/api/employee/{employee_id}/logs")
async def get_employee_logs(employee_id: str, tail: int = 100) -> dict:
    """Get logs for an employee's current or most recent task.

    Finds the active/latest node_id, then reads from disk (SSOT).
    Returns node_id so frontend can subscribe to the canonical endpoint.
    """
    from onemancompany.core.vessel import employee_manager

    tail = max(1, min(tail, 200))

    # Find current running or most recent node
    current_entry = employee_manager._current_entries.get(employee_id)
    if current_entry:
        logs = _read_node_log(
            str(Path(current_entry.tree_path).parent), current_entry.node_id, tail
        )
        if logs:
            return {"logs": logs, "node_id": current_entry.node_id}

    # Fallback: most recent from schedule
    for entry in reversed(employee_manager._schedule.get(employee_id, [])):
        logs = _read_node_log(str(Path(entry.tree_path).parent), entry.node_id, tail)
        if logs:
            return {"logs": logs, "node_id": entry.node_id}

    return {"logs": [], "node_id": ""}


@router.get("/api/employee/{employee_id}/progress-log")
async def get_employee_progress_log(employee_id: str, limit: int = 50) -> dict:
    """Get cross-task work history summaries from progress.log."""
    from onemancompany.core.config import EMPLOYEES_DIR, ENCODING_UTF8

    limit = max(1, min(limit, 200))
    path = EMPLOYEES_DIR / employee_id / "progress.log"
    if not path.exists():
        return {"entries": []}
    try:
        lines = path.read_text(encoding=ENCODING_UTF8).strip().split("\n")
        entries = []
        for line in lines[-limit:]:
            if line.startswith("[") and "]" in line:
                ts_end = line.index("]") + 1
                entries.append({"timestamp": line[1:ts_end - 1], "content": line[ts_end:].strip()})
            elif line.strip():
                entries.append({"timestamp": "", "content": line})
        return {"entries": entries}
    except Exception as e:
        logger.warning("Failed to read progress log for {}: {}", employee_id, e)
        return {"entries": []}


def _read_node_log(project_dir: str, node_id: str, limit: int) -> list[dict]:
    """Read JSONL execution log from nodes/{node_id}/execution.log."""
    import json as _json
    log_path = Path(project_dir) / "nodes" / node_id / "execution.log"
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        logs = []
        for line in lines[-limit:]:
            try:
                e = _json.loads(line)
                logs.append({"timestamp": e.get("ts", ""), "type": e.get("type", ""), "content": e.get("content", "")})
            except _json.JSONDecodeError:
                logs.append({"timestamp": "", "type": "", "content": line})
        return logs
    except Exception as e:
        logger.warning("Failed to read node log {}/{}: {}", project_dir, node_id, e)
        return []


def _find_node_project_dir(node_id: str) -> str:
    """Find project_dir for a node by searching task_index files."""
    from onemancompany.core.config import EMPLOYEES_DIR
    from onemancompany.core.store import load_task_index
    if not EMPLOYEES_DIR.exists():
        return ""
    for emp_dir in EMPLOYEES_DIR.iterdir():
        if not emp_dir.is_dir():
            continue
        for entry in load_task_index(emp_dir.name):
            if entry.get("node_id") == node_id:
                tp = entry.get("tree_path", "")
                return str(Path(tp).parent) if tp else ""
    return ""


async def _sync_tree_cancel(cancelled_node_ids: list[tuple[str, str]]) -> None:
    """Update task tree nodes for cancelled tasks.

    Args:
        cancelled_node_ids: list of (node_id, tree_path) tuples.
    """
    from pathlib import Path
    from onemancompany.core.task_tree import get_tree, save_tree_async

    # Group by tree_path for efficiency
    trees: dict[str, object] = {}
    for node_id, tree_path in cancelled_node_ids:
        if not tree_path:
            continue
        if tree_path not in trees:
            tp = Path(tree_path)
            if tp.exists():
                trees[tree_path] = get_tree(tp)
            else:
                trees[tree_path] = None
        tree = trees[tree_path]
        if not tree:
            continue
        node = tree.get_node(node_id)
        if node and node.status not in (TaskPhase.ACCEPTED, TaskPhase.FAILED, TaskPhase.CANCELLED):
            # CEO cancellation — force status bypass for terminal override
            node.status = TaskPhase.CANCELLED.value
            node.result = "Cancelled by CEO"
            from onemancompany.core.events import CompanyEvent as _CE
            await event_bus.publish(_CE(
                type=EventType.TREE_UPDATE,
                payload={"project_id": tree.project_id, "event_type": "node_updated",
                         "node_id": node_id, "data": {"status": TaskPhase.CANCELLED.value}},
                agent=SYSTEM_AGENT,
            ))
    # Save modified trees
    for tree_path_str, tree in trees.items():
        if tree:
            save_tree_async(Path(tree_path_str))


@router.post("/api/task/{project_id}/abort")
async def abort_task(project_id: str) -> dict:
    """Abort all agent tasks related to a project.

    Cancels pending/in-progress tasks on all agent boards, cancels running
    asyncio tasks, removes from company active_tasks, and broadcasts state update.
    """
    from onemancompany.core.agent_loop import employee_manager

    cancelled_count = employee_manager.abort_project(project_id)

    # Cancel ALL non-terminal tree nodes (including waiting/pending ones not yet pushed to schedule)
    cancelled_tree_nodes = 0
    from onemancompany.core.project_archive import get_project_dir, load_project as _lp
    from onemancompany.core.task_tree import get_tree, save_tree_async
    from pathlib import Path as _Path
    from datetime import datetime as _dt

    pdir = get_project_dir(project_id)
    if pdir:
        tree_path = _Path(pdir) / TASK_TREE_FILENAME
        if tree_path.exists():
            tree = get_tree(tree_path, project_id=project_id)
            for node in tree.all_nodes():
                if node.status not in (TaskPhase.ACCEPTED, TaskPhase.FAILED, TaskPhase.CANCELLED):
                    # CEO abort — force status bypass for terminal override
                    node.status = TaskPhase.CANCELLED.value
                    node.result = "Cancelled by CEO (project aborted)"
                    cancelled_tree_nodes += 1
            save_tree_async(tree_path)

    # Trigger 4: CEO aborts → cancelled (via store for mark_dirty)
    from onemancompany.core import store as _store
    proj_doc = _lp(project_id)
    if proj_doc and proj_doc.get("status") not in (ITER_STATUS_COMPLETED, ITER_STATUS_CANCELLED, ITER_STATUS_FAILED):
        await _store.save_project_status(
            project_id, ITER_STATUS_CANCELLED, completed_at=_dt.now().isoformat()
        )

    # Broadcast state
    await event_bus.publish(
        CompanyEvent(type=EventType.STATE_SNAPSHOT, payload={}, agent=SYSTEM_AGENT)
    )

    return {"status": "ok", "cancelled": cancelled_count, "tree_nodes_cancelled": cancelled_tree_nodes}


@router.post("/api/employee/{employee_id}/abort")
async def abort_employee_tasks(employee_id: str) -> dict:
    """Abort all tasks for a specific employee."""
    from onemancompany.core.agent_loop import employee_manager

    count = employee_manager.abort_employee(employee_id)
    await event_bus.publish(
        CompanyEvent(type=EventType.STATE_SNAPSHOT, payload={}, agent=SYSTEM_AGENT)
    )
    return {"status": "ok", "cancelled": count, "employee_id": employee_id}


@router.post("/api/abort-all")
async def abort_all_tasks() -> dict:
    """Abort all tasks for all employees. Panic button."""
    from onemancompany.core.agent_loop import employee_manager

    count = await employee_manager.abort_all()
    await event_bus.publish(
        CompanyEvent(type=EventType.STATE_SNAPSHOT, payload={}, agent=SYSTEM_AGENT)
    )
    return {"status": "ok", "cancelled": count}


@router.post("/api/employee/{employee_id}/task/{task_id}/cancel")
async def cancel_agent_task(employee_id: str, task_id: str) -> dict:
    """Cancel a specific task node on an agent's schedule.

    task_id here is the TaskNode ID (node_id).
    """
    from datetime import datetime
    from pathlib import Path

    from onemancompany.core.agent_loop import employee_manager
    from onemancompany.core.task_tree import get_tree, save_tree_async

    # Find the entry in the schedule OR check if it's the currently running task
    entry_found = None
    for entry in employee_manager._schedule.get(employee_id, []):
        if entry.node_id == task_id:
            entry_found = entry
            break

    # Also check the running task's entry (running tasks are popped from schedule)
    running_entry = employee_manager._current_entries.get(employee_id)
    if not entry_found and running_entry and running_entry.node_id == task_id:
        entry_found = running_entry

    if not entry_found:
        return {"status": "error", "message": "Task not found in schedule or running tasks"}

    # Load tree and node
    tp = Path(entry_found.tree_path)
    if not tp.exists():
        return {"status": "error", "message": "Tree file not found"}

    tree = get_tree(tp)
    node = tree.get_node(task_id)
    if not node:
        return {"status": "error", "message": "Node not found in tree"}

    if node.status not in (TaskPhase.PENDING, TaskPhase.PROCESSING, TaskPhase.HOLDING):
        return {"status": "error", "message": f"Task already {node.status}"}

    was_in_progress = node.status == TaskPhase.PROCESSING

    # Force cancel — bypass transition validation
    node.status = TaskPhase.CANCELLED.value
    node.completed_at = datetime.now().isoformat()
    node.result = "Cancelled by CEO"
    save_tree_async(tp)

    # Remove from schedule
    employee_manager.unschedule(employee_id, task_id)

    # Stop any holding watchdog crons associated with this task
    from onemancompany.core.automation import stop_cron
    stop_cron(employee_id, f"reply_{task_id}")
    stop_cron(employee_id, f"holding_{task_id}")

    # Cancel the running asyncio.Task if this was in_progress
    if was_in_progress and employee_id in employee_manager._running_tasks:
        running = employee_manager._running_tasks[employee_id]
        if not running.done():
            running.cancel()

    # Reset employee status if no more scheduled tasks
    has_active = any(True for _ in employee_manager._schedule.get(employee_id, []))
    if not has_active:
        await _store.save_employee_runtime(employee_id, status=STATUS_IDLE, current_task_summary="")

    # Broadcast state
    await event_bus.publish(
        CompanyEvent(type=EventType.STATE_SNAPSHOT, payload={}, agent=SYSTEM_AGENT)
    )

    return {"status": "ok"}


@router.put("/api/employee/{employee_id}/settings")
async def update_employee_custom_settings(employee_id: str, body: dict) -> dict:
    """Save custom manifest settings (target_email, polling_interval, etc.) to settings.json."""
    from onemancompany.core.config import save_custom_settings

    _require_employee(employee_id)
    # Filter out keys handled by dedicated endpoints
    reserved = {"hosting", "llm_model", "temperature", "api_key", "api_provider"}
    updates = {k: v for k, v in body.items() if k not in reserved}
    if not updates:
        return {"status": "ok", "message": "No custom settings to save"}
    result = save_custom_settings(employee_id, updates)
    return {"status": "ok", "settings": result}


@router.put("/api/employee/{employee_id}/model")
async def update_employee_model(employee_id: str, body: dict) -> dict:
    """Update the LLM model for a specific employee. Saves to profile.yaml."""
    import yaml

    from onemancompany.core.config import EMPLOYEES_DIR, employee_configs

    model_id = body.get("model", "")
    if not model_id:
        return {"error": "Missing model"}

    emp = _require_employee(employee_id)

    # Compute new salary — skip OpenRouter pricing for self-hosted or non-openrouter providers
    cfg = employee_configs.get(employee_id)
    hosting = cfg.hosting if cfg else emp.get("hosting", "company")
    api_provider = cfg.api_provider if cfg else "openrouter"
    if hosting == "self" or api_provider != "openrouter":
        new_salary = cfg.salary_per_1m_tokens if cfg else 0.0
    else:
        from onemancompany.core.model_costs import compute_salary
        new_salary = compute_salary(model_id)

    # Update in-memory config
    if cfg:
        cfg.llm_model = model_id
        cfg.salary_per_1m_tokens = new_salary

    # Persist via store
    await _store.save_employee(employee_id, {"llm_model": model_id, "salary_per_1m_tokens": new_salary})

    # Rebuild the in-memory LLM agent so the new model takes effect immediately
    _rebuild_employee_agent(employee_id)

    await event_bus.publish(
        CompanyEvent(
            type=EventType.AGENT_DONE,
            payload={
                "role": "CEO",
                "summary": f"Updated {emp.get('name', '')} ({emp.get('nickname', '')})'s model to {model_id}, salary=${new_salary}/1M",
            },
            agent="CEO",
        )
    )

    return {"status": "updated", "employee_id": employee_id, "model": model_id, "salary_per_1m_tokens": new_salary}


@router.put("/api/employee/{employee_id}/hosting")
async def update_employee_hosting(employee_id: str, body: dict) -> dict:
    """Switch an employee's hosting mode between company-hosted and self-hosted.

    Changing hosting mode requires a server restart to re-register the employee
    with the appropriate executor (LangChain vs Claude Code session).
    """
    import yaml
    import json as _json

    from onemancompany.core.config import EMPLOYEES_DIR, employee_configs, invalidate_manifest_cache

    new_hosting = body.get("hosting", "")
    if new_hosting not in (HostingMode.COMPANY, HostingMode.SELF):
        return {"error": "Invalid hosting mode. Use 'company' or 'self'."}

    emp = _require_employee(employee_id)

    cfg = employee_configs.get(employee_id)
    if not cfg:
        return {"error": "Employee config not found"}

    old_hosting = cfg.hosting

    if old_hosting == new_hosting:
        return {"status": "unchanged", "hosting": new_hosting}

    # Update in-memory config
    cfg.hosting = new_hosting

    # Persist via store
    hosting_updates: dict = {"hosting": new_hosting}
    if new_hosting == HostingMode.SELF:
        hosting_updates["api_provider"] = "anthropic"
        hosting_updates["auth_method"] = "api_key"  # self-hosted uses Claude CLI auth, keep valid default
        cfg.auth_method = "api_key"
    await _store.save_employee(employee_id, hosting_updates)

    # Update manifest.json to reflect hosting change and adjust settings sections
    manifest_path = EMPLOYEES_DIR / employee_id / MANIFEST_FILENAME
    if manifest_path.exists():
        manifest = _json.loads(manifest_path.read_text(encoding=ENCODING_UTF8))
        manifest["hosting"] = new_hosting

        sections = manifest.get("settings", {}).get("sections", [])

        if new_hosting == HostingMode.SELF:
            # Add connection section if not present
            has_connection = any(s.get("id") == "connection" for s in sections)
            if not has_connection:
                sections.insert(0, {
                    "id": "connection",
                    "title": "Connection",
                    "fields": [
                        {"key": "sessions", "type": "readonly", "label": "Sessions", "value_from": "api:sessions"},
                    ],
                })
            # Remove LLM section (self-hosted uses its own model)
            sections[:] = [s for s in sections if s.get("id") != "llm"]
        else:
            # Remove connection section
            sections[:] = [s for s in sections if s.get("id") != "connection"]
            # Add LLM section back if not present
            has_llm = any(s.get("id") == "llm" for s in sections)
            if not has_llm:
                sections.append({
                    "id": "llm",
                    "title": "LLM Configuration",
                    "fields": [
                        {"key": "llm_model", "type": "select", "label": "Model", "options_from": "api:models"},
                        {"key": "temperature", "type": "number", "label": "Temperature", "default": 0.7, "min": 0, "max": 2, "step": 0.1},
                    ],
                })

        manifest_path.write_text(_json.dumps(manifest, indent=2, ensure_ascii=False), encoding=ENCODING_UTF8)
        invalidate_manifest_cache(employee_id)

    hosting_label = "Self-hosted (Claude Code)" if new_hosting == HostingMode.SELF else "Company-hosted (LangChain)"
    await event_bus.publish(
        CompanyEvent(
            type=EventType.AGENT_DONE,
            payload={
                "role": "CEO",
                "summary": f"Switched {emp['name']} to {hosting_label}. Restart required to take effect.",
            },
            agent="CEO",
        )
    )

    return {
        "status": "updated",
        "hosting": new_hosting,
        "restart_required": True,
        "message": f"Hosting changed to '{new_hosting}'. Server restart required to re-register agent.",
    }




# ===== Global API Settings =====


def _get_talent_market_connected() -> bool:
    """Check if the cloud Talent Market MCP session is active."""
    try:
        from onemancompany.agents.recruitment import talent_market
        return talent_market.connected
    except ImportError:
        return False


def _get_local_talent_count() -> int:
    """Count local talent packages available."""
    try:
        from onemancompany.core.config import list_available_talents
        return len(list_available_talents())
    except ImportError:
        return 0


@router.get("/api/talent-pool")
async def get_talent_pool() -> dict:
    """Return the talent pool — purchased talents (API) or local packages."""
    from onemancompany.agents.recruitment import talent_market

    if talent_market.connected:
        try:
            data = await talent_market.list_my_talents()
            talents = []
            for t in data.get("talents", []):
                talents.append({
                    "talent_id": t.get("talent_id", t.get("id", "")),
                    "name": t.get("name", ""),
                    "role": t.get("role", ""),
                    "skills": t.get("skills", []),
                    "status": "purchased",
                    "purchased_at": t.get("purchased_at", ""),
                })
            return {"source": "api", "talents": talents}
        except Exception as e:
            logger.error("Failed to fetch talent pool from API: {}", e)
            # Fall through to local

    from onemancompany.core.config import list_available_talents, load_talent_profile
    talents = []
    for t in list_available_talents():
        profile = load_talent_profile(t["id"])
        if profile:
            talents.append({
                "talent_id": profile.get("id", t["id"]),
                "name": profile.get("name", t["id"]),
                "role": profile.get("role", ""),
                "skills": profile.get("skills", []),
                "status": "local",
            })
    return {"source": "local", "talents": talents}


@router.get("/api/settings/api")
async def get_api_settings() -> dict:
    """Return current global API configuration status."""
    from onemancompany.core.config import settings

    or_key = settings.openrouter_api_key
    ant_key = settings.anthropic_api_key

    # Talent market config from config.yaml
    from onemancompany.core.config import load_app_config
    tm = load_app_config().get("talent_market", {})
    tm_key = tm.get("api_key", "")

    return {
        "openrouter": {
            "api_key_set": bool(or_key),
            "api_key_preview": ("..." + or_key[-4:]) if len(or_key) >= 4 else "",
            "base_url": settings.openrouter_base_url,
            "default_model": settings.default_llm_model,
        },
        "anthropic": {
            "api_key_set": bool(ant_key),
            "api_key_preview": ("..." + ant_key[-4:]) if len(ant_key) >= 4 else "",
            "auth_method": settings.anthropic_auth_method,
        },
        "talent_market": {
            "api_key_set": bool(tm_key),
            "api_key_preview": ("..." + tm_key[-4:]) if len(tm_key) >= 4 else "",
            "mode": "cloud" if bool(tm_key) else "local",
            "connected": _get_talent_market_connected(),
            "local_talent_count": _get_local_talent_count(),
        },
    }


@router.put("/api/settings/api")
async def update_api_settings(body: dict) -> dict:
    """Update global API configuration (writes to .env + refreshes in-memory)."""
    from onemancompany.core.config import settings, update_env_var

    provider = body.get("provider", "")

    if provider == "talent_market":
        # Save talent market API key to config.yaml
        import yaml
        from onemancompany.core.config import APP_CONFIG_PATH, load_app_config, reload_app_config
        api_key = body.get("api_key", "")
        if not api_key:
            return {"error": "API key is required"}
        config = load_app_config()
        tm = config.setdefault("talent_market", {})
        tm["api_key"] = api_key
        APP_CONFIG_PATH.write_text(yaml.dump(config, default_flow_style=False, allow_unicode=True), encoding=ENCODING_UTF8)
        reload_app_config()

        # Reconnect Talent Market with new API key
        try:
            from onemancompany.agents.recruitment import stop_talent_market, start_talent_market
            await stop_talent_market()
            await start_talent_market()
        except Exception as e:
            logger.error("Failed to reconnect Talent Market: {}", e)

        return {
            "status": "updated",
            "talent_market": {
                "api_key_set": True,
                "api_key_preview": "..." + api_key[-4:] if len(api_key) >= 4 else "",
            },
        }

    if provider != "openrouter":
        return {"error": "Only 'openrouter' and 'talent_market' providers are supported. "
                "Anthropic auth is managed via OAuth flow, not API key."}

    api_key = body.get("api_key", "")
    if api_key:
        update_env_var("OPENROUTER_API_KEY", api_key)
    base_url = body.get("base_url", "")
    if base_url:
        update_env_var("OPENROUTER_BASE_URL", base_url)
    default_model = body.get("default_model", "")
    if default_model:
        update_env_var("DEFAULT_LLM_MODEL", default_model)

    # Return refreshed status
    from onemancompany.core.config import settings as refreshed
    or_key = refreshed.openrouter_api_key
    ant_key = refreshed.anthropic_api_key
    return {
        "status": "updated",
        "openrouter": {
            "api_key_set": bool(or_key),
            "api_key_preview": ("..." + or_key[-4:]) if len(or_key) >= 4 else "",
            "base_url": refreshed.openrouter_base_url,
            "default_model": refreshed.default_llm_model,
        },
        "anthropic": {
            "api_key_set": bool(ant_key),
            "api_key_preview": ("..." + ant_key[-4:]) if len(ant_key) >= 4 else "",
            "auth_method": refreshed.anthropic_auth_method,
        },
    }


@router.post("/api/settings/api/test")
async def test_api_connection(body: dict) -> dict:
    """Deprecated — use POST /api/auth/verify instead."""
    from onemancompany.core.auth_verify import probe_chat

    provider = body.get("provider", "openrouter")
    api_key = body.get("api_key", "")
    model = body.get("model", "")

    ok, error = await probe_chat(provider, api_key, model)
    return {"ok": ok, "error": error} if not ok else {"ok": True}


@router.post("/api/settings/api/oauth/start")
async def company_oauth_start() -> dict:
    """Start company-level Anthropic OAuth PKCE flow.

    Same as per-employee OAuth, but saves the token to .env (company level).
    """
    import base64
    import hashlib
    import secrets

    code_verifier = secrets.token_urlsafe(43)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    state = "company_" + secrets.token_urlsafe(32)

    _oauth_sessions[state] = {
        "employee_id": "__company__",
        "code_verifier": code_verifier,
        "redirect_uri": ANTHROPIC_REDIRECT_URI,
    }

    auth_url = (
        f"{ANTHROPIC_AUTH_URL}"
        f"?client_id={ANTHROPIC_OAUTH_CLIENT_ID}"
        f"&redirect_uri={ANTHROPIC_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=user:inference+user:profile"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
        f"&state={state}"
    )

    return {"auth_url": auth_url, "state": state}


# ===== OAuth Login (Anthropic PKCE) =====

# In-memory store for pending OAuth sessions: state -> {employee_id, code_verifier}
_oauth_sessions: dict[str, dict] = {}

@router.post("/api/employee/{employee_id}/oauth/start")
async def oauth_start(employee_id: str) -> dict:
    """Start OAuth PKCE flow for an employee.

    Returns the authorization URL. The user authorizes in a popup,
    then Anthropic redirects back to our localhost callback which
    automatically exchanges the code for tokens.
    """
    import base64
    import hashlib
    import secrets

    from onemancompany.core.config import employee_configs

    emp = _require_employee(employee_id)

    cfg = employee_configs.get(employee_id)
    if not cfg:
        return {"error": "Employee config not found"}
    if cfg.hosting == HostingMode.SELF:
        return {"error": "Self-hosted employee uses Claude CLI's built-in auth. Run 'claude' in terminal to login."}
    if cfg.auth_method != "oauth":
        return {"error": "Employee does not use OAuth authentication"}

    # PKCE: generate code_verifier and code_challenge
    code_verifier = secrets.token_urlsafe(43)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    state = secrets.token_urlsafe(32)

    _oauth_sessions[state] = {
        "employee_id": employee_id,
        "code_verifier": code_verifier,
        "redirect_uri": ANTHROPIC_REDIRECT_URI,
    }

    auth_url = (
        f"{ANTHROPIC_AUTH_URL}"
        f"?client_id={ANTHROPIC_OAUTH_CLIENT_ID}"
        f"&redirect_uri={ANTHROPIC_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=user:inference+user:profile"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
        f"&state={state}"
    )

    return {"auth_url": auth_url, "state": state}


@router.post("/api/employee/{employee_id}/oauth/exchange")
async def oauth_exchange(employee_id: str, body: dict) -> dict:
    """Exchange the authorization code (from Anthropic callback page) for tokens.

    The user copies the code from the Anthropic callback URL and submits it here.
    We exchange it for an access token, then create a permanent API key.
    """
    import httpx
    import yaml

    from onemancompany.core.config import EMPLOYEES_DIR, employee_configs

    code = body.get("code", "").strip()
    state = body.get("state", "").strip()
    if not code or not state:
        return {"error": "Missing code or state"}

    session = _oauth_sessions.pop(state, None)
    if not session:
        return {"error": "Invalid or expired OAuth session"}

    if session["employee_id"] != employee_id:
        return {"error": "Employee ID mismatch"}

    code_verifier = session["code_verifier"]
    redirect_uri = session["redirect_uri"]

    # Step 1: Exchange authorization code for access token
    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": ANTHROPIC_OAUTH_CLIENT_ID,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    }
    try:
        async with httpx.AsyncClient() as client:
            # Try form-urlencoded first (OAuth spec standard)
            resp = await client.post(
                ANTHROPIC_TOKEN_URL,
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15.0,
            )
            # If form fails, try JSON
            if resp.status_code != 200:
                resp = await client.post(
                    ANTHROPIC_TOKEN_URL,
                    json=token_data,
                    timeout=15.0,
                )
            if resp.status_code != 200:
                return {"error": f"Token exchange failed ({resp.status_code}): {resp.text[:300]}"}
            tokens = resp.json()
    except Exception as e:
        return {"error": f"Token exchange error: {e}"}

    access_token = tokens.get("access_token", "")
    if not access_token:
        return {"error": "No access_token in response"}

    # Step 2: Try to create a permanent API key using the OAuth token
    api_key = access_token  # fallback: use access token directly
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                ANTHROPIC_CREATE_KEY_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"name": f"OneManCompany-{employee_id}"},
                timeout=15.0,
            )
            if resp.status_code == 200:
                key_data = resp.json()
                api_key = key_data.get("api_key", access_token)
    except Exception as _e:
        logger.debug("OAuth key exchange failed, falling back to access token: {}", _e)

    # Store the key
    cfg = employee_configs.get(employee_id)
    if cfg:
        cfg.api_key = api_key
        cfg.oauth_refresh_token = tokens.get("refresh_token", "")

        # Persist to disk via store
        await _store.save_employee(employee_id, {
            "api_key": api_key,
            "oauth_refresh_token": tokens.get("refresh_token", ""),
        })

    _oauth_emp = _load_emp(employee_id)
    emp_name = _oauth_emp.get("name", employee_id) if _oauth_emp else employee_id

    await event_bus.publish(
        CompanyEvent(
            type=EventType.AGENT_DONE,
            payload={"role": "CEO", "summary": f"{emp_name} OAuth login successful."},
            agent="CEO",
        )
    )

    return {"status": "ok", "employee_id": employee_id, "api_key_set": True}


@router.get("/api/oauth/callback")
async def oauth_callback(code: str = "", state: str = "", error: str = ""):
    """Handle OAuth redirect from Anthropic.  Exchanges code for tokens."""
    import httpx
    import yaml

    from fastapi.responses import HTMLResponse

    from onemancompany.core.config import EMPLOYEES_DIR, employee_configs

    if error:
        return HTMLResponse(f"<html><body><h2>Login failed</h2><p>{error}</p>"
                            "<script>window.close()</script></body></html>")

    session = _oauth_sessions.pop(state, None)
    if not session:
        return HTMLResponse("<html><body><h2>Invalid session</h2><p>OAuth state mismatch.</p>"
                            "<script>window.close()</script></body></html>")

    employee_id = session["employee_id"]
    code_verifier = session["code_verifier"]
    redirect_uri = session["redirect_uri"]

    # Exchange authorization code for tokens (try form-urlencoded, then JSON)
    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": ANTHROPIC_OAUTH_CLIENT_ID,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                ANTHROPIC_TOKEN_URL, data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15.0,
            )
            if resp.status_code != 200:
                resp = await client.post(ANTHROPIC_TOKEN_URL, json=token_data, timeout=15.0)
            if resp.status_code != 200:
                return HTMLResponse(f"<html><body><h2>Token exchange failed</h2>"
                                    f"<p>{resp.status_code}: {resp.text}</p>"
                                    "<script>window.close()</script></body></html>")
            tokens = resp.json()
    except Exception as e:
        return HTMLResponse(f"<html><body><h2>Token exchange error</h2><p>{e}</p>"
                            "<script>window.close()</script></body></html>")

    access_token = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")
    api_key = access_token

    # Company-level OAuth: save to .env instead of employee profile
    if employee_id == "__company__":
        from onemancompany.core.config import update_env_var
        update_env_var("ANTHROPIC_API_KEY", api_key)
        update_env_var("ANTHROPIC_AUTH_METHOD", "oauth")
        if refresh_token:
            update_env_var("ANTHROPIC_REFRESH_TOKEN", refresh_token)

        await event_bus.publish(
            CompanyEvent(
                type=EventType.AGENT_DONE,
                payload={"role": "CEO", "summary": "Company Anthropic OAuth login successful."},
                agent="CEO",
            )
        )

        return HTMLResponse(
            "<html><body style='font-family:monospace;text-align:center;padding:40px;'>"
            "<h2>Login Successful</h2>"
            "<p>Company Anthropic API is now authenticated.</p>"
            "<p>This window will close automatically...</p>"
            "<script>window.opener && window.opener.postMessage('oauth_done','*'); "
            "setTimeout(()=>window.close(), 1500);</script>"
            "</body></html>"
        )

    # Store tokens in employee config
    cfg = employee_configs.get(employee_id)
    if cfg:
        cfg.api_key = api_key
        cfg.oauth_refresh_token = refresh_token

        # Persist to disk via store
        await _store.save_employee(employee_id, {
            "api_key": api_key,
            "oauth_refresh_token": refresh_token,
        })

    _oauth_emp2 = _load_emp(employee_id)
    emp_name = _oauth_emp2.get("name", employee_id) if _oauth_emp2 else employee_id

    await event_bus.publish(
        CompanyEvent(
            type=EventType.AGENT_DONE,
            payload={"role": "CEO", "summary": f"{emp_name} OAuth login successful."},
            agent="CEO",
        )
    )

    # OAuth employee is now ready — notify COO if there's a pending project
    coo_ctx = _pending_oauth_hire.pop(employee_id, None)
    if coo_ctx:
        logger.info(f"[oauth-done] {emp_name} ready — notifying COO for project {coo_ctx.get('project_id', '?')}")
        _notify_coo_hire_ready(employee_id, coo_ctx)
    else:
        logger.info(f"[oauth-done] {emp_name} — no pending COO context")

    return HTMLResponse(
        "<html><body style='font-family:monospace;text-align:center;padding:40px;'>"
        f"<h2>Login Successful</h2>"
        f"<p>{emp_name} is now authenticated.</p>"
        "<p>This window will close automatically...</p>"
        "<script>window.opener && window.opener.postMessage('oauth_done','*'); "
        "setTimeout(()=>window.close(), 1500);</script>"
        "</body></html>"
    )


@router.post("/api/employee/{employee_id}/oauth/refresh")
async def oauth_refresh(employee_id: str) -> dict:
    """Refresh an expired OAuth access token using the stored refresh token."""
    import httpx
    import yaml

    from onemancompany.core.config import EMPLOYEES_DIR, employee_configs

    cfg = employee_configs.get(employee_id)
    if not cfg or not cfg.oauth_refresh_token:
        return {"error": "No refresh token available"}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                ANTHROPIC_TOKEN_URL,
                json={
                    "grant_type": "refresh_token",
                    "refresh_token": cfg.oauth_refresh_token,
                    "client_id": ANTHROPIC_OAUTH_CLIENT_ID,
                },
                timeout=15.0,
            )
            if resp.status_code != 200:
                return {"error": f"Refresh failed: {resp.status_code}"}
            tokens = resp.json()
    except Exception as e:
        return {"error": f"Refresh error: {e}"}

    cfg.api_key = tokens.get("access_token", cfg.api_key)
    cfg.oauth_refresh_token = tokens.get("refresh_token", cfg.oauth_refresh_token)

    # Persist to disk via store
    await _store.save_employee(employee_id, {
        "api_key": cfg.api_key,
        "oauth_refresh_token": cfg.oauth_refresh_token,
    })

    return {"status": "refreshed"}


# ===== Self-Hosted Session Endpoints =====

@router.get("/api/employee/{employee_id}/sessions")
async def get_employee_sessions(employee_id: str) -> dict:
    """List all Claude Code sessions for a self-hosted employee."""
    from onemancompany.core.config import employee_configs
    from onemancompany.core.claude_session import list_sessions

    cfg = employee_configs.get(employee_id)
    if not cfg or cfg.hosting != HostingMode.SELF:
        return {"error": "Employee is not self-hosted"}

    return {"employee_id": employee_id, "sessions": list_sessions(employee_id)}


@router.delete("/api/employee/{employee_id}/sessions/{project_id:path}")
async def delete_employee_session(employee_id: str, project_id: str) -> dict:
    """Clean up a session record for a completed project."""
    from onemancompany.core.config import employee_configs
    from onemancompany.core.claude_session import cleanup_session

    cfg = employee_configs.get(employee_id)
    if not cfg or cfg.hosting != HostingMode.SELF:
        return {"error": "Employee is not self-hosted"}

    cleanup_session(employee_id, project_id)
    return {"status": "ok", "employee_id": employee_id, "project_id": project_id}


# ===== Company Culture =====

@router.get("/api/company-culture")
async def get_company_culture() -> dict:
    """Get all company culture items."""
    return {"items": _store.load_culture()}


@router.post("/api/company-culture")
async def add_culture_item(body: dict) -> dict:
    """CEO adds a new item to the company culture. Applies to all employees."""
    from datetime import datetime

    content = body.get("content", "").strip()
    if not content:
        return {"error": "Missing content"}

    item = {
        "content": content,
        "created_at": datetime.now().isoformat(),
    }
    items = _store.load_culture()
    items.append(item)
    await _store.save_culture(items)

    await event_bus.publish(
        CompanyEvent(
            type=EventType.COMPANY_CULTURE_UPDATED,
            payload={"item": item, "total": len(items)},
            agent="CEO",
        )
    )
    return {"status": "added", "item": item, "total": len(items)}


@router.delete("/api/company-culture/{index}")
async def remove_culture_item(index: int) -> dict:
    """CEO removes a company culture item by index."""
    items = _store.load_culture()

    if index < 0 or index >= len(items):
        return {"error": "Invalid index"}

    removed = items.pop(index)
    await _store.save_culture(items)

    await event_bus.publish(
        CompanyEvent(
            type=EventType.COMPANY_CULTURE_UPDATED,
            payload={"removed": removed, "total": len(items)},
            agent="CEO",
        )
    )
    return {"status": "removed", "removed": removed}


# ===== Company Direction =====

@router.get("/api/company/direction")
async def get_company_direction() -> dict:
    """Get the current company direction/strategy."""
    return {"direction": _store.load_direction()}


@router.put("/api/company/direction")
async def update_company_direction(body: dict) -> dict:
    """CEO updates the company direction/strategy."""
    direction = body.get("direction", "")
    await _store.save_direction(direction)

    await event_bus.publish(
        CompanyEvent(
            type=EventType.COMPANY_DIRECTION_UPDATED,
            payload={"direction": direction},
            agent="CEO",
        )
    )
    return {"status": "ok", "direction": direction}


# ===== File Upload (CEO multimodal) =====

@router.post("/api/upload")
async def upload_file(file: UploadFile, project_id: str = "") -> dict:
    """Save uploaded file to project directory (or global uploads if no project)."""
    from onemancompany.core.project_archive import get_project_dir

    content = await file.read()
    if len(content) > MAX_UPLOAD_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large (max {MAX_UPLOAD_FILE_SIZE // 1024 // 1024}MB)")
    if project_id:
        pdir = Path(get_project_dir(project_id))
        upload_dir = pdir / "attachments"
    else:
        from datetime import datetime
        upload_dir = COMPANY_DIR / "uploads" / datetime.now().strftime("%Y%m%d")
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_filename(file.filename)
    dest = _save_file_deduped(upload_dir, safe_name, content)
    logger.debug("Uploaded file {} to {}", safe_name, dest)
    return {
        "path": str(dest),
        "filename": safe_name,
        "size": len(content),
        "content_type": file.content_type or "",
    }


# ===== File Editor (CEO Approval) =====

@router.get("/api/file-edits")
async def get_pending_edits() -> dict:
    """List all pending file edit requests."""
    from onemancompany.core.file_editor import list_pending_edits
    return {"edits": list_pending_edits()}


@router.post("/api/file-edits/{edit_id}/approve")
async def approve_file_edit(edit_id: str) -> dict:
    """CEO approves a file edit. Backs up original, writes new content."""
    from onemancompany.core.file_editor import execute_edit

    result = execute_edit(edit_id)
    if result["status"] == "error":
        return result

    await event_bus.publish(
        CompanyEvent(
            type=EventType.FILE_EDIT_APPLIED,
            payload={
                "edit_id": edit_id,
                "rel_path": result["rel_path"],
                "backup_path": result.get("backup_path"),
            },
            agent="CEO",
        )
    )
    return result


@router.post("/api/file-edits/{edit_id}/reject")
async def reject_file_edit(edit_id: str) -> dict:
    """CEO rejects a file edit."""
    from onemancompany.core.file_editor import reject_edit

    result = reject_edit(edit_id)
    if result["status"] == "error":
        return result

    await event_bus.publish(
        CompanyEvent(
            type=EventType.FILE_EDIT_REJECTED,
            payload={"edit_id": edit_id, "rel_path": result["rel_path"]},
            agent="CEO",
        )
    )
    return result




# ===== Project Archive =====

@router.get("/api/dashboard/costs")
async def get_dashboard_costs() -> dict:
    from onemancompany.core.project_archive import get_cost_summary
    summary = get_cost_summary()
    # Add overhead costs from non-project LLM calls
    from onemancompany.core.state import company_state as _cs
    oh = _cs.overhead_costs
    summary["overhead"] = {
        "total_cost_usd": round(oh.total_cost_usd, 4),
        "total_input_tokens": oh.total_input_tokens,
        "total_output_tokens": oh.total_output_tokens,
        "by_category": {
            cat: {
                "cost_usd": round(v.get("cost_usd", 0.0), 4),
                "input_tokens": v.get("input_tokens", 0),
                "output_tokens": v.get("output_tokens", 0),
            }
            for cat, v in oh.by_category.items()
        },
    }
    project_total = summary.get("total", {}).get("cost_usd", 0.0)
    overhead_total = oh.total_cost_usd
    summary["grand_total_usd"] = round(project_total + overhead_total, 4)
    return summary


@router.get("/api/projects")
async def get_projects() -> dict:
    """List all projects (v1 + v2 summary view for the project wall)."""
    from onemancompany.core.project_archive import list_projects
    return {"projects": list_projects()}


@router.post("/api/projects")
async def create_project_endpoint(body: dict) -> dict:
    """Create a new named project."""
    from onemancompany.core.project_archive import create_named_project

    name = body.get("name", "").strip()
    if not name:
        return {"error": "Missing project name"}
    project_id = create_named_project(name)
    return {"project_id": project_id, "name": name}


@router.get("/api/projects/named")
async def list_named_projects_endpoint() -> dict:
    """List all projects (v1 + v2)."""
    from onemancompany.core.project_archive import list_projects
    return {"projects": list_projects()}


@router.get("/api/projects/named/{project_id}")
async def get_named_project_detail(project_id: str) -> dict:
    """Get a named project's details with all its iterations."""
    from pathlib import Path

    from onemancompany.core.project_archive import list_project_files, load_iteration, load_named_project
    proj = load_named_project(project_id)
    if not proj:
        return {"error": "Named project not found"}
    # Load iteration summaries and aggregate cost
    iterations = []
    total_cost_usd = 0.0
    for iter_id in proj.get("iterations", []):
        iter_doc = load_iteration(project_id, iter_id)
        if iter_doc:
            iter_cost = iter_doc.get("cost", {}).get("actual_cost_usd", 0.0)
            total_cost_usd += iter_cost
            # Use qualified iteration ID for consistent file listing
            qualified_iter = f"{project_id}/{iter_id}"
            iter_files = list_project_files(qualified_iter)
            iterations.append({
                "iteration_id": iter_doc.get("iteration_id", iter_id),
                "task": iter_doc.get("task", ""),
                "status": iter_doc.get("status", ""),
                "created_at": iter_doc.get("created_at", ""),
                "completed_at": iter_doc.get("completed_at"),
                "current_owner": iter_doc.get("current_owner", ""),
                "cost_usd": round(iter_cost, 4),
                "project_dir": iter_doc.get("project_dir", ""),
                "files": iter_files,
            })
    proj["iteration_details"] = iterations
    proj["total_cost_usd"] = round(total_cost_usd, 4)
    return proj


@router.post("/api/projects/{project_id}/archive")
async def archive_project_endpoint(project_id: str) -> dict:
    """Archive a named project — cancels all running tasks first."""
    from onemancompany.core.project_archive import archive_project, load_named_project
    proj = load_named_project(project_id)
    if not proj:
        return {"error": "Named project not found"}

    # Cancel all running/pending tasks for all iterations of this project
    from onemancompany.core.agent_loop import employee_manager
    iterations = proj.get("iterations", [])
    total_cancelled = 0
    for iter_id in iterations:
        full_pid = f"{project_id}/{iter_id}"
        total_cancelled += employee_manager.abort_project(full_pid)

    archive_project(project_id)
    logger.info("[archive] Archived project {} — cancelled {} task(s)", project_id, total_cancelled)
    await event_bus.publish(CompanyEvent(type=EventType.STATE_SNAPSHOT, payload={}, agent=SYSTEM_AGENT))
    return {"status": "archived", "project_id": project_id, "tasks_cancelled": total_cancelled}


@router.patch("/api/projects/{project_id}/name")
async def rename_project(project_id: str, body: dict) -> dict:
    """Rename a project (update display name)."""
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    from onemancompany.core.config import PROJECTS_DIR as _PROJ_DIR
    from onemancompany.core.project_archive import update_project_name

    candidate = _PROJ_DIR / project_id / "project.yaml"
    if not candidate.exists():
        raise HTTPException(404, "Project not found")
    update_project_name(project_id, name)
    return {"status": "ok", "name": name}


@router.post("/api/projects/continue")
async def continue_iteration(body: dict) -> dict:
    """Continue an existing iteration without creating a new one.

    Pushes a continuation task to the responsible officer (COO) with
    the original task, acceptance criteria, and last feedback.
    """
    from pathlib import Path
    from onemancompany.core.agent_loop import get_agent_loop
    from onemancompany.core.project_archive import (
        _resolve_and_load,
        _save_resolved,
        append_action,
    )

    project_id = body.get("project_id", "")
    iteration_id = body.get("iteration_id", "")
    if not iteration_id:
        return {"error": "Missing iteration_id"}

    # Load the iteration document
    version, doc, key = _resolve_and_load(iteration_id)
    if not doc:
        return {"error": "Iteration not found"}

    if doc.get("status") == ITER_STATUS_COMPLETED:
        return {"error": "Iteration already completed"}

    task = doc.get("task", "")
    criteria = doc.get("acceptance_criteria", [])
    acceptance_result = doc.get("acceptance_result")
    ea_review_result = doc.get("ea_review_result")
    officer_id = doc.get("responsible_officer") or COO_ID
    project_dir = doc.get("project_dir", "")

    # Build feedback summary from last round
    feedback_lines = []
    if acceptance_result:
        status = "Passed" if acceptance_result.get("accepted") else "Failed"
        feedback_lines.append(f"Last acceptance result: {status}")
        if acceptance_result.get("notes"):
            feedback_lines.append(f"Acceptance notes: {acceptance_result['notes']}")
    if ea_review_result:
        status = "Approved" if ea_review_result.get("approved") else "Rejected"
        feedback_lines.append(f"EA review: {status}")
        if ea_review_result.get("notes"):
            feedback_lines.append(f"EA notes: {ea_review_result['notes']}")
    feedback_text = "\n".join(feedback_lines) if feedback_lines else "(No feedback from previous round)"

    criteria_text = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(criteria)) if criteria else "(No acceptance criteria set)"

    # Reset acceptance/EA results for re-evaluation
    doc["acceptance_result"] = None
    doc["ea_review_result"] = None
    doc["status"] = "in_progress"
    doc["dispatches"] = []
    _save_resolved(version, key, doc)

    # Note: task tree handles dispatch tracking now

    # Build continuation task description — route to EA like initial task flow
    ctx_id = f"{project_id}/{iteration_id}" if project_id and not iteration_id.startswith(project_id) else iteration_id
    continuation_task = (
        f"CEO requests to continue the current iteration (no new iteration)\n\n"
        f"Original task: {task}\n\n"
        f"Acceptance criteria:\n{criteria_text}\n\n"
        f"Previous feedback:\n{feedback_text}\n\n"
        f"Please analyze the incomplete or improvable parts based on the above, then dispatch to the appropriate owner.\n\n"
        f"[Project ID: {ctx_id}] [Project workspace: {project_dir}]"
    )

    # Route to EA (same as initial task flow) to ensure full task tree activation
    loop = get_agent_loop(EA_ID)
    if not loop:
        return {"error": f"No agent loop for EA {EA_ID}"}

    # Add a new subtree to the existing tree (don't overwrite)
    try:
        from onemancompany.core.task_tree import get_tree, save_tree_async, TaskTree
        from onemancompany.core.vessel import _save_project_tree
        from onemancompany.core.agent_loop import employee_manager

        tree_path = Path(project_dir) / TASK_TREE_FILENAME
        if tree_path.exists():
            tree = get_tree(tree_path, project_id=ctx_id)
            root = tree.get_node(tree.root_id)

            # Add CEO "continue" node under root
            continue_node = tree.add_child(
                parent_id=tree.root_id,
                employee_id=CEO_ID,
                description=f"[Continue] {feedback_text}",
                acceptance_criteria=[],
            )
            continue_node.node_type = NodeType.CEO_FOLLOWUP
            continue_node.status = TaskPhase.ACCEPTED.value

            # Add new EA child under the continue node
            ea_child = tree.add_child(
                parent_id=continue_node.id,
                employee_id=EA_ID,
                description=continuation_task,
                acceptance_criteria=[],
            )

            # Keep CEO root in PROCESSING
            if root and root.node_type == NodeType.CEO_PROMPT:
                root.status = TaskPhase.PROCESSING.value

            save_tree_async(tree_path)
            employee_manager.schedule_node(EA_ID, ea_child.id, str(tree_path))
        else:
            # No tree yet — create fresh (shouldn't happen for continue)
            tree = TaskTree(project_id=ctx_id)
            ceo_root = tree.create_root(employee_id=CEO_ID, description=task)
            ceo_root.node_type = NodeType.CEO_PROMPT
            ceo_root.set_status(TaskPhase.PROCESSING)
            ea_child = tree.add_child(
                parent_id=ceo_root.id,
                employee_id=EA_ID,
                description=continuation_task,
                acceptance_criteria=[],
            )
            _save_project_tree(project_dir, tree)
            employee_manager.schedule_node(EA_ID, ea_child.id, str(tree_path))

        employee_manager._schedule_next(EA_ID)
    except Exception as e:
        logger.error("Failed to initialize continuation task tree: {}", e)

    # Log the action
    append_action(iteration_id, CEO_ID, "continue", f"CEO requested continuation of current iteration")

    await event_bus.publish(
        CompanyEvent(type=EventType.STATE_SNAPSHOT, payload={}, agent=SYSTEM_AGENT)
    )

    return {
        "status": "continued",
        "routed_to": EA_ID,
        "iteration_id": iteration_id,
    }


def _build_plugin_context(dispatches: list[dict], project_id: str, project_status: str = "") -> dict:
    """Build the context dict that plugin transformers consume."""
    employees: dict[str, dict] = {}
    for d in dispatches:
        emp_id = d.get("employee_id", "")
        if emp_id and emp_id not in employees:
            _ctx_emp = _load_emp(emp_id)
            if _ctx_emp:
                employees[emp_id] = {"name": _ctx_emp.get("name", ""), "nickname": _ctx_emp.get("nickname", "")}
    return {"employees": employees, "project_id": project_id, "project_status": project_status}


def _tree_nodes_to_dispatches(project_id: str) -> list[dict]:
    """Convert task tree nodes into dispatch-like dicts for plugin transforms."""
    from pathlib import Path
    from onemancompany.core.project_archive import get_project_dir
    from onemancompany.core.task_tree import get_tree

    project_dir = get_project_dir(project_id)
    if not project_dir:
        return []
    path = Path(project_dir) / TASK_TREE_FILENAME
    if not path.exists():
        return []
    tree = get_tree(path, project_id=project_id)
    nodes = tree.all_nodes()

    # Map TaskPhase statuses to kanban-compatible statuses
    status_map = {
        TaskPhase.PENDING.value: "pending",
        TaskPhase.PROCESSING.value: "in_progress",
        TaskPhase.HOLDING.value: "in_progress",
        TaskPhase.COMPLETED.value: "completed",
        TaskPhase.ACCEPTED.value: "completed",
        TaskPhase.FINISHED.value: "completed",
        TaskPhase.FAILED.value: "completed",
        TaskPhase.BLOCKED.value: "pending",
        TaskPhase.CANCELLED.value: "completed",
    }

    dispatches = []
    for node in nodes:
        # Skip system nodes (ceo_prompt, ceo_followup) — they aren't real tasks
        if node.node_type in (NodeType.CEO_PROMPT, NodeType.CEO_FOLLOWUP):
            continue
        dispatches.append({
            "dispatch_id": node.id,
            "employee_id": node.employee_id,
            "description": node.description,
            "status": status_map.get(node.status, "pending"),
            "phase": 1,  # flat for now
            "dispatched_at": node.created_at,
            "completed_at": node.completed_at or None,
            "task_type": node.node_type,
            "depends_on": node.depends_on,
            "estimated_duration_min": 0,
            "scheduled_start": None,
        })
    return dispatches


@router.get("/api/projects/{project_id}/board")
async def get_project_board(project_id: str) -> dict:
    """Get kanban board data — backward-compatible wrapper over plugin transformers."""
    from onemancompany.core.plugin_registry import plugin_registry
    from onemancompany.core.project_archive import _resolve_and_load

    version, doc, key = _resolve_and_load(project_id)
    if not doc:
        raise HTTPException(404, "Project not found")

    dispatches = _tree_nodes_to_dispatches(project_id) or doc.get("dispatches", [])
    ctx = _build_plugin_context(dispatches, project_id, doc.get("status", ""))

    # Delegate to plugin transformers
    kanban_data = plugin_registry.transform("kanban", dispatches, ctx)
    timeline_data = plugin_registry.transform("timeline", dispatches, ctx)

    return {
        "columns": kanban_data.get("columns", {}),
        "timeline": timeline_data.get("timeline", []),
        "phases": kanban_data.get("phases", []),
    }


# ===== Task Queue Endpoint =====


def _tree_summary(project_id: str) -> dict | None:
    """Return a compact summary of a project's task tree."""
    from pathlib import Path

    from onemancompany.core.project_archive import get_project_dir
    from onemancompany.core.task_tree import get_tree

    project_dir = get_project_dir(project_id)
    if not project_dir:
        return None
    path = Path(project_dir) / TASK_TREE_FILENAME
    if not path.exists():
        return None
    tree = get_tree(path, project_id=project_id)
    nodes = tree.all_nodes()
    if not nodes:
        return None

    total = len(nodes)
    by_status: dict[str, int] = {}
    for n in nodes:
        by_status[n.status] = by_status.get(n.status, 0) + 1

    terminal = sum(by_status.get(s, 0) for s in (TaskPhase.ACCEPTED.value, TaskPhase.FAILED.value, TaskPhase.CANCELLED.value))
    processing = by_status.get(TaskPhase.PROCESSING.value, 0)
    completed = by_status.get(TaskPhase.COMPLETED.value, 0)

    # Collect actively working nodes (non-terminal)
    active_nodes = []
    has_children = total > 1
    for n in nodes:
        # For multi-node trees, skip root (it's the coordinator)
        if has_children and n.id == tree.root_id:
            continue
        if n.status in (TaskPhase.PROCESSING, TaskPhase.COMPLETED, TaskPhase.PENDING):
            active_nodes.append({
                "id": n.id,
                "employee_id": n.employee_id,
                "description": n.description_preview[:80],
                "status": n.status,
            })

    # Root node result for completed tasks
    root_node = tree.get_node(tree.root_id)
    if root_node:
        root_node.load_content(path.parent)
    root_result = root_node.result if root_node else ""

    return {
        "root_id": tree.root_id,
        "total": total,
        "by_status": by_status,
        "terminal": terminal,
        "processing": processing,
        "completed": completed,
        "active_nodes": active_nodes,
        "root_result": root_result,
    }


@router.get("/api/task-queue")
async def get_task_queue() -> list[dict]:
    """Return tasks from persistent project files, enriched with tree summaries.

    Source of truth is the filesystem (project.yaml), not in-memory state.
    This survives restarts without any snapshot/restore logic.
    """
    from onemancompany.core.project_archive import list_projects

    result = []
    for p in list_projects():
        # Skip v2 named projects (shown in PROJECTS panel)
        if p.get("is_named"):
            continue
        if p.get("status") == "archived":
            continue
        tree = _tree_summary(p["project_id"])
        # project.yaml is the single source of truth for status
        status = _normalize_project_status(p.get("status", ""))

        entry = {
            "project_id": p["project_id"],
            "task": p.get("task", ""),
            "routed_to": p.get("routed_to", ""),
            "current_owner": p.get("current_owner", ""),
            "status": status,
            "created_at": p.get("created_at", ""),
            "completed_at": p.get("completed_at", ""),
            "result": "",
            "tree": tree,
        }
        # Get result from tree root if available
        if tree and tree.get("root_result"):
            entry["result"] = tree["root_result"][:200]
        result.append(entry)
    return result


def _normalize_project_status(status: str) -> str:
    """Map project.yaml status values to task queue display status."""
    mapping = {
        "in_progress": "processing",
        "pending": "pending",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
    }
    return mapping.get(status, status)



# ===== Task Tree Endpoint =====


def _load_project_tree_for_api(project_id: str):
    """Load TaskTree for a project, trying known project directories."""
    from pathlib import Path

    from onemancompany.core.task_tree import get_tree
    from onemancompany.core.project_archive import get_project_dir
    from onemancompany.core.project_archive import (
        _is_iteration,
        _find_project_for_iteration,
        _split_qualified_iter,
        PROJECTS_DIR,
    )

    # If project_id is an iteration ID, look in the iteration directory first
    if _is_iteration(project_id):
        slug = _find_project_for_iteration(project_id)
        if slug:
            _, bare_id = _split_qualified_iter(project_id)
            iter_tree = PROJECTS_DIR / slug / "iterations" / bare_id / TASK_TREE_FILENAME
            if iter_tree.exists():
                return get_tree(iter_tree, project_id=project_id)

    project_dir = get_project_dir(project_id)
    if not project_dir:
        return None
    path = Path(project_dir) / TASK_TREE_FILENAME
    if not path.exists():
        return None
    return get_tree(path, project_id=project_id)


@router.get("/api/projects/{project_id}/{iteration_id}/tree")
async def get_iteration_tree(project_id: str, iteration_id: str) -> dict:
    """Get the task tree for a qualified iteration (slug/iter_NNN/tree)."""
    import re
    if not re.match(r"^iter_\d+$", iteration_id):
        raise HTTPException(404, "Not found")
    return await get_project_tree(f"{project_id}/{iteration_id}")


@router.get("/api/projects/{project_id}/tree")
async def get_project_tree(project_id: str) -> dict:
    """Get the task tree for a project."""
    tree = _load_project_tree_for_api(project_id)
    if tree is None:
        raise HTTPException(status_code=404, detail="Task tree not found")
    tree.load_all_content()

    # Build employee info lookup
    employee_info: dict[str, dict] = {}
    for node in tree.all_nodes():
        eid = node.employee_id
        if eid and eid not in employee_info:
            if eid == CEO_ID:
                employee_info[eid] = {
                    "name": "CEO",
                    "nickname": "CEO",
                    "role": "Chief Executive Officer",
                    "avatar_url": f"/api/employees/{CEO_ID}/avatar",
                }
            else:
                _tree_emp = _load_emp(eid)
                if _tree_emp:
                    employee_info[eid] = {
                        "name": _tree_emp.get("name", ""),
                        "nickname": _tree_emp.get("nickname", ""),
                        "role": _tree_emp.get("role", ""),
                        "avatar_url": f"/api/employees/{eid}/avatar",
                    }

    nodes = []
    for n in tree.all_nodes():
        d = n.to_dict()
        d["description"] = n.description or n.description_preview or ""
        d["result"] = n.result or ""
        # Compute dependency_status
        if n.depends_on:
            if n.status == TaskPhase.BLOCKED:
                d["dependency_status"] = "blocked"
            elif tree.all_deps_resolved(n.id):
                d["dependency_status"] = "resolved"
            else:
                d["dependency_status"] = "waiting"
        else:
            d["dependency_status"] = "resolved"
        d["employee_info"] = employee_info.get(n.employee_id, {})
        nodes.append(d)

    return {
        "project_id": tree.project_id,
        "root_id": tree.root_id,
        "nodes": nodes,
    }


@router.get("/api/task-tree/{node_id}/logs")
async def get_node_execution_logs(node_id: str):
    """Load execution logs for a task node from disk."""
    import json as _json
    from onemancompany.core.config import PROJECTS_DIR, TASK_TREE_FILENAME
    from onemancompany.core.task_tree import get_tree

    # Find the node across all project trees to get project_dir
    project_dir = ""
    if PROJECTS_DIR.exists():
        for tree_path in PROJECTS_DIR.rglob(TASK_TREE_FILENAME):
            tree = get_tree(tree_path)
            node = tree.get_node(node_id)
            if node:
                project_dir = node.project_dir or str(tree_path.parent)
                break

    if not project_dir:
        return []

    log_path = Path(project_dir) / "nodes" / node_id / "execution.log"
    if not log_path.exists():
        return []

    logs = []
    for line in log_path.read_text(encoding="utf-8").strip().split("\n"):
        if line:
            try:
                logs.append(_json.loads(line))
            except _json.JSONDecodeError:
                logger.debug("Skipping malformed log line in node {}", node_id)
                continue
    return logs


@router.post("/api/employees/{employee_id}/avatar")
async def upload_avatar(employee_id: str, request: Request) -> dict:
    """Upload an avatar image for an employee."""
    from onemancompany.core.config import EMPLOYEES_DIR
    body = await request.body()
    if not body or len(body) > 512 * 1024:
        raise HTTPException(status_code=400, detail="Invalid or oversized image (max 512KB)")
    avatar_path = EMPLOYEES_DIR / employee_id / "avatar.png"
    avatar_path.parent.mkdir(parents=True, exist_ok=True)
    avatar_path.write_bytes(body)
    return {"status": "ok", "url": f"/api/employees/{employee_id}/avatar"}


@router.get("/api/employees/{employee_id}/avatar")
async def get_avatar(employee_id: str):
    """Serve an employee's avatar image, falling back to default piggy."""
    from onemancompany.core.config import EMPLOYEES_DIR, HR_DIR
    avatar_path = EMPLOYEES_DIR / employee_id / "avatar.png"
    if not avatar_path.exists():
        avatar_path = EMPLOYEES_DIR / employee_id / "avatar.jpg"
    if not avatar_path.exists():
        avatar_path = EMPLOYEES_DIR / employee_id / "avatar.jpeg"
    if avatar_path.exists():
        media = "image/png" if avatar_path.suffix == ".png" else "image/jpeg"
        return FileResponse(avatar_path, media_type=media)
    # Fallback: look for a named avatar matching the employee's sprite field
    avatars_dir = HR_DIR / "avatars"
    if avatars_dir.exists():
        from onemancompany.core.store import load_employee
        emp_data = load_employee(employee_id)
        sprite = emp_data.get(PF_SPRITE, "") if emp_data else ""
        if sprite:
            for ext in (".png", ".jpg", ".jpeg"):
                named = avatars_dir / f"{sprite}{ext}"
                if named.exists():
                    media = "image/png" if ext == ".png" else "image/jpeg"
                    return FileResponse(named, media_type=media)
        # Random fallback
        avatars = sorted(p for p in avatars_dir.iterdir() if p.suffix in (".png", ".jpg", ".jpeg"))
        if avatars:
            idx = int(employee_id) % len(avatars) if employee_id.isdigit() else hash(employee_id) % len(avatars)
            pick = avatars[idx]
            media = "image/png" if pick.suffix == ".png" else "image/jpeg"
            return FileResponse(pick, media_type=media)
    # Legacy fallback
    default = HR_DIR / "piggy.jpg"
    if default.exists():
        return FileResponse(default, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="No avatar")


@router.get("/api/employees/{employee_id}/projects")
async def get_employee_projects(employee_id: str) -> list[dict]:
    """Get list of projects an employee participated in."""
    return _scan_employee_projects(employee_id)


@router.get("/api/employees/{employee_id}/projects/{project_id}/retrospective")
async def get_employee_project_retrospective(employee_id: str, project_id: str) -> dict:
    """Get an employee's retrospective summary for a specific project.

    Extracts self-evaluation and feedback from the project timeline.
    """
    from onemancompany.core.config import PROJECTS_DIR
    import yaml

    result: dict = {
        "employee_id": employee_id,
        "project_id": project_id,
        "self_evaluation": "",
        "feedback": "",
        "senior_reviews": [],
        "hr_improvements": [],
    }

    # Scan project.yaml and iteration yamls for timeline entries
    pdir = PROJECTS_DIR / project_id
    if not pdir.exists():
        return result

    # Collect timeline entries from project.yaml and all iteration yamls
    timeline_entries: list[dict] = []
    for yaml_file in pdir.glob("*.yaml"):
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding=ENCODING_UTF8)) or {}
        except Exception as exc:
            logger.debug("Failed to parse {}: {}", yaml_file, exc)
            continue
        timeline_entries.extend(data.get("timeline", []))

    # Extract this employee's retrospective content
    for entry in timeline_entries:
        eid = entry.get(TL_FIELD_EMPLOYEE_ID, "")
        action = entry.get(TL_FIELD_ACTION, "")
        detail = entry.get(TL_FIELD_DETAIL, "")
        if eid == employee_id and action == TL_ACTION_SELF_EVAL:
            result["self_evaluation"] = detail
        elif eid == employee_id and action == TL_ACTION_EMPLOYEE_FEEDBACK:
            result["feedback"] = detail

    # Extract senior reviews mentioning this employee
    emp_data = _load_emp(employee_id)
    emp_name = emp_data.get(PF_NAME, "") if emp_data else ""
    emp_nickname = emp_data.get(PF_NICKNAME, "") if emp_data else ""
    for entry in timeline_entries:
        action = entry.get(TL_FIELD_ACTION, "")
        detail = entry.get(TL_FIELD_DETAIL, "")
        if action == TL_ACTION_SENIOR_REVIEW and detail:
            # Check if the review mentions this employee by name or nickname
            if emp_name and emp_name in detail or emp_nickname and emp_nickname in detail:
                reviewer_id = entry.get(TL_FIELD_EMPLOYEE_ID, "")
                reviewer_data = _load_emp(reviewer_id)
                reviewer_name = reviewer_data.get(PF_NAME, reviewer_id) if reviewer_data else reviewer_id
                result["senior_reviews"].append({
                    "reviewer": reviewer_name,
                    "review": detail,
                })

    # Extract HR improvement suggestions for this employee
    for entry in timeline_entries:
        action = entry.get(TL_FIELD_ACTION, "")
        detail = entry.get(TL_FIELD_DETAIL, "")
        if action == TL_ACTION_IMPROVEMENT and detail:
            if emp_name and emp_name in detail or emp_nickname and emp_nickname in detail:
                result["hr_improvements"].append(detail)

    return result


# ===== Plugin System Endpoints =====

@router.get("/api/plugins")
async def list_plugins(view_type: str | None = None) -> list[dict]:
    """List registered plugins, optionally filtered by view_type."""
    from onemancompany.core.plugin_registry import plugin_registry

    manifests = plugin_registry.list_plugins(view_type)
    return [
        {
            "id": m.id,
            "name": m.name,
            "version": m.version,
            "description": m.description,
            "icon": m.icon,
            "order": m.order,
            "view_type": m.view_type,
            "render_function": m.render_function,
        }
        for m in manifests
    ]


@router.get("/api/plugins/{plugin_id}/script")
async def get_plugin_script(plugin_id: str):
    """Serve a plugin's JavaScript file."""
    from onemancompany.core.plugin_registry import plugin_registry

    plugin = plugin_registry.get(plugin_id)
    if not plugin:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    script_path = plugin.plugin_dir / plugin.manifest.frontend_script
    if not script_path.exists():
        raise HTTPException(404, f"Script not found for plugin '{plugin_id}'")
    return FileResponse(str(script_path), media_type="application/javascript")


@router.get("/api/plugins/{plugin_id}/style")
async def get_plugin_style(plugin_id: str):
    """Serve a plugin's CSS file."""
    from onemancompany.core.plugin_registry import plugin_registry

    plugin = plugin_registry.get(plugin_id)
    if not plugin:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")
    if not plugin.manifest.frontend_style:
        raise HTTPException(404, f"No style for plugin '{plugin_id}'")
    style_path = plugin.plugin_dir / plugin.manifest.frontend_style
    if not style_path.exists():
        raise HTTPException(404, f"Style not found for plugin '{plugin_id}'")
    return FileResponse(str(style_path), media_type="text/css")


@router.get("/api/projects/{project_id}/plugin/{plugin_id}")
async def get_project_plugin_data(project_id: str, plugin_id: str) -> dict:
    """Execute a plugin's transformer on a project's dispatches."""
    from onemancompany.core.plugin_registry import plugin_registry
    from onemancompany.core.project_archive import _resolve_and_load

    plugin = plugin_registry.get(plugin_id)
    if not plugin:
        raise HTTPException(404, f"Plugin '{plugin_id}' not found")

    version, doc, key = _resolve_and_load(project_id)
    if not doc:
        raise HTTPException(404, "Project not found")

    dispatches = _tree_nodes_to_dispatches(project_id) or doc.get("dispatches", [])
    ctx = _build_plugin_context(dispatches, project_id, doc.get("status", ""))

    return plugin_registry.transform(plugin_id, dispatches, ctx)


@router.get("/api/projects/{project_id}/{iteration_id}")
async def get_iteration_detail(project_id: str, iteration_id: str) -> dict:
    """Get iteration detail via qualified path: /api/projects/{slug}/{iter_id}."""
    import re
    if not re.match(r"^iter_\d+$", iteration_id):
        raise HTTPException(404, "Not found")
    qualified = f"{project_id}/{iteration_id}"
    return await get_project_detail(qualified)


@router.get("/api/projects/{project_id}")
async def get_project_detail(project_id: str) -> dict:
    """Get full project detail including timeline and workspace files."""
    from onemancompany.core.project_archive import get_project_dir, list_project_files, load_project
    doc = load_project(project_id)
    if not doc:
        return {"error": "Project not found"}
    doc["project_dir"] = get_project_dir(project_id)
    doc["files"] = list_project_files(project_id)
    return doc


@router.get("/api/projects/{project_id}/{iteration_id}/files/{file_path:path}")
async def get_iteration_file(project_id: str, iteration_id: str, file_path: str):
    """Read a file from an iteration workspace (slug/iter_NNN/files/...)."""
    import re
    if not re.match(r"^iter_\d+$", iteration_id):
        raise HTTPException(404, "Not found")
    return await get_project_file(f"{project_id}/{iteration_id}", file_path)


@router.get("/api/projects/{project_id}/files/{file_path:path}")
async def get_project_file(project_id: str, file_path: str):
    """Read a file from a project workspace."""
    from pathlib import Path

    from fastapi.responses import Response

    from onemancompany.core.project_archive import get_project_dir

    workspace = Path(get_project_dir(project_id))
    target = (workspace / file_path).resolve()
    # Security: ensure path stays within workspace
    if not str(target).startswith(str(workspace.resolve())):
        return Response(content="Forbidden", status_code=403)
    if not target.is_file():
        return Response(content="Not found", status_code=404)

    # Determine content type
    suffix = target.suffix.lower()
    text_types = {".txt", ".md", ".py", ".js", ".html", ".css", ".yaml", ".yml",
                  ".json", ".csv", ".tsv", ".xml", ".sh", ".toml", ".cfg", ".ini",
                  ".log", ".rst", ".tex", ".sql", ".r", ".rb", ".go", ".java",
                  ".c", ".cpp", ".h", ".hpp", ".rs", ".swift", ".kt", ".ts", ".tsx", ".jsx"}
    if suffix in text_types:
        content = target.read_text(encoding=ENCODING_UTF8, errors="replace")
        media = "text/plain; charset=utf-8"
        if suffix == ".html":
            media = "text/html; charset=utf-8"
        elif suffix == ".json":
            media = "application/json; charset=utf-8"
        elif suffix == ".md":
            media = "text/markdown; charset=utf-8"
        return Response(content=content, media_type=media)
    else:
        # Binary files: serve as download
        content = target.read_bytes()
        media = "application/octet-stream"
        if suffix == ".png":
            media = "image/png"
        elif suffix in (".jpg", ".jpeg"):
            media = "image/jpeg"
        elif suffix == ".gif":
            media = "image/gif"
        elif suffix == ".svg":
            media = "image/svg+xml"
        elif suffix == ".pdf":
            media = "application/pdf"
        return Response(content=content, media_type=media)


# ===== Employee Workspace =====

@router.get("/api/employee/{employee_id}/workspace")
async def list_employee_workspace(employee_id: str, subdir: str = "") -> dict:
    """List files in an employee's workspace directory."""
    from onemancompany.core.config import get_workspace_dir

    ws = get_workspace_dir(employee_id)
    target = (ws / subdir).resolve() if subdir else ws.resolve()
    if not str(target).startswith(str(ws.resolve())):
        return {"error": "Forbidden", "files": []}
    if not target.is_dir():
        return {"files": []}

    files = []
    for item in sorted(target.iterdir()):
        rel = str(item.relative_to(ws))
        entry = {"name": item.name, "path": rel, "is_dir": item.is_dir()}
        if item.is_file():
            entry["size"] = item.stat().st_size
        files.append(entry)
    return {"employee_id": employee_id, "files": files}


@router.get("/api/employee/{employee_id}/workspace/files/{file_path:path}")
async def get_employee_workspace_file(employee_id: str, file_path: str):
    """Read a file from an employee's workspace."""
    from pathlib import Path

    from fastapi.responses import Response

    from onemancompany.core.config import get_workspace_dir

    ws = get_workspace_dir(employee_id)
    target = (ws / file_path).resolve()
    if not str(target).startswith(str(ws.resolve())):
        return Response(content="Forbidden", status_code=403)
    if not target.is_file():
        return Response(content="Not found", status_code=404)

    suffix = target.suffix.lower()
    text_types = {".txt", ".md", ".py", ".js", ".html", ".css", ".yaml", ".yml",
                  ".json", ".csv", ".tsv", ".xml", ".sh", ".toml", ".cfg", ".ini",
                  ".log", ".rst", ".tex", ".sql", ".r", ".rb", ".go", ".java",
                  ".c", ".cpp", ".h", ".hpp", ".rs", ".swift", ".kt", ".ts", ".tsx", ".jsx"}
    if suffix in text_types:
        content = target.read_text(encoding=ENCODING_UTF8, errors="replace")
        return Response(content=content, media_type="text/plain; charset=utf-8")
    else:
        content = target.read_bytes()
        media = "application/octet-stream"
        if suffix == ".png": media = "image/png"
        elif suffix in (".jpg", ".jpeg"): media = "image/jpeg"
        elif suffix == ".gif": media = "image/gif"
        return Response(content=content, media_type=media)


@router.get("/api/employee/{employee_id}/workspace/download")
async def download_employee_workspace(employee_id: str):
    """Download the employee's workspace as a zip file."""
    import io
    import zipfile

    from fastapi.responses import StreamingResponse

    from onemancompany.core.config import get_workspace_dir

    ws = get_workspace_dir(employee_id)
    if not ws.is_dir():
        from fastapi.responses import Response
        return Response(content="Workspace not found", status_code=404)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # TODO: O(N) scan — consider node-to-project index if this becomes slow
        for fpath in ws.rglob("*"):
            if fpath.is_file():
                zf.write(fpath, fpath.relative_to(ws))
    buf.seek(0)

    _dl_emp = _load_emp(employee_id)
    name = _dl_emp.get("nickname", employee_id) if _dl_emp else employee_id
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}_workspace.zip"'},
    )


@router.get("/api/projects/{project_id}/{iteration_id}/download")
async def download_iteration_workspace(project_id: str, iteration_id: str):
    """Download an iteration workspace as a zip file (slug/iter_NNN/download)."""
    import re
    if not re.match(r"^iter_\d+$", iteration_id):
        raise HTTPException(404, "Not found")
    return await download_project_workspace(f"{project_id}/{iteration_id}")


@router.get("/api/projects/{project_id}/download")
async def download_project_workspace(project_id: str):
    """Download a project workspace as a zip file."""
    import io
    import zipfile

    from fastapi.responses import StreamingResponse

    from pathlib import Path

    from onemancompany.core.project_archive import get_project_dir

    pdir = Path(get_project_dir(project_id))
    if not pdir.is_dir():
        from fastapi.responses import Response
        return Response(content="Project workspace not found", status_code=404)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # TODO: O(N) scan — consider node-to-project index if this becomes slow
        for fpath in pdir.rglob("*"):
            if fpath.is_file():
                zf.write(fpath, fpath.relative_to(pdir))
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{project_id}_workspace.zip"'},
    )


# ===== Ex-Employees =====

@router.get("/api/ex-employees")
async def get_ex_employees() -> dict:
    """List all ex-employees."""
    ex_emps = _store.load_ex_employees()
    return {"ex_employees": list(ex_emps.values())}


@router.post("/api/ex-employees/{employee_id}/rehire")
async def rehire_ex_employee(employee_id: str) -> dict:
    """Re-hire an ex-employee: move folder back and restore to active state."""
    from onemancompany.core.config import move_ex_employee_back

    ex_emps = _store.load_ex_employees()
    if employee_id not in ex_emps:
        return {"error": "Ex-employee not found"}

    ex_data = ex_emps[employee_id]

    # Move folder back from ex-employees/ to employees/
    if not move_ex_employee_back(employee_id):
        return {"error": "Failed to move employee folder"}

    # Find next available desk position using department-based layout
    from onemancompany.core.layout import compute_layout, get_next_desk_for_department
    is_remote = ex_data.get("remote", False)
    dept = ex_data.get("department", "General")
    if is_remote:
        desk_pos = [-1, -1]
    else:
        desk_pos = list(get_next_desk_for_department(company_state, dept))

    # Persist via store — reset performance for rehire
    await _store.save_employee(employee_id, {
        "name": ex_data.get("name", ""),
        "nickname": ex_data.get("nickname", ""),
        "level": 1,
        "department": dept,
        "role": ex_data.get("role", ""),
        "skills": ex_data.get("skills", []),
        "current_quarter_tasks": 0,
        "performance_history": [],
        "desk_position": desk_pos,
        "sprite": ex_data.get("sprite", "employee_default"),
        "remote": is_remote,
    })
    await _store.save_employee_runtime(employee_id, status=STATUS_IDLE)

    # Recompute layout
    compute_layout(company_state)

    # Register in EmployeeManager for on-site employees
    if not is_remote:
        from onemancompany.core.agent_loop import get_agent_loop, register_and_start_agent, register_self_hosted
        if not get_agent_loop(employee_id):
            emp_profile = _store.load_employee(employee_id) or {}
            if emp_profile.get("hosting") == HostingMode.SELF:
                register_self_hosted(employee_id)
            else:
                from onemancompany.agents.base import EmployeeAgent
                await register_and_start_agent(employee_id, EmployeeAgent(employee_id))

    emp_name = ex_data.get("name", "")
    emp_nickname = ex_data.get("nickname", "")
    emp_role = ex_data.get("role", "")
    await _store.append_activity({
        "type": "employee_rehired",
        "name": emp_name,
        "nickname": emp_nickname,
        "role": emp_role,
    })
    rehired_data = _store.load_employee(employee_id)
    await event_bus.publish(
        CompanyEvent(
            type=EventType.EMPLOYEE_REHIRED,
            payload=rehired_data,
            agent="CEO",
        )
    )

    return {
        "status": "rehired",
        "employee_id": employee_id,
        "name": emp_name,
    }


# ===== Hiring Requests (COO → CEO → HR) =====

@router.get("/api/hiring-requests")
async def list_hiring_requests() -> list[dict]:
    """List all pending hiring requests from COO."""
    from onemancompany.agents.coo_agent import pending_hiring_requests
    return [
        {"request_id": rid, **req}
        for rid, req in pending_hiring_requests.items()
    ]


# COO hiring context queue — populated when COO calls request_hiring() (auto-approved),
# consumed when hire_candidate()/batch_hire fires. Stores COO's requested role,
# department, hire_id, and project info for role override and HOLDING resume.
_pending_coo_hire_queue: list[dict] = []

# Per-employee OAuth wait context — for OAuth employees, the hire completes
# only after login. This maps employee_id -> COO context so oauth_callback
# can notify COO.
_pending_oauth_hire: dict[str, dict] = {}

# Active onboarding state — tracks in-flight onboarding batches so the
# frontend can restore the progress modal after a page refresh.
# Structure: { batch_id: { "items": { candidate_id: {name, role, step, message} }, "total": N } }
_active_onboarding: dict[str, dict] = {}


def _track_onboarding_progress(batch_id: str, candidate_id: str, name: str, role: str, step: str, message: str, total: int) -> None:
    """Update in-memory onboarding tracker for state recovery on refresh."""
    if batch_id not in _active_onboarding:
        _active_onboarding[batch_id] = {"items": {}, "total": total}
    _active_onboarding[batch_id]["items"][candidate_id] = {
        "name": name, "role": role, "step": step, "message": message,
    }
    # Mark batch as done (but don't auto-remove — frontend dismisses explicitly)
    items = _active_onboarding[batch_id]["items"]
    if len(items) >= total and all(v["step"] in ("completed", "failed") for v in items.values()):
        _active_onboarding[batch_id]["done"] = True


def _notify_coo_hire_ready(employee_id: str, ctx: dict) -> None:
    """Resume COO's HOLDING task after a hired employee is fully ready.

    Matches by hire_id in the HOLDING node's metadata for exact correlation.
    Falls back to adhoc notification if no matching HOLDING task found.

    Args:
        employee_id: The newly hired employee's ID.
        ctx: COO hiring context dict with keys: hire_id, role, department,
             project_id, project_dir, reason.
    """
    project_id = ctx.get("project_id", "")
    project_dir = ctx.get("project_dir", "")
    hire_id = ctx.get("hire_id", "")

    _hire_emp = _load_emp(employee_id)
    emp_name = _hire_emp.get("name", employee_id) if _hire_emp else employee_id
    role = ctx.get("role", "Employee")

    # Try to resume COO's HOLDING task matched by hire_id
    from onemancompany.core.vessel import employee_manager, _parse_holding_metadata
    resumed = False
    for entry in employee_manager._schedule.get(COO_ID, []):
        from onemancompany.core.task_tree import get_tree
        tree = get_tree(entry.tree_path)
        node = tree.get_node(entry.node_id)
        if not node or node.status != TaskPhase.HOLDING.value:
            continue
        # Match by hire_id in HOLDING metadata
        holding_meta = _parse_holding_metadata(node.result)
        if holding_meta and holding_meta.get("hire_id") == hire_id:
            import asyncio
            resume_result = f"Hiring complete: {emp_name} (#{employee_id}) has onboarded ({role}). Please continue project execution."
            main_loop = getattr(employee_manager, "_event_loop", None)
            if main_loop and main_loop.is_running():
                main_loop.call_soon_threadsafe(
                    main_loop.create_task,
                    employee_manager.resume_held_task(COO_ID, entry.node_id, resume_result),
                )
                resumed = True
                logger.info("[hire-ready] Resumed COO holding task {} via hire_id={}", entry.node_id, hire_id)
            break

    if not resumed:
        # No matching HOLDING task — push as adhoc notification
        followup = (
            f"New employee ready notification\n\n"
            f"Employee {emp_name} (#{employee_id}) has onboarded and is ready ({role}).\n"
            f"Please dispatch project tasks to this employee using dispatch_child().\n\n"
            f"Original hiring reason: {ctx.get('reason', '')}\n"
            f"[Project ID: {project_id}] [Project workspace: {project_dir}]"
        )
        _push_adhoc_task(COO_ID, followup, project_id=project_id, project_dir=project_dir)
        logger.info("[hire-ready] No HOLDING task with hire_id={}, pushed adhoc for {}", hire_id, emp_name)


@router.post("/api/hiring-requests/{request_id}/decide")
async def decide_hiring_request(request_id: str, body: dict) -> dict:
    """Legacy endpoint — hiring is now auto-approved by COO.

    Kept for manual override: CEO can still reject a pending hire.
    Body: { "approved": true/false, "note": "optional comment" }
    """
    from onemancompany.agents.coo_agent import pending_hiring_requests

    req = pending_hiring_requests.get(request_id, None)
    if not req:
        return {"error": f"Hiring request '{request_id}' not found"}

    approved = body.get("approved", True)
    note = body.get("note", "")

    if not approved:
        # CEO rejects — remove from pending and cancel
        pending_hiring_requests.pop(request_id, None)
        await event_bus.publish(CompanyEvent(
            type=EventType.HIRING_REQUEST_DECIDED,
            payload={"hire_id": request_id, "approved": False, "role": req["role"], "note": note},
            agent="CEO",
        ))
        # TODO: cancel HR task if already dispatched

    return {
        "status": DecisionStatus.APPROVED.value if approved else DecisionStatus.REJECTED.value,
        "hire_id": request_id,
        "role": req["role"],
    }


# ===== Candidate Selection =====

@router.post("/api/candidates/hire")
async def hire_candidate(body: HireRequest) -> dict:
    """CEO selects a candidate to hire from the shortlist.

    Launches onboarding in background — returns immediately.
    """
    from onemancompany.agents.recruitment import pending_candidates

    candidates = pending_candidates.get(body.batch_id, [])
    candidate = next((c for c in candidates if c.get("id") == body.candidate_id), None)
    if not candidate:
        return {"error": "Candidate not found"}

    # Pop COO hiring context (FIFO) — overrides talent's indicative role
    coo_ctx: dict = {}
    if _pending_coo_hire_queue:
        coo_ctx = _pending_coo_hire_queue.pop(0)
        logger.info("[hiring] Applying COO context: role='{}' over talent role='{}'", coo_ctx.get("role"), candidate.get("role"))

    # Launch onboarding as background task
    spawn_background(
        _do_hire_single(body.batch_id, body.candidate_id, body.nickname, candidate, coo_ctx)
    )

    return {
        "status": "onboarding",
        "candidate_id": body.candidate_id,
        "name": candidate["name"],
        "message": "Onboarding started in background",
    }


def _check_talent_required_fields(talent_data: dict) -> list[str]:
    """Return list of required fields missing from talent profile."""
    missing = []
    for field in _TALENT_REQUIRED_FIELDS:
        if not talent_data.get(field):
            missing.append(field)
    # Self-hosted talents don't need llm_model/api_provider/auth_method
    if talent_data.get("hosting") != HostingMode.SELF:
        if not talent_data.get("llm_model"):
            missing.append("llm_model")
        if not talent_data.get("api_provider"):
            missing.append("api_provider")
        if not talent_data.get("auth_method"):
            missing.append("auth_method")
    return missing


async def _publish_talent_profile_error(
    talent_id: str, missing_fields: list[str], source_repo: str = "",
    *, is_missing: bool = False, clone_error: str = "",
) -> None:
    """Publish an error event to the frontend about talent profile issues."""
    if is_missing:
        message = f"Talent '{talent_id}' profile not found on disk."
        if clone_error:
            message += f" Clone failed: {clone_error}"
        else:
            message += " The talent repo may have failed to clone."
    else:
        message = (
            f"Talent '{talent_id}' profile is missing required fields: "
            f"{', '.join(missing_fields)}. "
            f"Please contact the talent uploader to fix."
        )

    # Build talent market link if source_repo is available
    talent_link = source_repo or ""
    payload: dict = {
        "role": "HR",
        "summary": message,
    }
    if talent_link:
        payload["talent_link"] = talent_link
        payload["summary"] += f"\n\nTalent repo: {talent_link}"
    payload["missing_fields"] = missing_fields
    payload["talent_id"] = talent_id

    logger.warning("[hiring] {}", message)
    await event_bus.publish(CompanyEvent(
        type=EventType.TALENT_PROFILE_ERROR,
        payload=payload,
        agent="HR",
    ))


async def _cleanup_single_hire_failure(
    batch_id: str, candidate_id: str, candidate: dict, error_msg: str,
) -> None:
    """Clean up hiring state when single-hire fails before execute_hire."""
    from onemancompany.agents.recruitment import pending_candidates, _persist_candidates

    _track_onboarding_progress(batch_id, candidate_id, candidate.get("name", ""), "", "failed", error_msg, 1)
    await event_bus.publish(CompanyEvent(
        type=EventType.ONBOARDING_PROGRESS,
        payload={"batch_id": batch_id, "candidate_id": candidate_id,
                 "name": candidate.get("name", ""), "step": "failed",
                 "message": error_msg},
        agent="HR",
    ))
    pending_candidates.pop(batch_id, None)
    _persist_candidates()

    # Resume HR's HOLDING task
    from onemancompany.core.vessel import employee_manager as _em_hr
    held_node_id = _em_hr.find_holding_task(HR_ID, f"batch_id={batch_id}")
    if held_node_id:
        await _em_hr.resume_held_task(HR_ID, held_node_id, f"Hire failed: {error_msg}")


async def _do_hire_single(
    batch_id: str, candidate_id: str, nickname: str,
    candidate: dict, coo_ctx: dict,
) -> None:
    """Background task: execute hire + post-hire notifications."""
    from pathlib import Path
    from onemancompany.agents.recruitment import pending_candidates, _persist_candidates
    from onemancompany.agents.onboarding import execute_hire, generate_nickname

    logger.info("[hiring] Starting single hire: batch_id={}, candidate={}", batch_id, candidate.get("name"))
    try:
        # Auto-generate nickname if not provided
        if not nickname:
            try:
                nickname = await asyncio.wait_for(
                    generate_nickname(candidate["name"], candidate.get("role", ""), is_founding=False),
                    timeout=120,
                )
            except asyncio.TimeoutError:
                logger.warning("[hiring] Nickname generation timed out for {}", candidate["name"])
                nickname = ""

        # Clone talent repo if sourced from Talent Market (batch-hire does this
        # before _do_batch_hire, but single-hire was missing this step).
        talent_id = candidate.get("talent_id", "") or candidate.get("id", "")
        clone_error: str = ""
        if talent_id and candidate.get("source_repo"):
            from onemancompany.agents.onboarding import clone_talent_repo
            from onemancompany.agents.recruitment import talent_market
            try:
                onboard_result = await talent_market.onboard(talent_id)
                repo_url = onboard_result.get("repo_url", "") or candidate.get("source_repo", "")
                if repo_url:
                    await clone_talent_repo(repo_url, talent_id)
            except Exception as e:
                clone_error = str(e)
                logger.warning("[hiring] Failed to clone talent {}: {}", talent_id, e)

        # Read authoritative fields from the talent profile on disk;
        # fall back to candidate dict for AI-generated / repo-less talents.
        talent_data: dict = {}
        if talent_id:
            from onemancompany.core.config import load_talent_profile
            talent_data = load_talent_profile(talent_id)

        if not talent_data:
            # No on-disk profile — use candidate data from Talent Market API
            logger.debug("[hiring] No local profile for talent {}, using candidate data", talent_id)
            talent_data = candidate

        # Validate required fields
        missing = _check_talent_required_fields(talent_data)
        if missing:
            source_repo = candidate.get("source_repo", "")
            await _publish_talent_profile_error(talent_id or candidate_id, missing, source_repo)
            await _cleanup_single_hire_failure(batch_id, candidate_id, candidate, f"Talent profile missing fields: {', '.join(missing)}")
            return

        skill_names = [s["name"] if isinstance(s, dict) else s for s in candidate.get("skill_set", [])]

        hire_role = coo_ctx.get("role") or ""
        hire_department = coo_ctx.get("department", "")

        # Ensure COO's requested role is in the role mappings
        if coo_ctx.get("role"):
            from onemancompany.core.config import ROLE_DEPARTMENT_MAP
            from onemancompany.core.state import ROLE_TITLES
            if coo_ctx["role"] not in ROLE_TITLES:
                ROLE_TITLES[coo_ctx["role"]] = coo_ctx["role"]
            if coo_ctx["role"] not in ROLE_DEPARTMENT_MAP and hire_department:
                ROLE_DEPARTMENT_MAP[coo_ctx["role"]] = hire_department

        cand_name = candidate["name"]
        cand_role = hire_role or candidate.get("role", "")

        async def _single_progress(step, message):
            _track_onboarding_progress(batch_id, candidate_id, cand_name, cand_role, step, message, 1)
            step_index = ONBOARDING_STEP_ORDER.index(step) if step in ONBOARDING_STEP_ORDER else -1
            await event_bus.publish(CompanyEvent(
                type=EventType.ONBOARDING_PROGRESS,
                payload={"batch_id": batch_id, "candidate_id": candidate_id,
                         "name": cand_name, "step": step,
                         "step_index": step_index,
                         "total_steps": 4, "current": 1, "total": 1,
                         "message": message},
                agent="HR",
            ))

        is_self = talent_data.get("hosting") == HostingMode.SELF
        emp = await execute_hire(
            name=cand_name,
            nickname=nickname or "",
            role=hire_role,
            skills=skill_names,
            talent_id=talent_id,
            llm_model="" if is_self else talent_data.get("llm_model", ""),
            temperature=float(talent_data.get("temperature", 0.7)),
            image_model=candidate.get("image_model", ""),
            api_provider="" if is_self else talent_data.get("api_provider", "openrouter"),
            hosting=talent_data.get("hosting", HostingMode.COMPANY.value),
            auth_method=talent_data.get("auth_method", "api_key"),
            sprite=candidate.get("sprite", "employee_default"),
            remote=candidate.get("remote", False),
            department=hire_department,
            progress_callback=_single_progress,
        )

        # Notify COO that the hire is ready (or stash for OAuth completion)
        if coo_ctx.get("project_id"):
            auth_method = talent_data.get("auth_method", "api_key")
            if auth_method == AuthMethod.OAUTH:
                _pending_oauth_hire[emp.id] = coo_ctx
            else:
                _notify_coo_hire_ready(emp.id, coo_ctx)

        # Resume project lifecycle
        from onemancompany.agents.hr_agent import _pending_project_ctx
        from onemancompany.core.project_archive import append_action, complete_project
        ctx = _pending_project_ctx.pop(batch_id, {})
        pid = ctx.get("project_id", "")
        if pid:
            append_action(pid, HR_ID, "onboarding complete", f"{candidate['name']} has onboarded, employee ID {emp.id}")
            complete_project(pid, f"Hired {candidate['name']}")

        pending_candidates.pop(batch_id, None)
        _persist_candidates()

        # Resume HR's HOLDING task
        from onemancompany.core.vessel import employee_manager as _em_hr
        held_node_id = _em_hr.find_holding_task(HR_ID, f"batch_id={batch_id}")
        if held_node_id:
            await _em_hr.resume_held_task(HR_ID, held_node_id, f"Hired {candidate['name']} (ID: {emp.id})")

        # Broadcast state update
        await event_bus.publish(CompanyEvent(type=EventType.STATE_SNAPSHOT, payload={}, agent="CEO"))
        logger.info("[hiring] Background hire completed: {} ({})", candidate["name"], emp.id)

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("[hiring] Background hire failed for {}", candidate.get("name"))
        await _cleanup_single_hire_failure(batch_id, candidate_id, candidate, str(e))


@router.post("/api/candidates/hire-from-cv")
async def hire_from_cv(body: dict) -> dict:
    """Hire an employee directly from a CV JSON (bypasses talent market search).

    The CV JSON format mirrors the Talent Market profile schema. Required fields:
      name, role. Optional: skills, llm_model, api_provider, hosting, auth_method,
      temperature, salary_per_1m_tokens, system_prompt_template, talent_id.
    """
    from onemancompany.agents.onboarding import execute_hire, generate_nickname

    cv = body.get("cv")
    if not cv or not isinstance(cv, dict):
        return {"error": "Missing or invalid 'cv' field"}

    name = cv.get("name", "").strip()
    role = cv.get("role", "").strip()
    if not name:
        return {"error": "CV missing required field: name"}
    if not role:
        return {"error": "CV missing required field: role"}

    hosting = cv.get("hosting", HostingMode.COMPANY.value)
    is_self = hosting == HostingMode.SELF
    skills = [s if isinstance(s, str) else s.get("name", "") for s in cv.get("skills", [])]
    talent_id = cv.get("talent_id", "")
    try:
        temperature = float(cv.get("temperature", 0.7))
    except (ValueError, TypeError):
        temperature = 0.7

    logger.debug("[cv_hire] Received CV: name={}, role={}, hosting={}, skills={}, talent_id={}",
                 name, role, hosting, skills, talent_id)

    try:
        nickname = await asyncio.wait_for(
            generate_nickname(name, role, is_founding=False), timeout=120
        )
    except asyncio.TimeoutError:
        nickname = ""
    logger.debug("[cv_hire] Generated nickname={} for {}", nickname, name)

    import time as _time
    batch_id = f"cv_{talent_id or name.lower().replace(' ', '_')}_{int(_time.time())}"

    async def _cv_progress(step, message):
        step_index = ONBOARDING_STEP_ORDER.index(step) if step in ONBOARDING_STEP_ORDER else -1
        await event_bus.publish(CompanyEvent(
            type=EventType.ONBOARDING_PROGRESS,
            payload={"batch_id": batch_id, "candidate_id": talent_id or name,
                     "name": name, "step": step, "step_index": step_index,
                     "total_steps": 4, "current": 1, "total": 1, "message": message},
            agent="HR",
        ))

    async def _publish_cv_error(message: str) -> None:
        logger.error("[cv_hire] {}", message)
        await event_bus.publish(CompanyEvent(
            type=EventType.TALENT_PROFILE_ERROR,
            payload={"role": "HR", "summary": message, "talent_id": talent_id, "missing_fields": []},
            agent="HR",
        ))

    async def _do_cv_hire():
        try:
            # Clone talent repo so copy_talent_assets can copy skills/tools/manifest
            if talent_id:
                from onemancompany.agents.onboarding import clone_talent_repo, resolve_talent_dir
                from onemancompany.agents.recruitment import talent_market
                source_repo = cv.get("source_repo", "")
                repo_url = source_repo
                if not repo_url:
                    try:
                        from onemancompany.core.config import load_app_config
                        tm_url = load_app_config().get("talent_market", {}).get("url", "https://api.one-man-company.com/mcp/sse")
                        onboard_result = await talent_market.onboard(talent_id)
                        repo_url = onboard_result.get("repo_url", "")
                    except Exception as e:
                        await _publish_cv_error(
                            f"Failed to fetch repo URL for talent '{talent_id}' from Talent Market ({tm_url}): {e}"
                        )
                        return
                if not repo_url:
                    # AI-generated / repo-less talents: skip clone, proceed with hire using CV data
                    logger.info("[cv_hire] Talent '{}' has no repo URL — skipping clone, hiring with CV data only", talent_id)
                else:
                    try:
                        await clone_talent_repo(repo_url, talent_id)
                    except Exception as e:
                        await _publish_cv_error(
                            f"Failed to clone talent repo '{repo_url}' for '{talent_id}': {e}"
                        )
                        return
                    if not resolve_talent_dir(talent_id):
                        from onemancompany.core.config import TALENTS_RUNTIME_DIR
                        cloned_dirs = [d for d in TALENTS_RUNTIME_DIR.iterdir() if d.is_dir()]
                        await _publish_cv_error(
                            f"Talent repo cloned but directory not found for '{talent_id}'. "
                            f"Available: {[d.name for d in cloned_dirs]}. Add 'source_repo' to CV pointing directly to the talent repo."
                        )
                        return

            emp = await execute_hire(
                name=name,
                nickname=nickname,
                role=role,
                skills=skills,
                talent_id=talent_id,
                llm_model="" if is_self else cv.get("llm_model", ""),
                temperature=temperature,
                api_provider="" if is_self else cv.get("api_provider", "openrouter"),
                hosting=hosting,
                auth_method=cv.get("auth_method", "api_key"),
                remote=False,
                progress_callback=_cv_progress,
            )
            await event_bus.publish(CompanyEvent(type=EventType.STATE_SNAPSHOT, payload={}, agent="CEO"))
            logger.info("[cv_hire] Hired {} ({})", name, emp.id)

            # Dispatch COO to assign department and desk position
            _push_adhoc_task(
                COO_ID,
                f"A new employee has just onboarded via CV hire. Please assign department and role using assign_department(employee_id, department, role).\n"
                f"Available departments: Engineering, Design, Analytics, Marketing\n"
                f"Determine the role based on the employee's name and skills.\n\n"
                f"- {name}（{nickname}）#{emp.id}",
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[cv_hire] Failed to hire {}", name)
            await _publish_cv_error(f"Onboarding failed for '{name}': {e}")

    spawn_background(_do_cv_hire())
    return {"status": "onboarding", "name": name, "role": role, "message": "Onboarding started in background"}


@router.post("/api/candidates/dismiss")
async def dismiss_shortlist(body: dict) -> dict:
    """CEO dismissed the shortlist — cancel this recruitment round."""
    from onemancompany.agents.recruitment import pending_candidates, _pending_project_ctx

    batch_id = body.get("batch_id", "")
    if not batch_id:
        return {"status": "error", "message": "batch_id required"}

    # Clean up pending data
    pending_candidates.pop(batch_id, None)
    _pending_project_ctx.pop(batch_id, None)

    from onemancompany.agents.recruitment import _persist_candidates
    _persist_candidates()

    # Resume HR's HOLDING task so it doesn't hang forever
    from onemancompany.core.vessel import employee_manager as _em
    dismiss_reason = "CEO认为这次招聘是不需要的或者错误的，已取消本轮招聘"
    held_node_id = _em.find_holding_task(HR_ID, f"batch_id={batch_id}")
    if held_node_id:
        await _em.resume_held_task(HR_ID, held_node_id, dismiss_reason)

    await event_bus.publish(CompanyEvent(
        type=EventType.ACTIVITY,
        payload={"text": "CEO dismissed the shortlist — this recruitment round is cancelled.", "cls": "ceo"},
        agent="CEO",
    ))
    await event_bus.publish(CompanyEvent(type=EventType.STATE_SNAPSHOT, payload={}, agent="CEO"))

    return {"status": "ok", "message": "Shortlist dismissed"}


@router.get("/api/onboarding/status")
async def onboarding_status() -> dict:
    """Return active onboarding batches for frontend state recovery."""
    return {"batches": _active_onboarding}


@router.post("/api/onboarding/dismiss")
async def onboarding_dismiss(body: dict) -> dict:
    """Dismiss a completed onboarding batch (frontend 'Done' button)."""
    batch_id = body.get("batch_id", "")
    if not batch_id:
        return {"error": "batch_id is required"}
    removed = _active_onboarding.pop(batch_id, None)
    logger.debug("[onboarding] Dismissed batch {}: {}", batch_id, "found" if removed else "not found")
    return {"ok": True}


@router.post("/api/candidates/batch-hire")
async def batch_hire_candidates(body: dict) -> dict:
    """Batch hire multiple candidates. Returns immediately, onboarding runs in background."""
    from onemancompany.agents.recruitment import pending_candidates, _pending_project_ctx

    batch_id = body.get("batch_id", "")
    selections = body.get("selections", [])
    logger.debug("[batch-hire] Received request: batch_id={}, selections={}", batch_id, len(selections))

    if not selections:
        logger.debug("[batch-hire] No selections, returning error")
        return {"error": "No candidates selected"}

    all_candidates = pending_candidates.get(batch_id, [])
    logger.debug("[batch-hire] pending_candidates keys={}, found {} candidates for batch_id={}",
                 list(pending_candidates.keys()), len(all_candidates), batch_id)
    if not all_candidates:
        return {"error": "Batch not found"}

    # --- Talent Market purchase (must complete before background work) ---
    from onemancompany.agents.recruitment import talent_market
    session_id = _pending_project_ctx.get(batch_id, {}).get("session_id", "")
    talent_ids = [s.get("candidate_id", "") for s in selections]

    if talent_market.connected and talent_ids:
        try:
            purchase_result = await talent_market.hire(talent_ids, session_id=session_id)
            if purchase_result.get("error"):
                return {
                    "error": purchase_result.get("error", "Purchase failed"),
                    "balance": purchase_result.get("balance"),
                    "required": purchase_result.get("required"),
                    "shortfall": purchase_result.get("shortfall"),
                }
        except Exception as e:
            logger.error("Talent Market purchase failed: {}", e)
            return {"error": f"Purchase failed: {e}"}

        # Clone talents concurrently
        from onemancompany.agents.onboarding import clone_talent_repo

        async def _onboard_one(tid):
            try:
                onboard_result = await talent_market.onboard(tid)
                repo_url = onboard_result.get("repo_url", "")
                if repo_url:
                    await clone_talent_repo(repo_url, tid)
            except Exception as e:
                logger.error("Failed to onboard/clone talent {}: {}", tid, e)

        await asyncio.gather(*[_onboard_one(tid) for tid in talent_ids])

    # Collect COO contexts for all selections
    coo_ctxs = []
    for _ in selections:
        if _pending_coo_hire_queue:
            coo_ctxs.append(_pending_coo_hire_queue.pop(0))
        else:
            coo_ctxs.append({})

    # Launch background task for the actual hiring
    logger.debug("[batch-hire] Launching background _do_batch_hire: batch_id={}, {} selections, {} candidates, {} coo_ctxs",
                 batch_id, len(selections), len(all_candidates), len(coo_ctxs))
    spawn_background(
        _do_batch_hire(batch_id, selections, list(all_candidates), coo_ctxs)
    )

    names = []
    for sel in selections:
        cid = sel.get("candidate_id", "")
        c = next((c for c in all_candidates if (c.get("id") or c.get("talent_id")) == cid), None)
        if c:
            names.append(c.get("name", cid))
    return {"status": "onboarding", "count": len(selections), "names": names, "message": "Batch onboarding started in background"}


async def _do_batch_hire(
    batch_id: str, selections: list[dict],
    all_candidates: list[dict], coo_ctxs: list[dict],
) -> None:
    """Background task: batch hire + post-hire notifications."""
    from pathlib import Path
    from onemancompany.agents.recruitment import pending_candidates, _pending_project_ctx, _persist_candidates
    from onemancompany.agents.onboarding import execute_hire, generate_nickname
    from onemancompany.core.config import load_talent_profile

    total = len(selections)
    results = []
    hired_names: list[str] = []
    logger.debug("[batch-hire] _do_batch_hire entered: batch_id={}, {} selections, {} all_candidates",
                 batch_id, total, len(all_candidates))

    # Clear pending batch immediately — CEO already approved, data is no longer
    # "pending review". This unblocks HR from submitting new shortlists.
    pending_candidates.pop(batch_id, None)
    _persist_candidates()
    logger.debug("[batch-hire] Cleared pending batch, remaining keys={}", list(pending_candidates.keys()))

    logger.info("[batch-hire] Starting batch hire: batch_id={}, {} candidates", batch_id, total)

    try:
        # Pre-generate nicknames (pool-based, instant — no LLM)
        nickname_map: dict[str, str] = {}
        for sel in selections:
            cid = sel.get("candidate_id", "")
            candidate = next((c for c in all_candidates if (c.get("id") or c.get("talent_id")) == cid), None)
            if not candidate:
                continue
            cand_name = candidate.get("name", cid)
            coo_ctx_role = sel.get("role", "") or candidate.get("role", "Engineer")
            try:
                logger.debug("[batch-hire] Generating nickname for cid={}, name={}, role={}", cid, cand_name, coo_ctx_role)
                nickname_map[cid] = await generate_nickname(cand_name, coo_ctx_role, is_founding=False)
                logger.debug("[batch-hire] Nickname for {}: '{}'", cid, nickname_map[cid])
            except Exception as exc:
                logger.debug("[batch-hire] Nickname generation failed for {}: {}", cid, exc)
                nickname_map[cid] = ""
        logger.debug("[batch-hire] Nicknames ready ({}), starting hire loop", nickname_map)

        for idx, sel in enumerate(selections):
            candidate_id = sel.get("candidate_id", "")

            candidate = next((c for c in all_candidates if (c.get("id") or c.get("talent_id")) == candidate_id), None)
            if not candidate:
                _track_onboarding_progress(batch_id, candidate_id, candidate_id, "", "failed", "Candidate not found", total)
                await event_bus.publish(CompanyEvent(
                    type=EventType.ONBOARDING_PROGRESS,
                    payload={"batch_id": batch_id, "candidate_id": candidate_id,
                             "name": candidate_id, "step": "failed",
                             "step_index": -1, "total_steps": 4, "current": idx + 1, "total": total,
                             "message": "Candidate not found"},
                    agent="HR",
                ))
                results.append({"candidate_id": candidate_id, "status": "error", "error": "Not found"})
                continue

            cand_name = candidate.get("name", candidate_id)
            talent_id = candidate.get("talent_id", "") or candidate.get("id", "")

            talent_data: dict = {}
            if talent_id:
                talent_data = load_talent_profile(talent_id)

            if not talent_data:
                # No on-disk profile — use candidate data from Talent Market API
                logger.debug("[batch-hire] No local profile for talent {}, using candidate data", talent_id)
                talent_data = candidate

            # Validate required fields
            missing = _check_talent_required_fields(talent_data)
            if missing:
                source_repo = candidate.get("source_repo", "")
                await _publish_talent_profile_error(talent_id or candidate_id, missing, source_repo)
                results.append({"candidate_id": candidate_id, "status": "error", "name": cand_name,
                                "error": f"Talent profile missing fields: {', '.join(missing)}"})
                continue

            skill_names = [s["name"] if isinstance(s, dict) else s for s in candidate.get("skill_set", candidate.get("skills", []))]

            coo_ctx = coo_ctxs[idx] if idx < len(coo_ctxs) else {}
            final_role = coo_ctx.get("role") or ""
            final_dept = coo_ctx.get("department", "")

            if coo_ctx.get("role"):
                from onemancompany.core.config import ROLE_DEPARTMENT_MAP
                from onemancompany.core.state import ROLE_TITLES
                if coo_ctx["role"] not in ROLE_TITLES:
                    ROLE_TITLES[coo_ctx["role"]] = coo_ctx["role"]
                if coo_ctx["role"] not in ROLE_DEPARTMENT_MAP and final_dept:
                    ROLE_DEPARTMENT_MAP[coo_ctx["role"]] = final_dept

            # Progress callback
            sel_role = sel.get("role", "") or candidate.get("role", "")
            async def _make_progress_cb(cid, name, idx_val, role):
                async def cb(step, message):
                    step_index = ONBOARDING_STEP_ORDER.index(step) if step in ONBOARDING_STEP_ORDER else -1
                    _track_onboarding_progress(batch_id, cid, name, role, step, message, total)
                    await event_bus.publish(CompanyEvent(
                        type=EventType.ONBOARDING_PROGRESS,
                        payload={"batch_id": batch_id, "candidate_id": cid,
                                 "name": name, "step": step,
                                 "step_index": step_index,
                                 "total_steps": 4, "current": idx_val + 1, "total": total,
                                 "message": message},
                        agent="HR",
                    ))
                return cb

            progress_cb = await _make_progress_cb(candidate_id, cand_name, idx, sel_role)

            try:
                nickname = nickname_map.get(candidate_id, "") or await generate_nickname(cand_name, final_role, is_founding=False)
                logger.debug("[batch-hire] Hiring {}/{}: cid={}, name={}, nickname={}, role={}, talent_id={}",
                             idx + 1, total, candidate_id, cand_name, nickname, final_role, talent_id)
                emp = await execute_hire(
                    name=cand_name,
                    nickname=nickname,
                    role=final_role,
                    skills=skill_names,
                    talent_id=talent_id,
                    llm_model="" if talent_data.get("hosting") == HostingMode.SELF else talent_data.get("llm_model", ""),
                    temperature=float(talent_data.get("temperature", 0.7)),
                    image_model=candidate.get("image_model", ""),
                    api_provider="" if talent_data.get("hosting") == HostingMode.SELF else talent_data.get("api_provider", "openrouter"),
                    hosting=talent_data.get("hosting", HostingMode.COMPANY.value),
                    auth_method=talent_data.get("auth_method", "api_key"),
                    sprite=candidate.get("sprite", "employee_default"),
                    remote=candidate.get("remote", False),
                    department=final_dept,
                    progress_callback=progress_cb,
                )
                results.append({"candidate_id": candidate_id, "status": "hired", "employee_id": emp.id, "name": cand_name, "nickname": nickname})

                if coo_ctx.get("project_id"):
                    auth_method = talent_data.get("auth_method", "api_key")
                    if auth_method == AuthMethod.OAUTH:
                        _pending_oauth_hire[emp.id] = coo_ctx
                    else:
                        _notify_coo_hire_ready(emp.id, coo_ctx)

            except Exception as e:
                # NOTE: We do NOT use _cleanup_single_hire_failure() here because
                # batch-hire has different lifecycle semantics: pending_candidates
                # is already cleared at the top, HR task resume happens in `finally`,
                # and we need per-candidate progress with idx/total counters.
                logger.exception("[hiring] execute_hire failed for {}", cand_name)
                _track_onboarding_progress(batch_id, candidate_id, cand_name, sel_role, "failed", str(e), total)
                await event_bus.publish(CompanyEvent(
                    type=EventType.ONBOARDING_PROGRESS,
                    payload={"batch_id": batch_id, "candidate_id": candidate_id,
                             "name": cand_name, "step": "failed",
                             "step_index": -1, "total_steps": 4, "current": idx + 1, "total": total,
                             "message": str(e)},
                    agent="HR",
                ))
                results.append({"candidate_id": candidate_id, "status": "error", "error": str(e)})

        # Resume project lifecycle
        from onemancompany.core.project_archive import append_action, complete_project
        ctx = _pending_project_ctx.pop(batch_id, {})
        pid = ctx.get("project_id", "")
        hired_names = [r["name"] for r in results if r["status"] == "hired"]
        if pid and hired_names:
            append_action(pid, HR_ID, "batch onboarding complete", f"{', '.join(hired_names)} have onboarded")
            complete_project(pid, f"Batch hired: {', '.join(hired_names)}")

        # Dispatch COO task for department assignment (only if no project context)
        last_coo_ctx = coo_ctxs[-1] if coo_ctxs else {}
        if not last_coo_ctx.get("project_id"):
            hired_entries = [r for r in results if r["status"] == "hired"]
            if hired_entries:
                emp_lines = "\n".join(
                    f"- {r['name']}（{r.get('nickname', '')}）#{r['employee_id']}"
                    for r in hired_entries
                )
                _push_adhoc_task(
                    COO_ID,
                    f"The following new employees have just onboarded. Please assign departments and roles to each using assign_department(employee_id, department, role).\n"
                    f"Available departments: Engineering, Design, Analytics, Marketing\n"
                    f"Determine the role based on the employee's name and skills (e.g., Engineer, Designer, PM, QA Engineer, etc.).\n\n"
                    f"{emp_lines}",
                )

        logger.info("[hiring] Background batch hire completed: {} hired, {} failed",
                     len(hired_names), len(results) - len(hired_names))

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("[hiring] Background batch hire failed")
    finally:
        # ALWAYS resume HR HOLDING task — even on partial/total failure
        resume_msg = f"Batch hired: {', '.join(hired_names)}" if hired_names else "Batch hire completed (no candidates hired)"
        try:
            from onemancompany.core.vessel import employee_manager as _em_batch
            held_node_id = _em_batch.find_holding_task(HR_ID, f"batch_id={batch_id}")
            if held_node_id:
                await _em_batch.resume_held_task(HR_ID, held_node_id, resume_msg)
                logger.info("[hiring] Resumed HR holding task {}", held_node_id)
            else:
                logger.debug("[hiring] No matching HR holding task found for batch_id={}", batch_id)
        except Exception as resume_exc:
            logger.error("[hiring] Failed to resume HR holding task: {}", resume_exc)

        try:
            await event_bus.publish(CompanyEvent(type=EventType.STATE_SNAPSHOT, payload={}, agent="CEO"))
        except Exception as pub_exc:
            logger.debug("[hiring] Could not publish state_snapshot in finally: {}", pub_exc)


@router.post("/api/candidates/interview")
async def interview_candidate(body: InterviewRequest) -> InterviewResponse:
    """CEO interviews a candidate by asking a question. Supports text and image input.

    Request body validated by InterviewRequest (see recruitment.py for schema).
    Returns InterviewResponse.
    """
    from onemancompany.agents.base import make_llm
    from langchain_core.messages import HumanMessage, SystemMessage

    candidate = body.candidate
    skill_desc = ", ".join(s.name for s in candidate.skill_set)
    full_prompt = (
        f"{candidate.system_prompt}\n\n"
        f"You are in an interview. Your name is {candidate.name}, "
        f"your role is {candidate.role}, "
        f"and your skills include: {skill_desc}.\n"
        f"Answer the interview question thoughtfully and demonstrate your expertise."
    )

    # Build message content — text + optional images
    content: list = [{"type": "text", "text": body.question}]
    for img_b64 in body.images[:3]:  # limit to 3 images per message
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
        })

    llm = make_llm(HR_ID)
    result = await tracked_ainvoke(llm, [
        SystemMessage(content=full_prompt),
        HumanMessage(content=content if body.images else body.question),
    ], category="interview", employee_id=HR_ID)

    return InterviewResponse(
        candidate_id=candidate.id,
        question=body.question,
        answer=result.content,
    )


# ===== Remote Worker Endpoints =====

# In-memory store for remote worker state
_remote_workers: dict[str, dict] = {}   # employee_id -> registration info
_remote_task_queues: dict[str, list[dict]] = {}  # employee_id -> [task, ...]
_remote_task_project_map: dict[str, str] = {}    # task_id -> project_id (for cost tracking)


@router.post("/api/remote/register")
async def remote_register(body: dict) -> dict:
    """Remote worker registers itself with the company."""
    from onemancompany.talent_market.remote_protocol import RemoteWorkerRegistration

    reg = RemoteWorkerRegistration(**body)
    _remote_workers[reg.employee_id] = {
        "worker_url": reg.worker_url,
        "capabilities": reg.capabilities,
        "status": STATUS_IDLE,
        "current_task_id": None,
    }
    # Ensure task queue exists
    if reg.employee_id not in _remote_task_queues:
        _remote_task_queues[reg.employee_id] = []

    await event_bus.publish(
        CompanyEvent(
            type=EventType.REMOTE_WORKER_REGISTERED,
            payload={"employee_id": reg.employee_id, "capabilities": reg.capabilities},
            agent=SYSTEM_AGENT,
        )
    )
    return {"status": "registered", "employee_id": reg.employee_id}


@router.get("/api/remote/tasks/{employee_id}")
async def remote_get_tasks(employee_id: str) -> dict:
    """Remote worker polls for pending tasks."""
    queue = _remote_task_queues.get(employee_id, [])
    if not queue:
        return {"task": None}
    # Pop the first pending task
    task = queue.pop(0)
    # Update worker status
    if employee_id in _remote_workers:
        _remote_workers[employee_id]["status"] = "busy"
        _remote_workers[employee_id]["current_task_id"] = task.get("task_id")
    # Remember task_id → project_id mapping for cost tracking on result submission
    task_id = task.get("task_id", "")
    project_id = task.get("project_id", "")
    if task_id and project_id:
        _remote_task_project_map[task_id] = project_id
    return {"task": task}


@router.post("/api/remote/results")
async def remote_submit_results(body: dict) -> dict:
    """Remote worker submits task results."""
    from onemancompany.talent_market.remote_protocol import TaskResult

    result = TaskResult(**body)
    # Update worker status
    if result.employee_id in _remote_workers:
        _remote_workers[result.employee_id]["status"] = STATUS_IDLE
        _remote_workers[result.employee_id]["current_task_id"] = None

    # Record token usage from remote worker if provided
    if result.input_tokens or result.output_tokens:
        from onemancompany.core.project_archive import record_project_cost
        from onemancompany.agents.base import _record_overhead
        record_overhead_model = result.model_used or "remote"
        _record_overhead("remote_worker", record_overhead_model, result.input_tokens, result.output_tokens, result.estimated_cost_usd)
        # Also record to project cost breakdown (was previously imported but never called)
        project_id = _remote_task_project_map.pop(result.task_id, "")
        if project_id:
            record_project_cost(
                project_id, result.employee_id, record_overhead_model,
                result.input_tokens, result.output_tokens, result.estimated_cost_usd,
            )

    await event_bus.publish(
        CompanyEvent(
            type=EventType.REMOTE_TASK_COMPLETED,
            payload={
                "task_id": result.task_id,
                "employee_id": result.employee_id,
                "status": result.status,
                "output": result.output[:MAX_SUMMARY_LEN],
            },
            agent=SYSTEM_AGENT,
        )
    )
    return {"status": "received", "task_id": result.task_id}


@router.post("/api/remote/heartbeat")
async def remote_heartbeat(body: dict) -> dict:
    """Remote worker sends a keep-alive heartbeat."""
    from onemancompany.talent_market.remote_protocol import HeartbeatPayload

    hb = HeartbeatPayload(**body)
    if hb.employee_id in _remote_workers:
        _remote_workers[hb.employee_id]["status"] = hb.status
        _remote_workers[hb.employee_id]["current_task_id"] = hb.current_task_id
    return {"status": "ok"}


@router.get("/api/tools/{tool_id}/icon")
async def get_tool_icon(tool_id: str):
    """Serve the tool's icon.png from its folder."""
    from onemancompany.core.config import TOOLS_DIR

    tool = company_state.tools.get(tool_id)
    if not tool or not tool.folder_name:
        raise HTTPException(status_code=404, detail="Tool not found")
    icon_path = TOOLS_DIR / tool.folder_name / "icon.png"
    if not icon_path.exists():
        raise HTTPException(status_code=404, detail="Icon not found")
    return FileResponse(icon_path, media_type="image/png")


@router.get("/api/tools/{tool_id}/definition")
async def get_tool_definition(tool_id: str):
    """Return tool definition with dynamic sections for the tool detail view.

    Sections are built from tool.yaml declarations:
    - oauth: OAuth login/credentials config
    - env_vars: Environment variable configuration
    - access: Allowed users display
    - files: Source file listing
    - definition: Raw tool.yaml content
    """
    import os

    import yaml as _yaml

    from onemancompany.core.config import TOOLS_DIR

    tool = company_state.tools.get(tool_id)
    if not tool or not tool.folder_name:
        raise HTTPException(status_code=404, detail="Tool not found")
    tool_yaml_path = TOOLS_DIR / tool.folder_name / "tool.yaml"
    raw = tool_yaml_path.read_text() if tool_yaml_path.exists() else ""
    tool_data = {}
    try:
        tool_data = _yaml.safe_load(raw) or {}
    except Exception as exc:
        logger.warning("Failed to parse tool.yaml for {}: {}", tool_id, exc)

    # Build sections dynamically from tool.yaml content
    sections: list[dict] = []

    # 1. OAuth section — auto-detected from oauth: key
    oauth_cfg = tool_data.get("oauth")
    if oauth_cfg:
        service_name = oauth_cfg.get("service_name", "")
        client_id_env = oauth_cfg.get("client_id_env", "")
        client_secret_env = oauth_cfg.get("client_secret_env", "")
        has_credentials = bool(os.environ.get(client_id_env)) and bool(os.environ.get(client_secret_env))

        is_authorized = False
        if has_credentials:
            try:
                from onemancompany.core.oauth import get_oauth_token, OAuthServiceConfig
                config = OAuthServiceConfig(
                    service_name=service_name,
                    authorize_url=oauth_cfg.get("authorize_url", ""),
                    token_url=oauth_cfg.get("token_url", ""),
                    scopes=oauth_cfg.get("scopes", ""),
                    client_id_env=client_id_env,
                    client_secret_env=client_secret_env,
                )
                is_authorized = get_oauth_token(config) is not None
            except Exception as exc:
                logger.debug("OAuth token check failed for {}: {}", service_name, exc)

        # Provide masked preview of current credentials
        raw_id = os.environ.get(client_id_env, "")
        client_id_preview = (raw_id[:8] + "..." + raw_id[-4:]) if len(raw_id) > 12 else ("***" if raw_id else "")

        # credentials_help — how to obtain API keys
        creds_help = oauth_cfg.get("credentials_help")
        help_data = {}
        if creds_help:
            help_data["credentials_help_text"] = creds_help.get("text", "")
            help_data["credentials_help_url"] = creds_help.get("url", "")
        # Always include redirect_uri so frontend can display it
        redirect_port = oauth_cfg.get("redirect_port", 8585)
        help_data["redirect_uri"] = f"http://localhost:{redirect_port}/callback"

        sections.append({
            "type": "oauth",
            "title": f"OAuth — {service_name.title()}",
            "service_name": service_name,
            "has_credentials": has_credentials,
            "is_authorized": is_authorized,
            "client_id_env": client_id_env,
            "client_secret_env": client_secret_env,
            "client_id_preview": client_id_preview,
            **help_data,
        })

    # 2. env_vars section — auto-detected from env_vars: key
    env_vars_cfg = tool_data.get("env_vars")
    if env_vars_cfg:
        vars_list = []
        for v in env_vars_cfg:
            name = v.get("name", "")
            raw_val = os.environ.get(name, "")
            if v.get("secret", False):
                display_val = ("***" + raw_val[-4:]) if len(raw_val) > 4 else ("***" if raw_val else "")
            else:
                display_val = raw_val
            vars_list.append({
                "name": name,
                "label": v.get("label", name),
                "placeholder": v.get("placeholder", ""),
                "secret": v.get("secret", False),
                "value": display_val,
                "is_set": bool(raw_val),
            })
        # credentials_help for env_vars section
        env_help = env_vars_cfg[0].get("credentials_help") if env_vars_cfg else None
        # Also check top-level env_vars_help in tool_data
        env_help = env_help or tool_data.get("credentials_help")
        env_help_data = {}
        if env_help and isinstance(env_help, dict):
            env_help_data["credentials_help_text"] = env_help.get("text", "")
            env_help_data["credentials_help_url"] = env_help.get("url", "")

        sections.append({
            "type": "env_vars",
            "title": "Configuration",
            "vars": vars_list,
            **env_help_data,
        })

    # 3. Access control section
    allowed = tool_data.get("allowed_users")
    if allowed is not None:
        users_info = []
        for uid in (allowed or []):
            _access_emp = _load_emp(uid)
            users_info.append({"id": uid, "name": _access_emp.get("name", uid) if _access_emp else uid})
        sections.append({
            "type": "access",
            "title": "Access Control",
            "allowed_users": users_info,
            "open_access": len(allowed or []) == 0 and "allowed_users" not in tool_data,
        })
    else:
        sections.append({
            "type": "access",
            "title": "Access Control",
            "allowed_users": [],
            "open_access": True,
        })

    # 4. Templates section — auto-detected from templates: key
    templates_cfg = tool_data.get("templates")
    if templates_cfg:
        templates_dir_name = templates_cfg.get("dir", "templates")
        templates_dir = TOOLS_DIR / tool.folder_name / templates_dir_name
        template_files = []
        if templates_dir.is_dir():
            for tf in sorted(templates_dir.iterdir()):
                if tf.is_file() and not tf.name.startswith("."):
                    # Parse frontmatter for name/description
                    content = tf.read_text(encoding=ENCODING_UTF8)
                    tmpl_meta = {"filename": tf.name, "name": tf.stem, "description": ""}
                    if content.startswith("---"):
                        parts = content.split("---", 2)
                        if len(parts) >= 3:
                            try:
                                fm = _yaml.safe_load(parts[1]) or {}
                                tmpl_meta["name"] = fm.get("name", tf.stem)
                                tmpl_meta["description"] = fm.get("description", "")
                            except Exception as exc:
                                logger.debug("Failed to parse template frontmatter {}: {}", tf.name, exc)
                    template_files.append(tmpl_meta)
        sections.append({
            "type": "templates",
            "title": "Email Templates",
            "templates_dir": templates_dir_name,
            "templates": template_files,
        })

    # 5. Files section
    if tool.files:
        sections.append({
            "type": "files",
            "title": "Source Files",
            "files": tool.files,
        })

    # 5. Definition section (raw YAML)
    sections.append({
        "type": "definition",
        "title": "Definition (tool.yaml)",
        "content": raw,
    })

    return {
        "id": tool_id,
        "name": tool.name,
        "description": tool.description,
        "folder": tool.folder_name,
        "files": tool.files,
        "has_icon": tool.has_icon,
        "sections": sections,
    }


@router.post("/api/tools/{tool_id}/oauth/login")
async def tool_oauth_login(tool_id: str):
    """Trigger OAuth login flow for a tool. Returns the auth URL."""
    import os

    import yaml as _yaml

    from onemancompany.core.config import TOOLS_DIR

    tool = company_state.tools.get(tool_id)
    if not tool or not tool.folder_name:
        raise HTTPException(status_code=404, detail="Tool not found")

    tool_yaml_path = TOOLS_DIR / tool.folder_name / "tool.yaml"
    if not tool_yaml_path.exists():
        raise HTTPException(status_code=404, detail="Tool config not found")

    tool_data = _yaml.safe_load(tool_yaml_path.read_text()) or {}
    oauth_cfg = tool_data.get("oauth")
    if not oauth_cfg:
        raise HTTPException(status_code=400, detail="Tool does not use OAuth")

    service_name = oauth_cfg.get("service_name", "")
    client_id_env = oauth_cfg.get("client_id_env", "")
    client_secret_env = oauth_cfg.get("client_secret_env", "")

    if not os.environ.get(client_id_env) or not os.environ.get(client_secret_env):
        return {
            "status": "error",
            "message": f"Missing credentials. Set env vars: {client_id_env}, {client_secret_env}",
        }

    from onemancompany.core.oauth import OAuthServiceConfig, _trigger_oauth_popup
    config = OAuthServiceConfig(
        service_name=service_name,
        authorize_url=oauth_cfg.get("authorize_url", ""),
        token_url=oauth_cfg.get("token_url", ""),
        scopes=oauth_cfg.get("scopes", ""),
        client_id_env=client_id_env,
        client_secret_env=client_secret_env,
    )
    auth_url = _trigger_oauth_popup(config)
    if not auth_url:
        return {"status": "error", "message": "Failed to start OAuth flow"}

    return {"status": "ok", "auth_url": auth_url}


@router.post("/api/tools/{tool_id}/oauth/logout")
async def tool_oauth_logout(tool_id: str):
    """Revoke OAuth tokens for a tool."""
    import yaml as _yaml

    from onemancompany.core.config import TOOLS_DIR

    tool = company_state.tools.get(tool_id)
    if not tool or not tool.folder_name:
        raise HTTPException(status_code=404, detail="Tool not found")

    tool_yaml_path = TOOLS_DIR / tool.folder_name / "tool.yaml"
    tool_data = _yaml.safe_load(tool_yaml_path.read_text()) or {}
    oauth_cfg = tool_data.get("oauth")
    if not oauth_cfg:
        raise HTTPException(status_code=400, detail="Tool does not use OAuth")

    service_name = oauth_cfg.get("service_name", "")
    from onemancompany.core.oauth import _token_cache_path
    cache_path = _token_cache_path(service_name)
    if cache_path.exists():
        cache_path.unlink()

    return {"status": "ok", "message": f"OAuth tokens for {service_name} revoked"}


@router.post("/api/tools/{tool_id}/oauth/credentials")
async def tool_oauth_set_credentials(tool_id: str, body: dict):
    """Set OAuth client credentials (client_id, client_secret) for a tool."""
    import os

    import yaml as _yaml

    from onemancompany.core.config import TOOLS_DIR

    tool = company_state.tools.get(tool_id)
    if not tool or not tool.folder_name:
        raise HTTPException(status_code=404, detail="Tool not found")

    tool_yaml_path = TOOLS_DIR / tool.folder_name / "tool.yaml"
    tool_data = _yaml.safe_load(tool_yaml_path.read_text()) or {}
    oauth_cfg = tool_data.get("oauth")
    if not oauth_cfg:
        raise HTTPException(status_code=400, detail="Tool does not use OAuth")

    client_id = body.get("client_id", "")
    client_secret = body.get("client_secret", "")
    if not client_id or not client_secret:
        return {"status": "error", "message": "Both client_id and client_secret required"}

    client_id_env = oauth_cfg.get("client_id_env", "")
    client_secret_env = oauth_cfg.get("client_secret_env", "")

    # Set in current process environment
    os.environ[client_id_env] = client_id
    os.environ[client_secret_env] = client_secret

    # Persist to .env file
    from pathlib import Path as _Path
    env_path = DATA_ROOT / DOT_ENV_FILENAME
    lines = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()

    # Update or append
    updated = set()
    for i, line in enumerate(lines):
        if line.startswith(f"{client_id_env}="):
            lines[i] = f"{client_id_env}={client_id}"
            updated.add(client_id_env)
        elif line.startswith(f"{client_secret_env}="):
            lines[i] = f"{client_secret_env}={client_secret}"
            updated.add(client_secret_env)
    if client_id_env not in updated:
        lines.append(f"{client_id_env}={client_id}")
    if client_secret_env not in updated:
        lines.append(f"{client_secret_env}={client_secret}")
    env_path.write_text("\n".join(lines) + "\n")

    return {"status": "ok", "message": "Credentials saved"}


@router.post("/api/tools/{tool_id}/env")
async def tool_save_env_vars(tool_id: str, body: dict):
    """Save environment variables for a tool. Body is {VAR_NAME: value, ...}."""
    import os
    from pathlib import Path as _Path

    tool = company_state.tools.get(tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")

    if not body:
        return {"status": "error", "message": "No variables provided"}

    # Filter out empty values (don't overwrite existing with blank)
    to_save = {k: v for k, v in body.items() if v}
    if not to_save:
        return {"status": "ok", "message": "Nothing to update"}

    # Set in current process
    for name, value in to_save.items():
        os.environ[name] = value

    # Persist to .env
    env_path = DATA_ROOT / DOT_ENV_FILENAME
    lines = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()

    updated = set()
    for i, line in enumerate(lines):
        for name, value in to_save.items():
            if line.startswith(f"{name}="):
                lines[i] = f"{name}={value}"
                updated.add(name)
    for name, value in to_save.items():
        if name not in updated:
            lines.append(f"{name}={value}")
    env_path.write_text("\n".join(lines) + "\n")

    return {"status": "ok", "message": f"{len(to_save)} variable(s) saved"}


@router.get("/api/tools/{tool_id}/templates/{filename}")
async def tool_get_template(tool_id: str, filename: str):
    """Read a template file from a tool's templates directory."""
    import yaml as _yaml

    from onemancompany.core.config import TOOLS_DIR

    tool = company_state.tools.get(tool_id)
    if not tool or not tool.folder_name:
        raise HTTPException(status_code=404, detail="Tool not found")

    tool_yaml_path = TOOLS_DIR / tool.folder_name / "tool.yaml"
    tool_data = _yaml.safe_load(tool_yaml_path.read_text()) or {}
    templates_cfg = tool_data.get("templates")
    if not templates_cfg:
        raise HTTPException(status_code=400, detail="Tool does not have templates")

    templates_dir = TOOLS_DIR / tool.folder_name / templates_cfg.get("dir", "templates")
    file_path = templates_dir / filename
    if not file_path.is_file() or not file_path.resolve().is_relative_to(templates_dir.resolve()):
        raise HTTPException(status_code=404, detail="Template not found")

    return {"filename": filename, "content": file_path.read_text(encoding=ENCODING_UTF8)}


@router.put("/api/tools/{tool_id}/templates/{filename}")
async def tool_save_template(tool_id: str, filename: str, body: dict):
    """Save (create or update) a template file."""
    import yaml as _yaml

    from onemancompany.core.config import TOOLS_DIR

    tool = company_state.tools.get(tool_id)
    if not tool or not tool.folder_name:
        raise HTTPException(status_code=404, detail="Tool not found")

    content = body.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="Empty content")

    tool_yaml_path = TOOLS_DIR / tool.folder_name / "tool.yaml"
    tool_data = _yaml.safe_load(tool_yaml_path.read_text()) or {}
    templates_cfg = tool_data.get("templates")
    if not templates_cfg:
        raise HTTPException(status_code=400, detail="Tool does not have templates")

    templates_dir = TOOLS_DIR / tool.folder_name / templates_cfg.get("dir", "templates")
    templates_dir.mkdir(parents=True, exist_ok=True)
    file_path = templates_dir / filename
    if not file_path.resolve().is_relative_to(templates_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path.write_text(content, encoding=ENCODING_UTF8)
    return {"status": "ok", "filename": filename}


@router.delete("/api/tools/{tool_id}/templates/{filename}")
async def tool_delete_template(tool_id: str, filename: str):
    """Delete a template file."""
    import yaml as _yaml

    from onemancompany.core.config import TOOLS_DIR

    tool = company_state.tools.get(tool_id)
    if not tool or not tool.folder_name:
        raise HTTPException(status_code=404, detail="Tool not found")

    tool_yaml_path = TOOLS_DIR / tool.folder_name / "tool.yaml"
    tool_data = _yaml.safe_load(tool_yaml_path.read_text()) or {}
    templates_cfg = tool_data.get("templates")
    if not templates_cfg:
        raise HTTPException(status_code=400, detail="Tool does not have templates")

    templates_dir = TOOLS_DIR / tool.folder_name / templates_cfg.get("dir", "templates")
    file_path = templates_dir / filename
    if not file_path.is_file() or not file_path.resolve().is_relative_to(templates_dir.resolve()):
        raise HTTPException(status_code=404, detail="Template not found")

    file_path.unlink()
    return {"status": "ok", "filename": filename}


# ===== Sales Protocol (External Client API) =====

@router.post("/api/sales/submit")
async def sales_submit_task(body: dict) -> dict:
    """External clients submit tasks via the sales protocol."""
    client_name = body.get("client_name", "")
    description = body.get("description", "")
    if not client_name or not description:
        return {"error": "Missing client_name or description"}

    from datetime import datetime as _dt
    task_id = _uuid.uuid4().hex[:12]
    sales_task_dict = {
        "id": task_id,
        "client_name": client_name,
        "description": description,
        "requirements": body.get("requirements", ""),
        "budget_tokens": body.get("budget_tokens", 0),
        "status": "pending",
        "assigned_to": "",
        "contract_approved": False,
        "delivery": "",
        "settlement_tokens": 0,
        "created_at": _dt.now().isoformat(),
    }
    tasks = _store.load_sales_tasks()
    tasks.append(sales_task_dict)
    await _store.save_sales_tasks(tasks)

    await _store.append_activity({
        "type": "sales_task_submitted",
        "task_id": task_id,
        "client": client_name,
    })

    # Notify CSO about new external task
    from onemancompany.core.agent_loop import get_agent_loop

    cso_loop = get_agent_loop(CSO_ID)
    if cso_loop:
        cso_notification = (
            f"New external task from client '{client_name}'.\n"
            f"Task ID: {task_id}\n"
            f"Description: {description}\n"
            f"Requirements: {body.get('requirements', 'none')}\n"
            f"Budget tokens: {body.get('budget_tokens', 0)}\n\n"
            f"Please review this contract using review_contract()."
        )
        _push_adhoc_task(CSO_ID, cso_notification)

    await event_bus.publish(
        CompanyEvent(
            type=EventType.SALES_TASK_SUBMITTED,
            payload=sales_task_dict,
            agent="SALES",
        )
    )

    return {
        "status": "submitted",
        "task_id": task_id,
        "message": f"Task submitted. CSO will review your contract.",
    }


@router.get("/api/sales/tasks")
async def sales_list_tasks() -> dict:
    """List all sales tasks."""
    return {"tasks": _store.load_sales_tasks()}


@router.get("/api/sales/tasks/{task_id}")
async def sales_get_task(task_id: str) -> dict:
    """Get details of a specific sales task."""
    tasks = _store.load_sales_tasks()
    task = next((t for t in tasks if t.get("id") == task_id), None)
    if not task:
        return {"error": f"Sales task '{task_id}' not found"}
    return task


@router.post("/api/sales/tasks/{task_id}/deliver")
async def sales_deliver_task(task_id: str, body: dict) -> dict:
    """Mark a sales task as delivered."""
    tasks = _store.load_sales_tasks()
    task = next((t for t in tasks if t.get("id") == task_id), None)
    if not task:
        return {"error": f"Sales task '{task_id}' not found"}
    if task.get("status") != "in_production":
        return {"error": f"Task status is '{task.get('status')}', expected 'in_production'"}

    task["status"] = "delivered"
    task["delivery"] = body.get("delivery_summary", "")
    await _store.save_sales_tasks(tasks)
    await _store.append_activity({
        "type": "task_delivered",
        "task_id": task_id,
        "client": task.get("client_name", ""),
    })
    return {"status": "delivered", "task_id": task_id}


@router.post("/api/sales/tasks/{task_id}/settle")
async def sales_settle_task(task_id: str) -> dict:
    """Collect settlement tokens for a delivered task."""
    tasks = _store.load_sales_tasks()
    task = next((t for t in tasks if t.get("id") == task_id), None)
    if not task:
        return {"error": f"Sales task '{task_id}' not found"}
    if task.get("status") != "delivered":
        return {"error": f"Task status is '{task.get('status')}', must be 'delivered' to settle"}

    tokens = task.get("budget_tokens", 0)
    task["settlement_tokens"] = tokens
    task["status"] = "settled"
    await _store.save_sales_tasks(tasks)

    # Update company tokens in overhead
    overhead = _store.load_overhead()
    overhead["company_tokens"] = overhead.get("company_tokens", 0) + tokens
    await _store.save_overhead(overhead)

    return {
        "status": "settled",
        "task_id": task_id,
        "tokens_earned": tokens,
        "company_total_tokens": overhead["company_tokens"],
    }


@router.get("/api/sales/protocol")
async def sales_protocol() -> dict:
    """Return the sales protocol documentation (JSON schema for external clients)."""
    return {
        "protocol_version": "1.0",
        "description": "OneManCompany External Task Protocol",
        "endpoints": {
            "submit_task": {
                "method": "POST",
                "path": "/api/sales/submit",
                "body": {
                    "client_name": "string (required) — your company/name",
                    "description": "string (required) — what you need done",
                    "requirements": "string (optional) — detailed requirements",
                    "budget_tokens": "int (optional) — token budget for this task",
                },
            },
            "list_tasks": {
                "method": "GET",
                "path": "/api/sales/tasks",
                "description": "List all your submitted tasks",
            },
            "get_task": {
                "method": "GET",
                "path": "/api/sales/tasks/{task_id}",
                "description": "Get task details and current status",
            },
            "deliver": {
                "method": "POST",
                "path": "/api/sales/tasks/{task_id}/deliver",
                "body": {"delivery_summary": "string — summary of deliverable"},
            },
            "settle": {
                "method": "POST",
                "path": "/api/sales/tasks/{task_id}/settle",
                "description": "Collect settlement tokens after delivery",
            },
        },
        "task_statuses": [
            "pending", "accepted", "in_production", "delivered", "settled", "rejected",
        ],
    }


# ── Generic credentials endpoint ────────────────────────
@router.post("/api/credentials/{service_name}")
async def submit_credentials(service_name: str, request: Request) -> dict:
    """Receive credentials submitted from the generic popup form.

    Stores them as env vars (runtime only) and in the OAuth token cache
    so tools can pick them up on next invocation.
    """
    import os
    body = await request.json()

    # Store each field as an env var: SERVICENAME_FIELDNAME
    prefix = service_name.upper()
    for key, value in body.items():
        env_key = f"{prefix}_{key.upper()}"
        os.environ[env_key] = str(value)

    # Also persist to .env for next restart
    from onemancompany.core.config import COMPANY_ROOT
    env_path = COMPANY_ROOT.parent / DOT_ENV_FILENAME
    if env_path.exists():
        existing = env_path.read_text()
    else:
        existing = ""

    new_lines = []
    updated_keys = set()
    for key, value in body.items():
        env_key = f"{prefix}_{key.upper()}"
        updated_keys.add(env_key)
        new_lines.append(f"{env_key}={value}")

    # Update existing .env — replace existing keys, append new ones
    lines = existing.splitlines()
    result_lines = []
    for line in lines:
        k = line.split("=", 1)[0].strip()
        if k in updated_keys:
            continue  # Will be replaced
        result_lines.append(line)

    result_lines.extend(new_lines)
    env_path.write_text("\n".join(result_lines) + "\n")

    await event_bus.publish(CompanyEvent(
        type=EventType.CREDENTIALS_SUBMITTED,
        payload={"service": service_name, "fields": list(body.keys())},
        agent="CEO",
    ))

    return {"status": "ok", "service": service_name, "fields_saved": list(body.keys())}


@router.put("/api/employee/{employee_id}/secrets")
async def save_employee_secrets(employee_id: str, request: Request) -> dict:
    """Save manifest secret fields to .env (using their key names directly)."""
    import os
    body = await request.json()  # {"telegram_bot_token": "...", ...}

    from onemancompany.core.config import COMPANY_ROOT
    env_path = COMPANY_ROOT.parent / DOT_ENV_FILENAME
    existing = env_path.read_text() if env_path.exists() else ""

    updated_keys = {}
    for key, value in body.items():
        env_key = key.upper()
        os.environ[env_key] = str(value)
        updated_keys[env_key] = value

    # Update .env — replace existing keys, append new ones
    lines = existing.splitlines()
    result_lines = []
    for line in lines:
        k = line.split("=", 1)[0].strip()
        if k in updated_keys:
            continue
        result_lines.append(line)
    for k, v in updated_keys.items():
        result_lines.append(f"{k}={v}")
    env_path.write_text("\n".join(result_lines) + "\n")

    return {"status": "ok", "fields_saved": list(body.keys())}


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ceo_task":
                task = data.get("task", "")
                if task:
                    # Re-use the REST logic
                    await ceo_submit_task(task=task)
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# Snapshot provider — routes-level ephemeral state
# ---------------------------------------------------------------------------

from onemancompany.core.snapshot import snapshot_provider  # noqa: E402


@snapshot_provider("routes")
class _RoutesSnapshot:
    @staticmethod
    def save() -> dict:
        result: dict = {}
        if _pending_coo_hire_queue:
            result["pending_coo_hire_queue"] = _pending_coo_hire_queue
        if _pending_oauth_hire:
            result["pending_oauth_hire"] = _pending_oauth_hire
        if _remote_workers:
            result["remote_workers"] = _remote_workers
        if _remote_task_queues:
            result["remote_task_queues"] = _remote_task_queues
        if _remote_task_project_map:
            result["remote_task_project_map"] = _remote_task_project_map
        if _active_onboarding:
            result["active_onboarding"] = _active_onboarding
        return result

    @staticmethod
    def restore(data: dict) -> None:
        # COO hire context
        restored_coo_queue = data.get("pending_coo_hire_queue", [])
        if restored_coo_queue:
            _pending_coo_hire_queue.extend(restored_coo_queue)
        restored_oauth = data.get("pending_oauth_hire", {})
        if restored_oauth:
            _pending_oauth_hire.update(restored_oauth)

        # Onboarding state
        restored_onboarding = data.get("active_onboarding", {})
        if restored_onboarding:
            _active_onboarding.update(restored_onboarding)

        # Remote worker state
        restored_remote_workers = data.get("remote_workers", {})
        if restored_remote_workers:
            _remote_workers.update(restored_remote_workers)
        restored_remote_queues = data.get("remote_task_queues", {})
        if restored_remote_queues:
            _remote_task_queues.update(restored_remote_queues)
        restored_remote_map = data.get("remote_task_project_map", {})
        if restored_remote_map:
            _remote_task_project_map.update(restored_remote_map)


# =====================================================================
# Internal MCP Tool-Call API
# =====================================================================


@router.post("/api/internal/tool-call")
async def internal_tool_call(body: dict) -> dict:
    """Generic tool-call endpoint for MCP server (Claude CLI).

    Body: {employee_id, task_id, tool_name, args: {...}}

    Delegates to the unified execute_tool() which handles context setup.
    For MCP calls, task_id must be set explicitly since context vars
    aren't pre-set by vessel.
    """
    from onemancompany.core.tool_registry import execute_tool
    from onemancompany.core.vessel import (
        _current_task_id,
    )

    employee_id = body.get("employee_id", "")
    task_id = body.get("task_id", "")
    tool_name = body.get("tool_name", "")
    args = body.get("args", {})

    if not tool_name:
        raise HTTPException(400, "Missing tool_name")

    # For MCP calls, set task_id context var (vessel doesn't set it)
    task_token = None
    try:
        if task_id:
            task_token = _current_task_id.set(task_id)
        result = await execute_tool(employee_id, tool_name, args)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"MCP tool-call '{tool_name}' failed: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        if task_token is not None:
            _current_task_id.reset(task_token)


# ---------------------------------------------------------------------------
# Automation: webhooks + cron management
# ---------------------------------------------------------------------------

@router.post("/api/webhook/{employee_id}/{hook_name}")
async def webhook_trigger(employee_id: str, hook_name: str, body: dict = {}) -> dict:
    """Receive an external webhook call and dispatch a task to the employee."""
    from onemancompany.core.automation import handle_webhook
    result = await handle_webhook(employee_id, hook_name, body)
    if result.get("status") == "error":
        raise HTTPException(404, result["message"])
    return result


@router.get("/api/automations/{employee_id}")
async def get_automations(employee_id: str) -> dict:
    """List all automations (crons + webhooks) for an employee."""
    from onemancompany.core.automation import list_crons, list_webhooks
    return {
        "employee_id": employee_id,
        "crons": list_crons(employee_id),
        "webhooks": list_webhooks(employee_id),
    }


@router.post("/api/automations/{employee_id}/cron/{cron_name}/stop")
async def stop_cron_endpoint(employee_id: str, cron_name: str) -> dict:
    """Stop and remove a cron job for an employee."""
    from onemancompany.core.automation import stop_cron
    try:
        stop_cron(employee_id, cron_name)
        return {"status": "ok", "message": f"Cron '{cron_name}' stopped"}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/api/automations/{employee_id}/crons/stop-all")
async def stop_all_crons_endpoint(employee_id: str) -> dict:
    """Stop all cron jobs for an employee."""
    from onemancompany.core.automation import stop_all_crons_for_employee
    try:
        return stop_all_crons_for_employee(employee_id)
    except Exception as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# Tick-based resource endpoints (single-source-of-truth — read from disk)
# ---------------------------------------------------------------------------


@router.get("/api/employees")
async def list_employees():
    """List all active employees — reads from disk, reconciled with live execution state."""
    from onemancompany.core.store import load_all_employees
    from onemancompany.core.vessel import employee_manager as _em

    employees = load_all_employees()
    result = []
    for emp_id, data in employees.items():
        if emp_id == CEO_ID:
            continue  # CEO is the human user, not rendered as employee
        runtime = data.pop("runtime", {})
        data["id"] = emp_id
        data["employee_number"] = emp_id
        disk_status = runtime.get("status", STATUS_IDLE)
        # Reconcile: if EmployeeManager has a running task, override to working
        if emp_id in _em._running_tasks and disk_status != STATUS_WORKING:
            data["status"] = STATUS_WORKING
        else:
            data["status"] = disk_status
        data["is_listening"] = runtime.get("is_listening", False)
        data[PF_CURRENT_TASK_SUMMARY] = runtime.get(PF_CURRENT_TASK_SUMMARY, "")
        data["api_online"] = runtime.get("api_online", True)
        data["needs_setup"] = runtime.get("needs_setup", False)
        result.append(data)
    return result


@router.get("/api/rooms")
async def list_rooms():
    """List rooms with booking status — uses layout-computed positions."""
    return [r.to_dict() for r in company_state.meeting_rooms.values()]


@router.get("/api/rooms/{room_id}/chat")
async def get_room_chat(room_id: str):
    """Room chat history — reads from disk."""
    from onemancompany.core.store import load_room_chat
    return load_room_chat(room_id)


@router.get("/api/rooms/{room_id}/minutes")
async def get_room_minutes(room_id: str):
    """List archived meeting minutes for a room."""
    from onemancompany.core.store import load_meeting_minutes
    minutes = load_meeting_minutes(room_id)
    # Return lightweight list (exclude full messages)
    return [
        {
            "minute_id": m.get("minute_id", ""),
            "topic": m.get("topic", ""),
            "room_name": m.get("room_name", ""),
            "participants": m.get("participants", []),
            "summary": (m.get("summary", "") or "")[:200],
            "message_count": len(m.get("messages", [])),
        }
        for m in minutes
    ]


@router.get("/api/meeting-minutes/{minute_id}")
async def get_meeting_minute(minute_id: str):
    """Get full content of a single meeting minute."""
    from onemancompany.core.store import load_meeting_minute
    data = load_meeting_minute(minute_id)
    if not data:
        raise HTTPException(status_code=404, detail="Meeting minute not found")
    return data


@router.post("/api/rooms/{room_id}/chat")
async def post_room_chat(room_id: str, body: dict):
    """CEO sends a message to a meeting room chat.

    The message is persisted to disk, broadcast via WebSocket, and
    injected into the active pull_meeting token-grab loop (if any)
    so agents see it in their next evaluation round.
    """
    from datetime import datetime
    from onemancompany.core.store import append_room_chat
    from onemancompany.agents.common_tools import get_ceo_meeting_queue

    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message text required")

    entry = {
        "room_id": room_id,
        "speaker": "CEO",
        "role": "CEO",
        "message": message,
        "time": datetime.now().strftime("%H:%M:%S"),
    }
    await append_room_chat(room_id, entry)
    await event_bus.publish(
        CompanyEvent(
            type=EventType.MEETING_CHAT,
            payload=entry,
            agent="CEO",
        )
    )

    # Inject into active meeting's token-grab loop
    q = get_ceo_meeting_queue(room_id)
    if q is not None:
        await q.put(message)

    return {"status": "sent"}


@router.get("/api/rooms/{room_id}/minutes")
async def list_room_minutes(room_id: str, limit: int = 20):
    """List archived meeting minutes for a specific room."""
    from onemancompany.core.meeting_minutes import query_minutes
    return query_minutes(room_id=room_id, limit=limit)


@router.get("/api/meeting-minutes/{minute_id}")
async def get_minute_detail(minute_id: str):
    """Get full detail of a specific meeting minute."""
    from onemancompany.core.meeting_minutes import load_minute
    doc = load_minute(minute_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Meeting minutes not found")
    return doc


@router.get("/api/tools")
async def list_tools():
    """List tools — reads from disk."""
    from onemancompany.core.store import load_tools
    return load_tools()


@router.get("/api/employee/{employee_id}/oneonone")
async def get_oneonone_history(employee_id: str):
    """1-on-1 chat history — reads from disk."""
    from onemancompany.core.store import load_oneonone
    return load_oneonone(employee_id)


@router.get("/api/activity-log")
async def get_activity_log():
    """Activity log (last 50 entries) — reads from disk."""
    from onemancompany.core.store import load_activity_log
    log = load_activity_log()
    return log[-50:]


# ===== CEO Inbox =====


def _scan_ceo_inbox_nodes() -> list[dict]:
    """Scan all active task trees for ceo_request nodes that aren't terminal."""
    from onemancompany.core.config import PROJECTS_DIR
    from onemancompany.core.task_tree import get_tree

    results = []
    if not PROJECTS_DIR.exists():
        return results

    # Recursively find all task_tree.yaml files under projects/
    # TODO: O(N) scan — consider node-to-project index if this becomes slow
    for tree_path in PROJECTS_DIR.rglob(TASK_TREE_FILENAME):
        tree = get_tree(tree_path)
        for node in tree.all_nodes():
            if node.node_type != NodeType.CEO_REQUEST:
                continue
            # ACCEPTED/FINISHED/CANCELLED are fully resolved — hide from inbox
            if TaskPhase(node.status) in (TaskPhase.ACCEPTED, TaskPhase.FINISHED, TaskPhase.CANCELLED):
                continue
            # COMPLETED nodes stay visible if EA auto-replied (awaiting CEO confirmation)
            # Find dispatching employee (parent)
            from_id = ""
            from_nickname = ""
            parent = tree.get_node(node.parent_id) if node.parent_id else None
            if parent:
                from_id = parent.employee_id
                emp = _load_emp(from_id)
                from_nickname = emp.get("nickname", emp.get("name", "")) if emp else ""
            results.append({
                "project_id": node.project_id,
                "node_id": node.id,
                "description": node.description_preview or node.description or "",
                "from_employee_id": from_id,
                "from_nickname": from_nickname,
                "status": node.status,
                "created_at": node.created_at,
            })
    return results


def _find_ceo_node(node_id: str):
    """Find a ceo_request node across all project trees.

    Returns (node, tree, project_dir) or raises HTTPException(404).
    """
    from onemancompany.core.config import PROJECTS_DIR
    from onemancompany.core.task_tree import get_tree

    if PROJECTS_DIR.exists():
        # TODO: O(N) scan — consider node-to-project index if this becomes slow
        for tree_path in PROJECTS_DIR.rglob(TASK_TREE_FILENAME):
            tree = get_tree(tree_path)
            node = tree.get_node(node_id)
            if node and node.node_type == NodeType.CEO_REQUEST:
                return node, tree, str(tree_path.parent)
    raise HTTPException(status_code=404, detail=f"CEO request node {node_id} not found")


@router.get("/api/ceo/inbox")
async def get_ceo_inbox(page: int = 1, page_size: int = 10):
    """List CEO request nodes with pagination.

    Returns newest first. page=1 is the first page.
    """
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    items = _scan_ceo_inbox_nodes()
    # Sort newest first
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "items": items[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total else 1,
    }


@router.post("/api/ceo/inbox/{node_id}/open")
async def open_ceo_conversation(node_id: str):
    """Open a conversation with the dispatching employee."""
    from onemancompany.core.task_lifecycle import transition

    node, tree, project_dir = _find_ceo_node(node_id)

    # Check if session already exists (reopen)
    session = get_session(node_id)
    if session:
        messages = load_messages(Path(project_dir) / "conversations", node_id)
        return {"status": "resumed", "node_id": node_id, "messages": messages}

    # Transition to processing
    if node.status == TaskPhase.PENDING.value:
        transition(node.id, TaskPhase.PENDING, TaskPhase.PROCESSING)
        node.status = TaskPhase.PROCESSING.value
        from onemancompany.core.task_tree import save_tree_async
        save_tree_async(Path(project_dir) / TASK_TREE_FILENAME)

    # Find the dispatching employee (parent node's employee)
    parent = tree.get_node(node.parent_id) if node.parent_id else None
    employee_id = parent.employee_id if parent else ""

    # Resolve nickname
    emp = _load_emp(employee_id) if employee_id else None
    nickname = emp.get("nickname", emp.get("name", "")) if emp else ""

    # Create session
    session = ConversationSession(
        node_id=node_id,
        employee_id=employee_id,
        project_dir=project_dir,
        broadcast_fn=ws_manager.broadcast,
    )
    register_session(session)
    # EA auto-reply is OFF by default — CEO enables it via the sidebar checkbox
    # (POST /api/ceo/inbox/{node_id}/ea-auto-reply). This prevents unwanted
    # auto-replies when conversations are opened by schedule_auto_open_inbox().

    # Start conversation loop as background task
    spawn_background(_run_conversation_loop(session, node, tree, project_dir))

    messages = load_messages(Path(project_dir) / "conversations", node_id)
    node.load_content(project_dir)
    return {
        "status": "opened",
        "node_id": node_id,
        "messages": messages,
        "description": node.description,
        "employee_id": employee_id,
        "employee_nickname": nickname,
    }


def _parse_hold_reason(hold_reason: str | None) -> dict[str, str]:
    """Parse a hold_reason string like 'ceo_request=node_id,no_watchdog=1' into a dict."""
    result: dict[str, str] = {}
    for pair in (hold_reason or "").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[k.strip()] = v.strip()
    return result


async def _complete_ceo_request(
    node, tree, project_dir: str, *,
    target_status: TaskPhase = TaskPhase.ACCEPTED,
    result: str = "",
    notes: str = "",
) -> None:
    """Unified completion for CEO_REQUEST nodes.

    Handles ALL side effects in one place:
    1. Transition node to target_status (ACCEPTED or CANCELLED)
    2. Save tree to disk
    3. Resolve sibling dependencies (_trigger_dep_resolution)
    4. Resume HOLDING parent (ceo_request hold or awaiting_children hold)
    5. Broadcast inbox update
    """
    from onemancompany.core.task_lifecycle import can_transition
    from onemancompany.core.task_tree import save_tree_async
    from onemancompany.core.vessel import _trigger_dep_resolution, employee_manager

    if result:
        node.result = result
    if notes:
        node.acceptance_result = {"passed": target_status != TaskPhase.CANCELLED, "notes": notes}

    # 1. Transition node status (supports multi-hop: PROCESSING→COMPLETED→ACCEPTED)
    current = TaskPhase(node.status)
    if current == target_status:
        pass  # Already at target, idempotent
    elif can_transition(current, target_status):
        node.set_status(target_status)
    elif target_status == TaskPhase.ACCEPTED and can_transition(current, TaskPhase.COMPLETED):
        # Multi-hop: PROCESSING→COMPLETED→ACCEPTED
        node.set_status(TaskPhase.COMPLETED)
        node.set_status(TaskPhase.ACCEPTED)
    else:
        logger.warning("[ceo_request] Cannot transition node {} from {} to {} — skipping",
                        node.id, current.value, target_status.value)
        return

    # 2. Save tree
    tree_path = Path(project_dir) / TASK_TREE_FILENAME
    save_tree_async(tree_path)

    # 3. Resolve sibling dependencies
    _trigger_dep_resolution(project_dir, tree, node)

    # 4. Resume HOLDING parent
    parent = tree.get_node(node.parent_id) if node.parent_id else None
    if parent and parent.status == TaskPhase.HOLDING.value:
        hr_meta = _parse_hold_reason(parent.hold_reason)
        should_resume = (
            hr_meta.get("ceo_request") == node.id
            or "awaiting_children" in (parent.hold_reason or "")
        )
        if should_resume:
            resume_text = f"CEO request {node.id} resolved ({target_status.value}): {(node.result or '')[:500]}"
            resumed = await employee_manager.resume_held_task(
                parent.employee_id, parent.id, resume_text,
            )
            if resumed:
                logger.info("[ceo_request] Resumed HOLDING parent {} after node {} → {}",
                            parent.id, node.id, target_status.value)
            else:
                logger.warning("[ceo_request] Failed to resume parent {} for node {}", parent.id, node.id)

    # 5. Broadcast
    await ws_manager.broadcast({"type": "ceo_inbox_updated"})
    logger.info("[ceo_request] Completed node={} → {} notes={}", node.id, target_status.value, notes[:80])


async def _run_conversation_loop(session, node, tree, project_dir):
    """Run conversation loop and handle completion."""
    try:
        summary = await session.run()
        logger.info("[ceo_inbox] conversation completed: node={}, summary_len={}", node.id, len(summary))

        ea_auto_replied = not session._ceo_replied
        if ea_auto_replied:
            # EA auto-reply: fully automatic, go straight to ACCEPTED.
            # CEO doesn't need to confirm — can review the conversation later.
            await _complete_ceo_request(
                node, tree, project_dir,
                target_status=TaskPhase.ACCEPTED,
                result=summary,
                notes="EA auto-replied",
            )
        else:
            # CEO replied directly: go straight to ACCEPTED
            await _complete_ceo_request(
                node, tree, project_dir,
                target_status=TaskPhase.ACCEPTED,
                result=summary,
                notes="CEO responded directly",
            )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("Conversation loop error for {}: {}", session.node_id, e)
        # Fail the node so it doesn't stay zombie PROCESSING
        try:
            await _complete_ceo_request(
                node, tree, project_dir,
                target_status=TaskPhase.CANCELLED,
                result=f"Conversation error: {e}",
                notes="Conversation loop failed",
            )
        except Exception:
            logger.error("Failed to cancel node {} after conversation error", session.node_id)
    finally:
        session._cancel_ea_timer()
        unregister_session(session.node_id)
        await ws_manager.broadcast({"type": "ceo_inbox_updated"})


@router.post("/api/ceo/inbox/{node_id}/message")
async def send_ceo_message(node_id: str, body: dict):
    """CEO sends a message in an open conversation."""
    session = get_session(node_id)
    if not session:
        raise HTTPException(status_code=404, detail="No active conversation for this node")

    text = body.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="Message text required")

    msg = await session.send(text)
    return {"status": "sent", "message": msg}


@router.post("/api/ceo/inbox/{node_id}/upload")
async def upload_ceo_attachment(node_id: str, file: UploadFile):
    """CEO uploads a file attachment."""
    session = get_session(node_id)
    if not session:
        raise HTTPException(status_code=404, detail="No active conversation")

    workspace = Path(session.project_dir) / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    dest = workspace / file.filename
    content = await file.read()
    dest.write_bytes(content)

    attachment = {"filename": file.filename, "path": str(dest)}
    msg = await session.send(f"[Attached file: {file.filename}]", attachments=[attachment])
    return {"status": "uploaded", "message": msg}


@router.post("/api/ceo/inbox/{node_id}/complete")
async def complete_ceo_conversation(node_id: str):
    """CEO marks conversation as complete."""
    session = get_session(node_id)
    if not session:
        raise HTTPException(status_code=404, detail="No active conversation")

    await session.complete()
    return {"status": "completing", "node_id": node_id}


@router.post("/api/ceo/inbox/{node_id}/confirm")
async def confirm_ea_auto_reply(node_id: str):
    """CEO confirms an EA auto-replied inbox item → ACCEPTED, removed from inbox."""
    node, tree, project_dir = _find_ceo_node(node_id)
    if node.status not in (TaskPhase.COMPLETED.value, TaskPhase.PROCESSING.value):
        raise HTTPException(status_code=409, detail=f"Node is {node.status}, expected completed or processing")

    await _complete_ceo_request(
        node, tree, project_dir,
        target_status=TaskPhase.ACCEPTED,
        notes="CEO confirmed",
    )
    return {"status": "confirmed", "node_id": node_id}


@router.post("/api/ceo/inbox/{node_id}/dismiss")
async def dismiss_ceo_inbox_item(node_id: str):
    """Dismiss (hide) a CEO inbox item by marking it CANCELLED.

    Used for old/stale requests that CEO no longer needs to respond to.
    """
    from onemancompany.core.task_lifecycle import can_transition

    node, tree, project_dir = _find_ceo_node(node_id)
    if TaskPhase(node.status) in (TaskPhase.FINISHED, TaskPhase.CANCELLED):
        return {"status": "already_dismissed", "node_id": node_id}

    if not can_transition(TaskPhase(node.status), TaskPhase.CANCELLED):
        raise HTTPException(status_code=409, detail=f"Cannot dismiss node in {node.status} state")

    await _complete_ceo_request(
        node, tree, project_dir,
        target_status=TaskPhase.CANCELLED,
        result="Dismissed by CEO",
        notes="Dismissed by CEO",
    )
    return {"status": "dismissed", "node_id": node_id}


@router.post("/api/ceo/inbox/{node_id}/ea-auto-reply")
async def toggle_ea_auto_reply(node_id: str, body: dict):
    """Toggle EA auto-reply for a CEO inbox conversation.

    When enabled, if CEO doesn't reply within 60 seconds, EA will
    auto-reply with accept/reject decision on behalf of CEO.
    """
    enabled = body.get("enabled", False)
    session = get_session(node_id)
    if not session:
        raise HTTPException(status_code=404, detail="No active conversation")

    # Get description from node for EA to evaluate
    node, _tree, _project_dir = _find_ceo_node(node_id)
    description = node.description or node.description_preview or ""

    session.set_ea_auto_reply(enabled, description)
    return {"status": "ok", "ea_auto_reply": enabled}


@router.post("/api/ceo/report/{project_id}/confirm")
async def confirm_ceo_report(project_id: str):
    """CEO confirms a pending project completion report (early confirmation)."""
    from onemancompany.core.vessel import employee_manager

    confirmed = await employee_manager._confirm_ceo_report(project_id)
    if not confirmed:
        raise HTTPException(status_code=404, detail="No pending report for this project")
    return {"status": "ok", "project_id": project_id}


# ---------------------------------------------------------------------------
# System Cron management
# ---------------------------------------------------------------------------

@router.get("/api/system/crons")
async def list_system_crons() -> list[dict]:
    from onemancompany.core.system_cron import system_cron_manager
    return system_cron_manager.get_all()


@router.post("/api/system/crons/{name}/start")
async def start_system_cron(name: str) -> dict:
    from onemancompany.core.system_cron import system_cron_manager
    return system_cron_manager.start(name)


@router.post("/api/system/crons/{name}/stop")
async def stop_system_cron(name: str) -> dict:
    from onemancompany.core.system_cron import system_cron_manager
    return system_cron_manager.stop(name)


@router.patch("/api/system/crons/{name}")
async def update_system_cron(name: str, body: dict) -> dict:
    from onemancompany.core.system_cron import system_cron_manager
    interval = body.get("interval")
    if not interval:
        return {"status": "error", "message": "interval is required"}
    return system_cron_manager.update_interval(name, interval)


# ---------------------------------------------------------------------------
# Unified Conversation API
# ---------------------------------------------------------------------------

from onemancompany.core.conversation import (
    Conversation,
    ConversationService,
    Message,
    load_conversation_meta as load_conv_meta,
    load_messages as load_conv_messages,
    save_conversation_meta,
)
from onemancompany.core.models import ConversationType, ConversationPhase

_conversation_service = ConversationService()
_active_adapter_tasks: set[asyncio.Task] = set()

_VALID_CONV_TYPES = {t.value for t in ConversationType}
_VALID_CONV_PHASES = {p.value for p in ConversationPhase}


def _parse_iso_timestamp(ts: str | None) -> float:
    """Parse ISO timestamp to unix seconds; invalid values sort as 0."""
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return 0.0


def _pick_reusable_oneonone_conversation(employee_id: str) -> tuple[Conversation, Path] | None:
    """Pick best historical 1-on-1 conversation for this employee.

    Priority:
    1) Conversations that already have messages
    2) Most recently active (last message timestamp, fallback to created_at)
    """
    import onemancompany.core.conversation as conversation_core

    conv_base = conversation_core.EMPLOYEES_DIR / employee_id / "conversations"
    if not conv_base.exists():
        return None

    best: tuple[tuple[int, float, float], Conversation, Path] | None = None
    for conv_dir in conv_base.iterdir():
        if not conv_dir.is_dir():
            continue
        meta_path = conv_dir / "meta.yaml"
        if not meta_path.exists():
            continue
        try:
            conv = load_conv_meta(conv_dir.name, conv_dir)
        except Exception:
            logger.warning("[conversation] failed to load meta from {}", conv_dir)
            continue
        if conv.type != "oneonone" or conv.employee_id != employee_id or conv.phase == ConversationPhase.CLOSING.value:
            continue

        try:
            msgs = load_conv_messages(conv_dir)
        except Exception:
            logger.warning("[conversation] failed to load messages from {}", conv_dir)
            msgs = []

        has_messages = 1 if msgs else 0
        last_msg_ts = _parse_iso_timestamp(msgs[-1].timestamp) if msgs else 0.0
        created_ts = _parse_iso_timestamp(conv.created_at)
        score = (has_messages, max(last_msg_ts, created_ts), created_ts)

        if best is None or score > best[0]:
            best = (score, conv, conv_dir)

    if not best:
        return None
    return best[1], best[2]


_WORKSPACE_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


def _snapshot_workspace_images(employee_id: str) -> dict[str, tuple[int, int]]:
    """Return {relative_path: (mtime_ns, size)} for image files in employee workspace."""
    from onemancompany.core.config import get_workspace_dir

    ws = get_workspace_dir(employee_id).resolve()
    if not ws.exists():
        return {}
    snapshot: dict[str, tuple[int, int]] = {}
    # TODO: O(N) scan — consider node-to-project index if this becomes slow
    for p in ws.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in _WORKSPACE_IMAGE_SUFFIXES:
            continue
        try:
            st = p.stat()
            rel = p.relative_to(ws).as_posix()
            snapshot[rel] = (int(st.st_mtime_ns), int(st.st_size))
        except Exception:
            logger.debug("[workspace] skip unreadable file: {}", p)
            continue
    return snapshot


def _workspace_image_url(employee_id: str, rel_path: str) -> str:
    """Build a frontend-usable URL for an employee workspace file."""
    from urllib.parse import quote

    emp = quote(employee_id, safe="")
    rel = quote(rel_path, safe="/")
    return f"/api/employee/{emp}/workspace/files/{rel}"


def _collect_new_workspace_image_urls(
    employee_id: str,
    before: dict[str, tuple[int, int]],
    limit: int = 8,
) -> list[str]:
    """Collect URLs for image files created/modified since snapshot `before`."""
    after = _snapshot_workspace_images(employee_id)
    changed: list[tuple[int, str]] = []
    for rel, state in after.items():
        if before.get(rel) != state:
            changed.append((state[0], rel))
    changed.sort(key=lambda x: x[0], reverse=True)
    return [_workspace_image_url(employee_id, rel) for _, rel in changed[:limit]]


def _extract_workspace_image_urls_from_text(employee_id: str, text: str, limit: int = 8) -> list[str]:
    """Extract image file refs from reply text and convert to workspace file URLs."""
    from onemancompany.core.config import get_workspace_dir

    if not text:
        return []

    ws = get_workspace_dir(employee_id).resolve()
    # Absolute paths + "workspace/..." paths.
    candidates: list[str] = []
    candidates.extend(
        m.group(1) for m in re.finditer(
            r"(/[^\s'\"<>]+\.(?:png|jpg|jpeg|gif|webp|svg))",
            text,
            flags=re.IGNORECASE,
        )
    )
    candidates.extend(
        m.group(1) for m in re.finditer(
            r"(workspace/[^\s'\"<>]+\.(?:png|jpg|jpeg|gif|webp|svg))",
            text,
            flags=re.IGNORECASE,
        )
    )

    urls: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        try:
            if raw.startswith("workspace/"):
                resolved = (ws / raw[len("workspace/"):]).resolve()
            else:
                resolved = Path(raw).expanduser().resolve()
        except Exception:
            logger.debug("[workspace] skip unresolvable image path: {}", raw)
            continue

        if not resolved.is_relative_to(ws):
            continue
        if not resolved.is_file() or resolved.suffix.lower() not in _WORKSPACE_IMAGE_SUFFIXES:
            continue
        rel = resolved.relative_to(ws).as_posix()
        url = _workspace_image_url(employee_id, rel)
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


@router.post("/api/conversation/create")
async def create_conversation(body: dict) -> dict:
    conv_type = body.get("type", "")
    employee_id = body.get("employee_id", "")
    if conv_type not in _VALID_CONV_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid type: must be one of {_VALID_CONV_TYPES}")
    if not employee_id:
        raise HTTPException(status_code=400, detail="employee_id is required")
    if conv_type == ConversationType.CEO_INBOX.value and not body.get("project_dir"):
        raise HTTPException(status_code=400, detail="project_dir is required for ceo_inbox conversations")

    # Reuse prior one-on-one thread per employee so restarting meeting preserves history.
    if conv_type == ConversationType.ONE_ON_ONE.value and body.get("reuse_existing", True):
        reusable = _pick_reusable_oneonone_conversation(employee_id)
        if reusable:
            conv, conv_dir = reusable
            _conversation_service.ensure_indexed(conv.id, conv_dir)
            if conv.phase != ConversationPhase.ACTIVE.value:
                conv.phase = ConversationPhase.ACTIVE.value
                conv.closed_at = None
                save_conversation_meta(conv)
                await event_bus.publish(CompanyEvent(
                    type="conversation_phase",
                    payload={
                        "conv_id": conv.id,
                        "phase": conv.phase,
                        "type": conv.type,
                        "employee_id": conv.employee_id,
                    },
                ))
            return conv.to_dict()

    conv = await _conversation_service.create(
        type=conv_type,
        employee_id=employee_id,
        tools_enabled=body.get("tools_enabled", False),
        **{
            k: v for k, v in body.items()
            if k not in ("type", "employee_id", "tools_enabled", "reuse_existing")
        },
    )
    return conv.to_dict()


@router.get("/api/conversation/{conv_id}")
async def get_conversation(conv_id: str) -> dict:
    try:
        conv = _conversation_service.get(conv_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv.to_dict()


@router.get("/api/conversation/{conv_id}/messages")
async def get_conversation_messages(conv_id: str) -> dict:
    try:
        msgs = _conversation_service.get_messages(conv_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"messages": [m.to_dict() for m in msgs]}


@router.post("/api/conversation/{conv_id}/message")
async def send_conversation_message(conv_id: str, body: dict) -> dict:
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required and must be non-empty")
    try:
        msg = await _conversation_service.send_message(
            conv_id, sender="ceo", role="CEO", text=text,
            attachments=body.get("attachments"),
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # Dispatch to adapter in background (don't await — async reply via WebSocket)
    task = asyncio.create_task(_dispatch_conversation_to_adapter(conv_id, msg))
    _active_adapter_tasks.add(task)
    task.add_done_callback(_active_adapter_tasks.discard)
    return {"status": "sent", "message": msg.to_dict()}


@router.post("/api/conversation/{conv_id}/upload")
async def upload_conversation_files(conv_id: str, files: list[UploadFile]) -> dict:
    try:
        conv = _conversation_service.get(conv_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    saved_paths = []
    # Use conversation directory for uploads — never fall back to CWD
    from onemancompany.core.conversation import resolve_conv_dir
    workspace = resolve_conv_dir(conv) / "uploads"
    workspace.mkdir(parents=True, exist_ok=True)
    for file in files:
        # Sanitize filename to prevent path traversal
        safe_name = Path(file.filename).name
        if not safe_name:
            continue
        # Enforce per-file size limit (read limit+1 bytes to detect overflow without full read)
        content = await file.read(_MAX_UPLOAD_SIZE + 1)
        if len(content) > _MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail=f"File '{safe_name}' exceeds {_MAX_UPLOAD_SIZE // (1024*1024)}MB limit")
        dest = workspace / safe_name
        dest.write_bytes(content)
        saved_paths.append(str(dest))
    return {"attachments": saved_paths}


@router.post("/api/conversation/{conv_id}/close")
async def close_conversation(conv_id: str, wait_hooks: bool = False) -> dict:
    try:
        conv, hook_result = await _conversation_service.close(conv_id, wait_hooks=wait_hooks)
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    resp = conv.to_dict()
    if hook_result:
        resp["hook_result"] = hook_result
    return resp


@router.post("/api/conversation/{conv_id}/clear")
async def clear_conversation_history(conv_id: str) -> dict:
    """Clear all 1-on-1 message history for the current conversation's employee."""
    try:
        conv = _conversation_service.get(conv_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conv.type != "oneonone":
        raise HTTPException(status_code=400, detail="Clear history is only supported for oneonone conversations")
    if not conv.employee_id:
        raise HTTPException(status_code=400, detail="Conversation has no employee_id")

    import onemancompany.core.conversation as conversation_core

    conv_base = conversation_core.EMPLOYEES_DIR / conv.employee_id / "conversations"
    if not conv_base.exists():
        return {
            "status": "cleared",
            "employee_id": conv.employee_id,
            "conversations_scanned": 0,
            "conversations_cleared": 0,
        }

    scanned = 0
    cleared = 0
    for conv_dir in conv_base.iterdir():
        if not conv_dir.is_dir():
            continue
        meta_path = conv_dir / "meta.yaml"
        if not meta_path.exists():
            continue
        try:
            c = load_conv_meta(conv_dir.name, conv_dir)
        except Exception:
            logger.warning("[conversation] skip unreadable meta when clearing history: {}", conv_dir)
            continue
        if c.type != "oneonone" or c.employee_id != conv.employee_id:
            continue
        scanned += 1
        msg_path = conv_dir / "messages.yaml"
        if msg_path.exists():
            msg_path.write_text("[]\n", encoding="utf-8")
            cleared += 1

    # Keep legacy 1-on-1 history endpoint consistent.
    legacy_history_path = conversation_core.EMPLOYEES_DIR / conv.employee_id / "oneonone_history.yaml"
    if legacy_history_path.exists():
        legacy_history_path.write_text("[]\n", encoding="utf-8")

    return {
        "status": "cleared",
        "employee_id": conv.employee_id,
        "conversations_scanned": scanned,
        "conversations_cleared": cleared,
    }


@router.get("/api/conversations")
async def list_conversations(type: str | None = None, phase: str | None = None) -> dict:
    if phase and phase not in _VALID_CONV_PHASES:
        raise HTTPException(status_code=400, detail=f"Invalid phase: must be one of {_VALID_CONV_PHASES}")
    if phase and phase != ConversationPhase.ACTIVE.value:
        convs = _conversation_service.list_by_phase(type=type, phase=phase)
    else:
        convs = _conversation_service.list_active(type=type)
    return {"conversations": [c.to_dict() for c in convs]}


async def _dispatch_conversation_to_adapter(conv_id: str, ceo_message: Message) -> None:
    """Background task: dispatch CEO message to adapter, persist reply."""
    try:
        conv = _conversation_service.get(conv_id)
        messages = _conversation_service.get_messages(conv_id)
        workspace_before = (
            _snapshot_workspace_images(conv.employee_id)
            if conv.type == "oneonone" else {}
        )

        from onemancompany.core.conversation_adapters import get_adapter, _get_executor_type

        executor_type = _get_executor_type(conv.employee_id)
        adapter_cls = get_adapter(executor_type)
        adapter = adapter_cls()
        reply = await adapter.send(conv, messages[:-1], ceo_message)
        reply_text = reply if isinstance(reply, str) else str(reply)
        attachment_urls: list[str] = []
        if conv.type == "oneonone":
            # Prefer files created/updated during this reply.
            attachment_urls = _collect_new_workspace_image_urls(
                conv.employee_id,
                workspace_before,
            )
            # Fallback: parse explicit file paths mentioned in the reply text.
            if not attachment_urls:
                attachment_urls = _extract_workspace_image_urls_from_text(
                    conv.employee_id,
                    reply_text,
                )

        # Persist agent reply — get employee name from disk (SSOT)
        emp_data = _store.load_employee(conv.employee_id)
        emp_name = emp_data.get("name", conv.employee_id) if emp_data else conv.employee_id
        await _conversation_service.send_message(
            conv_id,
            sender=conv.employee_id,
            role=emp_name,
            text=reply_text,
            attachments=attachment_urls,
        )
    except Exception:
        logger.exception("[conversation] adapter dispatch failed for {}", conv_id)
        try:
            await _conversation_service.send_message(
                conv_id, sender=SYSTEM_SENDER, role="System",
                text="Agent is not responding. Please try again.",
            )
        except Exception:
            logger.exception("[conversation] failed to send error message for {}", conv_id)
