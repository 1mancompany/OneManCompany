"""Common tools available to ALL employees — default tools every employee has.

The main tool here is `pull_meeting` (pull meeting / sync-up): any employee can pull
relevant colleagues into a meeting room for a focused discussion.
"""

from __future__ import annotations

import asyncio
import json
import re

from langchain_core.tools import tool
from loguru import logger

from onemancompany.agents.base import get_employee_skills_prompt, get_employee_tools_prompt, make_llm, tracked_ainvoke
from onemancompany.core.config import COO_ID, HR_ID, MAX_DISCUSSION_SUMMARY_LEN, MAX_PRINCIPLES_LEN
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.state import company_state
from onemancompany.tools.sandbox import SANDBOX_TOOLS

# Context vars for sub-task support — set by PersistentAgentLoop during execution
from onemancompany.core.agent_loop import _current_loop, _current_task_id


async def _publish(event_type: str, payload: dict, agent: str = "MEETING") -> None:
    await event_bus.publish(CompanyEvent(type=event_type, payload=payload, agent=agent))


async def _chat(room_id: str, speaker: str, role: str, message: str) -> None:
    await _publish("meeting_chat", {
        "room_id": room_id,
        "speaker": speaker,
        "role": role,
        "message": message,
    })


@tool
def read_file(file_path: str, employee_id: str = "") -> dict:
    """Read the contents of a file.

    Access scope depends on your permissions:
    - company/ files: available to all employees with company_file_access
    - src/ files: requires backend_code_maintenance permission (use "src/..." path)

    Args:
        file_path: File path, e.g. "human_resource/employees/00002/profile.yaml" or "src/onemancompany/api/routes.py"
        employee_id: Your employee ID (for permission check)

    Returns:
        A dict containing the file contents, or an error message.
    """
    from onemancompany.core.file_editor import _resolve_path

    permissions = []
    if employee_id:
        emp = company_state.employees.get(employee_id)
        if emp:
            permissions = emp.permissions

    resolved = _resolve_path(file_path, permissions=permissions)
    if resolved is None:
        return {"status": "error", "message": f"Access denied or invalid path: {file_path}"}
    if not resolved.exists():
        return {"status": "error", "message": f"File not found: {file_path}"}
    if not resolved.is_file():
        return {"status": "error", "message": f"Not a file: {file_path}"}
    try:
        content = resolved.read_text(encoding="utf-8")
        return {"status": "ok", "path": file_path, "content": content}
    except Exception as e:
        return {"status": "error", "message": f"Read failed: {e}"}


@tool
def list_directory(dir_path: str = "", employee_id: str = "") -> dict:
    """List files and subdirectories.

    Access scope depends on your permissions:
    - company/ directories: available to all employees
    - src/ directories: requires backend_code_maintenance permission (use "src/..." path)

    Args:
        dir_path: Directory path, e.g. "business/projects" or "src/onemancompany/core". Empty = company root.
        employee_id: Your employee ID (for permission check)

    Returns:
        A dict containing the list of entries.
    """
    from onemancompany.core.file_editor import _resolve_path

    permissions = []
    if employee_id:
        emp = company_state.employees.get(employee_id)
        if emp:
            permissions = emp.permissions

    resolved = _resolve_path(dir_path or ".", permissions=permissions)
    if resolved is None:
        return {"status": "error", "message": f"Access denied or invalid path: {dir_path}"}
    if not resolved.exists() or not resolved.is_dir():
        return {"status": "error", "message": f"Directory not found: {dir_path}"}
    try:
        entries = []
        for item in sorted(resolved.iterdir()):
            if item.name.startswith("."):
                continue  # skip hidden files
            entries.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
            })
        return {"status": "ok", "path": dir_path or ".", "entries": entries}
    except Exception as e:
        return {"status": "error", "message": f"Failed to read directory: {e}"}


