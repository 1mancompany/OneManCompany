"""FastAPI routes — REST endpoints + WebSocket."""

from __future__ import annotations

import asyncio
import traceback
import uuid as _uuid
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from loguru import logger

from onemancompany.agents.base import tracked_ainvoke
from onemancompany.api.websocket import ws_manager
from onemancompany.core.config import (
    CEO_ID,
    COMPANY_DIR,
    COO_ID,
    CSO_ID,
    DATA_ROOT,
    EA_ID,
    HR_ID,
    MAX_SUMMARY_LEN,
    STATUS_IDLE,
)
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.talent_market.boss_online import HireRequest, InterviewRequest, InterviewResponse
from onemancompany.core.state import company_state

router = APIRouter()

# ===== Inquiry Sessions =====

@dataclass
class InquirySession:
    session_id: str
    task: str
    room_id: str
    agent_role: str  # "HR", "COO", "EA", or "CSO"
    participants: list[str]
    history: list[dict]  # [{role: 'ceo'|'agent', speaker: str, content: str}]

_inquiry_sessions: dict[str, InquirySession] = {}


def _get_employee_manager():
    """Lazy import to avoid circular dependency."""
    from onemancompany.core.vessel import employee_manager
    return employee_manager


def _require_employee(employee_id: str):
    """Get employee or raise 404. Use for consistent validation across routes."""
    emp = company_state.employees.get(employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return emp


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
            data = yaml.safe_load(pyaml.read_text(encoding="utf-8")) or {}
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
        return {"status": "deferred", "message": "Restart scheduled after current tasks complete"}


@router.post("/api/admin/clear-tasks")
async def admin_clear_tasks() -> dict:
    """Clear all stale active tasks and reset employee statuses to idle."""
    from onemancompany.core.state import get_active_tasks
    cleared = len(get_active_tasks())
    # Archive all persisted tasks
    from onemancompany.core.task_persistence import load_active_tasks, archive_task
    from onemancompany.core.config import EMPLOYEES_DIR
    for emp_dir in sorted(EMPLOYEES_DIR.iterdir()):
        if not emp_dir.is_dir():
            continue
        for t in load_active_tasks(emp_dir.name):
            archive_task(emp_dir.name, t)
    for emp in company_state.employees.values():
        emp.status = STATUS_IDLE
    await event_bus.publish(
        CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
    )
    return {"status": "cleared", "tasks_removed": cleared}


@router.get("/api/state")
async def get_state() -> dict:
    return company_state.to_json()


async def _start_inquiry(task: str) -> dict:
    """Start an inquiry session: book a room, get initial answer, return session info."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from onemancompany.agents.base import make_llm, get_employee_skills_prompt, get_employee_tools_prompt, get_employee_talent_persona

    # Route to the best agent via configurable routing table
    from onemancompany.core.config import route_inquiry
    agent_role, agent_id = route_inquiry(task)

    emp = company_state.employees.get(agent_id)

    # Book a meeting room
    room = None
    for r in company_state.meeting_rooms.values():
        if not r.is_booked:
            room = r
            break
    if not room:
        return {"error": "No meeting rooms available"}

    room.is_booked = True
    room.booked_by = CEO_ID
    room.participants = [CEO_ID, agent_id]

    await event_bus.publish(
        CompanyEvent(
            type="meeting_booked",
            payload={"room_id": room.id, "room_name": room.name, "participants": room.participants},
            agent="CEO",
        )
    )

    # Build agent system prompt
    skills_str = ", ".join(emp.skills) if emp and emp.skills else "general"
    principles_section = f"\nYour work principles:\n{emp.work_principles}" if emp and emp.work_principles else ""
    culture_items = company_state.company_culture
    culture_section = ""
    if culture_items:
        rules = "\n".join(f"  {i+1}. {item.get('content', '')}" for i, item in enumerate(culture_items))
        culture_section = f"\nCompany culture:\n{rules}"

    persona_section = get_employee_talent_persona(agent_id)
    skills_section = get_employee_skills_prompt(agent_id)
    tools_section = get_employee_tools_prompt(agent_id)

    # Colleague info
    colleagues = []
    for e in company_state.employees.values():
        if e.id not in (CEO_ID, agent_id):
            colleagues.append(f"  - {e.name} ({e.nickname}): {e.role}, {e.department}")
    colleagues_section = "\nColleagues:\n" + "\n".join(colleagues) if colleagues else ""

    system_prompt = (
        f"You are {emp.name} ({emp.nickname}), the company {agent_role}. "
        f"Skills: {skills_str}. "
        f"You are in a meeting room with the CEO answering their inquiry. "
        f"Be helpful, concise, and knowledgeable. Use your expertise and awareness of company operations."
        f"{persona_section}{principles_section}{culture_section}{skills_section}{tools_section}{colleagues_section}"
    )

    # Publish CEO question as meeting_chat
    await event_bus.publish(
        CompanyEvent(
            type="meeting_chat",
            payload={"room_id": room.id, "speaker": "CEO", "role": "CEO", "message": task},
            agent="CEO",
        )
    )

    # LLM generates initial answer
    llm = make_llm(agent_id)
    result = await tracked_ainvoke(llm, [
        SystemMessage(content=system_prompt),
        HumanMessage(content=task),
    ], category="inquiry", employee_id=agent_id)
    answer = result.content

    # Publish agent response as meeting_chat
    speaker_name = emp.name if emp else agent_role
    await event_bus.publish(
        CompanyEvent(
            type="meeting_chat",
            payload={"room_id": room.id, "speaker": speaker_name, "role": agent_role, "message": answer},
            agent=agent_role,
        )
    )

    # Create and store session
    session_id = _uuid.uuid4().hex[:12]
    session = InquirySession(
        session_id=session_id,
        task=task,
        room_id=room.id,
        agent_role=agent_role,
        participants=[CEO_ID, agent_id],
        history=[
            {"role": "ceo", "speaker": "CEO", "content": task},
            {"role": "agent", "speaker": speaker_name, "content": answer},
        ],
    )
    session._system_prompt = system_prompt  # stash for follow-up chats
    _inquiry_sessions[session_id] = session

    # Publish inquiry_started event
    await event_bus.publish(
        CompanyEvent(
            type="inquiry_started",
            payload={
                "session_id": session_id,
                "room_id": room.id,
                "agent_role": agent_role,
                "task": task,
            },
            agent="CEO",
        )
    )

    # Broadcast state so frontend sees the booked room
    await event_bus.publish(
        CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
    )

    return {
        "task_type": "inquiry",
        "session_id": session_id,
        "room_id": room.id,
        "agent_role": agent_role,
        "status": "inquiry_active",
    }


@router.post("/api/ceo/qa")
async def ceo_qa(body: dict) -> dict:
    """CEO Q&A mode: direct LLM answer, no project creation."""
    import base64
    from pathlib import Path

    from langchain_core.messages import HumanMessage, SystemMessage
    from onemancompany.agents.base import make_llm

    question = body.get("question", "")
    attachments = body.get("attachments", [])
    if not question:
        return {"error": "Empty question"}

    # Build multimodal content blocks for images, text descriptions for others
    content_blocks: list = [{"type": "text", "text": question}]
    for att in attachments:
        att_path = att.get("path", "")
        att_type = att.get("type", "file")
        content_type = att.get("content_type", "")
        if att_type == "image" and att_path:
            try:
                img_data = Path(att_path).read_bytes()
                img_b64 = base64.b64encode(img_data).decode()
                mime = content_type or "image/png"
                content_blocks.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{img_b64}"},
                })
            except Exception:
                content_blocks.append({
                    "type": "text",
                    "text": f"\n[附件: {att.get('filename', 'file')} (路径: {att_path})]",
                })
        elif att_path:
            content_blocks.append({
                "type": "text",
                "text": f"\n[附件: {att.get('filename', 'file')} (保存在 {att_path})]",
            })

    # Use multimodal content if there are image attachments, plain text otherwise
    if len(content_blocks) > 1:
        human_msg = HumanMessage(content=content_blocks)
    else:
        human_msg = HumanMessage(content=question)

    llm = make_llm()
    result = await tracked_ainvoke(llm, [
        SystemMessage(content="你是 CEO 的 AI 助理，简洁回答问题。"),
        human_msg,
    ], category="qa")
    answer = result.content

    await event_bus.publish(
        CompanyEvent(
            type="ceo_qa",
            payload={"question": question, "answer": answer},
            agent="CEO",
        )
    )

    return {"answer": answer}


@router.post("/api/ceo/respond")
async def ceo_respond(body: dict) -> dict:
    """CEO responds to an employee report — approve or request revision.

    If the employee is blocking on report_to_ceo(action_required=True),
    the response unblocks the tool call directly (no new task needed).
    Otherwise, pushes a follow-up task to the employee's board.
    """
    from onemancompany.agents.common_tools import resolve_ceo_pending
    from onemancompany.core.vessel import employee_manager

    employee_id = body.get("employee_id", "")
    project_id = body.get("project_id", "")
    subject = body.get("subject", "")
    message = body.get("message", "").strip()
    action = body.get("action", "approve")

    if not employee_id:
        return {"error": "Missing employee_id"}

    handle = employee_manager.get_handle(employee_id)
    if not handle:
        return {"error": f"Employee {employee_id} not found"}

    # Try to unblock a waiting report_to_ceo call first
    resolved = resolve_ceo_pending(employee_id, project_id, {
        "action": action, "message": message,
    })

    if not resolved:
        # No blocking call — push a follow-up task instead
        project_dir = ""
        if project_id:
            from onemancompany.core.project_archive import load_project
            proj = load_project(project_id)
            if proj:
                project_dir = proj.get("project_dir", "")

        if action == "revise" and message:
            task_desc = (
                f"CEO Review 反馈 — 需要修改\n\n"
                f"原汇报主题: {subject}\n\n"
                f"CEO指示:\n{message}\n\n"
                f"请根据CEO的指示进行修改，完成后再次调用 report_to_ceo() 汇报结果。"
            )
        else:
            task_desc = (
                f"CEO已批准 — 请继续执行\n\n"
                f"原汇报主题: {subject}\n\n"
                f"CEO已审阅通过，无修改意见。请继续执行后续步骤。\n"
                f"如果有待发送的邮件、待执行的操作等，请立即执行。"
            )
        handle.push_task(task_desc, project_id=project_id, project_dir=project_dir)

    await event_bus.publish(CompanyEvent(
        type="ceo_response",
        payload={
            "employee_id": employee_id,
            "project_id": project_id,
            "action": action,
            "message": message,
            "subject": subject,
        },
        agent="CEO",
    ))

    return {"status": "ok", "action": action, "resolved_pending": resolved}


@router.post("/api/ceo/task")
async def ceo_submit_task(body: dict) -> dict:
    """CEO submits a task, routed to the appropriate agent via persistent loop."""
    from onemancompany.core.agent_loop import get_agent_loop
    from onemancompany.core.project_archive import (
        create_iteration,
        create_named_project,
        create_project,
        get_project_dir,
        get_project_workspace,
    )

    task = body.get("task", "")
    if not task:
        return {"error": "Empty task"}

    attachments = body.get("attachments", [])
    project_id = body.get("project_id", "")
    project_name = body.get("project_name", "")

    company_state.ceo_tasks.append(task)
    company_state.activity_log.append({"type": "ceo_task", "task": task})

    # Classify task type — EA can override later via set_acceptance_criteria
    from onemancompany.core.task_lifecycle import classify_task_type
    task_type = classify_task_type(task).value

    iter_id = ""
    if project_id:
        # Continue an existing named project with a new iteration
        iter_id = create_iteration(project_id, task, "pending")
        pdir = get_project_workspace(project_id)
        pid = project_id
    elif project_name:
        # Create a new named project + first iteration
        project_id = create_named_project(project_name)
        iter_id = create_iteration(project_id, task, "pending")
        pdir = get_project_workspace(project_id)
        pid = project_id
    else:
        # No project association — legacy one-shot project
        pid = create_project(task, "pending", [e.id for e in company_state.employees.values()], task_type=task_type)
        pdir = get_project_dir(pid)

    await event_bus.publish(
        CompanyEvent(type="ceo_task_submitted", payload={"task": task}, agent="CEO")
    )
    # Broadcast so frontend sees the queued task immediately
    await event_bus.publish(
        CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
    )

    # Use qualified iteration ID to avoid cross-project collisions
    # (e.g. "first-game/iter_002" instead of bare "iter_002")
    ctx_id = f"{pid}/{iter_id}" if iter_id else pid

    # ALL CEO tasks go to EA for classification and routing
    # Build attachment info string
    attach_info = ""
    if attachments:
        lines = [f"- 附件: {a.get('filename', 'file')} (保存在 {a.get('path', '')})" for a in attachments]
        attach_info = "\n\nCEO附带了以下文件:\n" + "\n".join(lines)

    loop = get_agent_loop(EA_ID)
    if loop:
        ea_task = (
            f"CEO下发了新任务，请分析并分派给合适的负责人:\n\n"
            f"任务: {task}{attach_info}\n\n"
            f"[Project ID: {ctx_id}] [Project workspace: {pdir}]"
        )
        ea_agent_task = loop.push_task(ea_task, project_id=ctx_id, project_dir=pdir)

        # Initialize task tree: CEO node as root, EA as child
        try:
            from onemancompany.core.task_tree import TaskTree
            from onemancompany.core.vessel import _save_project_tree
            tree = TaskTree(project_id=ctx_id)
            # CEO root node — records original prompt
            ceo_root = tree.create_root(employee_id=CEO_ID, description=task)
            ceo_root.node_type = "ceo_prompt"
            ceo_root.status = "processing"
            # EA node as child of CEO
            ea_node = tree.add_child(
                parent_id=ceo_root.id,
                employee_id=EA_ID,
                description=task,
                acceptance_criteria=[],
            )
            tree.task_id_map[ea_agent_task.id] = ea_node.id
            _save_project_tree(pdir, tree)
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
    from onemancompany.core.task_tree import TaskTree
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
    tree_path = Path(pdir) / "task_tree.yaml"
    previous_result = ""
    if tree_path.exists():
        tree = TaskTree.load(tree_path, project_id=project_id)
        root = tree.get_node(tree.root_id)
        if root and root.result:
            previous_result = root.result

    # Build follow-up task for EA
    context_parts = [
        f"CEO对已完成的任务追加了新指示:\n",
        f"原始任务: {original_task}\n",
    ]
    if previous_result:
        # Truncate very long results
        prev = previous_result[:2000]
        if len(previous_result) > 2000:
            prev += "...(truncated)"
        context_parts.append(f"上次任务结果:\n{prev}\n")
    context_parts.append(f"CEO追加指示: {instructions}\n")
    context_parts.append(
        f"\n请根据CEO的追加指示和之前的任务上下文继续执行。"
        f"如需分派子任务，使用 dispatch_child()。\n\n"
        f"[Project ID: {project_id}] [Project workspace: {pdir}]"
    )
    followup_task = "\n".join(context_parts)

    # Append to existing tree (or create new if none exists)
    tree_path = Path(pdir) / "task_tree.yaml"
    if tree_path.exists():
        tree = TaskTree.load(tree_path, project_id=project_id)
    else:
        tree = TaskTree(project_id=project_id)

    ea_loop = get_agent_loop(EA_ID)
    if not ea_loop:
        raise HTTPException(status_code=503, detail="EA agent not available")

    ea_agent_task = ea_loop.push_task(followup_task, project_id=project_id, project_dir=pdir)

    if tree.root_id:
        # Start new branch — deactivates old nodes
        tree.new_branch()

        # Find the EA node (child of CEO root, or legacy root)
        ea_node = tree.get_ea_node()
        if ea_node:
            ea_node.status = "pending"
            ea_node.result = ""
            ea_node.branch = tree.current_branch
            ea_node.branch_active = True
            tree.task_id_map[ea_agent_task.id] = ea_node.id

            # Add CEO follow-up as child of EA node
            followup_node = tree.add_child(
                parent_id=ea_node.id,
                employee_id=CEO_ID,
                description=instructions,
                acceptance_criteria=[],
            )
            followup_node.node_type = "ceo_followup"
            followup_node.status = "accepted"
            followup_node.branch = tree.current_branch
            followup_node.branch_active = True
        else:
            # Fallback — create EA child under existing root
            child = tree.add_child(
                parent_id=tree.root_id,
                employee_id=EA_ID,
                description=instructions,
                acceptance_criteria=[],
            )
            child.branch = tree.current_branch
            child.branch_active = True
            tree.task_id_map[ea_agent_task.id] = child.id

        # Update CEO root status
        root = tree.get_node(tree.root_id)
        if root and root.node_type == "ceo_prompt":
            root.status = "processing"
            root.branch = tree.current_branch
            root.branch_active = True
    else:
        # No root yet — create CEO root + EA child
        ceo_root = tree.create_root(employee_id=CEO_ID, description=instructions)
        ceo_root.node_type = "ceo_prompt"
        ceo_root.status = "processing"
        ea_child = tree.add_child(
            parent_id=ceo_root.id,
            employee_id=EA_ID,
            description=instructions,
            acceptance_criteria=[],
        )
        tree.task_id_map[ea_agent_task.id] = ea_child.id

    _save_project_tree(pdir, tree)

    # Update project.yaml status back to in_progress
    doc["status"] = "in_progress"
    doc["completed_at"] = None
    from onemancompany.core.project_archive import _save_resolved
    _save_resolved(_ver, _key, doc)

    # Log the follow-up
    append_action(project_id, "ceo", "追加指示", instructions[:200])

    await event_bus.publish(
        CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
    )

    return {"status": "ok", "project_id": project_id}


@router.post("/api/oneonone/chat")
async def oneonone_chat(body: dict) -> dict:
    """Per-message 1-on-1 chat.

    For founding employees with a registered agent loop, the CEO message is
    pushed as a task to the agent's task board so the agent can use its tools
    (e.g. HR can call search_candidates via Boss Online MCP).

    For normal employees (no agent loop), falls back to a plain LLM call.
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    from onemancompany.agents.base import make_llm
    from onemancompany.core.agent_loop import get_agent_loop

    employee_id = body.get("employee_id", "")
    message = body.get("message", "")
    history = body.get("history", [])
    attachments = body.get("attachments", [])

    if not employee_id or not message:
        return {"error": "Missing employee_id or message"}

    emp = company_state.employees.get(employee_id)
    if not emp:
        return {"error": f"Employee '{employee_id}' not found"}

    # Build attachment info string for prompt injection
    attach_info = ""
    if attachments:
        lines = [f"- 附件: {a.get('filename', 'file')} (保存在 {a.get('path', '')})" for a in attachments]
        attach_info = "\n\nCEO附带了以下文件:\n" + "\n".join(lines)

    # On first message (empty history), mark employee as in meeting
    if not history:
        emp.is_listening = True
        await event_bus.publish(
            CompanyEvent(
                type="guidance_start",
                payload={"employee_id": employee_id, "name": emp.name},
                agent="CEO",
            )
        )

    # --- Self-hosted path: relay CEO message directly to Claude session ---
    from onemancompany.core.config import employee_configs as _oneonone_cfgs
    _oneonone_cfg = _oneonone_cfgs.get(employee_id)
    if _oneonone_cfg and _oneonone_cfg.hosting == "self":
        from onemancompany.core.claude_session import run_claude_session

        # First message: give minimal context. Subsequent: just relay CEO's words.
        # Session memory handles the rest — let the employee be themselves.
        if not history:
            prompt = (
                f"你正在和公司CEO进行1-1会议。CEO对你说：\n\n{message}{attach_info}"
            )
        else:
            prompt = f"CEO: {message}{attach_info}"

        output = await run_claude_session(
            employee_id, "1-1",
            prompt=prompt,
            work_dir="",
            max_turns=10,
            timeout=120,
        )
        text = output["output"] if isinstance(output, dict) else output
        return {"response": text}

    # --- Agent path: founding employees with a registered agent loop ---
    loop = get_agent_loop(employee_id)
    if loop:
        context = ""
        if history:
            context = "以下是你和CEO的对话历史:\n" + "\n".join(
                f"{'CEO' if e.get('role') == 'ceo' else '你'}: {e['content']}"
                for e in history
            ) + "\n\n"
        task_desc = (
            f"[1-on-1 Meeting] CEO对你说:\n{context}"
            f"CEO: {message}{attach_info}\n\n"
            f"请回应CEO。如果CEO要求你执行某项操作（如招聘、搜索候选人等），请使用你的工具来完成。"
        )
        agent_task = loop.push_task(task_desc)
        # Wait for the task to complete (poll with timeout)
        import asyncio
        for _ in range(120):  # up to 60 seconds
            await asyncio.sleep(0.5)
            t = loop.board.get_task(agent_task.id)
            if t and t.status in ("complete", "failed", "finished"):
                break
        # Extract the result from logs
        t = loop.board.get_task(agent_task.id)
        if t:
            # Prefer the result field (set on task completion)
            if t.result:
                return {"response": str(t.result)}
            # Fallback: find the last llm_output in logs
            if t.logs:
                for log_entry in reversed(t.logs):
                    if log_entry.get("type") in ("llm_output", "result"):
                        return {"response": str(log_entry["content"])}
                return {"response": str(t.logs[-1].get("content", "（处理完成）"))}
        return {"response": "（任务已提交，正在处理中）"}

    # --- Fallback: plain LLM for normal employees without agent loop ---
    from onemancompany.agents.base import get_employee_skills_prompt, get_employee_tools_prompt, get_employee_talent_persona

    skills_str = ", ".join(emp.skills) if emp.skills else "general"
    persona_section = get_employee_talent_persona(employee_id)
    principles_section = f"\nYour work principles:\n{emp.work_principles}" if emp.work_principles else ""
    culture_items = company_state.company_culture
    culture_section = ""
    if culture_items:
        rules = "\n".join(f"  {i+1}. {item.get('content', '')}" for i, item in enumerate(culture_items))
        culture_section = f"\nCompany culture:\n{rules}"

    skills_section = get_employee_skills_prompt(employee_id)
    tools_section = get_employee_tools_prompt(employee_id)

    system_prompt = (
        f"You are {emp.name} ({emp.nickname}), a {emp.role} in {emp.department}. "
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
    result = await tracked_ainvoke(llm, messages, category="oneonone", employee_id=employee_id)

    content = result.content
    # Normalize content — some models return list of content blocks
    if isinstance(content, list):
        content = "\n".join(
            c.get("text", str(c)) if isinstance(c, dict) else str(c)
            for c in content
        )
    return {"response": content or ""}


@router.post("/api/oneonone/end")
async def oneonone_end(body: dict) -> dict:
    """End meeting. LLM reflects on transcript, updates work principles, and saves 1-1 note."""
    from datetime import datetime

    from langchain_core.messages import HumanMessage, SystemMessage

    from onemancompany.agents.base import make_llm
    from onemancompany.core.config import save_employee_guidance, save_work_principles

    employee_id = body.get("employee_id", "")
    history = body.get("history", [])

    if not employee_id:
        return {"error": "Missing employee_id"}

    emp = company_state.employees.get(employee_id)
    if not emp:
        return {"error": f"Employee '{employee_id}' not found"}

    principles_updated = False
    note_saved = False

    if history:
        # Build transcript
        transcript_lines = []
        for entry in history:
            speaker = "CEO" if entry.get("role") == "ceo" else emp.name
            transcript_lines.append(f"{speaker}: {entry['content']}")
        transcript = "\n".join(transcript_lines)

        current_principles = emp.work_principles or "(No work principles yet)"

        # Combined prompt: reflect on principles AND generate 1-1 summary
        reflection_prompt = (
            f"You are {emp.name} ({emp.nickname}, {emp.role}, Department: {emp.department}).\n\n"
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

        llm = make_llm(employee_id)
        result = await tracked_ainvoke(llm, [
            SystemMessage(content="You are an employee reflecting on a meeting with the CEO."),
            HumanMessage(content=reflection_prompt),
        ], category="oneonone", employee_id=employee_id)
        response_text = result.content.strip()

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
                emp.work_principles = new_principles
                save_work_principles(employee_id, new_principles)
                principles_updated = True

        # Parse and save 1-1 summary as guidance note
        if "SUMMARY:" in response_text:
            summary_start = response_text.index("SUMMARY:") + len("SUMMARY:")
            summary_text = response_text[summary_start:].strip()
            if summary_text:
                date_str = datetime.now().strftime("%Y-%m-%d")
                note = f"**{date_str} 1-1 Meeting**\n{summary_text}"
                emp.guidance_notes.append(note)
                save_employee_guidance(employee_id, emp.guidance_notes)
                note_saved = True

    # End the meeting
    emp.is_listening = False
    await event_bus.publish(
        CompanyEvent(
            type="guidance_end",
            payload={
                "employee_id": employee_id,
                "name": emp.name,
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
        "meeting_rooms": [m.to_dict() for m in company_state.meeting_rooms.values()]
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
        loop.push_task(task)
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
    company_state.activity_log.append({
        "type": "meeting_released",
        "room": room.name,
        "participants": old_participants,
    })
    await event_bus.publish(
        CompanyEvent(
            type="meeting_released",
            payload={"room_id": room_id, "room_name": room.name},
            agent="COO",
        )
    )
    return {"status": "released", "room_name": room.name}


# ===== Inquiry Chat Endpoints =====

@router.post("/api/inquiry/chat")
async def inquiry_chat(body: dict) -> dict:
    """CEO sends a follow-up message in an active inquiry session."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    from onemancompany.agents.base import make_llm

    session_id = body.get("session_id", "")
    message = body.get("message", "")

    if not session_id or not message:
        return {"error": "Missing session_id or message"}

    session = _inquiry_sessions.get(session_id)
    if not session:
        return {"error": "Inquiry session not found"}

    _inquiry_agent_map = {"HR": HR_ID, "COO": COO_ID, "CSO": CSO_ID, "EA": EA_ID}
    agent_id = _inquiry_agent_map.get(session.agent_role, COO_ID)
    emp = company_state.employees.get(agent_id)
    speaker_name = emp.name if emp else session.agent_role

    # Publish CEO message as meeting_chat
    await event_bus.publish(
        CompanyEvent(
            type="meeting_chat",
            payload={"room_id": session.room_id, "speaker": "CEO", "role": "CEO", "message": message},
            agent="CEO",
        )
    )

    # Build LangChain messages from session history
    system_prompt = getattr(session, "_system_prompt", f"You are the company {session.agent_role}.")
    messages = [SystemMessage(content=system_prompt)]
    for entry in session.history:
        if entry["role"] == "ceo":
            messages.append(HumanMessage(content=entry["content"]))
        else:
            messages.append(AIMessage(content=entry["content"]))
    messages.append(HumanMessage(content=message))

    llm = make_llm(agent_id)
    result = await tracked_ainvoke(llm, messages, category="inquiry", employee_id=agent_id)
    answer = result.content

    # Publish agent response as meeting_chat
    await event_bus.publish(
        CompanyEvent(
            type="meeting_chat",
            payload={"room_id": session.room_id, "speaker": speaker_name, "role": session.agent_role, "message": answer},
            agent=session.agent_role,
        )
    )

    # Update session history
    session.history.append({"role": "ceo", "speaker": "CEO", "content": message})
    session.history.append({"role": "agent", "speaker": speaker_name, "content": answer})

    return {"response": answer, "speaker": speaker_name}


@router.post("/api/inquiry/end")
async def inquiry_end(body: dict) -> dict:
    """End an inquiry session: release room, save CEO questions as guidance."""
    session_id = body.get("session_id", "")

    if not session_id:
        return {"error": "Missing session_id"}

    session = _inquiry_sessions.get(session_id)
    if not session:
        return {"error": "Inquiry session not found"}

    _inquiry_agent_map = {"HR": HR_ID, "COO": COO_ID, "CSO": CSO_ID, "EA": EA_ID}
    agent_id = _inquiry_agent_map.get(session.agent_role, COO_ID)

    # Release the meeting room
    room = company_state.meeting_rooms.get(session.room_id)
    if room and room.is_booked:
        room.is_booked = False
        room.booked_by = ""
        room.participants = []
        await event_bus.publish(
            CompanyEvent(
                type="meeting_released",
                payload={"room_id": room.id, "room_name": room.name},
                agent="CEO",
            )
        )

    # Publish inquiry_ended event
    await event_bus.publish(
        CompanyEvent(
            type="inquiry_ended",
            payload={"session_id": session_id, "room_id": session.room_id, "agent_role": session.agent_role},
            agent="CEO",
        )
    )

    # Remove session
    _inquiry_sessions.pop(session_id, None)

    # Broadcast state
    await event_bus.publish(
        CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
    )

    return {"status": "ended", "session_id": session_id}


@router.post("/api/hr/review")
async def trigger_hr_review() -> dict:
    from onemancompany.core.agent_loop import get_agent_loop

    loop = get_agent_loop(HR_ID)
    if loop:
        # Build review task description inline (same logic as run_quarterly_review)
        from onemancompany.core.config import TASKS_PER_QUARTER
        from onemancompany.core.state import LEVEL_NAMES

        reviewable, not_ready = [], []
        for e in company_state.employees.values():
            hist_str = ", ".join(
                f"Q{i+1}={h['score']}" for i, h in enumerate(e.performance_history)
            ) or "no history"
            info = (
                f"- {e.name} (花名: {e.nickname}, ID: {e.id}, "
                f"Title: {e.title}, Lv.{e.level} {LEVEL_NAMES.get(e.level, '')}, "
                f"Q tasks: {e.current_quarter_tasks}/3, "
                f"Performance history: [{hist_str}])"
            )
            if e.current_quarter_tasks >= TASKS_PER_QUARTER:
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
        loop.push_task(review_task)
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
            type="workflow_updated",
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

    result = emp.to_dict()
    result["llm_model"] = llm_model
    result["api_provider"] = api_provider
    result["api_key_set"] = bool(api_key)
    result["api_key_preview"] = ("..." + api_key[-4:]) if len(api_key) >= 4 else ""
    result["hosting"] = cfg.hosting if cfg else "company"
    result["auth_method"] = cfg.auth_method if cfg else "api_key"
    result["oauth_logged_in"] = bool(cfg.api_key) if cfg and cfg.auth_method == "oauth" else False
    result["tool_permissions"] = list(cfg.tool_permissions) if cfg and cfg.tool_permissions else []

    # Include manifest if available
    manifest = load_manifest(employee_id)
    if manifest:
        result["manifest"] = manifest

    # Include custom settings (target_email, polling_interval, etc.)
    from onemancompany.core.config import load_custom_settings
    custom = load_custom_settings(employee_id)
    result.update(custom)

    if cfg and cfg.hosting == "self":
        from onemancompany.core.claude_session import list_sessions
        result["sessions"] = list_sessions(employee_id)

    return result


@router.post("/api/employee/{employee_id}/fire")
async def fire_employee(employee_id: str, body: dict) -> dict:
    """Fire an employee directly (CEO action). Cannot fire founding employees."""
    from onemancompany.agents.termination import execute_fire

    reason = body.get("reason", "CEO decision")
    result = await execute_fire(employee_id, reason)
    if "error" in result:
        return result
    result["state"] = company_state.to_json()
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
    emp = company_state.employees.get(employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return {"employee_id": employee_id, "okrs": emp.okrs}


@router.put("/api/employee/{employee_id}/okrs")
async def update_employee_okrs(employee_id: str, body: dict) -> dict:
    """Update OKRs for an employee."""
    from onemancompany.core.config import update_employee_field

    emp = company_state.employees.get(employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    okrs = body.get("okrs", [])
    emp.okrs = okrs
    update_employee_field(employee_id, "okrs", okrs)

    await event_bus.publish(CompanyEvent(
        type="okr_updated",
        payload={"employee_id": employee_id, "okrs": okrs},
        agent="CEO",
    ))

    return {"employee_id": employee_id, "okrs": okrs}


@router.get("/api/employee/{employee_id}/taskboard")
async def get_employee_taskboard(employee_id: str) -> dict:
    """Get an agent's task board (per-agent tasks, not company-level)."""
    from onemancompany.core.agent_loop import get_agent_loop

    loop = get_agent_loop(employee_id)
    if not loop:
        return {"tasks": []}
    return {"tasks": loop.board.to_dict()}


@router.get("/api/employee/{employee_id}/logs")
async def get_employee_logs(employee_id: str) -> dict:
    """Get execution logs for the agent's current (or most recent) task."""
    from onemancompany.core.agent_loop import get_agent_loop

    loop = get_agent_loop(employee_id)
    if not loop:
        return {"logs": []}
    # Return current task logs, or the last completed task's logs
    if loop._current_task:
        return {"logs": loop._current_task.logs[-50:]}
    # Find most recent task with logs
    for task in reversed(loop.board.tasks):
        if task.logs:
            return {"logs": task.logs[-50:]}
    return {"logs": []}


async def _sync_tree_cancel(cancelled_tasks: list) -> None:
    """Update task tree nodes for cancelled tasks."""
    from onemancompany.core.vessel import _load_project_tree, _node_id_for_task

    # Group by project_dir for efficiency
    trees: dict[str, object] = {}
    for task in cancelled_tasks:
        if not task.project_dir:
            continue
        if task.project_dir not in trees:
            trees[task.project_dir] = _load_project_tree(task.project_dir)
        tree = trees[task.project_dir]
        if not tree:
            continue
        node_id = _node_id_for_task(tree, task.id)
        if node_id:
            node = tree.get_node(node_id)
            if node and node.status not in ("accepted", "failed", "cancelled"):
                node.status = "cancelled"
                node.result = task.result or "Cancelled by CEO"
                from onemancompany.core.events import CompanyEvent as _CE
                await event_bus.publish(_CE(
                    type="tree_update",
                    payload={"project_id": tree.project_id, "event_type": "node_updated",
                             "node_id": node_id, "data": {"status": "cancelled"}},
                    agent="SYSTEM",
                ))
    # Save modified trees
    from pathlib import Path
    for pdir, tree in trees.items():
        if tree:
            tree.save(Path(pdir) / "task_tree.yaml")


@router.post("/api/task/{project_id}/abort")
async def abort_task(project_id: str) -> dict:
    """Abort all agent tasks related to a project.

    Cancels pending/in-progress tasks on all agent boards, cancels running
    asyncio tasks, removes from company active_tasks, and broadcasts state update.
    """
    from onemancompany.core.agent_loop import employee_manager

    cancelled_tasks = employee_manager.abort_project(project_id)

    # Update tree nodes
    await _sync_tree_cancel(cancelled_tasks)

    # Broadcast state
    await event_bus.publish(
        CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
    )

    return {"status": "ok", "cancelled": len(cancelled_tasks)}


@router.post("/api/employee/{employee_id}/task/{task_id}/cancel")
async def cancel_agent_task(employee_id: str, task_id: str) -> dict:
    """Cancel a specific task (or sub-task) on an agent's board."""
    from datetime import datetime

    from onemancompany.core.agent_loop import employee_manager, get_agent_loop

    loop = get_agent_loop(employee_id)
    if not loop:
        return {"status": "error", "message": "Agent not found"}

    task = loop.board.get_task(task_id)
    if not task:
        return {"status": "error", "message": "Task not found"}

    if task.status not in ("pending", "processing", "holding"):
        return {"status": "error", "message": f"Task already {task.status}"}

    was_in_progress = task.status == "processing"

    from onemancompany.core.task_persistence import persist_task, archive_task

    # Cancel target task
    all_cancelled = [task]
    task.status = "cancelled"
    task.completed_at = datetime.now().isoformat()
    task.result = "Cancelled by CEO"
    persist_task(employee_id, task)
    archive_task(employee_id, task)
    employee_manager._log(employee_id, task, "cancelled", "CEO cancelled this task")
    employee_manager._publish_task_update(employee_id, task)

    # Stop any holding watchdog crons associated with this task
    from onemancompany.core.automation import stop_cron
    stop_cron(employee_id, f"reply_{task_id}")
    stop_cron(employee_id, f"holding_{task_id}")

    # Cancel the running asyncio.Task if this was in_progress
    if was_in_progress and employee_id in employee_manager._running_tasks:
        running = employee_manager._running_tasks[employee_id]
        if not running.done():
            running.cancel()

    # Reset employee status if no more active tasks
    board = employee_manager.boards.get(employee_id)
    has_active = False
    if board:
        has_active = any(
            t.status in ("pending", "processing", "holding")
            for t in board.tasks
        )
    if not has_active:
        emp = company_state.employees.get(employee_id)
        if emp:
            emp.status = STATUS_IDLE
            emp.current_task_summary = ""

    # Update tree nodes for cancelled tasks
    await _sync_tree_cancel(all_cancelled)

    # Broadcast state
    await event_bus.publish(
        CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
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

    # Compute new salary — skip OpenRouter pricing for non-openrouter providers
    cfg = employee_configs.get(employee_id)
    api_provider = cfg.api_provider if cfg else "openrouter"
    if api_provider == "openrouter":
        from onemancompany.core.model_costs import compute_salary
        new_salary = compute_salary(model_id)
    else:
        new_salary = cfg.salary_per_1m_tokens if cfg else 0.0

    # Update in-memory config
    if cfg:
        cfg.llm_model = model_id
        cfg.salary_per_1m_tokens = new_salary

    # Update in-memory employee state
    emp.salary_per_1m_tokens = new_salary

    # Update profile.yaml on disk
    from onemancompany.core.config import update_employee_profile
    update_employee_profile(employee_id, {"llm_model": model_id, "salary_per_1m_tokens": new_salary})

    await event_bus.publish(
        CompanyEvent(
            type="agent_done",
            payload={
                "role": "CEO",
                "summary": f"Updated {emp.name} ({emp.nickname})'s model to {model_id}, salary=${new_salary}/1M",
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
    if new_hosting not in ("company", "self"):
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

    # Update profile.yaml on disk
    from onemancompany.core.config import load_employee_profile_yaml, save_employee_profile_yaml
    profile_data = load_employee_profile_yaml(employee_id)
    if profile_data:
        profile_data["hosting"] = new_hosting
        if new_hosting == "self":
            profile_data.setdefault("api_provider", "anthropic")
            # Self-hosted uses Claude CLI auth, not OAuth
            profile_data.pop("auth_method", None)
            cfg.auth_method = "api_key"  # reset in-memory
        save_employee_profile_yaml(employee_id, profile_data)

    # Update manifest.json to reflect hosting change and adjust settings sections
    manifest_path = EMPLOYEES_DIR / employee_id / "manifest.json"
    if manifest_path.exists():
        manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["hosting"] = new_hosting

        sections = manifest.get("settings", {}).get("sections", [])

        if new_hosting == "self":
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

        manifest_path.write_text(_json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        invalidate_manifest_cache(employee_id)

    hosting_label = "Self-hosted (Claude Code)" if new_hosting == "self" else "Company-hosted (LangChain)"
    await event_bus.publish(
        CompanyEvent(
            type="agent_done",
            payload={
                "role": "CEO",
                "summary": f"Switched {emp.name} to {hosting_label}. Restart required to take effect.",
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


@router.put("/api/employee/{employee_id}/provider")
async def update_employee_provider(employee_id: str, body: dict) -> dict:
    """Switch the API provider for an employee (openrouter / anthropic)."""
    import yaml

    from onemancompany.core.config import EMPLOYEES_DIR, employee_configs

    new_provider = body.get("api_provider", "")
    if new_provider not in ("openrouter", "anthropic"):
        return {"error": "Invalid provider. Use 'openrouter' or 'anthropic'."}

    emp = _require_employee(employee_id)

    cfg = employee_configs.get(employee_id)
    if not cfg:
        return {"error": "Employee config not found"}

    old_provider = cfg.api_provider
    cfg.api_provider = new_provider
    emp.api_provider = new_provider

    # When switching to anthropic, use company-level key if employee has none
    from onemancompany.core.config import settings as app_settings
    if new_provider == "anthropic" and not cfg.api_key:
        cfg.api_key = app_settings.anthropic_api_key
        cfg.auth_method = app_settings.anthropic_auth_method

    # Update profile.yaml
    from onemancompany.core.config import load_employee_profile_yaml, save_employee_profile_yaml
    profile_data = load_employee_profile_yaml(employee_id)
    if profile_data:
        profile_data["api_provider"] = new_provider
        if new_provider == "anthropic" and not profile_data.get("api_key"):
            profile_data["api_key"] = app_settings.anthropic_api_key
            profile_data["auth_method"] = app_settings.anthropic_auth_method
        save_employee_profile_yaml(employee_id, profile_data)

    # Rebuild agent LLM
    _rebuild_employee_agent(employee_id)

    await event_bus.publish(
        CompanyEvent(
            type="agent_done",
            payload={
                "role": "CEO",
                "summary": f"Switched {emp.name}'s provider: {old_provider} → {new_provider}",
            },
            agent="CEO",
        )
    )

    return {
        "status": "updated",
        "employee_id": employee_id,
        "api_provider": new_provider,
        "api_key_set": bool(cfg.api_key),
        "model": cfg.llm_model,
    }


@router.put("/api/employee/{employee_id}/api-key")
async def update_employee_api_key(employee_id: str, body: dict) -> dict:
    """Update the API key (and optionally model) for a custom-provider employee."""
    import yaml

    from onemancompany.core.config import EMPLOYEES_DIR, employee_configs

    emp = _require_employee(employee_id)

    cfg = employee_configs.get(employee_id)
    if not cfg:
        return {"error": "Employee config not found"}

    if cfg.api_provider == "openrouter":
        return {"error": "This employee uses OpenRouter — API key is not applicable"}

    new_key = body.get("api_key", "")
    new_model = body.get("model", "")

    # Update in-memory config
    cfg.api_key = new_key
    if new_model:
        cfg.llm_model = new_model

    # Update profile.yaml on disk
    from onemancompany.core.config import update_employee_profile
    updates = {"api_key": new_key}
    if new_model:
        updates["llm_model"] = new_model
    update_employee_profile(employee_id, updates)

    # If an agent loop exists, rebuild its LLM so the new key takes effect
    _rebuild_employee_agent(employee_id)

    await event_bus.publish(
        CompanyEvent(
            type="agent_done",
            payload={
                "role": "CEO",
                "summary": f"Updated {emp.name}'s API key ({cfg.api_provider})"
                           + (f", model={new_model}" if new_model else ""),
            },
            agent="CEO",
        )
    )

    return {
        "status": "updated",
        "employee_id": employee_id,
        "api_provider": cfg.api_provider,
        "api_key_set": bool(new_key),
        "model": cfg.llm_model,
        "hosting": cfg.hosting,
    }


# ===== Global API Settings =====


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
        APP_CONFIG_PATH.write_text(yaml.dump(config, default_flow_style=False, allow_unicode=True), encoding="utf-8")
        reload_app_config()

        # Reconnect Boss Online with new API key
        try:
            from onemancompany.agents.recruitment import stop_boss_online, start_boss_online
            await stop_boss_online()
            await start_boss_online()
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
    """Test connectivity for a given API provider."""
    from onemancompany.core.heartbeat import _check_anthropic_key, _check_openrouter_key
    from onemancompany.core.config import settings

    provider = body.get("provider", "")
    if provider == "openrouter":
        ok = await _check_openrouter_key()
        return {"provider": "openrouter", "ok": ok}
    elif provider == "anthropic":
        ok = await _check_anthropic_key(settings.anthropic_api_key)
        return {"provider": "anthropic", "ok": ok}
    return {"error": "Invalid provider"}


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

ANTHROPIC_OAUTH_CLIENT_ID = "8ccecd22-59d4-4db0-a971-530cf734fd17"
ANTHROPIC_AUTH_URL = "https://api.anthropic.com/authorize"
ANTHROPIC_TOKEN_URL = "https://api.anthropic.com/token"
ANTHROPIC_REDIRECT_URI = "http://localhost:8000/api/oauth/callback"
ANTHROPIC_CREATE_KEY_URL = "https://api.anthropic.com/api/oauth/claude_cli/create_api_key"


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
    if not cfg or cfg.auth_method != "oauth":
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

        from onemancompany.core.config import update_employee_profile
        update_employee_profile(employee_id, {
            "api_key": api_key,
            "oauth_refresh_token": tokens.get("refresh_token", ""),
        })

    emp = company_state.employees.get(employee_id)
    emp_name = emp.name if emp else employee_id

    await event_bus.publish(
        CompanyEvent(
            type="agent_done",
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
                type="agent_done",
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

        # Persist to profile.yaml
        from onemancompany.core.config import update_employee_profile
        update_employee_profile(employee_id, {
            "api_key": api_key,
            "oauth_refresh_token": refresh_token,
        })

    emp = company_state.employees.get(employee_id)
    emp_name = emp.name if emp else employee_id

    await event_bus.publish(
        CompanyEvent(
            type="agent_done",
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

    # Persist
    from onemancompany.core.config import update_employee_profile
    update_employee_profile(employee_id, {
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
    if not cfg or cfg.hosting != "self":
        return {"error": "Employee is not self-hosted"}

    return {"employee_id": employee_id, "sessions": list_sessions(employee_id)}


@router.delete("/api/employee/{employee_id}/sessions/{project_id:path}")
async def delete_employee_session(employee_id: str, project_id: str) -> dict:
    """Clean up a session record for a completed project."""
    from onemancompany.core.config import employee_configs
    from onemancompany.core.claude_session import cleanup_session

    cfg = employee_configs.get(employee_id)
    if not cfg or cfg.hosting != "self":
        return {"error": "Employee is not self-hosted"}

    cleanup_session(employee_id, project_id)
    return {"status": "ok", "employee_id": employee_id, "project_id": project_id}


# ===== Company Culture =====

@router.get("/api/company-culture")
async def get_company_culture() -> dict:
    """Get all company culture items."""
    return {"items": company_state.company_culture}


@router.post("/api/company-culture")
async def add_culture_item(body: dict) -> dict:
    """CEO adds a new item to the company culture. Applies to all employees."""
    from datetime import datetime

    from onemancompany.core.config import save_company_culture

    content = body.get("content", "").strip()
    if not content:
        return {"error": "Missing content"}

    item = {
        "content": content,
        "created_at": datetime.now().isoformat(),
    }
    company_state.company_culture.append(item)
    save_company_culture(company_state.company_culture)

    await event_bus.publish(
        CompanyEvent(
            type="company_culture_updated",
            payload={"item": item, "total": len(company_state.company_culture)},
            agent="CEO",
        )
    )
    return {"status": "added", "item": item, "total": len(company_state.company_culture)}


@router.delete("/api/company-culture/{index}")
async def remove_culture_item(index: int) -> dict:
    """CEO removes a company culture item by index."""
    from onemancompany.core.config import save_company_culture

    if index < 0 or index >= len(company_state.company_culture):
        return {"error": "Invalid index"}

    removed = company_state.company_culture.pop(index)
    save_company_culture(company_state.company_culture)

    await event_bus.publish(
        CompanyEvent(
            type="company_culture_updated",
            payload={"removed": removed, "total": len(company_state.company_culture)},
            agent="CEO",
        )
    )
    return {"status": "removed", "removed": removed}


# ===== Company Direction =====

@router.get("/api/company/direction")
async def get_company_direction() -> dict:
    """Get the current company direction/strategy."""
    return {"direction": company_state.company_direction}


@router.put("/api/company/direction")
async def update_company_direction(body: dict) -> dict:
    """CEO updates the company direction/strategy."""
    from onemancompany.core.config import save_company_direction

    direction = body.get("direction", "")
    company_state.company_direction = direction
    save_company_direction(direction)

    await event_bus.publish(
        CompanyEvent(
            type="company_direction_updated",
            payload={"direction": direction},
            agent="CEO",
        )
    )
    return {"status": "ok", "direction": direction}


# ===== File Upload (CEO multimodal) =====

@router.post("/api/upload")
async def upload_file(file: UploadFile) -> dict:
    """Save uploaded file, return path and metadata."""
    from datetime import datetime
    from uuid import uuid4

    upload_dir = COMPANY_DIR / "uploads" / datetime.now().strftime("%Y%m%d")
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{uuid4().hex[:8]}_{file.filename}"
    content = await file.read()
    dest.write_bytes(content)
    return {
        "path": str(dest),
        "filename": file.filename,
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
            type="file_edit_applied",
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
            type="file_edit_rejected",
            payload={"edit_id": edit_id, "rel_path": result["rel_path"]},
            agent="CEO",
        )
    )
    return result


# ===== Resolutions (Batch File-Edit Review) =====

@router.get("/api/resolutions/deferred")
async def get_deferred_edits() -> dict:
    """List all deferred edits across all resolutions."""
    from onemancompany.core.resolutions import list_deferred_edits
    return {"edits": list_deferred_edits()}


@router.get("/api/resolutions")
async def get_resolutions() -> dict:
    """List all resolutions (summary view)."""
    from onemancompany.core.resolutions import list_resolutions
    return {"resolutions": list_resolutions()}


@router.get("/api/resolutions/{resolution_id}")
async def get_resolution_detail(resolution_id: str) -> dict:
    """Get full resolution detail including all edits."""
    from onemancompany.core.resolutions import load_resolution
    data = load_resolution(resolution_id)
    if not data:
        return {"error": "Resolution not found"}
    return data


@router.post("/api/resolutions/{resolution_id}/decide")
async def decide_on_resolution(resolution_id: str, body: dict) -> dict:
    """CEO submits decisions for each edit in a resolution.

    Body: { "decisions": { "edit_id": "approve"|"reject"|"defer", ... } }
    """
    from onemancompany.core.resolutions import decide_resolution

    decisions = body.get("decisions", {})
    if not decisions:
        return {"error": "No decisions provided"}

    result = decide_resolution(resolution_id, decisions)
    if result.get("status") == "ok":
        await event_bus.publish(
            CompanyEvent(
                type="resolution_decided",
                payload={"resolution_id": resolution_id, "results": result.get("results", [])},
                agent="CEO",
            )
        )
    return result


@router.post("/api/resolutions/deferred/{resolution_id}/{edit_id}/execute")
async def execute_deferred(resolution_id: str, edit_id: str) -> dict:
    """Execute a previously deferred edit (checks MD5 staleness)."""
    from onemancompany.core.resolutions import execute_deferred_edit

    result = execute_deferred_edit(resolution_id, edit_id)
    if result.get("status") == "ok":
        await event_bus.publish(
            CompanyEvent(
                type="file_edit_applied",
                payload={
                    "edit_id": edit_id,
                    "rel_path": result.get("rel_path", ""),
                    "backup_path": result.get("backup_path"),
                },
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
    oh = company_state.overhead_costs
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
    """List all named projects (v2)."""
    from onemancompany.core.project_archive import list_named_projects
    return {"projects": list_named_projects()}


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
            # Resolve files for this iteration
            iter_project_dir = iter_doc.get("project_dir", "")
            iter_files = []
            if iter_project_dir:
                ws = Path(iter_project_dir)
                if ws.is_dir():
                    iter_files = [
                        str(p.relative_to(ws))
                        for p in sorted(ws.rglob("*"))
                        if p.is_file()
                    ]
            iterations.append({
                "iteration_id": iter_doc.get("iteration_id", iter_id),
                "task": iter_doc.get("task", ""),
                "status": iter_doc.get("status", ""),
                "created_at": iter_doc.get("created_at", ""),
                "completed_at": iter_doc.get("completed_at"),
                "current_owner": iter_doc.get("current_owner", ""),
                "cost_usd": round(iter_cost, 4),
                "project_dir": iter_project_dir,
                "files": iter_files,
            })
    proj["iteration_details"] = iterations
    proj["total_cost_usd"] = round(total_cost_usd, 4)
    return proj


@router.post("/api/projects/{project_id}/archive")
async def archive_project_endpoint(project_id: str) -> dict:
    """Archive a named project."""
    from onemancompany.core.project_archive import archive_project, load_named_project
    proj = load_named_project(project_id)
    if not proj:
        return {"error": "Named project not found"}
    archive_project(project_id)
    return {"status": "archived", "project_id": project_id}


@router.post("/api/projects/continue")
async def continue_iteration(body: dict) -> dict:
    """Continue an existing iteration without creating a new one.

    Pushes a continuation task to the responsible officer (COO) with
    the original task, acceptance criteria, and last feedback.
    """
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

    if doc.get("status") == "completed":
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
        status = "通过" if acceptance_result.get("accepted") else "未通过"
        feedback_lines.append(f"上次验收结果: {status}")
        if acceptance_result.get("notes"):
            feedback_lines.append(f"验收备注: {acceptance_result['notes']}")
    if ea_review_result:
        status = "通过" if ea_review_result.get("approved") else "驳回"
        feedback_lines.append(f"EA审核: {status}")
        if ea_review_result.get("notes"):
            feedback_lines.append(f"EA备注: {ea_review_result['notes']}")
    feedback_text = "\n".join(feedback_lines) if feedback_lines else "（无上轮反馈）"

    criteria_text = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(criteria)) if criteria else "（未设置验收标准）"

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
        f"CEO要求继续推进当前轮次（不创建新迭代）\n\n"
        f"原始任务: {task}\n\n"
        f"验收标准:\n{criteria_text}\n\n"
        f"上轮反馈:\n{feedback_text}\n\n"
        f"请根据以上信息分析未完成或需改进的部分，然后分派给合适的负责人执行。\n\n"
        f"[Project ID: {ctx_id}] [Project workspace: {project_dir}]"
    )

    # Route to EA (same as initial task flow) to ensure full task tree activation
    loop = get_agent_loop(EA_ID)
    if not loop:
        return {"error": f"No agent loop for EA {EA_ID}"}

    ea_agent_task = loop.push_task(continuation_task, project_id=ctx_id, project_dir=project_dir)

    # Initialize a new task tree so the full dispatch chain is tracked
    try:
        from onemancompany.core.task_tree import TaskTree
        from onemancompany.core.vessel import _save_project_tree
        tree = TaskTree(project_id=ctx_id)
        root = tree.create_root(employee_id=EA_ID, description=f"[续做] {task[:80]}")
        tree.task_id_map[ea_agent_task.id] = root.id
        _save_project_tree(project_dir, tree)
    except Exception as e:
        logger.error("Failed to initialize continuation task tree: {}", e)

    # Log the action
    append_action(iteration_id, CEO_ID, "continue", f"CEO requested continuation of current iteration")

    await event_bus.publish(
        CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
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
            emp = company_state.employees.get(emp_id)
            if emp:
                employees[emp_id] = {"name": emp.name, "nickname": emp.nickname}
    return {"employees": employees, "project_id": project_id, "project_status": project_status}


@router.get("/api/projects/{project_id}/board")
async def get_project_board(project_id: str) -> dict:
    """Get kanban board data — backward-compatible wrapper over plugin transformers."""
    from onemancompany.core.plugin_registry import plugin_registry
    from onemancompany.core.project_archive import _resolve_and_load

    version, doc, key = _resolve_and_load(project_id)
    if not doc:
        raise HTTPException(404, "Project not found")

    dispatches = doc.get("dispatches", [])
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
    from onemancompany.core.task_tree import TaskTree

    project_dir = get_project_dir(project_id)
    if not project_dir:
        return None
    path = Path(project_dir) / "task_tree.yaml"
    if not path.exists():
        return None
    tree = TaskTree.load(path, project_id=project_id)
    nodes = list(tree._nodes.values())
    if not nodes:
        return None

    total = len(nodes)
    by_status: dict[str, int] = {}
    for n in nodes:
        by_status[n.status] = by_status.get(n.status, 0) + 1

    terminal = sum(by_status.get(s, 0) for s in ("accepted", "failed", "cancelled"))
    processing = by_status.get("processing", 0)
    completed = by_status.get("completed", 0)

    # Collect actively working nodes (non-terminal)
    active_nodes = []
    has_children = total > 1
    for n in nodes:
        # For multi-node trees, skip root (it's the coordinator)
        if has_children and n.id == tree.root_id:
            continue
        if n.status in ("processing", "completed", "pending"):
            active_nodes.append({
                "id": n.id,
                "employee_id": n.employee_id,
                "description": n.description[:80],
                "status": n.status,
            })

    # Root node result for completed tasks
    root_node = tree.get_node(tree.root_id)
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
        tree = _tree_summary(p["project_id"])
        # Use tree-aggregated status when available (more accurate than project.yaml)
        file_status = _normalize_project_status(p.get("status", ""))
        tree_status = _aggregate_tree_status(tree)
        status = tree_status if tree_status else file_status

        entry = {
            "project_id": p["project_id"],
            "task": p.get("task", ""),
            "task_type": p.get("task_type", "simple"),
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


# Priority order for aggregating status across tree nodes.
# Lower index = higher priority (shown first).
_STATUS_PRIORITY = [
    "processing",   # actively running
    "holding",      # blocked/waiting
    "pending",      # queued
    "failed",       # error
    "cancelled",    # aborted
    "completed",    # done, not yet accepted
    "accepted",     # fully done
]


def _aggregate_tree_status(tree_summary: dict | None) -> str | None:
    """Derive project status from tree node statuses.

    Returns the highest-priority status found across all nodes,
    or None if no tree / no nodes.
    """
    if not tree_summary:
        return None
    by_status = tree_summary.get("by_status", {})
    if not by_status:
        return None
    for status in _STATUS_PRIORITY:
        if by_status.get(status, 0) > 0:
            return status
    return None


# ===== Task Tree Endpoint =====


def _load_project_tree_for_api(project_id: str):
    """Load TaskTree for a project, trying known project directories."""
    from pathlib import Path

    from onemancompany.core.task_tree import TaskTree
    from onemancompany.core.project_archive import get_project_dir

    project_dir = get_project_dir(project_id)
    if not project_dir:
        return None
    path = Path(project_dir) / "task_tree.yaml"
    if not path.exists():
        return None
    return TaskTree.load(path, project_id=project_id)


def _has_avatar(employee_id: str) -> bool:
    """Check if an employee has an uploaded avatar."""
    from onemancompany.core.config import EMPLOYEES_DIR
    return (EMPLOYEES_DIR / employee_id / "avatar.png").exists()


@router.get("/api/projects/{project_id}/tree")
async def get_project_tree(project_id: str) -> dict:
    """Get the task tree for a project."""
    tree = _load_project_tree_for_api(project_id)
    if tree is None:
        raise HTTPException(status_code=404, detail="Task tree not found")

    # Build employee info lookup
    employee_info: dict[str, dict] = {}
    for node in tree._nodes.values():
        eid = node.employee_id
        if eid and eid not in employee_info:
            if eid == CEO_ID:
                employee_info[eid] = {
                    "name": "CEO",
                    "nickname": "CEO",
                    "role": "Chief Executive Officer",
                    "avatar_url": "",
                }
            else:
                emp = company_state.employees.get(eid)
                if emp:
                    employee_info[eid] = {
                        "name": getattr(emp, "name", ""),
                        "nickname": getattr(emp, "nickname", ""),
                        "role": getattr(emp, "role", ""),
                        "avatar_url": f"/api/employees/{eid}/avatar" if _has_avatar(eid) else "",
                    }

    nodes = []
    for n in tree._nodes.values():
        d = n.to_dict()
        # Compute dependency_status
        if n.depends_on:
            if n.status == "blocked":
                d["dependency_status"] = "blocked"
            elif tree.all_deps_terminal(n.id):
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
    """Serve an employee's avatar image."""
    from onemancompany.core.config import EMPLOYEES_DIR
    avatar_path = EMPLOYEES_DIR / employee_id / "avatar.png"
    if not avatar_path.exists():
        raise HTTPException(status_code=404, detail="No avatar")
    return FileResponse(avatar_path, media_type="image/png")


@router.get("/api/employees/{employee_id}/projects")
async def get_employee_projects(employee_id: str) -> list[dict]:
    """Get list of projects an employee participated in."""
    return _scan_employee_projects(employee_id)


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

    dispatches = doc.get("dispatches", [])
    ctx = _build_plugin_context(dispatches, project_id, doc.get("status", ""))

    return plugin_registry.transform(plugin_id, dispatches, ctx)


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
        content = target.read_text(encoding="utf-8", errors="replace")
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
        content = target.read_text(encoding="utf-8", errors="replace")
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
        for fpath in ws.rglob("*"):
            if fpath.is_file():
                zf.write(fpath, fpath.relative_to(ws))
    buf.seek(0)

    emp = company_state.employees.get(employee_id)
    name = emp.nickname if emp else employee_id
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}_workspace.zip"'},
    )


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
    return {"ex_employees": [e.to_dict() for e in company_state.ex_employees.values()]}


@router.post("/api/ex-employees/{employee_id}/rehire")
async def rehire_ex_employee(employee_id: str) -> dict:
    """Re-hire an ex-employee: move folder back and restore to active state."""
    from onemancompany.core.config import (
        load_employee_guidance,
        load_work_principles,
        move_ex_employee_back,
    )
    from onemancompany.core.state import Employee

    if employee_id not in company_state.ex_employees:
        return {"error": "Ex-employee not found"}

    ex_emp = company_state.ex_employees[employee_id]

    # Move folder back from ex-employees/ to employees/
    if not move_ex_employee_back(employee_id):
        return {"error": "Failed to move employee folder"}

    # Reload guidance and principles from restored folder
    guidance = load_employee_guidance(employee_id)
    principles = load_work_principles(employee_id)

    # Find next available desk position using department-based layout
    from onemancompany.core.layout import compute_layout, get_next_desk_for_department, persist_all_desk_positions
    is_remote = ex_emp.remote
    if is_remote:
        desk_pos = (-1, -1)
    else:
        desk_pos = get_next_desk_for_department(company_state, ex_emp.department)

    # Restore to active employees with reset performance
    emp = Employee(
        id=ex_emp.id,
        name=ex_emp.name,
        nickname=ex_emp.nickname,
        level=1,  # rehired employees start at level 1
        department=ex_emp.department,
        role=ex_emp.role,
        skills=ex_emp.skills,
        current_quarter_tasks=0,
        performance_history=[],
        desk_position=desk_pos,
        sprite=ex_emp.sprite,
        guidance_notes=guidance,
        work_principles=principles,
        remote=ex_emp.remote,
    )
    company_state.employees[employee_id] = emp
    del company_state.ex_employees[employee_id]

    # Recompute layout and persist all desk positions
    compute_layout(company_state)
    persist_all_desk_positions(company_state)

    # Register in EmployeeManager for on-site employees
    if not is_remote:
        from onemancompany.core.agent_loop import get_agent_loop, register_and_start_agent, register_self_hosted
        if not get_agent_loop(employee_id):
            from onemancompany.core.config import employee_configs as _rehire_cfgs
            _rehire_cfg = _rehire_cfgs.get(employee_id)
            if _rehire_cfg and _rehire_cfg.hosting == "self":
                register_self_hosted(employee_id)
            else:
                from onemancompany.agents.base import EmployeeAgent
                await register_and_start_agent(employee_id, EmployeeAgent(employee_id))

    company_state.activity_log.append({
        "type": "employee_rehired",
        "name": emp.name,
        "nickname": emp.nickname,
        "role": emp.role,
    })
    await event_bus.publish(
        CompanyEvent(
            type="employee_rehired",
            payload=emp.to_dict(),
            agent="CEO",
        )
    )

    return {
        "status": "rehired",
        "employee_id": employee_id,
        "name": emp.name,
        "state": company_state.to_json(),
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


# COO hiring context queue — populated when CEO approves a hiring request,
# consumed when hire_candidate() fires. Stores COO's requested role,
# department, and project info so we can override the talent's indicative
# role and correctly notify COO after the employee is ready.
_pending_coo_hire_queue: list[dict] = []

# Per-employee OAuth wait context — for OAuth employees, the hire completes
# only after login. This maps employee_id -> COO context so oauth_callback
# can notify COO.
_pending_oauth_hire: dict[str, dict] = {}


def _notify_coo_hire_ready(employee_id: str, ctx: dict) -> None:
    """Push a follow-up task to COO after a hired employee is fully ready.

    Marks the hiring dispatch as complete and gives COO the project context
    so it can dispatch_child() to the new employee.

    Args:
        employee_id: The newly hired employee's ID.
        ctx: COO hiring context dict with keys: request_id, role, department,
             project_id, project_dir, reason.
    """
    project_id = ctx.get("project_id", "")
    project_dir = ctx.get("project_dir", "")
    request_id = ctx.get("request_id", "")

    # Note: hiring dispatch tracking now handled by task tree

    from onemancompany.core.agent_loop import get_agent_loop
    coo_loop = get_agent_loop(COO_ID)
    if coo_loop:
        emp = company_state.employees.get(employee_id)
        emp_name = emp.name if emp else employee_id
        role = ctx.get("role", "Employee")
        followup = (
            f"新员工就绪通知\n\n"
            f"员工 {emp_name} (#{employee_id}) 已入职并准备就绪（{role}）。\n"
            f"请将项目相关任务 dispatch_child() 给该员工执行。\n\n"
            f"原始招聘原因: {ctx.get('reason', '')}\n"
            f"[Project ID: {project_id}] [Project workspace: {project_dir}]"
        )
        coo_loop.push_task(followup, project_id=project_id, project_dir=project_dir)
        logger.info(f"[hire-ready] Pushed follow-up to COO for {emp_name} on project {project_id}")
    else:
        logger.warning(f"[hire-ready] No COO agent loop — cannot notify about {employee_id}")


@router.post("/api/hiring-requests/{request_id}/decide")
async def decide_hiring_request(request_id: str, body: dict) -> dict:
    """CEO approves or rejects a hiring request from COO.

    Body: { "approved": true/false, "note": "optional comment" }
    If approved, HR automatically starts searching for candidates.
    """
    from onemancompany.agents.coo_agent import pending_hiring_requests

    req = pending_hiring_requests.pop(request_id, None)
    if not req:
        return {"error": f"Hiring request '{request_id}' not found"}

    approved = body.get("approved", False)
    note = body.get("note", "")

    await event_bus.publish(CompanyEvent(
        type="hiring_request_decided",
        payload={
            "request_id": request_id,
            "approved": approved,
            "role": req["role"],
            "note": note,
        },
        agent="CEO",
    ))

    project_id = req.get("project_id", "")
    project_dir = req.get("project_dir", "")
    hiring_dispatch_id = f"hiring_{request_id}"

    if approved:
        # Build JD from COO's request and push to HR's task board
        from onemancompany.core.agent_loop import get_agent_loop

        skills_str = ", ".join(req.get("desired_skills", []))
        jd = f"招聘 {req['role']}"
        if req.get("department"):
            jd += f"（部门: {req['department']}）"
        if skills_str:
            jd += f"（技能要求: {skills_str}）"
        jd += f"\n原因: {req['reason']}"
        if note:
            jd += f"\nCEO 备注: {note}"

        hr_loop = get_agent_loop(HR_ID)
        if hr_loop:
            hr_loop.push_task(jd)
        else:
            logger.warning("No agent loop for HR — hiring task dropped")

        # Queue COO context so hire_candidate() can override the talent's
        # indicative role with COO's requested role and department.
        _pending_coo_hire_queue.append({
            "request_id": request_id,
            "role": req["role"],
            "department": req.get("department", ""),
            "project_id": project_id,
            "project_dir": project_dir,
            "reason": req["reason"],
        })
        logger.info(f"[hiring] Queued COO context: role='{req['role']}' dept='{req.get('department', '')}' req={request_id}")
    else:
        # Rejected — no dispatch cleanup needed (task tree handles tracking)
        pass

    return {
        "status": "approved" if approved else "rejected",
        "request_id": request_id,
        "role": req["role"],
    }


# ===== Candidate Selection =====

@router.post("/api/candidates/hire")
async def hire_candidate(body: HireRequest) -> dict:
    """CEO selects a candidate to hire from the shortlist.

    Executes the full hire flow in code — no LLM involved.
    Reads authoritative fields directly from the talent profile.
    """
    from onemancompany.agents.hr_agent import pending_candidates
    from onemancompany.agents.onboarding import execute_hire

    candidates = pending_candidates.get(body.batch_id, [])
    candidate = next((c for c in candidates if c.get("id") == body.candidate_id), None)
    if not candidate:
        return {"error": "Candidate not found"}

    # Auto-generate wuxia-themed 花名 (nickname) if not provided
    nickname = body.nickname
    if not nickname:
        from onemancompany.agents.onboarding import generate_nickname
        nickname = await generate_nickname(candidate["name"], candidate["role"], is_founding=False)

    # Read authoritative fields from the talent profile (LLM shortlist may drop them)
    talent_id = candidate.get("talent_id", "") or candidate.get("id", "")
    talent_data: dict = {}
    if talent_id:
        from onemancompany.core.config import load_talent_profile
        talent_data = load_talent_profile(talent_id)

    skill_names = [s["name"] if isinstance(s, dict) else s for s in candidate.get("skill_set", [])]

    # Pop COO hiring context (FIFO) — overrides talent's indicative role
    # with what COO actually requested.
    coo_ctx: dict = {}
    if _pending_coo_hire_queue:
        coo_ctx = _pending_coo_hire_queue.pop(0)
        logger.info(f"[hiring] Applying COO context: role='{coo_ctx['role']}' over talent role='{candidate['role']}'")

    # Determine final role and department
    hire_role = coo_ctx.get("role") or candidate["role"]
    hire_department = coo_ctx.get("department", "")

    # Ensure COO's requested role is in the role mappings
    if coo_ctx.get("role"):
        from onemancompany.core.config import ROLE_DEPARTMENT_MAP
        from onemancompany.core.state import ROLE_TITLES
        if coo_ctx["role"] not in ROLE_TITLES:
            ROLE_TITLES[coo_ctx["role"]] = coo_ctx["role"]
            logger.info(f"[hiring] Added '{coo_ctx['role']}' to ROLE_TITLES")
        if coo_ctx["role"] not in ROLE_DEPARTMENT_MAP and hire_department:
            ROLE_DEPARTMENT_MAP[coo_ctx["role"]] = hire_department
            logger.info(f"[hiring] Added '{coo_ctx['role']}' → '{hire_department}' to ROLE_DEPARTMENT_MAP")

    try:
        emp = await execute_hire(
            name=candidate["name"],
            nickname=nickname or "",
            role=hire_role,
            skills=skill_names,
            talent_id=talent_id,
            llm_model=talent_data.get("llm_model", "") or candidate.get("llm_model", ""),
            temperature=float(talent_data.get("temperature", 0.7)),
            image_model=candidate.get("image_model", ""),
            api_provider=talent_data.get("api_provider", "openrouter") or candidate.get("api_provider", "openrouter"),
            hosting=talent_data.get("hosting", "company"),
            auth_method=talent_data.get("auth_method", "api_key"),
            sprite=candidate.get("sprite", "employee_default"),
            remote=candidate.get("remote", False),
            department=hire_department,
        )
    except Exception as e:
        traceback.print_exc()
        return {"error": f"Hire failed: {e!s}"}

    # Notify COO that the hire is ready (or stash for OAuth completion)
    if coo_ctx.get("project_id"):
        auth_method = talent_data.get("auth_method", "api_key")
        if auth_method == "oauth":
            _pending_oauth_hire[emp.id] = coo_ctx
            logger.info(f"[hiring] Stashed COO context for OAuth employee {emp.id}")
        else:
            _notify_coo_hire_ready(emp.id, coo_ctx)

    # Resume project lifecycle: stashed project_id is restored so
    # retrospective runs after the *actual* hire, not after shortlist.
    from onemancompany.agents.hr_agent import _pending_project_ctx
    from onemancompany.core.project_archive import append_action, complete_project
    ctx = _pending_project_ctx.pop(body.batch_id, {})
    pid = ctx.get("project_id", "")
    if pid:
        append_action(pid, HR_ID, "入职完成", f"{candidate['name']} 已入职，工号 {emp.id}")
        complete_project(pid, f"Hired {candidate['name']}")
        # No retrospective — hiring is not a project iteration.
        # Retrospective only triggers after project acceptance + rectification.

    pending_candidates.pop(body.batch_id, None)

    # Resume HR's HOLDING task so EA knows the hire is done
    from onemancompany.core.agent_loop import get_agent_loop
    hr_loop = get_agent_loop(HR_ID)
    if hr_loop:
        for t in hr_loop.board.tasks:
            if t.status == "holding" and t.result and f"batch_id={body.batch_id}" in t.result:
                await hr_loop.resume_held_task(HR_ID, t.id, f"Hired {candidate['name']} (ID: {emp.id})")
                break

    # Explicit state broadcast to ensure frontend updates
    await event_bus.publish(
        CompanyEvent(type="state_snapshot", payload={}, agent="CEO")
    )

    return {
        "status": "hired",
        "employee_id": emp.id,
        "name": candidate["name"],
        "nickname": nickname,
        "state": company_state.to_json(),
    }


@router.post("/api/candidates/batch-hire")
async def batch_hire_candidates(body: dict) -> dict:
    """Batch hire multiple candidates from a role-grouped shortlist.

    Request body:
        batch_id: str
        selections: list of {candidate_id: str, role: str}
    """
    from onemancompany.agents.recruitment import pending_candidates, _pending_project_ctx
    from onemancompany.agents.onboarding import execute_hire, generate_nickname
    from onemancompany.core.config import load_talent_profile

    batch_id = body.get("batch_id", "")
    selections = body.get("selections", [])

    if not selections:
        return {"error": "No candidates selected"}

    all_candidates = pending_candidates.get(batch_id, [])
    if not all_candidates:
        return {"error": "Batch not found"}

    total = len(selections)
    results = []

    for idx, sel in enumerate(selections):
        candidate_id = sel.get("candidate_id", "")
        hire_role = sel.get("role", "")

        # Find candidate — check both "id" and "talent_id" fields
        candidate = next((c for c in all_candidates if (c.get("id") or c.get("talent_id")) == candidate_id), None)
        if not candidate:
            await event_bus.publish(CompanyEvent(
                type="onboarding_progress",
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

        # Read authoritative fields from talent profile
        talent_data: dict = {}
        if talent_id:
            talent_data = load_talent_profile(talent_id)

        skill_names = [s["name"] if isinstance(s, dict) else s for s in candidate.get("skill_set", candidate.get("skills", []))]

        # Apply COO context if available
        coo_ctx: dict = {}
        if _pending_coo_hire_queue:
            coo_ctx = _pending_coo_hire_queue.pop(0)

        final_role = coo_ctx.get("role") or hire_role or candidate.get("role", "Engineer")
        final_dept = coo_ctx.get("department", "")

        # Register custom roles
        if coo_ctx.get("role"):
            from onemancompany.core.config import ROLE_DEPARTMENT_MAP
            from onemancompany.core.state import ROLE_TITLES
            if coo_ctx["role"] not in ROLE_TITLES:
                ROLE_TITLES[coo_ctx["role"]] = coo_ctx["role"]
            if coo_ctx["role"] not in ROLE_DEPARTMENT_MAP and final_dept:
                ROLE_DEPARTMENT_MAP[coo_ctx["role"]] = final_dept

        # Progress callback — publishes WebSocket events
        async def _make_progress_cb(cid, name, idx_val):
            async def cb(step, message):
                step_order = ["assigning_id", "copying_skills", "registering_agent", "completed"]
                step_index = step_order.index(step) if step in step_order else -1
                await event_bus.publish(CompanyEvent(
                    type="onboarding_progress",
                    payload={"batch_id": batch_id, "candidate_id": cid,
                             "name": name, "step": step,
                             "step_index": step_index,
                             "total_steps": 4, "current": idx_val + 1, "total": total,
                             "message": message},
                    agent="HR",
                ))
            return cb

        progress_cb = await _make_progress_cb(candidate_id, cand_name, idx)

        try:
            nickname = await generate_nickname(cand_name, final_role, is_founding=False)
            emp = await execute_hire(
                name=cand_name,
                nickname=nickname,
                role=final_role,
                skills=skill_names,
                talent_id=talent_id,
                llm_model=talent_data.get("llm_model", "") or candidate.get("llm_model", ""),
                temperature=float(talent_data.get("temperature", 0.7)),
                image_model=candidate.get("image_model", ""),
                api_provider=talent_data.get("api_provider", "openrouter") or candidate.get("api_provider", "openrouter"),
                hosting=talent_data.get("hosting", "company"),
                auth_method=talent_data.get("auth_method", "api_key"),
                sprite=candidate.get("sprite", "employee_default"),
                remote=candidate.get("remote", False),
                department=final_dept,
                progress_callback=progress_cb,
            )
            results.append({"candidate_id": candidate_id, "status": "hired", "employee_id": emp.id, "name": cand_name, "nickname": nickname})

            # Handle COO notification
            if coo_ctx.get("project_id"):
                auth_method = talent_data.get("auth_method", "api_key")
                if auth_method == "oauth":
                    _pending_oauth_hire[emp.id] = coo_ctx
                else:
                    _notify_coo_hire_ready(emp.id, coo_ctx)

        except Exception as e:
            traceback.print_exc()
            await event_bus.publish(CompanyEvent(
                type="onboarding_progress",
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
        append_action(pid, HR_ID, "批量入职完成", f"{', '.join(hired_names)} 已入职")
        complete_project(pid, f"Batch hired: {', '.join(hired_names)}")

    pending_candidates.pop(batch_id, None)

    # Resume HR HOLDING task
    from onemancompany.core.agent_loop import get_agent_loop
    hr_loop = get_agent_loop(HR_ID)
    if hr_loop:
        for t in hr_loop.board.tasks:
            if t.status == "holding" and t.result and f"batch_id={batch_id}" in t.result:
                await hr_loop.resume_held_task(HR_ID, t.id, f"Batch hired: {', '.join(hired_names)}")
                break

    await event_bus.publish(CompanyEvent(type="state_snapshot", payload={}, agent="CEO"))

    return {"status": "ok", "count": len(hired_names), "results": results, "state": company_state.to_json()}


@router.post("/api/candidates/interview")
async def interview_candidate(body: InterviewRequest) -> InterviewResponse:
    """CEO interviews a candidate by asking a question. Supports text and image input.

    Request body validated by InterviewRequest (see boss_online.py for schema).
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
        "status": "idle",
        "current_task_id": None,
    }
    # Ensure task queue exists
    if reg.employee_id not in _remote_task_queues:
        _remote_task_queues[reg.employee_id] = []

    await event_bus.publish(
        CompanyEvent(
            type="remote_worker_registered",
            payload={"employee_id": reg.employee_id, "capabilities": reg.capabilities},
            agent="SYSTEM",
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
        _remote_workers[result.employee_id]["status"] = "idle"
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
            type="remote_task_completed",
            payload={
                "task_id": result.task_id,
                "employee_id": result.employee_id,
                "status": result.status,
                "output": result.output[:MAX_SUMMARY_LEN],
            },
            agent="SYSTEM",
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
            emp = company_state.employees.get(uid)
            users_info.append({"id": uid, "name": emp.name if emp else uid})
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
                    content = tf.read_text(encoding="utf-8")
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
    env_path = DATA_ROOT / ".env"
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
    env_path = DATA_ROOT / ".env"
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

    return {"filename": filename, "content": file_path.read_text(encoding="utf-8")}


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

    file_path.write_text(content, encoding="utf-8")
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
    from onemancompany.core.state import SalesTask

    client_name = body.get("client_name", "")
    description = body.get("description", "")
    if not client_name or not description:
        return {"error": "Missing client_name or description"}

    task_id = _uuid.uuid4().hex[:12]
    sales_task = SalesTask(
        id=task_id,
        client_name=client_name,
        description=description,
        requirements=body.get("requirements", ""),
        budget_tokens=body.get("budget_tokens", 0),
    )
    company_state.sales_tasks[task_id] = sales_task

    company_state.activity_log.append({
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
        cso_loop.push_task(cso_notification)

    await event_bus.publish(
        CompanyEvent(
            type="sales_task_submitted",
            payload=sales_task.to_dict(),
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
    return {
        "tasks": [t.to_dict() for t in company_state.sales_tasks.values()]
    }


@router.get("/api/sales/tasks/{task_id}")
async def sales_get_task(task_id: str) -> dict:
    """Get details of a specific sales task."""
    task = company_state.sales_tasks.get(task_id)
    if not task:
        return {"error": f"Sales task '{task_id}' not found"}
    return task.to_dict()


@router.post("/api/sales/tasks/{task_id}/deliver")
async def sales_deliver_task(task_id: str, body: dict) -> dict:
    """Mark a sales task as delivered."""
    task = company_state.sales_tasks.get(task_id)
    if not task:
        return {"error": f"Sales task '{task_id}' not found"}
    if task.status != "in_production":
        return {"error": f"Task status is '{task.status}', expected 'in_production'"}

    task.status = "delivered"
    task.delivery = body.get("delivery_summary", "")
    company_state.activity_log.append({
        "type": "task_delivered",
        "task_id": task_id,
        "client": task.client_name,
    })
    return {"status": "delivered", "task_id": task_id}


@router.post("/api/sales/tasks/{task_id}/settle")
async def sales_settle_task(task_id: str) -> dict:
    """Collect settlement tokens for a delivered task."""
    task = company_state.sales_tasks.get(task_id)
    if not task:
        return {"error": f"Sales task '{task_id}' not found"}
    if task.status != "delivered":
        return {"error": f"Task status is '{task.status}', must be 'delivered' to settle"}

    tokens = task.budget_tokens
    task.settlement_tokens = tokens
    task.status = "settled"
    company_state.company_tokens += tokens
    return {
        "status": "settled",
        "task_id": task_id,
        "tokens_earned": tokens,
        "company_total_tokens": company_state.company_tokens,
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
    env_path = COMPANY_ROOT.parent / ".env"
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
        type="credentials_submitted",
        payload={"service": service_name, "fields": list(body.keys())},
        agent="CEO",
    ))

    return {"status": "ok", "service": service_name, "fields_saved": list(body.keys())}


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
                    await ceo_submit_task({"task": task})
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
        # Serialize inquiry sessions
        inquiry_data = {}
        for sid, sess in _inquiry_sessions.items():
            inquiry_data[sid] = {
                "session_id": sess.session_id,
                "task": sess.task,
                "room_id": sess.room_id,
                "agent_role": sess.agent_role,
                "participants": sess.participants,
                "history": sess.history,
                "system_prompt": getattr(sess, "_system_prompt", ""),
            }

        result: dict = {}
        if _pending_coo_hire_queue:
            result["pending_coo_hire_queue"] = _pending_coo_hire_queue
        if _pending_oauth_hire:
            result["pending_oauth_hire"] = _pending_oauth_hire
        if inquiry_data:
            result["inquiry_sessions"] = inquiry_data
        if _remote_workers:
            result["remote_workers"] = _remote_workers
        if _remote_task_queues:
            result["remote_task_queues"] = _remote_task_queues
        if _remote_task_project_map:
            result["remote_task_project_map"] = _remote_task_project_map
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

        # Inquiry sessions
        for sid, sdata in data.get("inquiry_sessions", {}).items():
            sess = InquirySession(
                session_id=sdata["session_id"],
                task=sdata["task"],
                room_id=sdata["room_id"],
                agent_role=sdata["agent_role"],
                participants=sdata["participants"],
                history=sdata["history"],
            )
            sess._system_prompt = sdata.get("system_prompt", "")
            _inquiry_sessions[sid] = sess

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
