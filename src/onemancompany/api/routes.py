"""FastAPI routes — REST endpoints + WebSocket."""

from __future__ import annotations

import asyncio
import json
import traceback

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from onemancompany.agents.base import BaseAgentRunner
from onemancompany.api.websocket import ws_manager
from onemancompany.core.events import CompanyEvent, event_bus
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

    try:
        result = await coro
        # Record agent output in project timeline
        if project_id and result:
            summary = result[:500] if isinstance(result, str) else str(result)[:500]
            append_action(project_id, agent_name.lower(), f"{agent_name} 完成任务", summary)
            _sync_task_owner(agent_name.lower())
    except Exception as e:
        traceback.print_exc()
        if project_id:
            append_action(project_id, agent_name.lower(), f"{agent_name} 出错", str(e)[:300])
        await event_bus.publish(
            CompanyEvent(
                type="agent_done",
                payload={"role": agent_name, "summary": f"Error: {e!s}"},
                agent=agent_name,
            )
        )
        return  # don't run routine after error

    if run_routine_after:
        try:
            from onemancompany.core.routine import run_post_task_routine
            await run_post_task_routine(run_routine_after, project_id=project_id)
        except Exception as e:
            traceback.print_exc()
            await event_bus.publish(
                CompanyEvent(
                    type="agent_done",
                    payload={"role": "ROUTINE", "summary": f"Routine error: {e!s}"},
                    agent="ROUTINE",
                )
            )

    # Reset all employees to idle after task + routine completes
    for emp in company_state.employees.values():
        emp.status = "idle"

    # Remove from active tasks
    if project_id:
        company_state.active_tasks = [
            t for t in company_state.active_tasks if t.project_id != project_id
        ]

    # Mark project completed after routine
    if project_id:
        complete_project(project_id, run_routine_after or "任务完成")


@router.get("/api/state")
async def get_state() -> dict:
    return company_state.to_json()


@router.post("/api/ceo/task")
async def ceo_submit_task(body: dict) -> dict:
    """CEO submits a task, routed to the appropriate agent."""
    from onemancompany.agents.coo_agent import coo_agent
    from onemancompany.agents.hr_agent import hr_agent
    from onemancompany.core.project_archive import create_project

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
        if emp.level < 4:
            emp.status = "working"

    # Simple keyword-based routing
    task_lower = task.lower()
    hr_keywords = ["hire", "recruit", "employee", "staff", "review", "performance",
                    "fire", "dismiss", "terminate",
                    "招聘", "员工", "评价", "评估", "花名", "晋升", "开除", "解雇", "辞退"]
    if any(w in task_lower for w in hr_keywords):
        pid = create_project(task, "HR", [e.id for e in company_state.employees.values()])
        company_state.active_tasks.append(TaskEntry(project_id=pid, task=task, routed_to="HR"))
        asyncio.create_task(
            _run_agent_safe(hr_agent.run(task), "HR", run_routine_after=task, project_id=pid)
        )
        return {"routed_to": "HR", "status": "processing", "project_id": pid}
    else:
        pid = create_project(task, "COO", [e.id for e in company_state.employees.values()])
        company_state.active_tasks.append(TaskEntry(project_id=pid, task=task, routed_to="COO"))
        asyncio.create_task(
            _run_agent_safe(coo_agent.run(task), "COO", run_routine_after=task, project_id=pid)
        )
        return {"routed_to": "COO", "status": "processing", "project_id": pid}


@router.post("/api/ceo/guidance")
async def ceo_give_guidance(body: dict) -> dict:
    """CEO gives guidance to a specific agent. The agent listens, records, and acknowledges."""
    from onemancompany.agents.coo_agent import coo_agent
    from onemancompany.agents.hr_agent import hr_agent

    employee_id = body.get("employee_id", "")
    guidance = body.get("guidance", "")
    if not employee_id or not guidance:
        return {"error": "Missing employee_id or guidance"}

    if employee_id not in company_state.employees:
        return {"error": f"Employee '{employee_id}' not found"}

    # Route to the right agent
    agent_map: dict[str, BaseAgentRunner] = {
        "hr": hr_agent,
        "coo": coo_agent,
    }

    agent = agent_map.get(employee_id)
    if agent:
        asyncio.create_task(
            _run_agent_safe(agent.receive_guidance(guidance), agent.role)
        )
        return {"status": "listening", "employee_id": employee_id}

    # For dynamically hired employees that don't have a dedicated agent,
    # still record the guidance note directly
    emp = company_state.employees[employee_id]
    emp.guidance_notes.append(guidance)
    company_state.activity_log.append(
        {"type": "guidance", "employee": emp.name, "note": guidance}
    )
    await event_bus.publish(
        CompanyEvent(
            type="guidance_noted",
            payload={
                "employee_id": employee_id,
                "name": emp.name,
                "guidance": guidance,
                "acknowledgment": f"{emp.name} 已记录领导指示。",
            },
            agent="CEO",
        )
    )
    return {"status": "noted", "employee_id": employee_id}


@router.get("/api/employee/{employee_id}/guidance")
async def get_employee_guidance(employee_id: str) -> dict:
    """Get all guidance notes for a specific employee."""
    emp = company_state.employees.get(employee_id)
    if not emp:
        return {"error": "Employee not found"}
    return {"employee_id": employee_id, "name": emp.name, "guidance_notes": emp.guidance_notes}


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
        f"员工 {employee_id} 申请预约会议室，"
        f"参会人员: {', '.join(participants) if participants else '无'}，"
        f"会议目的: {purpose or '未说明'}。"
        f"请检查是否有空闲会议室并处理此请求。"
    )
    asyncio.create_task(_run_agent_safe(coo_agent.run(task), "COO"))
    return {"status": "processing", "message": "COO 正在处理会议室申请"}


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

    task_summary = body.get("task_summary", "常规任务完成")
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
                "summary": f"已将 {emp.name}({emp.nickname}) 的模型更新为 {model_id}",
            },
            agent="CEO",
        )
    )

    return {"status": "updated", "employee_id": employee_id, "model": model_id}