@tool
async def propose_file_edit(
    file_path: str,
    new_content: str,
    reason: str,
    proposed_by: str = "",
    employee_id: str = "",
) -> dict:
    """Propose a file edit request (requires CEO approval before execution).

    Can edit files under the company directory. Employees with backend_code_maintenance
    permission can also propose edits to files under src/ (use "src/..." path).

    After submission, the CEO will see a diff comparison on the frontend.
    The edit is executed automatically once approved.
    The original file is backed up (timestamped) before execution for easy rollback.

    Args:
        file_path: File path relative to the company/ root, or "src/..." for backend code
        new_content: The complete new file content after editing
        reason: Explanation for the edit
        employee_id: Your employee ID (for permission check)

    Returns:
        Edit request status (pending_approval means submitted and awaiting approval).
    """
    from onemancompany.core.file_editor import propose_edit

    permissions = []
    if employee_id:
        emp = company_state.employees.get(employee_id)
        if emp:
            permissions = emp.permissions

    # Determine who is proposing (from the call context, default to unknown)
    # The agent name will be injected by the caller
    result = propose_edit(file_path, new_content, reason, proposed_by=proposed_by or "agent", permissions=permissions)
    if result["status"] == "error":
        return result

    from onemancompany.core.file_editor import pending_file_edits
    edit = pending_file_edits.get(result["edit_id"])
    if not edit:
        return result

    # If running inside a task (project_id context is set), collect for
    # batch resolution review instead of publishing an immediate event.
    from onemancompany.core.resolutions import current_project_id, collect_edit
    pid = current_project_id.get("")
    if pid:
        collect_edit(pid, edit)
    else:
        # No task context — fall back to immediate event (old behavior)
        await _publish("file_edit_proposed", {
            "edit_id": edit["edit_id"],
            "rel_path": edit["rel_path"],
            "reason": edit["reason"],
            "proposed_by": edit["proposed_by"],
            "old_content": edit["old_content"],
            "new_content": edit["new_content"],
        })

    return result


@tool
def save_to_project(project_dir: str, filename: str, content: str) -> dict:
    """Save a file to the current project workspace directory.

    Use this to persist any output, code, report, or intermediate result for the project.
    The project_dir is provided in the task description as [Project workspace: ...].

    Args:
        project_dir: The project workspace path (from the task context).
        filename: File name or relative sub-path (e.g. "report.md", "code/main.py").
        content: The text content to save.

    Returns:
        A dict with status and the saved file path.
    """
    from pathlib import Path
    from onemancompany.core.config import PROJECTS_DIR

    project_path = Path(project_dir)
    if not str(project_path.resolve()).startswith(str(PROJECTS_DIR.resolve())):
        return {"status": "error", "message": "Invalid project directory"}

    # Write directly to the workspace path (don't derive project_id from path)
    file_path = project_path / filename
    resolved = file_path.resolve()
    if not str(resolved).startswith(str(project_path.resolve())):
        return {"status": "error", "message": f"Path escapes project directory: {filename}"}

    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")

    return {"status": "ok", "path": str(file_path), "relative": filename}


@tool
def list_project_workspace(project_dir: str) -> dict:
    """List all files in the current project workspace.

    Args:
        project_dir: The project workspace path (from the task context).

    Returns:
        A dict with the list of files in the project workspace.
    """
    from pathlib import Path
    from onemancompany.core.config import PROJECTS_DIR

    project_path = Path(project_dir)
    if not str(project_path.resolve()).startswith(str(PROJECTS_DIR.resolve())):
        return {"status": "error", "message": "Invalid project directory"}

    if not project_path.exists():
        return {"status": "ok", "project_dir": project_dir, "files": []}

    files = []
    for p in sorted(project_path.rglob("*")):
        if p.is_file():
            files.append(str(p.relative_to(project_path)))
    return {"status": "ok", "project_dir": project_dir, "files": files}


@tool
def list_colleagues() -> list[dict]:
    """List information about all colleagues, useful for deciding who to invite to a meeting.

    Returns:
        A list of dicts with id, name, nickname, role, department, level, skills.
    """
    return [
        {
            "id": emp.id,
            "name": emp.name,
            "nickname": emp.nickname,
            "role": emp.role,
            "department": emp.department,
            "level": emp.level,
            "skills": emp.skills,
        }
        for emp in company_state.employees.values()
    ]


