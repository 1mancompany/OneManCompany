"""Common tools available to ALL employees — default tools every employee has.

The main tool here is `pull_meeting` (pull meeting / sync-up): any employee can pull
relevant colleagues into a meeting room for a focused discussion.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime

from langchain_core.tools import tool
from loguru import logger

from onemancompany.agents.base import get_employee_skills_prompt, get_employee_tools_prompt, make_llm, tracked_ainvoke
from onemancompany.core.config import COO_ID, HR_ID, MAX_DISCUSSION_SUMMARY_LEN, MAX_PRINCIPLES_LEN, PROJECTS_DIR, get_workspace_dir
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.state import company_state

# ---------------------------------------------------------------------------
# CEO approval wait mechanism — report_to_ceo blocks until CEO responds
# ---------------------------------------------------------------------------
# Key: "employee_id:project_id" → {"event": asyncio.Event, "response": dict}
_ceo_pending: dict[str, dict] = {}


def _ceo_wait_key(employee_id: str, project_id: str) -> str:
    return f"{employee_id}:{project_id or '_'}"


def resolve_ceo_pending(employee_id: str, project_id: str, response: dict) -> bool:
    """Called from /api/ceo/respond to unblock a waiting report_to_ceo call."""
    key = _ceo_wait_key(employee_id, project_id)
    entry = _ceo_pending.get(key)
    if not entry:
        return False
    entry["response"] = response
    entry["event"].set()
    return True
from onemancompany.tools.sandbox import SANDBOX_TOOLS

# Context vars for sub-task support — set by Vessel during execution
from onemancompany.core.agent_loop import _current_vessel, _current_task_id


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
def read(file_path: str, employee_id: str = "") -> dict:
    """Read the contents of a file.

    Accessible paths:
    - Your workspace: "workspace/..." (your private workspace directory)
    - Company files: "company/..." or relative paths like "human_resource/..."
    - Source code: "src/..." (requires backend_code_maintenance permission)
    - Project files: full project workspace path

    Args:
        file_path: File path to read.
        employee_id: Your employee ID.
    """
    from onemancompany.core.file_editor import _resolve_path

    # Handle "workspace/..." shortcut → resolve to employee's workspace dir
    if file_path.startswith("workspace/") and employee_id:
        from pathlib import Path
        resolved = (get_workspace_dir(employee_id) / file_path[len("workspace/"):]).resolve()
    else:
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
def ls(dir_path: str = "", employee_id: str = "") -> dict:
    """List files and subdirectories.

    Accessible paths:
    - Your workspace: "workspace" or "workspace/subdir"
    - Company directories: "business/projects", "human_resource/employees", etc.
    - Source code: "src/onemancompany/core" (requires permission)
    - Project workspace: full project workspace path

    Args:
        dir_path: Directory path. Empty = company root.
        employee_id: Your employee ID.
    """
    from pathlib import Path
    from onemancompany.core.file_editor import _resolve_path

    # Handle "workspace" or "workspace/..." shortcut
    if employee_id and (dir_path == "workspace" or dir_path.startswith("workspace/")):
        suffix = dir_path[len("workspace"):].lstrip("/")
        resolved = (get_workspace_dir(employee_id) / suffix).resolve() if suffix else get_workspace_dir(employee_id).resolve()
    # Handle absolute project workspace paths
    elif dir_path and Path(dir_path).is_absolute() and str(Path(dir_path).resolve()).startswith(str(PROJECTS_DIR.resolve())):
        resolved = Path(dir_path).resolve()
    else:
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
                continue
            entries.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
            })
        return {"status": "ok", "path": dir_path or ".", "entries": entries}
    except Exception as e:
        return {"status": "error", "message": f"Failed to read directory: {e}"}




@tool
async def write(
    file_path: str,
    content: str,
    employee_id: str = "",
    project_dir: str = "",
) -> dict:
    """Write content to a file. Creates the file if it doesn't exist.

    Free zones (no approval needed):
    - Your workspace: "workspace/notes.md", "workspace/plan.md", etc.
    - Project workspace: files inside the current project_dir

    Other locations (company/, src/) require CEO approval.

    Args:
        file_path: File path to write.
        content: The text content to write.
        employee_id: Your employee ID.
        project_dir: Current project workspace path (auto-filled from task context).
    """
    from pathlib import Path
    from onemancompany.core.file_editor import _resolve_path, is_in_free_zone

    # Resolve path
    if file_path.startswith("workspace/") and employee_id:
        resolved = (get_workspace_dir(employee_id) / file_path[len("workspace/"):]).resolve()
    elif Path(file_path).is_absolute():
        resolved = Path(file_path).resolve()
    else:
        permissions = []
        if employee_id:
            emp = company_state.employees.get(employee_id)
            if emp:
                permissions = emp.permissions
        resolved = _resolve_path(file_path, permissions=permissions)

    if resolved is None:
        return {"status": "error", "message": f"Access denied or invalid path: {file_path}"}

    # Check if in free zone → direct write
    if is_in_free_zone(resolved, employee_id=employee_id, project_dir=project_dir):
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return {"status": "ok", "path": str(resolved)}

    # Not in free zone → propose edit (CEO approval)
    from onemancompany.core.file_editor import propose_edit, pending_file_edits

    permissions = []
    if employee_id:
        emp = company_state.employees.get(employee_id)
        if emp:
            permissions = emp.permissions

    result = propose_edit(file_path, content, "write via agent", proposed_by=employee_id or "agent", permissions=permissions)
    if result["status"] == "error":
        return result

    edit = pending_file_edits.get(result["edit_id"])
    if not edit:
        return result

    from onemancompany.core.resolutions import current_project_id, collect_edit
    pid = current_project_id.get("")
    if pid:
        collect_edit(pid, edit)
    else:
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
async def edit(
    file_path: str,
    new_content: str,
    reason: str = "",
    employee_id: str = "",
    project_dir: str = "",
) -> dict:
    """Edit (overwrite) an existing file.

    Free zones (no approval needed):
    - Your workspace: "workspace/..."
    - Project workspace: files inside the current project_dir

    Other locations (company/, src/) require CEO approval.

    Args:
        file_path: File path to edit.
        new_content: The complete new file content.
        reason: Explanation for the edit.
        employee_id: Your employee ID.
        project_dir: Current project workspace path (auto-filled from task context).
    """
    from pathlib import Path
    from onemancompany.core.file_editor import _resolve_path, is_in_free_zone

    # Resolve path
    if file_path.startswith("workspace/") and employee_id:
        resolved = (get_workspace_dir(employee_id) / file_path[len("workspace/"):]).resolve()
    elif Path(file_path).is_absolute():
        resolved = Path(file_path).resolve()
    else:
        permissions = []
        if employee_id:
            emp = company_state.employees.get(employee_id)
            if emp:
                permissions = emp.permissions
        resolved = _resolve_path(file_path, permissions=permissions)

    if resolved is None:
        return {"status": "error", "message": f"Access denied or invalid path: {file_path}"}

    # Check if in free zone → direct write
    if is_in_free_zone(resolved, employee_id=employee_id, project_dir=project_dir):
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(new_content, encoding="utf-8")
        return {"status": "ok", "path": str(resolved)}

    # Not in free zone → propose edit (CEO approval)
    from onemancompany.core.file_editor import propose_edit, pending_file_edits

    permissions = []
    if employee_id:
        emp = company_state.employees.get(employee_id)
        if emp:
            permissions = emp.permissions

    result = propose_edit(file_path, new_content, reason or "edit via agent", proposed_by=employee_id or "agent", permissions=permissions)
    if result["status"] == "error":
        return result

    edit_entry = pending_file_edits.get(result["edit_id"])
    if not edit_entry:
        return result

    from onemancompany.core.resolutions import current_project_id, collect_edit
    pid = current_project_id.get("")
    if pid:
        collect_edit(pid, edit_entry)
    else:
        await _publish("file_edit_proposed", {
            "edit_id": edit_entry["edit_id"],
            "rel_path": edit_entry["rel_path"],
            "reason": edit_entry["reason"],
            "proposed_by": edit_entry["proposed_by"],
            "old_content": edit_entry["old_content"],
            "new_content": edit_entry["new_content"],
        })

    return result


@tool
async def bash(command: str, employee_id: str = "", timeout_seconds: int = 30) -> dict:
    """Execute a shell command and return stdout/stderr.

    Use for running scripts, checking system state, or executing build commands.
    Commands run in the project root directory.

    Args:
        command: The shell command to execute.
        employee_id: Your employee ID.
        timeout_seconds: Max execution time in seconds (default 30, max 120).
    """
    import subprocess
    from onemancompany.core.config import PROJECT_ROOT

    timeout_seconds = min(timeout_seconds, 120)

    try:
        proc = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=str(PROJECT_ROOT),
            ),
        )
        return {
            "status": "ok",
            "returncode": proc.returncode,
            "stdout": proc.stdout[:5000] if proc.stdout else "",
            "stderr": proc.stderr[:2000] if proc.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": f"Command timed out after {timeout_seconds}s"}
    except Exception as e:
        return {"status": "error", "message": f"Execution failed: {e}"}


@tool
def list_colleagues() -> list[dict]:
    """List information about all colleagues including their roles, skills, tools, and current status.

    Returns:
        A list of dicts with id, name, nickname, role, department, level, skills,
        tools (authorized tool names), status, and current_task_summary.
    """
    results = []
    for emp in company_state.employees.values():
        # Gather authorized tool names for this colleague
        tool_names: list[str] = list(emp.tool_permissions) if emp.tool_permissions else []
        # Also include equipment room tools they have access to
        for t in company_state.tools.values():
            if not t.allowed_users or emp.id in t.allowed_users:
                if t.name not in tool_names:
                    tool_names.append(t.name)

        results.append({
            "id": emp.id,
            "name": emp.name,
            "nickname": emp.nickname,
            "role": emp.role,
            "department": emp.department,
            "level": emp.level,
            "skills": emp.skills,
            "tools": tool_names,
            "status": emp.status,
            "current_task": emp.current_task_summary or None,
        })
    return results


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
    loop = _current_vessel.get()
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
    from onemancompany.core.agent_loop import _current_vessel, _current_task_id
    from onemancompany.core.project_archive import set_acceptance_criteria as _set_criteria

    loop = _current_vessel.get()
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

    # Setting acceptance criteria promotes task_type to "project"
    from onemancompany.core.project_archive import load_project, _save_project
    proj = load_project(project_id)
    if proj and proj.get("task_type") != "project":
        proj["task_type"] = "project"
        _save_project(project_id, proj)

    return {
        "status": "ok",
        "project_id": project_id,
        "criteria_count": len(criteria),
        "responsible_officer": responsible_officer_id,
        "task_type": "project",
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
    from onemancompany.core.agent_loop import _current_vessel, _current_task_id
    from onemancompany.core.project_archive import set_acceptance_result, load_project

    loop = _current_vessel.get()
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
def ea_review_project(approved: bool, review_notes: str, needs_retrospective: bool = False) -> dict:
    """EA final quality review on behalf of CEO.

    Called by the EA after the responsible officer has already accepted the project.
    This is the CEO's quality gate — EA must verify the deliverables actually meet
    ALL requirements before the project is truly considered complete.

    If approved, the project proceeds to completion.
    If rejected, a rectification task is pushed back to the responsible officer.

    Args:
        approved: True if EA confirms all deliverables meet requirements
        review_notes: Detailed review notes — what was checked, evidence of verification,
                      and (if rejected) specific issues that need to be fixed
        needs_retrospective: True only for substantial project-level work that warrants
                            a team retrospective (复盘). Simple operational tasks
                            (sending emails, quick queries, etc.) should be False.
    """
    from onemancompany.core.agent_loop import _current_vessel, _current_task_id
    from onemancompany.core.project_archive import set_ea_review_result, load_project

    loop = _current_vessel.get()
    task_id = _current_task_id.get()
    if not loop or not task_id:
        return {"status": "error", "message": "No agent loop context."}

    task = loop.board.get_task(task_id)
    if not task:
        return {"status": "error", "message": "Current task not found."}

    project_id = task.project_id or task.original_project_id
    if not project_id:
        return {"status": "error", "message": "No project context."}

    set_ea_review_result(project_id, approved, review_notes,
                         needs_retrospective=needs_retrospective)

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
    from onemancompany.core.agent_loop import _current_vessel, _current_task_id
    from onemancompany.core.project_archive import set_project_budget as _set_budget

    loop = _current_vessel.get()
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
def save_project_plan(
    plan_title: str,
    background: str,
    market_research: dict,
    goals: list[str],
    non_goals: list[str],
    technical_approach: str,
    phases: list[dict],
    team_assignments: list[dict],
    risks: list[str],
    acceptance_criteria: list[str],
) -> dict:
    """dispatch前保存结构化项目计划到workspace/plan.md，并设置验收标准。

    产出 Claude Code Plan Mode 质量的文档：有背景、有市场调研、有技术方案、有具体任务分配、有验收标准。
    这是团队执行的 Single Source of Truth，所有员工通过 read("plan.md") 获取完整上下文。

    Args:
        plan_title: 项目计划标题
        background: 项目背景与问题描述 — 为什么要做这个项目，要解决什么问题
        market_research: 市场与用户调研结论:
            {sota: str — 当前领域SOTA技术/方案及行业最佳实践,
             competitors: [{name: str, strengths: str, weaknesses: str}] — 主要竞品分析,
             our_edge: str — 我们的差异化优势,
             user_pain_points: list[str] — 用户痛点（现有方案解决不了的问题）,
             user_delight_points: list[str] — 用户爽点（能产生口碑传播的体验）}
        goals: 项目目标列表 — 明确的、可验证的交付目标
        non_goals: 明确不做的事项 — 防止范围蔓延
        technical_approach: 技术/执行方案描述 — 架构选型、关键决策、集成方式、权衡取舍
        phases: 分阶段执行计划，每个phase:
            {phase_number: int, name: str, description: str,
             tasks: [{assignee_id: str, assignee_name: str, description: str,
                      deliverables: list[str], depends_on: list[str], complexity: str}],
             milestone: str}
            - complexity: "simple" | "medium" | "complex"
            - depends_on: 本任务依赖的其他任务描述（跨phase自动处理，此处记录phase内逻辑依赖）
        team_assignments: 团队分工总表 [{employee_id, employee_name, role_in_project, responsibilities}]
        risks: 已知风险及应对措施列表
        acceptance_criteria: 验收标准列表 — 每条必须可验证（能通过具体操作确认pass/fail）
    """
    from datetime import datetime

    from onemancompany.core.agent_loop import _current_vessel, _current_task_id
    from onemancompany.core.project_archive import set_acceptance_criteria as _set_criteria, save_project_file

    loop = _current_vessel.get()
    task_id = _current_task_id.get()
    if not loop or not task_id:
        return {"status": "error", "message": "No agent loop context."}

    task = loop.board.get_task(task_id)
    if not task:
        return {"status": "error", "message": "Current task not found."}

    project_id = task.project_id or task.original_project_id
    if not project_id:
        return {"status": "error", "message": "No project context — plans require a project."}

    # Build plan markdown — Claude Code Plan Mode quality
    lines: list[str] = []
    lines.append(f"# {plan_title}")
    lines.append("")
    lines.append(f"> Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}  ")
    lines.append(f"> Project: {project_id}")
    lines.append("")

    # --- Background ---
    lines.append("## Background")
    lines.append("")
    lines.append(background)
    lines.append("")

    # --- Market Research ---
    if market_research:
        lines.append("## Market & User Research")
        lines.append("")

        sota = market_research.get("sota", "")
        if sota:
            lines.append("### SOTA (State of the Art)")
            lines.append("")
            lines.append(sota)
            lines.append("")

        competitors = market_research.get("competitors", [])
        if competitors:
            lines.append("### Competitive Landscape")
            lines.append("")
            lines.append("| Competitor | Strengths | Weaknesses |")
            lines.append("|-----------|-----------|------------|")
            for comp in competitors:
                cname = comp.get("name", "")
                cstr = comp.get("strengths", "")
                cweak = comp.get("weaknesses", "")
                lines.append(f"| {cname} | {cstr} | {cweak} |")
            lines.append("")

            our_edge = market_research.get("our_edge", "")
            if our_edge:
                lines.append(f"**Our Differentiation**: {our_edge}")
                lines.append("")

        pain_points = market_research.get("user_pain_points", [])
        if pain_points:
            lines.append("### User Pain Points")
            lines.append("")
            for pp in pain_points:
                lines.append(f"- {pp}")
            lines.append("")

        delight_points = market_research.get("user_delight_points", [])
        if delight_points:
            lines.append("### User Delight Points")
            lines.append("")
            for dp in delight_points:
                lines.append(f"- {dp}")
            lines.append("")

    # --- Goals & Non-Goals ---
    lines.append("## Goals")
    lines.append("")
    for g in goals:
        lines.append(f"- {g}")
    lines.append("")

    if non_goals:
        lines.append("## Non-Goals (Explicit Exclusions)")
        lines.append("")
        for ng in non_goals:
            lines.append(f"- {ng}")
        lines.append("")

    # --- Technical Approach ---
    lines.append("## Technical Approach")
    lines.append("")
    lines.append(technical_approach)
    lines.append("")

    # --- Team ---
    if team_assignments:
        lines.append("## Team")
        lines.append("")
        lines.append("| Employee | Role | Responsibilities |")
        lines.append("|----------|------|-----------------|")
        for ta in team_assignments:
            eid = ta.get("employee_id", "")
            ename = ta.get("employee_name", eid)
            role_p = ta.get("role_in_project", "")
            resp = ta.get("responsibilities", "")
            lines.append(f"| {ename} ({eid}) | {role_p} | {resp} |")
        lines.append("")

    # --- Phases ---
    lines.append("## Execution Plan")
    lines.append("")
    total_tasks = 0
    for phase in sorted(phases, key=lambda p: p.get("phase_number", 0)):
        pnum = phase.get("phase_number", 0)
        pname = phase.get("name", "")
        pdesc = phase.get("description", "")
        milestone = phase.get("milestone", "")

        lines.append(f"### Phase {pnum}: {pname}")
        lines.append("")
        if pdesc:
            lines.append(pdesc)
            lines.append("")

        phase_tasks = phase.get("tasks", [])
        if phase_tasks:
            for i, pt in enumerate(phase_tasks, 1):
                total_tasks += 1
                assignee_name = pt.get("assignee_name", pt.get("assignee_id", "TBD"))
                assignee_id = pt.get("assignee_id", "")
                desc = pt.get("description", "")
                complexity = pt.get("complexity", "medium")
                depends = pt.get("depends_on", [])
                deliverables_list = pt.get("deliverables", [])

                complexity_badge = {"simple": "🟢", "medium": "🟡", "complex": "🔴"}.get(complexity, "🟡")
                lines.append(f"**P{pnum}-T{i}** {complexity_badge} `[{assignee_name}]` {desc}")
                if depends:
                    lines.append(f"  - Depends on: {', '.join(depends)}")
                if deliverables_list:
                    lines.append(f"  - Deliverables: {', '.join(f'`{d}`' for d in deliverables_list)}")
                lines.append("")

        if milestone:
            lines.append(f"**Milestone**: {milestone}")
            lines.append("")
        lines.append("---")
        lines.append("")

    # --- Risks ---
    if risks:
        lines.append("## Risks & Mitigations")
        lines.append("")
        for r in risks:
            lines.append(f"- {r}")
        lines.append("")

    # --- Acceptance Criteria ---
    if acceptance_criteria:
        lines.append("## Acceptance Criteria")
        lines.append("")
        for i, c in enumerate(acceptance_criteria, 1):
            lines.append(f"- [ ] **AC-{i}**: {c}")
        lines.append("")

    # --- Summary ---
    lines.append("---")
    lines.append("")
    lines.append(f"**Total**: {len(phases)} phases, {total_tasks} tasks, {len(acceptance_criteria)} acceptance criteria")
    lines.append("")

    plan_content = "\n".join(lines)

    # Save plan to project workspace
    result = save_project_file(project_id, "plan.md", plan_content)

    # Set acceptance criteria in project archive
    officer_id = loop.agent.employee_id
    _set_criteria(project_id, acceptance_criteria, officer_id)

    return {
        "status": "ok",
        "project_id": project_id,
        "plan_file": result.get("path", ""),
        "phase_count": len(phases),
        "task_count": total_tasks,
        "criteria_count": len(acceptance_criteria),
    }


@tool
async def report_to_ceo(subject: str, report: str, action_required: bool = False,
                        employee_id: str = "", project_id: str = "") -> dict:
    """Report findings to CEO, especially when company-level action is needed.

    Use when:
    - Missing API credentials or tools that only CEO can configure
    - Task requires capabilities the company doesn't have yet
    - Need CEO decision on requirement changes
    - Diagnosis of why a project task failed

    Args:
        subject: Brief title (e.g. "Roblox发布需要API凭证配置")
        report: Detailed findings and recommendations
        action_required: True if CEO must take action before work can continue
    """
    # Try to resolve employee_id and project_id from context if not provided
    if not employee_id:
        try:
            from onemancompany.core.vessel import _current_vessel
            vessel = _current_vessel.get()
            if vessel:
                employee_id = getattr(vessel, "employee_id", "")
        except Exception as exc:
            logger.debug("Could not resolve employee_id from vessel context: {}", exc)
    if not project_id:
        try:
            from onemancompany.core.vessel import _current_task_id, employee_manager
            task_id = _current_task_id.get()
            if task_id and employee_id:
                handle = employee_manager.get_handle(employee_id)
                if handle:
                    for t in handle._task_board:
                        if t.id == task_id and t.project_id:
                            project_id = t.project_id
                            break
        except Exception as exc:
            logger.debug("Could not resolve project_id from vessel context: {}", exc)

    payload = {
        "subject": subject,
        "report": report,
        "action_required": action_required,
        "timestamp": datetime.now().isoformat(),
    }
    if employee_id:
        payload["employee_id"] = employee_id
        emp = company_state.employees.get(employee_id)
        if emp:
            payload["employee_name"] = emp.name
    if project_id:
        payload["project_id"] = project_id

    await _publish("ceo_report", payload, agent="SYSTEM")

    if action_required and employee_id:
        # Block until CEO responds — create an asyncio.Event and wait
        key = _ceo_wait_key(employee_id, project_id)
        entry = {"event": asyncio.Event(), "response": {}}
        _ceo_pending[key] = entry
        try:
            await asyncio.wait_for(entry["event"].wait(), timeout=600)
            ceo_response = entry.get("response", {})
            action = ceo_response.get("action", "approve")
            message = ceo_response.get("message", "")
            if action == "revise" and message:
                return {
                    "status": "revision_requested",
                    "subject": subject,
                    "ceo_message": message,
                }
            return {
                "status": "approved",
                "subject": subject,
                "ceo_message": message or "CEO approved, proceed.",
            }
        except (asyncio.TimeoutError, TimeoutError):
            return {
                "status": "timeout",
                "subject": subject,
                "message": "CEO did not respond within 10 minutes. Proceed with best judgment.",
            }
        finally:
            _ceo_pending.pop(key, None)

    return {
        "status": "reported",
        "subject": subject,
        "action_required": action_required,
    }


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
    from onemancompany.core.agent_loop import get_agent_loop, _current_vessel, _current_task_id

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
            caller_loop = _current_vessel.get()
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
                from onemancompany.core.project_archive import record_dispatch, find_duplicate_dispatch
                dup = find_duplicate_dispatch(project_id, employee_id, task_description)
                if dup:
                    return {
                        "status": "duplicate",
                        "message": f"类似任务已存在 (dispatch_id={dup['dispatch_id']}): {dup['description'][:100]}",
                        "existing_dispatch": dup,
                    }
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
    caller_loop = _current_vessel.get()
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

    # Dedup check + record dispatch in project archive
    if project_id:
        from onemancompany.core.project_archive import record_dispatch, find_duplicate_dispatch
        dup = find_duplicate_dispatch(project_id, employee_id, task_description)
        if dup:
            return {
                "status": "duplicate",
                "message": f"类似任务已存在 (dispatch_id={dup['dispatch_id']}): {dup['description'][:100]}",
                "existing_dispatch": dup,
            }
        record_dispatch(project_id, employee_id, task_description)

    emp = company_state.employees.get(employee_id)
    emp_name = emp.name if emp else employee_id
    loop.push_task(task_description, project_id=project_id, project_dir=project_dir)
    return {"status": "dispatched", "employee": emp_name, "task": task_description[:200]}


@tool
def dispatch_team_tasks(tasks: list[dict]) -> dict:
    """将任务分阶段dispatch给多名员工。同阶段并行，后阶段等前阶段完成。

    Args:
        tasks: [{employee_id, description, phase}] — phase=1先执行, phase=2等phase=1完成
    """
    import uuid as _uuid
    from onemancompany.core.agent_loop import get_agent_loop, _current_vessel, _current_task_id
    from onemancompany.core.project_archive import record_team_dispatches, activate_dispatch

    # Validate all employee_ids
    for t in tasks:
        eid = t.get("employee_id", "")
        if not company_state.employees.get(eid):
            return {"status": "error", "message": f"Employee {eid} not found"}

    # Get project context from caller
    project_id = ""
    project_dir = ""
    caller_loop = _current_vessel.get()
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

    if not project_id:
        return {"status": "error", "message": "No project context — dispatch_team_tasks requires a project."}

    # Dedup check — filter out tasks that already have similar in-progress dispatches
    from onemancompany.core.project_archive import find_duplicate_dispatch
    deduplicated_tasks = []
    skipped = []
    for t in tasks:
        dup = find_duplicate_dispatch(project_id, t["employee_id"], t["description"])
        if dup:
            skipped.append({
                "employee_id": t["employee_id"],
                "description": t["description"][:100],
                "existing_dispatch_id": dup["dispatch_id"],
            })
        else:
            deduplicated_tasks.append(t)
    tasks = deduplicated_tasks
    if not tasks:
        return {
            "status": "duplicate",
            "message": "所有任务均已存在类似的进行中dispatch",
            "skipped": skipped,
        }

    # Group by phase and generate dispatch_ids
    from collections import defaultdict
    phases: dict[int, list[dict]] = defaultdict(list)
    for t in tasks:
        phase = t.get("phase", 1)
        phases[phase].append(t)

    sorted_phases = sorted(phases.keys())

    # Build dispatch records: phase N depends_on all dispatch_ids from phase N-1
    all_dispatches: list[dict] = []
    phase_dispatch_ids: dict[int, list[str]] = {}

    for phase_num in sorted_phases:
        phase_tasks = phases[phase_num]
        current_ids = []
        # Find the previous phase's dispatch_ids for depends_on
        prev_phase = None
        for p in sorted_phases:
            if p < phase_num:
                prev_phase = p
        depends_on = phase_dispatch_ids.get(prev_phase, []) if prev_phase is not None else []

        for t in phase_tasks:
            dispatch_id = _uuid.uuid4().hex[:8]
            current_ids.append(dispatch_id)
            is_phase_1 = (phase_num == sorted_phases[0])
            emp = company_state.employees.get(t["employee_id"])
            all_dispatches.append({
                "dispatch_id": dispatch_id,
                "employee_id": t["employee_id"],
                "description": t["description"][:200],
                "status": "in_progress" if is_phase_1 else "pending",
                "phase": phase_num,
                "depends_on": list(depends_on),
                "dispatched_at": datetime.now().isoformat() if is_phase_1 else None,
                "completed_at": None,
                "task_type": t.get("task_type", "execution"),
                "scheduled_start": t.get("scheduled_start", ""),
                "estimated_duration_min": t.get("estimated_duration_min", 0),
                "estimated_cost_usd": t.get("estimated_cost_usd", 0.0),
                "assignee_name": (emp.nickname or emp.name) if emp else t["employee_id"],
            })
        phase_dispatch_ids[phase_num] = current_ids

    # Persist all dispatches
    record_team_dispatches(project_id, all_dispatches)

    # Immediately dispatch phase 1 tasks
    dispatched = []
    first_phase = sorted_phases[0]
    for d in all_dispatches:
        if d["phase"] == first_phase:
            target = get_agent_loop(d["employee_id"])
            if target:
                emp = company_state.employees.get(d["employee_id"])
                target.push_task(d["description"], project_id=project_id, project_dir=project_dir)
                dispatched.append({
                    "employee": emp.name if emp else d["employee_id"],
                    "description": d["description"][:100],
                    "phase": d["phase"],
                })

    result = {
        "status": "dispatched",
        "project_id": project_id,
        "total_tasks": len(all_dispatches),
        "phases": len(sorted_phases),
        "phase_1_dispatched": len(dispatched),
        "dispatch_plan": [
            {
                "dispatch_id": d["dispatch_id"],
                "employee_id": d["employee_id"],
                "phase": d["phase"],
                "status": d["status"],
                "depends_on": d["depends_on"],
            }
            for d in all_dispatches
        ],
    }
    if skipped:
        result["skipped_duplicates"] = skipped
    return result


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

    # Check tool exists in registry
    from onemancompany.core.tool_registry import tool_registry
    meta = tool_registry.get_meta(tool_name)
    if not meta or meta.category != "gated":
        gated_names = [n for n in tool_registry.all_tool_names() if (tool_registry.get_meta(n) or object()).category == "gated"]
        return {"status": "error", "message": f"Unknown gated tool '{tool_name}'. Available: {', '.join(gated_names)}"}

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
# Automation tools
# ---------------------------------------------------------------------------

@tool
def set_cron(cron_name: str, interval: str, task_description: str, employee_id: str = "") -> dict:
    """Schedule a recurring task (cron job).

    The task will be dispatched to you at regular intervals automatically.

    Args:
        cron_name: Unique name for this cron job (e.g. 'daily_report', 'check_inbox').
        interval: How often to run. Examples: '30s', '5m', '1h', '6h', '1d'.
        task_description: What task to perform each time.
        employee_id: Your employee ID.
    """
    from onemancompany.core.automation import start_cron
    return start_cron(employee_id, cron_name, interval, task_description)


@tool
def stop_cron_job(cron_name: str, employee_id: str = "") -> dict:
    """Stop a running cron job.

    Args:
        cron_name: Name of the cron job to stop.
        employee_id: Your employee ID.
    """
    from onemancompany.core.automation import stop_cron
    return stop_cron(employee_id, cron_name)


@tool
def setup_webhook(hook_name: str, task_template: str = "", employee_id: str = "") -> dict:
    """Register a webhook endpoint that triggers tasks when called.

    Creates an endpoint at: POST /api/webhook/{employee_id}/{hook_name}
    External services can POST JSON to this URL to trigger a task for you.

    Args:
        hook_name: Unique webhook name (URL-safe, e.g. 'github_push', 'email_notify').
        task_template: Task description template. Use {payload} for the webhook body.
        employee_id: Your employee ID.
    """
    from onemancompany.core.automation import register_webhook
    return register_webhook(employee_id, hook_name, task_template)


@tool
def remove_webhook(hook_name: str, employee_id: str = "") -> dict:
    """Remove a registered webhook.

    Args:
        hook_name: Name of the webhook to remove.
        employee_id: Your employee ID.
    """
    from onemancompany.core.automation import unregister_webhook
    return unregister_webhook(employee_id, hook_name)


@tool
def list_automations(employee_id: str = "") -> dict:
    """List all your cron jobs and webhooks.

    Args:
        employee_id: Your employee ID.
    """
    from onemancompany.core.automation import list_crons, list_webhooks
    return {
        "crons": list_crons(employee_id),
        "webhooks": list_webhooks(employee_id),
    }


# ---------------------------------------------------------------------------
# Tool registration — register all internal tools into the unified registry
# ---------------------------------------------------------------------------

def _register_all_internal_tools() -> None:
    """Register all internal tools into the global ToolRegistry.

    Called once at import time. Categories:
      base  — available to all employees
      gated — requires tool_permissions grant
    """
    from onemancompany.core.tool_registry import ToolMeta, tool_registry

    _base = [
        list_colleagues, read, ls, write, edit, pull_meeting,
        create_subtask, dispatch_task, dispatch_team_tasks,
        report_to_ceo, request_tool_access,
    ]
    for t in _base:
        tool_registry.register(t, ToolMeta(name=t.name, category="base"))

    _gated = {
        "bash": bash,
        "use_tool": use_tool,
        "set_acceptance_criteria": set_acceptance_criteria,
        "accept_project": accept_project,
        "ea_review_project": ea_review_project,
        "set_project_budget": set_project_budget,
        "save_project_plan": save_project_plan,
        "manage_tool_access": manage_tool_access,
        "set_cron": set_cron,
        "stop_cron_job": stop_cron_job,
        "setup_webhook": setup_webhook,
        "remove_webhook": remove_webhook,
        "list_automations": list_automations,
    }
    for name, t in _gated.items():
        tool_registry.register(t, ToolMeta(name=name, category="gated"))

    # Sandbox tools
    for t in SANDBOX_TOOLS:
        tool_registry.register(t, ToolMeta(name=t.name, category="gated"))


_register_all_internal_tools()
