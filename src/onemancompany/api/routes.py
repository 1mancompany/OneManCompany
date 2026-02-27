"""FastAPI routes — REST endpoints + WebSocket."""

from __future__ import annotations

import asyncio
import json
import traceback

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from onemancompany.agents.base import BaseAgentRunner
from onemancompany.api.websocket import ws_manager
from onemancompany.core.config import (
    FOUNDING_LEVEL,
    HR_ID,
    HR_KEYWORDS,
    MAX_SUMMARY_LEN,
    STATUS_IDLE,
    STATUS_WORKING,
)
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.mcp_server.boss_online import HireRequest, InterviewRequest, InterviewResponse
from onemancompany.core.state import TaskEntry, company_state

router = APIRouter()


async def _run_agent_safe(
    coro, agent_name: str, run_routine_after: str = "", project_id: str = ""
) -> None:
    """Run an agent coroutine, catching and logging errors.

    If run_routine_after is set, trigger the company routine after the agent finishes.
    """
    from onemancompany.core.project_archive import append_action, complete_project

    def _sync_task_owner(owner_id: str) -> None:
        """Sync current_owner on the in-memory TaskEntry."""
        for t in company_state.active_tasks:
            if t.project_id == project_id:
                t.current_owner = owner_id
                break

    agent_error = False
    try:
        result = await coro
        # Record agent output in project timeline
        if project_id and result:
            summary = result[:MAX_SUMMARY_LEN] if isinstance(result, str) else str(result)[:MAX_SUMMARY_LEN]
            append_action(project_id, agent_name.lower(), f"{agent_name} task completed", summary)
            _sync_task_owner(agent_name.lower())
    except Exception as e:
        agent_error = True
        traceback.print_exc()
        if project_id:
            append_action(project_id, agent_name.lower(), f"{agent_name} error", str(e)[:MAX_SUMMARY_LEN])
        await event_bus.publish(
            CompanyEvent(
                type="agent_done",
                payload={"role": agent_name, "summary": f"Error: {e!s}"},
                agent=agent_name,
            )
        )

    # Always run routine (retrospective is valuable even after errors)
    if run_routine_after:
        try:
            from onemancompany.core.routine import run_post_task_routine
            await run_post_task_routine(run_routine_after, project_id=project_id)
        except Exception as e:
            traceback.print_exc()
            if project_id:
                append_action(project_id, "routine", "Routine error", str(e)[:MAX_SUMMARY_LEN])
            await event_bus.publish(
                CompanyEvent(
                    type="agent_done",
                    payload={"role": "ROUTINE", "summary": f"Routine error: {e!s}"},
                    agent="ROUTINE",
                )
            )

    # Cleanup always runs — reset employees, remove tasks, complete project
    for emp in company_state.employees.values():
        emp.status = STATUS_IDLE

    if project_id:
        company_state.active_tasks = [
            t for t in company_state.active_tasks if t.project_id != project_id
        ]

    if project_id:
        label = run_routine_after or "Task completed"
        if agent_error:
            label = f"{label} (with errors)"
        complete_project(project_id, label)

    # Broadcast updated state so frontend sees idle employees and cleared tasks
    await event_bus.publish(
        CompanyEvent(type="state_snapshot", payload={}, agent="SYSTEM")
    )


@router.get("/api/state")
async def get_state() -> dict:
    return company_state.to_json()