def _build_employee_context(emp) -> str:
    """Build identity + skills + tools context string for an employee."""
    principles_ctx = ""
    if emp.work_principles:
        principles_ctx = f"\nYour work principles:\n{emp.work_principles[:MAX_PRINCIPLES_LEN]}\n"
    skills_ctx = get_employee_skills_prompt(emp.id)
    tools_ctx = get_employee_tools_prompt(emp.id)
    return (
        f"You are {emp.name} ({emp.nickname}, Department: {emp.department}, {emp.role}, Lv.{emp.level}).\n"
        f"{principles_ctx}{skills_ctx}{tools_ctx}"
    )


def _format_chat_history(chat_history: list[dict]) -> str:
    """Format chat history list into a readable string."""
    if not chat_history:
        return "(No discussion yet.)"
    return "\n".join(f"  {m['speaker']}: {m['message']}" for m in chat_history)


def _build_evaluate_prompt(emp, topic: str, agenda: str, chat_history: list[dict]) -> str:
    """Build a prompt asking the employee whether they need to speak."""
    ctx = _build_employee_context(emp)
    history_text = _format_chat_history(chat_history)
    prompt = (
        f"{ctx}"
        f"You are attending a focused meeting.\n"
        f"Meeting topic: {topic}\n"
    )
    if agenda:
        prompt += f"Meeting agenda: {agenda}\n"
    prompt += (
        f"\nDiscussion so far:\n{history_text}\n\n"
        f"Decide whether you need to speak next. Answer YES only if you have a unique perspective, "
        f"an important concern, or actionable advice that has NOT already been covered. "
        f"Answer NO if the topic is outside your expertise, or your viewpoint has already been expressed by others.\n\n"
        f"Reply with YES or NO on the first line, then optionally a brief reason."
    )
    return prompt


def _build_speech_prompt(emp, topic: str, agenda: str, chat_history: list[dict]) -> str:
    """Build a prompt for the employee to deliver their contribution."""
    ctx = _build_employee_context(emp)
    history_text = _format_chat_history(chat_history)
    prompt = (
        f"{ctx}"
        f"You are attending a focused meeting.\n"
        f"Meeting topic: {topic}\n"
    )
    if agenda:
        prompt += f"Meeting agenda: {agenda}\n"
    prompt += (
        f"\nDiscussion so far:\n{history_text}\n\n"
        f"Based on your expertise and work principles, share your brief perspective (2-3 sentences). "
        f"Focus on what you can uniquely contribute from your role — suggestions, concerns, or actionable advice."
    )
    return prompt