# ===== Culture Wall (公司文化墙) =====

@router.get("/api/culture-wall")
async def get_culture_wall() -> dict:
    """Get all culture wall items."""
    return {"items": company_state.culture_wall}


@router.post("/api/culture-wall")
async def add_culture_item(body: dict) -> dict:
    """CEO adds a new item to the culture wall. Applies to all employees."""
    from datetime import datetime

    from onemancompany.core.config import save_culture_wall

    content = body.get("content", "").strip()
    if not content:
        return {"error": "Missing content"}

    item = {
        "content": content,
        "created_at": datetime.now().isoformat(),
    }
    company_state.culture_wall.append(item)
    save_culture_wall(company_state.culture_wall)

    await event_bus.publish(
        CompanyEvent(
            type="culture_wall_updated",
            payload={"item": item, "total": len(company_state.culture_wall)},
            agent="CEO",
        )
    )
    return {"status": "added", "item": item, "total": len(company_state.culture_wall)}


@router.delete("/api/culture-wall/{index}")
async def remove_culture_item(index: int) -> dict:
    """CEO removes a culture wall item by index."""
    from onemancompany.core.config import save_culture_wall

    if index < 0 or index >= len(company_state.culture_wall):
        return {"error": "Invalid index"}

    removed = company_state.culture_wall.pop(index)
    save_culture_wall(company_state.culture_wall)

    await event_bus.publish(
        CompanyEvent(
            type="culture_wall_updated",
            payload={"removed": removed, "total": len(company_state.culture_wall)},
            agent="CEO",
        )
    )
    return {"status": "removed", "removed": removed}


# ===== File Editor (文件编辑 — CEO审批) =====

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


# ===== Project Archive (项目墙) =====

@router.get("/api/projects")
async def get_projects() -> dict:
    """List all projects (summary view for the project wall)."""
    from onemancompany.core.project_archive import list_projects
    return {"projects": list_projects()}


@router.get("/api/projects/{project_id}")
async def get_project_detail(project_id: str) -> dict:
    """Get full project detail including timeline."""
    from onemancompany.core.project_archive import load_project
    doc = load_project(project_id)
    if not doc:
        return {"error": "Project not found"}
    return doc


# ===== Ex-Employees (离职员工) =====

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

    # Find next desk position
    count = len(company_state.employees)
    row = count // 5
    col = count % 5
    desk_pos = (2 + col * 3, 2 + row * 3)

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

    return {"status": "rehired", "employee_id": employee_id, "name": emp.name}


# ===== Candidate Selection (Boss Online → CEO) =====

@router.post("/api/candidates/hire")
async def hire_candidate(body: dict) -> dict:
    """CEO selects a candidate to hire from the shortlist."""
    from onemancompany.agents.hr_agent import hr_agent, pending_candidates

    batch_id = body.get("batch_id", "")
    candidate_id = body.get("candidate_id", "")
    nickname = body.get("nickname", "")

    candidates = pending_candidates.get(batch_id, [])
    candidate = next((c for c in candidates if c.get("id") == candidate_id), None)
    if not candidate:
        return {"error": "Candidate not found"}

    # Build hire JSON and let HR apply it
    skill_names = [s["name"] if isinstance(s, dict) else s for s in candidate.get("skill_set", [])]
    hire_json = json.dumps({
        "action": "hire",
        "employee": {
            "id": candidate["id"],
            "name": candidate["name"],
            "nickname": nickname or "",
            "role": candidate["role"],
            "skills": skill_names,
            "sprite": candidate.get("sprite", "employee_default"),
        },
    })
    await hr_agent._apply_results(f"```json\n{hire_json}\n```")
    pending_candidates.pop(batch_id, None)

    # Explicit state broadcast to ensure frontend updates
    await event_bus.publish(
        CompanyEvent(type="state_snapshot", payload={}, agent="CEO")
    )

    return {"status": "hired", "employee_id": candidate["id"], "name": candidate["name"]}


@router.post("/api/candidates/interview")
async def interview_candidate(body: dict) -> dict:
    """CEO interviews a candidate by asking a question. Returns the candidate's answer."""
    question = body.get("question", "")
    candidate = body.get("candidate", {})

    if not question or not candidate:
        return {"error": "Missing question or candidate data"}

    # Use the candidate's system_prompt to generate an answer
    from onemancompany.agents.base import make_llm
    from langchain_core.messages import HumanMessage, SystemMessage

    system_prompt = candidate.get("system_prompt", "你是一名求职者。")
    skill_desc = ", ".join(
        s["name"] if isinstance(s, dict) else s for s in candidate.get("skill_set", [])
    )
    full_prompt = (
        f"{system_prompt}\n\n"
        f"你正在面试，你的名字是 {candidate.get('name', '候选人')}，"
        f"你的角色是 {candidate.get('role', '工程师')}，"
        f"你的技能包括: {skill_desc}。\n"
        f"请认真回答面试问题，展示你的专业能力。用中文回答。"
    )

    llm = make_llm("hr")  # use HR's model for simulation
    result = await llm.ainvoke([
        SystemMessage(content=full_prompt),
        HumanMessage(content=question),
    ])

    return {
        "candidate_id": candidate.get("id", ""),
        "question": question,
        "answer": result.content,
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