@router.post("/api/ceo/task")
async def ceo_submit_task(body: dict) -> dict:
    """CEO submits a task, routed to the appropriate agent."""
    from onemancompany.agents.coo_agent import coo_agent
    from onemancompany.agents.hr_agent import hr_agent
    from onemancompany.core.project_archive import create_project, get_project_dir

    task = body.get("task", "")
    if not task:
        return {"error": "Empty task"}

    company_state.ceo_tasks.append(task)
    company_state.activity_log.append({"type": "ceo_task", "task": task})

    await event_bus.publish(
        CompanyEvent(type="ceo_task_submitted", payload={"task": task}, agent="CEO")
    )

    # Set all normal employees to "working" during task
    for emp in company_state.employees.values():
        if emp.level < FOUNDING_LEVEL:
            emp.status = STATUS_WORKING

    # Simple keyword-based routing
    task_lower = task.lower()
    if any(w in task_lower for w in HR_KEYWORDS):
        pid = create_project(task, "HR", [e.id for e in company_state.employees.values()])
        pdir = get_project_dir(pid)
        company_state.active_tasks.append(TaskEntry(project_id=pid, task=task, routed_to="HR", project_dir=pdir))
        task_with_ctx = f"{task}\n\n[Project workspace: {pdir} — save all outputs here]"
        asyncio.create_task(
            _run_agent_safe(hr_agent.run(task_with_ctx), "HR", run_routine_after=task, project_id=pid)
        )
        return {"routed_to": "HR", "status": "processing", "project_id": pid, "project_dir": pdir}
    else:
        pid = create_project(task, "COO", [e.id for e in company_state.employees.values()])
        pdir = get_project_dir(pid)
        company_state.active_tasks.append(TaskEntry(project_id=pid, task=task, routed_to="COO", project_dir=pdir))
        task_with_ctx = f"{task}\n\n[Project workspace: {pdir} — save all outputs here]"
        asyncio.create_task(
            _run_agent_safe(coo_agent.run(task_with_ctx), "COO", run_routine_after=task, project_id=pid)
        )
        return {"routed_to": "COO", "status": "processing", "project_id": pid, "project_dir": pdir}