@tool
async def pull_meeting(
    topic: str,
    participant_ids: list[str],
    agenda: str = "",
    initiator_id: str = "",
) -> dict:
    """Pull meeting / sync-up — initiate a multi-person discussion with colleagues.

    Meetings are ONLY for communication and discussion between 2+ people.
    If you need to think or plan alone, do it internally — never call a meeting with yourself.

    Automatically books a meeting room, organizes participants for discussion, and outputs meeting conclusions.
    Uses a token-grabbing mechanism: participants concurrently evaluate whether they need to speak,
    and the fastest respondent gets the floor. Meeting ends naturally when no one has more to say.

    Args:
        topic: Meeting topic, e.g. "Discuss technical plan for new feature"
        participant_ids: List of colleague IDs who should attend (must be 2+ people including yourself)
        agenda: Optional meeting agenda
        initiator_id: Initiator's ID (auto-filled, can be left empty)

    Returns:
        Meeting result, including discussion summary and action items.
    """
    # Validate participants
    valid_participants = []
    for pid in participant_ids:
        emp = company_state.employees.get(pid)
        if emp:
            valid_participants.append(emp)

    if not valid_participants:
        return {"status": "error", "message": "No valid participants found. Please check employee IDs."}

    # Prevent solo meetings — need at least 2 distinct people
    all_unique = set(pid for pid in participant_ids if company_state.employees.get(pid))
    if initiator_id:
        all_unique.add(initiator_id)
    if len(all_unique) < 2:
        return {
            "status": "error",
            "message": "A meeting requires at least 2 participants. "
            "Do not hold meetings alone — work on the task directly or dispatch it to someone.",
        }

    # Find a free meeting room
    room = None
    all_ids = [initiator_id] + participant_ids if initiator_id else participant_ids
    for r in company_state.meeting_rooms.values():
        if not r.is_booked and r.capacity >= len(all_ids):
            r.is_booked = True
            r.booked_by = initiator_id or participant_ids[0]
            r.participants = all_ids
            room = r
            break

    if not room:
        return {
            "status": "denied",
            "message": "No meeting rooms available. Please try again later or work on other tasks.",
        }

    # Publish booking event
    await _publish("meeting_booked", {
        "room_id": room.id,
        "room_name": room.name,
        "participants": room.participants,
    })

    initiator_name = "Initiator"
    if initiator_id:
        ini_emp = company_state.employees.get(initiator_id)
        if ini_emp:
            initiator_name = ini_emp.nickname or ini_emp.name

    await _chat(room.id, initiator_name, "employee",
                f"Hello everyone, I've initiated this meeting. Topic: {topic}")

    if agenda:
        await _chat(room.id, initiator_name, "employee", f"Agenda: {agenda}")

    try:
        # --- Token-grabbing discussion loop ---
        discussion_entries: list[dict] = []
        chat_history: list[dict] = [
            {"speaker": initiator_name, "message": f"Topic: {topic}"},
        ]
        if agenda:
            chat_history.append({"speaker": initiator_name, "message": f"Agenda: {agenda}"})

        # All participants (including initiator if present) can compete to speak
        speakers = list(valid_participants)
        if initiator_id:
            ini_emp = company_state.employees.get(initiator_id)
            if ini_emp and ini_emp not in speakers:
                speakers.append(ini_emp)

        max_rounds = 15
        loop = asyncio.get_running_loop()
        rounds_used = 0
        last_speaker_id: str = ""  # track last speaker for no-consecutive rule

        for round_num in range(max_rounds):
            rounds_used = round_num + 1

            # Concurrent evaluation — all participants judge whether they need to speak
            async def _evaluate(emp):
                prompt = _build_evaluate_prompt(emp, topic, agenda, chat_history)
                llm = make_llm(emp.id)
                t0 = loop.time()
                resp = await tracked_ainvoke(llm, prompt, category="meeting", employee_id=emp.id)
                t1 = loop.time()
                first_line = resp.content.strip().split("\n")[0].upper()[:20]
                wants = "YES" in first_line
                return (emp, wants, t1)

            results = await asyncio.gather(
                *[_evaluate(e) for e in speakers],
                return_exceptions=True,
            )

            # Filter out exceptions and those who don't want to speak
            willing = [
                (emp, ts)
                for r in results
                if not isinstance(r, Exception)
                for emp, wants, ts in [r]
                if wants
            ]

            if not willing:
                await _chat(room.id, "会议系统", "system", "所有人已表达完毕，会议结束。")
                break

            # Token grab — sort by timestamp, fastest wins
            willing.sort(key=lambda x: x[1])

            # No-consecutive rule: same person cannot get the token twice in a row
            winner = willing[0][0]
            if winner.id == last_speaker_id and len(willing) > 1:
                winner = willing[1][0]

            # Winner delivers their speech
            speech_prompt = _build_speech_prompt(winner, topic, agenda, chat_history)
            resp = await tracked_ainvoke(make_llm(winner.id), speech_prompt, category="meeting", employee_id=winner.id)
            last_speaker_id = winner.id

            display = winner.nickname or winner.name
            await _chat(room.id, display, winner.role, resp.content)
            chat_history.append({"speaker": display, "message": resp.content})
            discussion_entries.append({
                "id": winner.id,
                "name": winner.name,
                "nickname": winner.nickname,
                "comment": resp.content,
            })
        else:
            # max_rounds reached
            await _chat(room.id, "会议系统", "system", "会议已达最大轮次，自动结束。")

        # --- Synthesize meeting conclusion ---
        all_comments = "\n".join(
            f"[{d['name']}({d['nickname']})] {d['comment']}"
            for d in discussion_entries
        )
        summary_llm = make_llm(initiator_id or HR_ID)
        summary_prompt = (
            f"You are the meeting note-taker. Summarize the following focused meeting discussion.\n\n"
            f"Meeting topic: {topic}\n"
            f"Participants: {', '.join(e.nickname or e.name for e in valid_participants)}\n\n"
            f"Discussion:\n{all_comments}\n\n"
            f"Please output:\n"
            f"1. Meeting conclusions (2-3 sentences)\n"
            f"2. Action items (JSON array format): "
            f'[{{"assignee": "person responsible", "action": "specific action"}}]\n'
        )
        summary_resp = await tracked_ainvoke(summary_llm, summary_prompt, category="meeting", employee_id=initiator_id or HR_ID)
        summary_text = summary_resp.content

        await _chat(room.id, "Meeting Notes", "HR", f"[Meeting Summary] {summary_text[:200]}")

        # Parse action items
        action_items = []
        try:
            json_match = re.search(r'\[.*\]', summary_text, re.DOTALL)
            if json_match:
                action_items = json.loads(json_match.group())
        except (json.JSONDecodeError, AttributeError) as _e:
            logger.debug("Failed to parse meeting action items: {}", _e)

        company_state.activity_log.append({
            "type": "pull_meeting",
            "topic": topic,
            "initiator": initiator_id,
            "participants": [e.id for e in valid_participants],
            "room": room.name,
            "rounds": rounds_used,
        })

        return {
            "status": "completed",
            "room": room.name,
            "topic": topic,
            "participants": [e.nickname or e.name for e in valid_participants],
            "discussion": discussion_entries,
            "summary": summary_text[:MAX_DISCUSSION_SUMMARY_LEN],
            "action_items": action_items,
            "rounds": rounds_used,
        }

    finally:
        # Release meeting room
        room.is_booked = False
        room.booked_by = ""
        room.participants = []
        await _publish("meeting_released", {
            "room_id": room.id, "room_name": room.name,
        })


