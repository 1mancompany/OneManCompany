"""FastAPI routes — REST endpoints + WebSocket."""

from __future__ import annotations

import asyncio
import json
import traceback
import uuid as _uuid
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from loguru import logger

from onemancompany.agents.base import BaseAgentRunner, tracked_ainvoke
from onemancompany.api.websocket import ws_manager
from onemancompany.core.config import (
    CEO_ID,
    COMPANY_DIR,
    COO_ID,
    CSO_ID,
    EA_ID,
    FOUNDING_LEVEL,
    HR_ID,
    HR_KEYWORDS,
    MAX_SUMMARY_LEN,
    STATUS_IDLE,
    STATUS_WORKING,
)
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.talent_market.boss_online import HireRequest, InterviewRequest, InterviewResponse
from onemancompany.core.state import TaskEntry, company_state

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


async def _run_agent_safe(
    coro,
    agent_name: str,
    task_description: str = "",
    run_routine_after: str = "",
    project_id: str = "",
    project_dir: str = "",
) -> None:
    """Run an agent coroutine, catching and logging errors.

    Automatically registers a TaskEntry in active_tasks so every running
    agent task is visible in the task queue.  If *project_id* is empty a
    lightweight placeholder ID is generated.

    If run_routine_after is set, trigger the company routine after the agent finishes.
    """
    from onemancompany.core.project_archive import append_action, complete_project
    import uuid as _uuid

    def _match_task(t, pid):
        return t.project_id == pid or (t.iteration_id and t.iteration_id == pid)

    # --- Ensure a TaskEntry exists for this run ---
    if not project_id:
        project_id = f"_auto_{_uuid.uuid4().hex[:8]}"
    # Only add if not already tracked (CEO task route may have added one)
    already_tracked = any(_match_task(t, project_id) for t in company_state.active_tasks)
    if not already_tracked:
        company_state.active_tasks.append(
            TaskEntry(
                project_id=project_id,
                task=task_description or f"{agent_name} task",
                routed_to=agent_name,
                project_dir=project_dir,
            )
        )
        # Broadcast so frontend sees the new task immediately
        await event_bus.publish(
            CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
        )

    def _sync_task_owner(owner_id: str) -> None:
        """Sync current_owner on the in-memory TaskEntry."""
        for t in company_state.active_tasks:
            if _match_task(t, project_id):
                t.current_owner = owner_id
                break

    # Set the project_id context so propose_file_edit collects edits
    from onemancompany.core.resolutions import current_project_id
    ctx_token = current_project_id.set(project_id)

    agent_error = False
    try:
        result = await coro
        # Record agent output in project timeline
        if not project_id.startswith("_auto_") and result:
            summary = result[:MAX_SUMMARY_LEN] if isinstance(result, str) else str(result)[:MAX_SUMMARY_LEN]
            append_action(project_id, agent_name.lower(), f"{agent_name} task completed", summary)
            _sync_task_owner(agent_name.lower())
    except Exception as e:
        agent_error = True
        traceback.print_exc()
        if not project_id.startswith("_auto_"):
            append_action(project_id, agent_name.lower(), f"{agent_name} error", str(e)[:MAX_SUMMARY_LEN])
        await event_bus.publish(
            CompanyEvent(
                type="agent_done",
                payload={"role": agent_name, "summary": f"Error: {e!s}"},
                agent=agent_name,
            )
        )
    finally:
        current_project_id.reset(ctx_token)

    # Create a resolution if any file edits were accumulated during the task
    from onemancompany.core.resolutions import create_resolution
    resolution = create_resolution(project_id, task_description)
    if resolution:
        await event_bus.publish(
            CompanyEvent(type="resolution_ready", payload=resolution, agent="SYSTEM")
        )

    # Always run routine (retrospective is valuable even after errors)
    if run_routine_after:
        try:
            from onemancompany.core.routine import run_post_task_routine
            await run_post_task_routine(run_routine_after, project_id=project_id)
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

    # Cleanup sandbox container after each task
    from onemancompany.tools.sandbox import cleanup_sandbox as _cleanup_sandbox
    await _cleanup_sandbox()

    # Cleanup always runs — reset employees, remove tasks, complete project
    for emp in company_state.employees.values():
        emp.status = STATUS_IDLE

    company_state.active_tasks = [
        t for t in company_state.active_tasks if not _match_task(t, project_id)
    ]

    if not project_id.startswith("_auto_"):
        label = run_routine_after or "Task completed"
        if agent_error:
            label = f"{label} (with errors)"
        complete_project(project_id, label)

    # Flush any deferred reloads now that this task is done
    from onemancompany.core.state import flush_pending_reload
    flush_result = flush_pending_reload()
    if flush_result:
        updated = flush_result.get("employees_updated", [])
        added = flush_result.get("employees_added", [])
        if updated or added:
            print(f"[hot-reload] Post-task flush: {len(updated)} updated, {len(added)} added")

    # Broadcast updated state so frontend sees idle employees and cleared tasks
    await event_bus.publish(
        CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
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
    """CEO triggers a process restart to pick up code changes."""
    import os
    import sys
    from onemancompany.main import _save_ephemeral_state, _pending_code_changes

    _save_ephemeral_state()
    _pending_code_changes.clear()

    os.execv(sys.executable, [sys.executable, "-m", "onemancompany.main"])


@router.post("/api/admin/clear-tasks")
async def admin_clear_tasks() -> dict:
    """Clear all stale active tasks and reset employee statuses to idle."""
    cleared = len(company_state.active_tasks)
    company_state.active_tasks.clear()
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

    # Route to the best agent via keyword logic
    from onemancompany.core.config import SALES_KEYWORDS
    task_lower = task.lower()
    if any(w in task_lower for w in HR_KEYWORDS):
        agent_role = "HR"
        agent_id = HR_ID
    elif any(w in task_lower for w in SALES_KEYWORDS):
        agent_role = "CSO"
        agent_id = CSO_ID
    else:
        agent_role = "COO"
        agent_id = COO_ID

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
        pid = create_project(task, "pending", [e.id for e in company_state.employees.values()])
        pdir = get_project_dir(pid)

    task_entry = TaskEntry(
        project_id=pid,
        iteration_id=iter_id,
        task=task,
        routed_to="pending",
        project_dir=pdir,
    )
    company_state.active_tasks.append(task_entry)

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

    task_entry.routed_to = "EA"
    loop = get_agent_loop(EA_ID)
    if loop:
        ea_task = (
            f"CEO下发了新任务，请分析并分派给合适的负责人:\n\n"
            f"任务: {task}{attach_info}\n\n"
            f"[Project ID: {ctx_id}] [Project workspace: {pdir}]"
        )
        loop.push_task(ea_task, project_id=ctx_id, project_dir=pdir)
    else:
        # Fallback: direct fire-and-forget (should not happen)
        from onemancompany.agents.ea_agent import EAAgent
        _ea = EAAgent()
        task_with_ctx = f"{task}\n\n[Project workspace: {pdir} — save all outputs here]"
        asyncio.create_task(
            _run_agent_safe(_ea.run(task_with_ctx), "EA", run_routine_after=task, project_id=ctx_id, project_dir=pdir)
        )
    return {
        "routed_to": "EA",
        "status": "processing",
        "project_id": pid,
        "iteration_id": iter_id,
        "project_dir": pdir,
    }


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
        return {"response": output}

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
            if t and t.status in ("completed", "failed"):
                break
        # Extract the result from logs
        t = loop.board.get_task(agent_task.id)
        if t:
            # Prefer the result field (set on task completion)
            if t.result:
                return {"response": t.result}
            # Fallback: find the last llm_output in logs
            if t.logs:
                for log_entry in reversed(t.logs):
                    if log_entry.get("type") in ("llm_output", "result"):
                        return {"response": log_entry["content"]}
                return {"response": t.logs[-1].get("content", "（处理完成）")}
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

    return {"response": result.content}


@router.post("/api/oneonone/end")
async def oneonone_end(body: dict) -> dict:
    """End meeting. LLM reflects on transcript and conditionally updates work principles."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from onemancompany.agents.base import make_llm
    from onemancompany.core.config import save_work_principles

    employee_id = body.get("employee_id", "")
    history = body.get("history", [])

    if not employee_id:
        return {"error": "Missing employee_id"}

    emp = company_state.employees.get(employee_id)
    if not emp:
        return {"error": f"Employee '{employee_id}' not found"}

    principles_updated = False

    if history:
        # Build transcript
        transcript_lines = []
        for entry in history:
            speaker = "CEO" if entry.get("role") == "ceo" else emp.name
            transcript_lines.append(f"{speaker}: {entry['content']}")
        transcript = "\n".join(transcript_lines)

        current_principles = emp.work_principles or "(No work principles yet)"

        reflection_prompt = (
            f"You are {emp.name} ({emp.nickname}, {emp.role}, Department: {emp.department}).\n\n"
            f"You just had a 1-on-1 meeting with the CEO. Here is the conversation transcript:\n\n"
            f"{transcript}\n\n"
            f"Your current work principles:\n{current_principles}\n\n"
            f"Reflect: Did the CEO convey any actionable guidance, directives, or expectations "
            f"that should be incorporated into your work principles?\n\n"
            f"If YES — output UPDATED: followed by the complete updated work principles in Markdown format. "
            f"Keep existing principles that are still valid, incorporate the new guidance, "
            f"and resolve any conflicts (new guidance takes precedence).\n\n"
            f"If NO (it was casual chat, no actionable guidance) — output exactly: NO_UPDATE"
        )

        llm = make_llm(employee_id)
        result = await tracked_ainvoke(llm, [
            SystemMessage(content="You are an employee reflecting on a meeting with the CEO."),
            HumanMessage(content=reflection_prompt),
        ], category="oneonone", employee_id=employee_id)
        response_text = result.content.strip()

        if response_text.startswith("UPDATED:"):
            new_principles = response_text[len("UPDATED:"):].strip()
            emp.work_principles = new_principles
            save_work_principles(employee_id, new_principles)
            principles_updated = True

    # End the meeting
    emp.is_listening = False
    await event_bus.publish(
        CompanyEvent(
            type="guidance_end",
            payload={
                "employee_id": employee_id,
                "name": emp.name,
                "principles_updated": principles_updated,
            },
            agent="CEO",
        )
    )

    return {
        "status": "ended",
        "employee_id": employee_id,
        "principles_updated": principles_updated,
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
        from onemancompany.agents.coo_agent import COOAgent
        _coo = COOAgent()
        asyncio.create_task(_run_agent_safe(
            _coo.run(task), "COO",
            task_description=f"Book meeting: {purpose or 'room request'}",
        ))
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
        # Fallback
        from onemancompany.agents.hr_agent import HRAgent
        _hr = HRAgent()
        asyncio.create_task(_run_agent_safe(
            _hr.run_quarterly_review(), "HR",
            task_description="Quarterly performance review",
        ))
    return {"status": "HR review started"}


@router.post("/api/routine/start")
async def start_routine(body: dict) -> dict:
    """Trigger the post-task company routine (review meeting + operations review)."""
    from onemancompany.core.routine import run_post_task_routine

    task_summary = body.get("task_summary", "Routine task completed")
    participants = body.get("participants")  # None = all employees
    asyncio.create_task(_run_agent_safe(
        run_post_task_routine(task_summary, participants), "ROUTINE",
        task_description=f"Post-task routine: {task_summary[:50]}",
    ))
    return {"status": "routine_started"}


@router.post("/api/routine/approve")
async def approve_routine_actions(body: dict) -> dict:
    """CEO approves selected action items from a meeting report."""
    from onemancompany.core.routine import execute_approved_actions

    report_id = body.get("report_id", "")
    approved_indices = body.get("approved_indices", [])
    if not report_id:
        return {"error": "Missing report_id"}

    asyncio.create_task(_run_agent_safe(
        execute_approved_actions(report_id, approved_indices), "ROUTINE",
        task_description="Execute approved actions",
    ))
    return {"status": "executing_approved_actions"}


@router.post("/api/routine/all_hands")
async def start_all_hands(body: dict) -> dict:
    """CEO convenes an all-hands meeting. All employees absorb the meeting spirit."""
    from onemancompany.core.routine import run_all_hands_meeting

    message = body.get("message", "")
    if not message:
        return {"error": "Missing CEO message"}

    asyncio.create_task(_run_agent_safe(
        run_all_hands_meeting(message), "ROUTINE",
        task_description=f"All-hands meeting: {message[:50]}",
    ))
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

    emp = company_state.employees.get(employee_id)
    if not emp:
        return {"error": "Employee not found"}

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


@router.post("/api/task/{project_id}/abort")
async def abort_task(project_id: str) -> dict:
    """Abort all agent tasks related to a project.

    Cancels pending/in-progress tasks on all agent boards, cancels running
    asyncio tasks, removes from company active_tasks, and broadcasts state update.
    """
    from onemancompany.core.agent_loop import employee_manager

    total_cancelled = employee_manager.abort_project(project_id)

    # Remove from company-level active_tasks (match both project_id and iteration_id)
    company_state.active_tasks = [
        t for t in company_state.active_tasks
        if t.project_id != project_id and (not t.iteration_id or t.iteration_id != project_id)
    ]

    # Broadcast state
    await event_bus.publish(
        CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
    )

    return {"status": "ok", "cancelled": total_cancelled}


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

    if task.status not in ("pending", "in_progress"):
        return {"status": "error", "message": f"Task already {task.status}"}

    was_in_progress = task.status == "in_progress"

    task.status = "cancelled"
    task.completed_at = datetime.now().isoformat()
    task.result = "Cancelled by CEO"
    employee_manager._log(employee_id, task, "cancelled", "CEO cancelled this task")
    employee_manager._publish_task_update(employee_id, task)

    # Also cancel pending sub-tasks
    for sid in task.sub_task_ids:
        sub = loop.board.get_task(sid)
        if sub and sub.status in ("pending", "in_progress"):
            sub.status = "cancelled"
            sub.completed_at = datetime.now().isoformat()
            sub.result = "Parent task cancelled by CEO"
            employee_manager._log(employee_id, sub, "cancelled", "Parent task cancelled by CEO")
            employee_manager._publish_task_update(employee_id, sub)

    # Cancel the running asyncio.Task if this was in_progress
    if was_in_progress and employee_id in employee_manager._running_tasks:
        running = employee_manager._running_tasks[employee_id]
        if not running.done():
            running.cancel()

    # Broadcast state
    await event_bus.publish(
        CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
    )

    return {"status": "ok"}


@router.put("/api/employee/{employee_id}/model")
async def update_employee_model(employee_id: str, body: dict) -> dict:
    """Update the LLM model for a specific employee. Saves to profile.yaml."""
    import yaml

    from onemancompany.core.config import EMPLOYEES_DIR, employee_configs

    model_id = body.get("model", "")
    if not model_id:
        return {"error": "Missing model"}

    emp = company_state.employees.get(employee_id)
    if not emp:
        return {"error": "Employee not found"}

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
    profile_path = EMPLOYEES_DIR / employee_id / "profile.yaml"
    if profile_path.exists():
        with open(profile_path) as f:
            data = yaml.safe_load(f) or {}
        data["llm_model"] = model_id
        data["salary_per_1m_tokens"] = new_salary
        with open(profile_path, "w") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

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


@router.put("/api/employee/{employee_id}/api-key")
async def update_employee_api_key(employee_id: str, body: dict) -> dict:
    """Update the API key (and optionally model) for a custom-provider employee."""
    import yaml

    from onemancompany.core.config import EMPLOYEES_DIR, employee_configs

    emp = company_state.employees.get(employee_id)
    if not emp:
        return {"error": "Employee not found"}

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
    profile_path = EMPLOYEES_DIR / employee_id / "profile.yaml"
    if profile_path.exists():
        with open(profile_path) as f:
            data = yaml.safe_load(f) or {}
        data["api_key"] = new_key
        if new_model:
            data["llm_model"] = new_model
        with open(profile_path, "w") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    # If an agent loop exists, rebuild its LLM so the new key takes effect
    from onemancompany.core.agent_loop import get_agent_loop
    loop = get_agent_loop(employee_id)
    if loop and loop.agent:
        from onemancompany.agents.base import make_llm
        from langgraph.prebuilt import create_react_agent
        new_llm = make_llm(employee_id)
        # Rebuild the agent with the new LLM but same tools
        if hasattr(loop.agent, '_agent') and loop.agent._agent:
            old_tools = []
            from onemancompany.agents.common_tools import COMMON_TOOLS
            from onemancompany.core.config import load_employee_custom_tools
            custom_tools = load_employee_custom_tools(employee_id)
            old_tools = list(COMMON_TOOLS) + custom_tools
            loop.agent._agent = create_react_agent(model=new_llm, tools=old_tools)

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

    emp = company_state.employees.get(employee_id)
    if not emp:
        return {"error": "Employee not found"}

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

        profile_path = EMPLOYEES_DIR / employee_id / "profile.yaml"
        if profile_path.exists():
            with open(profile_path) as f:
                data = yaml.safe_load(f) or {}
            data["api_key"] = api_key
            data["oauth_refresh_token"] = tokens.get("refresh_token", "")
            with open(profile_path, "w") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

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

    # Try to create a permanent API key using the OAuth token
    api_key = access_token  # fallback: use access token directly
    try:
        async with httpx.AsyncClient() as client2:
            key_resp = await client2.post(
                ANTHROPIC_CREATE_KEY_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"name": f"OneManCompany-{employee_id}"},
                timeout=15.0,
            )
            if key_resp.status_code == 200:
                key_data = key_resp.json()
                api_key = key_data.get("api_key", access_token)
    except Exception as _e:
        logger.debug("OAuth key exchange failed, falling back to access token: {}", _e)

    # Store tokens in employee config
    cfg = employee_configs.get(employee_id)
    if cfg:
        cfg.api_key = api_key
        cfg.oauth_refresh_token = refresh_token

        # Persist to profile.yaml
        profile_path = EMPLOYEES_DIR / employee_id / "profile.yaml"
        if profile_path.exists():
            with open(profile_path) as f:
                data = yaml.safe_load(f) or {}
            data["api_key"] = api_key
            data["oauth_refresh_token"] = refresh_token
            with open(profile_path, "w") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    emp = company_state.employees.get(employee_id)
    emp_name = emp.name if emp else employee_id

    await event_bus.publish(
        CompanyEvent(
            type="agent_done",
            payload={"role": "CEO", "summary": f"{emp_name} OAuth login successful."},
            agent="CEO",
        )
    )

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
    profile_path = EMPLOYEES_DIR / employee_id / "profile.yaml"
    if profile_path.exists():
        with open(profile_path) as f:
            data = yaml.safe_load(f) or {}
        data["api_key"] = cfg.api_key
        data["oauth_refresh_token"] = cfg.oauth_refresh_token
        with open(profile_path, "w") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

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

    if approved:
        # Build JD from COO's request and dispatch to HR
        skills_str = ", ".join(req.get("desired_skills", []))
        jd = f"招聘 {req['role']}"
        if skills_str:
            jd += f"（技能要求: {skills_str}）"
        jd += f"\n原因: {req['reason']}"
        if note:
            jd += f"\nCEO 备注: {note}"

        asyncio.create_task(_run_agent_safe(
            _dispatch_hiring_to_hr(jd), "HR",
            task_description=f"Recruit {req['role']}",
        ))

    return {
        "status": "approved" if approved else "rejected",
        "request_id": request_id,
        "role": req["role"],
    }


async def _dispatch_hiring_to_hr(jd: str) -> str:
    """Dispatch a hiring task to the HR agent."""
    from onemancompany.core.agent_loop import employee_manager

    result = await employee_manager.dispatch("00002", jd)
    return result or ""


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

    try:
        emp = await execute_hire(
            name=candidate["name"],
            nickname=nickname or "",
            role=candidate["role"],
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
        )
    except Exception as e:
        traceback.print_exc()
        return {"error": f"Hire failed: {e!s}"}

    # Resume project lifecycle: stashed project_id is restored so
    # retrospective runs after the *actual* hire, not after shortlist.
    from onemancompany.agents.hr_agent import _pending_project_ctx
    from onemancompany.core.project_archive import append_action, complete_project
    ctx = _pending_project_ctx.pop(body.batch_id, {})
    pid = ctx.get("project_id", "")
    if pid:
        append_action(pid, HR_ID, "入职完成", f"{candidate['name']} 已入职，工号 {emp.id}")
        complete_project(pid, f"Hired {candidate['name']}")
        # Remove from active tasks (match both project_id and iteration_id)
        company_state.active_tasks = [
            t for t in company_state.active_tasks
            if t.project_id != pid and (not t.iteration_id or t.iteration_id != pid)
        ]
        # Run retrospective in background
        from onemancompany.core.routine import run_post_task_routine
        asyncio.create_task(
            run_post_task_routine(f"招聘任务: 招聘 {candidate['name']} 为 {candidate['role']}", project_id=pid)
        )

    pending_candidates.pop(body.batch_id, None)

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
    """Return tool.yaml contents + file list for the tool detail view."""
    from onemancompany.core.config import TOOLS_DIR

    tool = company_state.tools.get(tool_id)
    if not tool or not tool.folder_name:
        raise HTTPException(status_code=404, detail="Tool not found")
    tool_yaml_path = TOOLS_DIR / tool.folder_name / "tool.yaml"
    raw = tool_yaml_path.read_text() if tool_yaml_path.exists() else ""
    return {
        "id": tool_id,
        "name": tool.name,
        "description": tool.description,
        "folder": tool.folder_name,
        "files": tool.files,
        "yaml_content": raw,
        "has_icon": tool.has_icon,
    }


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