@router.post("/api/oneonone/chat")
async def oneonone_chat(body: dict) -> dict:
    """Per-message 1-on-1 chat. Frontend manages history, backend returns LLM response."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    from onemancompany.agents.base import make_llm

    employee_id = body.get("employee_id", "")
    message = body.get("message", "")
    history = body.get("history", [])

    if not employee_id or not message:
        return {"error": "Missing employee_id or message"}

    emp = company_state.employees.get(employee_id)
    if not emp:
        return {"error": f"Employee '{employee_id}' not found"}

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

    # Build persona prompt
    from onemancompany.agents.base import get_employee_skills_prompt, get_employee_tools_prompt

    skills_str = ", ".join(emp.skills) if emp.skills else "general"
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
        f"{principles_section}{culture_section}"
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
    result = await llm.ainvoke(messages)

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
        result = await llm.ainvoke([
            SystemMessage(content="You are an employee reflecting on a meeting with the CEO."),
            HumanMessage(content=reflection_prompt),
        ])
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
    from onemancompany.agents.coo_agent import coo_agent

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
    asyncio.create_task(_run_agent_safe(coo_agent.run(task), "COO"))
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


@router.post("/api/hr/review")
async def trigger_hr_review() -> dict:
    from onemancompany.agents.hr_agent import hr_agent

    asyncio.create_task(_run_agent_safe(hr_agent.run_quarterly_review(), "HR"))
    return {"status": "HR review started"}


@router.post("/api/routine/start")
async def start_routine(body: dict) -> dict:
    """Trigger the post-task company routine (review meeting + operations review)."""
    from onemancompany.core.routine import run_post_task_routine

    task_summary = body.get("task_summary", "Routine task completed")
    participants = body.get("participants")  # None = all employees
    asyncio.create_task(
        _run_agent_safe(run_post_task_routine(task_summary, participants), "ROUTINE")
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

    asyncio.create_task(
        _run_agent_safe(execute_approved_actions(report_id, approved_indices), "ROUTINE")
    )
    return {"status": "executing_approved_actions"}


@router.post("/api/routine/all_hands")
async def start_all_hands(body: dict) -> dict:
    """CEO convenes an all-hands meeting. All employees absorb the meeting spirit."""
    from onemancompany.core.routine import run_all_hands_meeting

    message = body.get("message", "")
    if not message:
        return {"error": "Missing CEO message"}

    asyncio.create_task(
        _run_agent_safe(run_all_hands_meeting(message), "ROUTINE")
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
    """Get full employee details including work principles and model config."""
    from onemancompany.core.config import employee_configs

    emp = company_state.employees.get(employee_id)
    if not emp:
        return {"error": "Employee not found"}

    cfg = employee_configs.get(employee_id)
    llm_model = cfg.llm_model if cfg else ""

    result = emp.to_dict()
    result["llm_model"] = llm_model
    return result


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

    # Update in-memory config
    cfg = employee_configs.get(employee_id)
    if cfg:
        cfg.llm_model = model_id

    # Update profile.yaml on disk
    profile_path = EMPLOYEES_DIR / employee_id / "profile.yaml"
    if profile_path.exists():
        with open(profile_path) as f:
            data = yaml.safe_load(f) or {}
        data["llm_model"] = model_id
        with open(profile_path, "w") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    await event_bus.publish(
        CompanyEvent(
            type="agent_done",
            payload={
                "role": "CEO",
                "summary": f"Updated {emp.name} ({emp.nickname})'s model to {model_id}",
            },
            agent="CEO",
        )
    )

    return {"status": "updated", "employee_id": employee_id, "model": model_id}


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


# ===== Project Archive =====

@router.get("/api/projects")
async def get_projects() -> dict:
    """List all projects (summary view for the project wall)."""
    from onemancompany.core.project_archive import list_projects
    return {"projects": list_projects()}


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
    )
    company_state.employees[employee_id] = emp
    del company_state.ex_employees[employee_id]

    # Recompute layout and persist all desk positions
    compute_layout(company_state)
    persist_all_desk_positions(company_state)

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


# ===== Candidate Selection =====

@router.post("/api/candidates/hire")
async def hire_candidate(body: HireRequest) -> dict:
    """CEO selects a candidate to hire from the shortlist.

    Request body validated by HireRequest (see boss_online.py for schema).
    """
    from onemancompany.agents.hr_agent import hr_agent, pending_candidates

    candidates = pending_candidates.get(body.batch_id, [])
    candidate = next((c for c in candidates if c.get("id") == body.candidate_id), None)
    if not candidate:
        return {"error": "Candidate not found"}

    # Peek at next employee number before hire to know the assigned id
    next_num = f"{company_state._next_employee_number:05d}"

    # Auto-generate 花名 (nickname) if not provided — let the "employee" pick their own
    nickname = body.nickname
    if not nickname:
        from onemancompany.agents.base import make_llm
        from langchain_core.messages import HumanMessage, SystemMessage

        gen_llm = make_llm(HR_ID)
        gen_prompt = (
            f"You are {candidate['name']}, a {candidate['role']}, "
            f"about to join a company. The company has a 花名 (nickname) culture — every employee picks a 花名 (two Chinese characters).\n"
            f"Requirements for your 花名:\n"
            f"- Exactly two Chinese characters\n"
            f"- Reflects your personality, expertise, or aspirations\n"
            f"- Meaningful and catchy\n"
            f"- Reference style: 飞鱼, 星辰, 雷鸣, 云帆, 青松, 铁锤, 晓风, 墨竹\n\n"
            f"Reply with ONLY your 花名 (two Chinese characters), nothing else."
        )
        gen_result = await gen_llm.ainvoke([
            SystemMessage(content="You are a new employee about to join the company. Pick a 花名 (nickname) for yourself."),
            HumanMessage(content=gen_prompt),
        ])
        nickname = gen_result.content.strip()
        # Clean up — extract exactly 2 Chinese characters
        import re
        chinese_chars = re.findall(r'[\u4e00-\u9fff]', nickname)
        if len(chinese_chars) >= 2:
            nickname = ''.join(chinese_chars[:2])
        elif chinese_chars:
            nickname = ''.join(chinese_chars)
        else:
            nickname = ""  # fallback — no valid nickname generated

    # Build hire JSON and let HR apply it (id is assigned by HR as employee_number)
    skill_names = [s["name"] if isinstance(s, dict) else s for s in candidate.get("skill_set", [])]
    hire_json = json.dumps({
        "action": "hire",
        "employee": {
            "name": candidate["name"],
            "nickname": nickname or "",
            "role": candidate["role"],
            "skills": skill_names,
            "sprite": candidate.get("sprite", "employee_default"),
        },
    })
    await hr_agent._apply_results(f"```json\n{hire_json}\n```")
    pending_candidates.pop(body.batch_id, None)

    # Explicit state broadcast to ensure frontend updates
    await event_bus.publish(
        CompanyEvent(type="state_snapshot", payload={}, agent="CEO")
    )

    return {
        "status": "hired",
        "employee_id": next_num,
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
    result = await llm.ainvoke([
        SystemMessage(content=full_prompt),
        HumanMessage(content=content if body.images else body.question),
    ])

    return InterviewResponse(
        candidate_id=candidate.id,
        question=body.question,
        answer=result.content,
    )


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