@tool
def use_tool(tool_name_or_id: str, employee_id: str) -> dict:
    """Use a company tool — checks authorization and returns tool details + file contents.

    Employees must be authorized (in allowed_users) to use restricted tools.
    Open-access tools (empty allowed_users) are available to everyone.

    Args:
        tool_name_or_id: The tool's ID, name (case-insensitive), or folder_name.
        employee_id: The employee ID requesting access.

    Returns:
        Tool metadata and file contents if authorized, or an access-denied message.
    """
    from pathlib import Path
    from onemancompany.core.config import TOOLS_DIR

    # Look up tool: by ID first, then name, then folder_name
    found: "OfficeTool | None" = None
    found = company_state.tools.get(tool_name_or_id)
    if not found:
        needle = tool_name_or_id.lower()
        for t in company_state.tools.values():
            if t.name.lower() == needle:
                found = t
                break
        if not found:
            for t in company_state.tools.values():
                if t.folder_name == tool_name_or_id:
                    found = t
                    break

    if not found:
        return {"status": "error", "message": f"Tool '{tool_name_or_id}' not found."}

    # Auth check
    if found.allowed_users and employee_id not in found.allowed_users:
        return {
            "status": "denied",
            "message": f"Access denied: employee {employee_id} is not authorized to use '{found.name}'.",
            "allowed_users": found.allowed_users,
        }

    # Build result with tool metadata
    result: dict = {
        "status": "ok",
        "id": found.id,
        "name": found.name,
        "description": found.description,
        "folder_name": found.folder_name,
        "files": {},
    }

    # Read file contents from the tool folder
    if found.folder_name:
        tool_folder = TOOLS_DIR / found.folder_name
        if tool_folder.is_dir():
            for fname in found.files:
                fpath = tool_folder / fname
                if not fpath.is_file():
                    continue
                # Skip binary files — just report size
                try:
                    content = fpath.read_text(encoding="utf-8")
                    result["files"][fname] = content
                except (UnicodeDecodeError, ValueError):
                    result["files"][fname] = f"[binary file, {fpath.stat().st_size} bytes]"

    return result


@tool
def create_subtask(description: str) -> dict:
    """Queue a sub-task on your task board for later processing.

    Use this when you need to decompose a complex task into smaller pieces.
    The sub-task will be executed after the current task completes.

    Args:
        description: What the sub-task should accomplish.

    Returns:
        A dict with the queued sub-task ID, or an error if no agent loop context.
    """
    loop = _current_loop.get()
    parent_id = _current_task_id.get()
    if not loop:
        return {"error": "No agent loop context — sub-tasks require an active task execution."}
    sub = loop.board.push(description, parent_id=parent_id)
    return {"status": "queued", "subtask_id": sub.id, "description": description}


