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
# CEO interaction — blocking wait for CEO response
# ---------------------------------------------------------------------------
# Key: "employee_id:project_id" → {"event": asyncio.Event, "response": dict}
_ceo_pending: dict[str, dict] = {}


def _ceo_wait_key(employee_id: str, project_id: str) -> str:
    return f"{employee_id}:{project_id or '_'}"


def resolve_ceo_pending(employee_id: str, project_id: str, response: dict) -> bool:
    """Called from API to unblock a waiting ask_ceo / report_to_ceo call."""
    key = _ceo_wait_key(employee_id, project_id)
    entry = _ceo_pending.get(key)
    if not entry:
        return False
    entry["response"] = response
    entry["event"].set()
    return True


def list_ceo_pending() -> list[dict]:
    """Return all pending CEO requests (for frontend polling)."""
    result = []
    for key, entry in _ceo_pending.items():
        if not entry["event"].is_set():
            result.append(entry.get("meta", {}))
    return result
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
async def report_to_ceo(subject: str, report: str, action_required: bool = False,
                        employee_id: str = "", project_id: str = "") -> dict:
    """Report to CEO or ask CEO a question. Always use this tool for CEO communication.

    Use when:
    - Need CEO approval on a plan, hire, budget, or decision
    - Missing API credentials or tools that only CEO can configure
    - Task requires capabilities the company doesn't have yet
    - Need CEO clarification on requirements
    - Diagnosis of why a project task failed

    If action_required=True, this tool BLOCKS until CEO responds (up to 10 min).
    CEO can approve or request revisions.

    Args:
        subject: Brief title (e.g. "招聘方案审批", "需要API凭证配置")
        report: Detailed findings, question, or proposal for CEO
        action_required: True if CEO must respond before work can continue
    """
    if not employee_id:
        try:
            vessel = _current_vessel.get()
            if vessel:
                employee_id = getattr(vessel, "employee_id", "")
        except (LookupError, Exception):
            pass
    if not project_id:
        try:
            task_id = _current_task_id.get()
            if task_id and employee_id:
                from onemancompany.core.vessel import employee_manager
                handle = employee_manager.get_handle(employee_id)
                if handle:
                    for t in handle._task_board:
                        if t.id == task_id and t.project_id:
                            project_id = t.project_id
                            break
        except (LookupError, Exception):
            pass

    emp_name = ""
    if employee_id:
        emp = company_state.employees.get(employee_id)
        if emp:
            emp_name = emp.name

    payload = {
        "employee_id": employee_id,
        "employee_name": emp_name,
        "project_id": project_id,
        "subject": subject,
        "report": report,
        "action_required": action_required,
        "timestamp": datetime.now().isoformat(),
    }

    if not action_required:
        # Fire-and-forget notification — no blocking
        await _publish("ceo_report", payload, agent="SYSTEM")
        return {"status": "reported", "subject": subject}

    # Blocking mode — wait for CEO response
    key = _ceo_wait_key(employee_id, project_id)
    entry = {
        "event": asyncio.Event(),
        "response": {},
        "meta": payload,
    }
    _ceo_pending[key] = entry
    await _publish("ceo_report", payload, agent="SYSTEM")

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
# Skill loading — on-demand skill content retrieval (Claude-style)
# ---------------------------------------------------------------------------

@tool
def load_skill(skill_name: str) -> dict:
    """Load a skill's full instructions by name.

    Call this BEFORE applying any skill. The skill catalog in your system prompt
    lists available skills with short descriptions. Use this tool to get the
    complete instructions for a skill you want to use.

    Args:
        skill_name: The skill name from your Available Skills list.

    Returns:
        The full skill content, or an error if the skill is not found.
    """
    try:
        vessel = _current_vessel.get()
    except LookupError:
        return {"status": "error", "message": "No employee context — cannot resolve skills."}

    employee_id = getattr(vessel, "employee_id", "")
    if not employee_id:
        return {"status": "error", "message": "No employee context."}

    from onemancompany.core.config import load_employee_skills
    skills = load_employee_skills(employee_id)
    if skill_name not in skills:
        available = list(skills.keys())
        return {"status": "error", "message": f"Skill '{skill_name}' not found. Available: {available}"}

    return {"status": "ok", "skill_name": skill_name, "content": skills[skill_name]}


# ---------------------------------------------------------------------------
# Resume held task — transition HOLDING → COMPLETE from agent context
# ---------------------------------------------------------------------------

@tool
def resume_held_task(task_id: str, result: str, employee_id: str = "") -> dict:
    """Resume a task that is in HOLDING state with the provided result.

    Use this when you have received a reply (e.g., from a human via email)
    for a task that is currently waiting (HOLDING).

    Args:
        task_id: The ID of the held task to resume.
        result: The result content to set on the task (e.g., email reply body).
        employee_id: Your employee ID.
    """
    if not employee_id:
        return {"status": "error", "message": "employee_id required"}

    from onemancompany.core.agent_loop import get_agent_loop
    loop = get_agent_loop(employee_id)
    if not loop:
        return {"status": "error", "message": f"No agent loop for {employee_id}"}

    import asyncio
    coro = loop.resume_held_task(employee_id, task_id, result)
    asyncio.ensure_future(coro)
    return {"status": "ok", "message": f"Resume scheduled for task {task_id}"}


@tool
def update_project_team(members: list[dict]) -> dict:
    """Update the team roster for the current project.

    Appends new members to the project's team list. Does not overwrite existing members.

    Args:
        members: List of dicts with 'employee_id' and 'role' keys.

    Returns:
        Confirmation with count of added members.
    """
    from onemancompany.core.vessel import _current_vessel, _current_task_id

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return {"status": "error", "message": "No agent context."}

    task = vessel.board.get_task(task_id)
    if not task or not task.project_dir:
        return {"status": "error", "message": "No project directory in current task."}

    from pathlib import Path
    from datetime import datetime
    import yaml

    project_yaml = Path(task.project_dir) / "project.yaml"
    if not project_yaml.exists():
        return {"status": "error", "message": "project.yaml not found."}

    data = yaml.safe_load(project_yaml.read_text(encoding="utf-8")) or {}
    team = data.get("team", [])

    now = datetime.now().isoformat()
    for m in members:
        team.append({
            "employee_id": m["employee_id"],
            "role": m.get("role", ""),
            "joined_at": now,
        })

    data["team"] = team
    project_yaml.write_text(
        yaml.dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    return {"status": "ok", "added": len(members), "total": len(team)}


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
        report_to_ceo, request_tool_access, load_skill,
        resume_held_task, update_project_team,
    ]
    for t in _base:
        tool_registry.register(t, ToolMeta(name=t.name, category="base"))

    # Tree tools self-register on import
    from onemancompany.agents import tree_tools as _tt  # noqa: F401

    _gated = {
        "bash": bash,
        "use_tool": use_tool,
        "set_project_budget": set_project_budget,
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