@tool
def set_acceptance_criteria(criteria: list[str], responsible_officer_id: str) -> dict:
    """Set acceptance criteria for the current project.

    The EA should call this BEFORE dispatching tasks. The responsible xxO
    can also call this later to refine/update criteria.

    Args:
        criteria: List of specific, measurable acceptance criteria
        responsible_officer_id: Employee ID of the xxO who will judge acceptance ('00003' for COO, '00005' for CSO)
    """
    from onemancompany.core.agent_loop import _current_loop, _current_task_id
    from onemancompany.core.project_archive import set_acceptance_criteria as _set_criteria

    loop = _current_loop.get()
    task_id = _current_task_id.get()
    if not loop or not task_id:
        return {"status": "error", "message": "No agent loop context."}

    task = loop.board.get_task(task_id)
    if not task:
        return {"status": "error", "message": "Current task not found."}

    project_id = task.project_id or task.original_project_id
    if not project_id:
        return {"status": "error", "message": "No project context — acceptance criteria require a project."}

    _set_criteria(project_id, criteria, responsible_officer_id)
    return {
        "status": "ok",
        "project_id": project_id,
        "criteria_count": len(criteria),
        "responsible_officer": responsible_officer_id,
    }


@tool
def accept_project(accepted: bool, notes: str = "") -> dict:
    """Accept or reject a project after reviewing acceptance criteria.

    Called by the responsible xxO during acceptance review.
    If accepted, the project will be marked complete and retrospective will run.
    If rejected, notes explaining what needs to be fixed are recorded.

    Args:
        accepted: True if ALL acceptance criteria are met
        notes: Explanation of the acceptance decision
    """
    from onemancompany.core.agent_loop import _current_loop, _current_task_id
    from onemancompany.core.project_archive import set_acceptance_result, load_project

    loop = _current_loop.get()
    task_id = _current_task_id.get()
    if not loop or not task_id:
        return {"status": "error", "message": "No agent loop context."}

    task = loop.board.get_task(task_id)
    if not task:
        return {"status": "error", "message": "Current task not found."}

    project_id = task.project_id or task.original_project_id
    if not project_id:
        return {"status": "error", "message": "No project context."}

    officer_id = loop.agent.employee_id
    set_acceptance_result(project_id, accepted, officer_id, notes)

    status = "accepted" if accepted else "rejected"
    return {
        "status": status,
        "project_id": project_id,
        "officer_id": officer_id,
        "notes": notes,
    }


@tool
def ea_review_project(approved: bool, review_notes: str) -> dict:
    """EA final quality review on behalf of CEO.

    Called by the EA after the responsible officer has already accepted the project.
    This is the CEO's quality gate — EA must verify the deliverables actually meet
    ALL requirements before the project is truly considered complete.

    If approved, the project proceeds to retrospective and completion.
    If rejected, a rectification task is pushed back to the responsible officer.

    Args:
        approved: True if EA confirms all deliverables meet requirements
        review_notes: Detailed review notes — what was checked, evidence of verification,
                      and (if rejected) specific issues that need to be fixed
    """
    from onemancompany.core.agent_loop import _current_loop, _current_task_id
    from onemancompany.core.project_archive import set_ea_review_result, load_project

    loop = _current_loop.get()
    task_id = _current_task_id.get()
    if not loop or not task_id:
        return {"status": "error", "message": "No agent loop context."}

    task = loop.board.get_task(task_id)
    if not task:
        return {"status": "error", "message": "Current task not found."}

    project_id = task.project_id or task.original_project_id
    if not project_id:
        return {"status": "error", "message": "No project context."}

    set_ea_review_result(project_id, approved, review_notes)

    status = "approved" if approved else "rejected"
    return {
        "status": status,
        "project_id": project_id,
        "review_notes": review_notes[:300],
    }


@tool
def set_project_budget(budget_usd: float) -> dict:
    """Set the estimated budget for the current project (in USD).
    Call this BEFORE dispatching tasks to establish a cost baseline.

    Args:
        budget_usd: Estimated budget in USD for LLM costs.
    """
    from onemancompany.core.agent_loop import _current_loop, _current_task_id
    from onemancompany.core.project_archive import set_project_budget as _set_budget

    loop = _current_loop.get()
    task_id = _current_task_id.get()
    if not loop or not task_id:
        return {"status": "error", "message": "No agent loop context."}

    task = loop.board.get_task(task_id)
    if not task:
        return {"status": "error", "message": "Current task not found."}

    project_id = task.project_id or task.original_project_id
    if not project_id:
        return {"status": "error", "message": "No project context."}

    _set_budget(project_id, budget_usd)
    return {"status": "ok", "project_id": project_id, "budget_usd": budget_usd}


@tool
def dispatch_task(employee_id: str, task_description: str) -> dict:
    """Dispatch a task to another employee's task board.

    Use this to delegate work to other agents (e.g. assign HR-sourced actions to the HR agent).
    The task will be queued on the target employee's board and executed autonomously
    via their registered launcher (LangChain agent, Claude CLI, etc.).
    The project context (project_id, project_dir) is automatically inherited from
    the current task so that post-task routines run after the *final* executor finishes.

    Args:
        employee_id: The target employee's ID (e.g. '00002' for HR).
        task_description: Clear description of what the employee should do.

    Returns:
        Confirmation that the task was pushed, or an error.
    """
    from onemancompany.core.agent_loop import get_agent_loop, _current_loop, _current_task_id

    loop = get_agent_loop(employee_id)
    if not loop:
        # Not registered in EmployeeManager — check if remote employee
        emp = company_state.employees.get(employee_id)
        if not emp:
            return {"status": "error", "message": f"Employee {employee_id} not found"}
        if emp.remote:
            # Remote employee: push to remote task queue
            import uuid as _uuid
            project_id = ""
            project_dir = ""
            caller_loop = _current_loop.get()
            caller_task_id = _current_task_id.get()
            if caller_loop and caller_task_id:
                caller_task = caller_loop.board.get_task(caller_task_id)
                if caller_task:
                    project_id = caller_task.project_id or caller_task.original_project_id
                    project_dir = caller_task.project_dir or caller_task.original_project_dir
                    if project_id:
                        caller_task.original_project_id = project_id
                        caller_task.original_project_dir = project_dir
                        caller_task.project_id = ""
            if project_id:
                from onemancompany.core.project_archive import record_dispatch
                record_dispatch(project_id, employee_id, task_description)
            from onemancompany.api.routes import _remote_task_queues
            if employee_id not in _remote_task_queues:
                _remote_task_queues[employee_id] = []
            _remote_task_queues[employee_id].append({
                "task_id": str(_uuid.uuid4())[:8],
                "description": task_description,
                "project_id": project_id,
                "project_dir": project_dir,
            })
            return {"status": "dispatched_remote", "employee": emp.name, "task": task_description[:200]}
        return {"status": "error", "message": f"No launcher registered for employee {employee_id}"}

    # Inherit project context — support multi-dispatch by checking original_project_id
    project_id = ""
    project_dir = ""
    caller_loop = _current_loop.get()
    caller_task_id = _current_task_id.get()
    if caller_loop and caller_task_id:
        caller_task = caller_loop.board.get_task(caller_task_id)
        if caller_task:
            project_id = caller_task.project_id or caller_task.original_project_id
            project_dir = caller_task.project_dir or caller_task.original_project_dir
            if project_id:
                caller_task.original_project_id = project_id
                caller_task.original_project_dir = project_dir
                caller_task.project_id = ""

    # Record dispatch in project archive
    if project_id:
        from onemancompany.core.project_archive import record_dispatch
        record_dispatch(project_id, employee_id, task_description)

    emp = company_state.employees.get(employee_id)
    emp_name = emp.name if emp else employee_id
    loop.push_task(task_description, project_id=project_id, project_dir=project_dir)
    return {"status": "dispatched", "employee": emp_name, "task": task_description[:200]}


@tool
def request_tool_access(tool_name: str, reason: str, employee_id: str = "") -> dict:
    """Request access to a restricted tool. The request will be sent to COO for approval.

    Use this when you need a tool that you don't currently have permission for.
    COO will evaluate based on your role and responsibilities.

    Args:
        tool_name: Name of the tool to request access to.
        reason: Why you need this tool for your current work.
        employee_id: Your employee ID.

    Returns:
        Status of the request.
    """
    emp = company_state.employees.get(employee_id)
    if not emp:
        return {"status": "error", "message": "Employee not found."}

    if tool_name in (emp.tool_permissions or []):
        return {"status": "already_granted", "message": f"You already have access to '{tool_name}'."}

    # Check tool exists
    if tool_name not in GATED_TOOLS:
        return {"status": "error", "message": f"Unknown tool '{tool_name}'. Available tools: {', '.join(GATED_TOOLS.keys())}"}

    # Dispatch to COO
    from onemancompany.core.agent_loop import get_agent_loop
    loop = get_agent_loop(COO_ID)
    if not loop:
        return {"status": "error", "message": "COO agent not available."}

    task_desc = (
        f"Tool access request: Employee {emp.name} (ID: {emp.id}, {emp.department}/{emp.role}, Lv.{emp.level}) "
        f"requests access to tool '{tool_name}'. Reason: {reason}. "
        f"Evaluate whether this is appropriate for their role and department. "
        f"If approved, call manage_tool_access(employee_id='{emp.id}', tool_name='{tool_name}', action='grant')."
    )
    loop.push_task(task_desc)
    return {"status": "requested", "message": f"Access request for '{tool_name}' sent to COO for review."}


@tool
def manage_tool_access(employee_id: str, tool_name: str, action: str, manager_id: str = "") -> dict:
    """Grant or revoke LangChain tool access for an employee. Only COO can use this.

    Args:
        employee_id: Target employee's ID.
        tool_name: Name of the tool to grant or revoke.
        action: "grant" or "revoke".
        manager_id: Your employee ID (must be COO).

    Returns:
        Updated tool permissions for the employee.
    """
    if manager_id != COO_ID:
        return {"status": "denied", "message": "Only COO (00003) can manage tool access."}

    emp = company_state.employees.get(employee_id)
    if not emp:
        return {"status": "error", "message": f"Employee {employee_id} not found."}

    if action == "grant":
        if tool_name not in (emp.tool_permissions or []):
            if emp.tool_permissions is None:
                emp.tool_permissions = []
            emp.tool_permissions.append(tool_name)
    elif action == "revoke":
        if emp.tool_permissions and tool_name in emp.tool_permissions:
            emp.tool_permissions.remove(tool_name)
    else:
        return {"status": "error", "message": f"Invalid action: {action}. Use 'grant' or 'revoke'."}

    # Persist to disk
    from onemancompany.core.config import update_tool_permissions
    update_tool_permissions(employee_id, emp.tool_permissions or [])

    return {
        "status": "ok",
        "employee": employee_id,
        "tool": tool_name,
        "action": action,
        "current_tool_permissions": emp.tool_permissions,
    }


# ---------------------------------------------------------------------------
# Tool categorization
# ---------------------------------------------------------------------------

# Base tools — always available to every employee, no permission check needed
BASE_TOOLS = [
    list_colleagues,
    save_to_project,
    list_project_workspace,
    pull_meeting,
    create_subtask,
    dispatch_task,
    request_tool_access,
]

# Gated tools — regular employees need these in their tool_permissions list
GATED_TOOLS: dict = {
    "read_file": read_file,
    "list_directory": list_directory,
    "propose_file_edit": propose_file_edit,
    "use_tool": use_tool,
    "set_acceptance_criteria": set_acceptance_criteria,
    "accept_project": accept_project,
    "ea_review_project": ea_review_project,
    "set_project_budget": set_project_budget,
}

# Add sandbox tools to gated pool
for _st in SANDBOX_TOOLS:
    GATED_TOOLS[_st.name] = _st

# Full set for founding agents (backward compat — includes all non-sandbox tools + manage_tool_access)
COMMON_TOOLS = [
    read_file,
    list_directory,
    propose_file_edit,
    save_to_project,
    list_project_workspace,
    list_colleagues,
    pull_meeting,
    use_tool,
    create_subtask,
    dispatch_task,
    set_acceptance_criteria,
    accept_project,
    ea_review_project,
    set_project_budget,
    manage_tool_access,
]
