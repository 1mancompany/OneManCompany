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
from onemancompany.core.config import COO_ID, ENCODING_UTF8, HR_ID, MAX_DISCUSSION_SUMMARY_LEN, MAX_PRINCIPLES_LEN, MEETING_SYSTEM_SENDER, PROJECT_YAML_FILENAME, PROJECTS_DIR, STATUS_IDLE, SYSTEM_SENDER, get_workspace_dir
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.state import company_state
from onemancompany.core.store import load_employee, load_all_employees

from onemancompany.tools.sandbox import SANDBOX_TOOLS, is_sandbox_enabled

# Context vars for sub-task support — set by Vessel during execution
from onemancompany.core.agent_loop import _current_vessel, _current_task_id


async def _publish(event_type: str, payload: dict, agent: str = "MEETING") -> None:
    await event_bus.publish(CompanyEvent(type=event_type, payload=payload, agent=agent))


async def _chat(room_id: str, speaker: str, role: str, message: str) -> None:
    from datetime import datetime
    entry = {
        "room_id": room_id,
        "speaker": speaker,
        "role": role,
        "message": message,
        "time": datetime.now().strftime("%H:%M:%S"),
    }
    await _publish("meeting_chat", entry)
    # Persist to disk so chat history survives page reload
    from onemancompany.core.store import append_room_chat
    await append_room_chat(room_id, entry)


# Track files read per employee — write/edit safety check
# Key: employee_id, Value: set of resolved file paths
_files_read_by_employee: dict[str, set[str]] = {}


def _resolve_employee_path(file_path: str, employee_id: str = ""):
    """Resolve a file path using employee permissions. Returns Path or None."""
    from pathlib import Path
    from onemancompany.core.file_editor import _resolve_path

    if file_path.startswith("workspace/") and employee_id:
        return (get_workspace_dir(employee_id) / file_path[len("workspace/"):]).resolve()
    if file_path and Path(file_path).is_absolute():
        return Path(file_path).resolve()
    permissions = []
    if employee_id:
        emp_data = load_employee(employee_id)
        if emp_data:
            permissions = emp_data.get("permissions", [])
    return _resolve_path(file_path, permissions=permissions)


@tool
def read(file_path: str, employee_id: str = "", offset: int = 0, limit: int = 0) -> dict:
    """Read the contents of a file.

    Accessible paths:
    - Your workspace: "workspace/..." (your private workspace directory)
    - Company files: "company/..." or relative paths like "human_resource/..."
    - Source code: "src/..." (requires backend_code_maintenance permission)
    - Absolute paths: any absolute file path

    Args:
        file_path: File path to read.
        employee_id: Your employee ID.
        offset: Line number to start reading from (1-based). 0 = start of file.
        limit: Max number of lines to read. 0 = read all.
    """
    resolved = _resolve_employee_path(file_path, employee_id)

    if resolved is None:
        return {"status": "error", "message": f"Access denied or invalid path: {file_path}"}
    if not resolved.exists():
        return {"status": "error", "message": f"File not found: {file_path}"}
    if not resolved.is_file():
        return {"status": "error", "message": f"Not a file: {file_path}"}
    try:
        content = resolved.read_text(encoding=ENCODING_UTF8)
        lines = content.splitlines(keepends=True)
        total_lines = len(lines)

        if offset > 0 or limit > 0:
            start = max(0, offset - 1) if offset > 0 else 0
            end = start + limit if limit > 0 else total_lines
            lines = lines[start:end]
            content = "".join(lines)

        _files_read_by_employee.setdefault(employee_id, set()).add(str(resolved))
        return {
            "status": "ok",
            "path": file_path,
            "content": content,
            "total_lines": total_lines,
        }
    except Exception as e:
        return {"status": "error", "message": f"Read failed: {e}"}




@tool
def ls(dir_path: str = "", employee_id: str = "") -> dict:
    """List files and subdirectories.

    Accessible paths:
    - Your workspace: "workspace" or "workspace/subdir"
    - Company directories: "business/projects", "human_resource/employees", etc.
    - Source code: "src/onemancompany/core" (requires permission)
    - Absolute paths: any absolute directory path

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
    # Handle absolute paths — read-only, safe to allow any path
    elif dir_path and Path(dir_path).is_absolute():
        resolved = Path(dir_path).resolve()
    else:
        permissions = []
        if employee_id:
            emp_data = load_employee(employee_id)
            if emp_data:
                permissions = emp_data.get("permissions", [])
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

    If the file already exists, you MUST read it first using the read() tool.
    This prevents accidental overwrites. Prefer edit() for modifying existing files.

    Args:
        file_path: File path to write.
        content: The text content to write.
        employee_id: Your employee ID.
        project_dir: Current project workspace path (auto-filled from task context).
    """
    from pathlib import Path

    resolved = _resolve_employee_path(file_path, employee_id)

    if resolved is None:
        return {"status": "error", "message": f"Access denied or invalid path: {file_path}"}

    is_update = resolved.exists()
    original_content = ""

    # Safety: must read before overwriting existing files
    if is_update:
        if str(resolved) not in _files_read_by_employee.get(employee_id, set()):
            return {
                "status": "error",
                "message": f"You must read '{file_path}' before overwriting it. Use read() first.",
            }
        original_content = resolved.read_text(encoding=ENCODING_UTF8)

    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding=ENCODING_UTF8)
    _files_read_by_employee.setdefault(employee_id, set()).add(str(resolved))

    result: dict = {
        "status": "ok",
        "path": str(resolved),
        "type": "update" if is_update else "create",
    }
    if is_update and original_content != content:
        # Compute a simple line-level diff summary
        old_lines = original_content.splitlines()
        new_lines = content.splitlines()
        result["lines_before"] = len(old_lines)
        result["lines_after"] = len(new_lines)
    return result




@tool
async def edit(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    employee_id: str = "",
) -> dict:
    """Perform exact string replacement in a file.

    You MUST read the file first using read() before editing.
    The old_string must match exactly (including whitespace/indentation).
    If old_string appears multiple times, either provide more context to make
    it unique, or set replace_all=True.

    Args:
        file_path: File path to edit.
        old_string: The exact text to find and replace.
        new_string: The replacement text (must differ from old_string).
        replace_all: If True, replace all occurrences. Default False.
        employee_id: Your employee ID.
    """
    resolved = _resolve_employee_path(file_path, employee_id)

    if resolved is None:
        return {"status": "error", "message": f"Access denied or invalid path: {file_path}"}
    if not resolved.exists():
        return {"status": "error", "message": f"File not found: {file_path}"}

    # Safety: must read before editing
    if str(resolved) not in _files_read_by_employee.get(employee_id, set()):
        return {
            "status": "error",
            "message": f"You must read '{file_path}' before editing it. Use read() first.",
        }

    if old_string == new_string:
        return {"status": "error", "message": "old_string and new_string are identical."}

    content = resolved.read_text(encoding=ENCODING_UTF8)
    count = content.count(old_string)

    if count == 0:
        return {"status": "error", "message": "old_string not found in the file."}
    if count > 1 and not replace_all:
        return {
            "status": "error",
            "message": f"old_string appears {count} times. Provide more context to make "
                       f"it unique, or set replace_all=True.",
        }

    if replace_all:
        new_content = content.replace(old_string, new_string)
        replacements = count
    else:
        new_content = content.replace(old_string, new_string, 1)
        replacements = 1

    resolved.write_text(new_content, encoding=ENCODING_UTF8)
    return {
        "status": "ok",
        "path": str(resolved),
        "replacements": replacements,
    }


@tool
async def bash(
    command: str,
    employee_id: str = "",
    timeout_seconds: int = 120,
    description: str = "",
) -> dict:
    """Execute a shell command and return stdout/stderr.

    Use for running scripts, checking system state, or executing build commands.
    Commands run in the project root directory.
    Prefer dedicated tools (read, ls, edit, grep, glob) over shell equivalents
    (cat, find, sed, awk) when possible.

    Args:
        command: The shell command to execute.
        employee_id: Your employee ID.
        timeout_seconds: Max execution time in seconds (default 120, max 600).
        description: Brief human-readable description of what the command does.
    """
    import subprocess
    from onemancompany.core.config import SOURCE_ROOT

    timeout_seconds = min(timeout_seconds, 600)

    try:
        proc = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=str(SOURCE_ROOT),
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
def glob_files(pattern: str, path: str = "", employee_id: str = "") -> dict:
    """Fast file search by glob pattern. Returns matching file paths sorted by modification time.

    Supports patterns like "**/*.py", "src/**/*.yaml", "*.md".

    Args:
        pattern: Glob pattern to match files against (e.g. "**/*.py").
        path: Directory to search in. Defaults to company root if empty.
        employee_id: Your employee ID.
    """
    from pathlib import Path

    if path:
        resolved = _resolve_employee_path(path, employee_id)
    else:
        from onemancompany.core.config import COMPANY_DIR
        resolved = COMPANY_DIR

    if resolved is None or not resolved.is_dir():
        return {"status": "error", "message": f"Directory not found: {path}"}

    try:
        matches = sorted(resolved.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        MAX_RESULTS = 100
        truncated = len(matches) > MAX_RESULTS
        filenames = [str(m) for m in matches[:MAX_RESULTS]]
        return {
            "status": "ok",
            "num_files": len(matches),
            "filenames": filenames,
            "truncated": truncated,
        }
    except Exception as e:
        return {"status": "error", "message": f"Glob failed: {e}"}


@tool
def grep_search(
    pattern: str,
    path: str = "",
    glob: str = "",
    case_insensitive: bool = False,
    context_lines: int = 0,
    output_mode: str = "files_with_matches",
    max_results: int = 50,
    employee_id: str = "",
) -> dict:
    """Search file contents using regex patterns.

    Args:
        pattern: Regex pattern to search for (Python re syntax).
        path: File or directory to search in. Defaults to company root.
        glob: Glob pattern to filter files (e.g. "*.py", "*.yaml").
        case_insensitive: Case insensitive search. Default False.
        context_lines: Number of lines to show before and after each match.
        output_mode: "files_with_matches" (file paths only), "content" (matching lines), "count" (match counts).
        max_results: Max number of results to return. Default 50.
        employee_id: Your employee ID.
    """
    from pathlib import Path

    if path:
        resolved = _resolve_employee_path(path, employee_id)
    else:
        from onemancompany.core.config import COMPANY_DIR
        resolved = COMPANY_DIR

    if resolved is None:
        return {"status": "error", "message": f"Access denied or invalid path: {path}"}
    if not resolved.exists():
        return {"status": "error", "message": f"Path not found: {path}"}

    flags = re.IGNORECASE if case_insensitive else 0
    try:
        compiled = re.compile(pattern, flags)
    except re.error as e:
        return {"status": "error", "message": f"Invalid regex: {e}"}

    # Collect files to search
    if resolved.is_file():
        files = [resolved]
    else:
        file_glob = glob or "**/*"
        files = [f for f in sorted(resolved.glob(file_glob)) if f.is_file()]

    results: list = []
    match_files: list[str] = []
    count_map: dict[str, int] = {}

    for fpath in files:
        try:
            text = fpath.read_text(encoding=ENCODING_UTF8, errors="replace")
        except Exception as e:
            logger.debug("grep_search: skipping unreadable file {}: {}", fpath, e)
            continue

        lines = text.splitlines()
        file_matches: list[dict] = []

        for i, line in enumerate(lines):
            if compiled.search(line):
                if output_mode == "content":
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    ctx = [{"line": start + j + 1, "text": lines[start + j]} for j in range(end - start)]
                    file_matches.append({"match_line": i + 1, "context": ctx})
                else:
                    file_matches.append({"line": i + 1})

        if file_matches:
            fpath_str = str(fpath)
            match_files.append(fpath_str)
            count_map[fpath_str] = len(file_matches)
            if output_mode == "content":
                results.append({"file": fpath_str, "matches": file_matches})

        if len(match_files) >= max_results:
            break

    if output_mode == "files_with_matches":
        return {"status": "ok", "num_files": len(match_files), "filenames": match_files}
    elif output_mode == "count":
        return {"status": "ok", "num_files": len(match_files), "counts": count_map}
    else:
        return {"status": "ok", "num_files": len(match_files), "results": results}


@tool
def list_colleagues() -> list[dict]:
    """List information about all colleagues including their roles, skills, tools, and current status.

    Returns:
        A list of dicts with id, name, nickname, role, department, level, skills,
        tools (authorized tool names), status, and current_task_summary.
    """
    results = []
    all_emps = load_all_employees()
    for emp_id, emp_data in all_emps.items():
        # Gather authorized tool names for this colleague
        tool_perms = emp_data.get("tool_permissions", [])
        tool_names: list[str] = list(tool_perms) if tool_perms else []
        # Also include equipment room tools they have access to
        for t in company_state.tools.values():
            if not t.allowed_users or emp_id in t.allowed_users:
                if t.name not in tool_names:
                    tool_names.append(t.name)

        runtime = emp_data.get("runtime", {})
        results.append({
            "id": emp_id,
            "name": emp_data.get("name", ""),
            "nickname": emp_data.get("nickname", ""),
            "role": emp_data.get("role", ""),
            "department": emp_data.get("department", ""),
            "level": emp_data.get("level", 1),
            "skills": emp_data.get("skills", []),
            "tools": tool_names,
            "status": runtime.get("status", emp_data.get("status", STATUS_IDLE)),
            "current_task": runtime.get("current_task_summary", "") or None,
        })
    return results


def _build_employee_context(emp_data: dict, emp_id: str = "") -> str:
    """Build identity + skills + tools context string for an employee (dict from store)."""
    eid = emp_id or emp_data.get("id", emp_data.get("employee_number", ""))
    work_principles = emp_data.get("work_principles", "")
    principles_ctx = ""
    if work_principles:
        principles_ctx = f"\nYour work principles:\n{work_principles[:MAX_PRINCIPLES_LEN]}\n"
    skills_ctx = get_employee_skills_prompt(eid)
    tools_ctx = get_employee_tools_prompt(eid)
    return (
        f"You are {emp_data.get('name', '')} ({emp_data.get('nickname', '')}, "
        f"Department: {emp_data.get('department', '')}, {emp_data.get('role', '')}, "
        f"Lv.{emp_data.get('level', 1)}).\n"
        f"{principles_ctx}{skills_ctx}{tools_ctx}"
    )


def _format_chat_history(chat_history: list[dict]) -> str:
    """Format chat history list into a readable string."""
    if not chat_history:
        return "(No discussion yet.)"
    return "\n".join(f"  {m['speaker']}: {m['message']}" for m in chat_history)


def _build_evaluate_prompt(emp_data: dict, emp_id: str, topic: str, agenda: str, chat_history: list[dict]) -> str:
    """Build a prompt asking the employee whether they need to speak."""
    ctx = _build_employee_context(emp_data, emp_id)
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


def _build_speech_prompt(emp_data: dict, emp_id: str, topic: str, agenda: str, chat_history: list[dict]) -> str:
    """Build a prompt for the employee to deliver their contribution."""
    ctx = _build_employee_context(emp_data, emp_id)
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
    # Validate participants — build list of (emp_id, emp_data) tuples
    valid_participants: list[tuple[str, dict]] = []
    for pid in participant_ids:
        emp_data = load_employee(pid)
        if emp_data:
            valid_participants.append((pid, emp_data))

    if not valid_participants:
        return {"status": "error", "message": "No valid participants found. Please check employee IDs."}

    # Prevent solo meetings — need at least 2 distinct people
    all_unique = set(pid for pid, _ in valid_participants)
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
    booker = initiator_id or participant_ids[0]
    for r in company_state.meeting_rooms.values():
        if not r.is_booked and r.capacity >= len(all_ids):
            r.is_booked = True
            r.booked_by = booker
            r.participants = all_ids
            from onemancompany.core.store import save_room
            await save_room(r.id, {
                "is_booked": True,
                "booked_by": booker,
                "participants": all_ids,
            })
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
        ini_data = load_employee(initiator_id)
        if ini_data:
            initiator_name = ini_data.get("nickname", "") or ini_data.get("name", "Initiator")

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
        # speakers is a list of (emp_id, emp_data) tuples
        speakers: list[tuple[str, dict]] = list(valid_participants)
        if initiator_id:
            ini_data = load_employee(initiator_id)
            if ini_data and initiator_id not in {pid for pid, _ in speakers}:
                speakers.append((initiator_id, ini_data))

        max_rounds = 15
        loop = asyncio.get_running_loop()
        rounds_used = 0
        last_speaker_id: str = ""  # track last speaker for no-consecutive rule

        for round_num in range(max_rounds):
            rounds_used = round_num + 1

            # Concurrent evaluation — all participants judge whether they need to speak
            async def _evaluate(eid_and_data: tuple[str, dict]):
                eid, edata = eid_and_data
                prompt = _build_evaluate_prompt(edata, eid, topic, agenda, chat_history)
                llm = make_llm(eid)
                t0 = loop.time()
                resp = await tracked_ainvoke(llm, prompt, category="meeting", employee_id=eid)
                t1 = loop.time()
                first_line = resp.content.strip().split("\n")[0].upper()[:20]
                wants = "YES" in first_line
                return (eid, edata, wants, t1)

            results = await asyncio.gather(
                *[_evaluate(e) for e in speakers],
                return_exceptions=True,
            )

            # Filter out exceptions and those who don't want to speak
            willing: list[tuple[str, dict, float]] = [
                (eid, edata, ts)
                for r in results
                if not isinstance(r, Exception)
                for eid, edata, wants, ts in [r]
                if wants
            ]

            if not willing:
                await _chat(room.id, MEETING_SYSTEM_SENDER, SYSTEM_SENDER, "All participants have finished speaking. Meeting concluded.")
                break

            # Token grab — sort by timestamp, fastest wins
            willing.sort(key=lambda x: x[2])

            # No-consecutive rule: same person cannot get the token twice in a row
            winner_id, winner_data, _ = willing[0]
            if winner_id == last_speaker_id and len(willing) > 1:
                winner_id, winner_data, _ = willing[1]

            # Winner delivers their speech
            speech_prompt = _build_speech_prompt(winner_data, winner_id, topic, agenda, chat_history)
            resp = await tracked_ainvoke(make_llm(winner_id), speech_prompt, category="meeting", employee_id=winner_id)
            last_speaker_id = winner_id

            display = winner_data.get("nickname", "") or winner_data.get("name", "")
            await _chat(room.id, display, winner_data.get("role", ""), resp.content)
            chat_history.append({"speaker": display, "message": resp.content})
            discussion_entries.append({
                "id": winner_id,
                "name": winner_data.get("name", ""),
                "nickname": winner_data.get("nickname", ""),
                "comment": resp.content,
            })
        else:
            # max_rounds reached
            await _chat(room.id, MEETING_SYSTEM_SENDER, SYSTEM_SENDER, "Meeting has reached the maximum number of rounds. Auto-concluded.")

        # --- Synthesize meeting conclusion ---
        all_comments = "\n".join(
            f"[{d['name']}({d['nickname']})] {d['comment']}"
            for d in discussion_entries
        )
        summary_llm = make_llm(initiator_id or HR_ID)
        participant_names = ", ".join(
            edata.get("nickname", "") or edata.get("name", "")
            for _, edata in valid_participants
        )
        summary_prompt = (
            f"You are the meeting note-taker. Summarize the following focused meeting discussion.\n\n"
            f"Meeting topic: {topic}\n"
            f"Participants: {participant_names}\n\n"
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

        from onemancompany.core.store import append_activity_sync
        append_activity_sync({
            "type": "pull_meeting",
            "topic": topic,
            "initiator": initiator_id,
            "participants": [pid for pid, _ in valid_participants],
            "room": room.name,
            "rounds": rounds_used,
        })

        return {
            "status": "completed",
            "room": room.name,
            "topic": topic,
            "participants": [
                edata.get("nickname", "") or edata.get("name", "")
                for _, edata in valid_participants
            ],
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
        from onemancompany.core.store import save_room
        await save_room(room.id, {
            "is_booked": False,
            "booked_by": "",
            "participants": [],
        })
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
                    content = fpath.read_text(encoding=ENCODING_UTF8)
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

    task = loop.get_task(task_id)
    if not task:
        return {"status": "error", "message": "Current task not found."}

    project_id = task.project_id or task.original_project_id
    if not project_id:
        return {"status": "error", "message": "No project context."}

    _set_budget(project_id, budget_usd)
    return {"status": "ok", "project_id": project_id, "budget_usd": budget_usd}


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
    emp_data = load_employee(employee_id)
    if not emp_data:
        return {"status": "error", "message": "Employee not found."}

    tool_perms = emp_data.get("tool_permissions", [])
    if tool_name in (tool_perms or []):
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

    emp_name = emp_data.get("name", employee_id)
    emp_dept = emp_data.get("department", "")
    emp_role = emp_data.get("role", "")
    emp_level = emp_data.get("level", 1)
    task_desc = (
        f"Tool access request: Employee {emp_name} (ID: {employee_id}, {emp_dept}/{emp_role}, Lv.{emp_level}) "
        f"requests access to tool '{tool_name}'. Reason: {reason}. "
        f"Evaluate whether this is appropriate for their role and department. "
        f"If approved, call manage_tool_access(employee_id='{employee_id}', tool_name='{tool_name}', action='grant')."
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

    # Read from store to validate existence; mutations still go through company_state (Task 9)
    emp_data = load_employee(employee_id)
    if not emp_data:
        return {"status": "error", "message": f"Employee {employee_id} not found."}

    current_perms = list(emp_data.get("tool_permissions", []) or [])

    if action == "grant":
        if tool_name not in current_perms:
            current_perms.append(tool_name)
    elif action == "revoke":
        if tool_name in current_perms:
            current_perms.remove(tool_name)
    else:
        return {"status": "error", "message": f"Invalid action: {action}. Use 'grant' or 'revoke'."}

    # Persist to disk
    import asyncio as _asyncio
    from onemancompany.core import store as _store
    try:
        _asyncio.get_running_loop().create_task(
            _store.save_employee(employee_id, {"tool_permissions": current_perms})
        )
    except RuntimeError:
        logger.debug("No event loop for tool_permissions persist of {}", employee_id)

    return {
        "status": "ok",
        "employee": employee_id,
        "tool": tool_name,
        "action": action,
        "current_tool_permissions": current_perms,
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

    from onemancompany.core.vessel import employee_manager
    import asyncio

    main_loop = getattr(employee_manager, "_event_loop", None)
    if main_loop and main_loop.is_running():
        coro = employee_manager.resume_held_task(employee_id, task_id, result)
        main_loop.call_soon_threadsafe(main_loop.create_task, coro)
    else:
        return {"status": "error", "message": "No event loop available to resume task"}

    return {"status": "ok", "message": f"Resume scheduled for task {task_id}"}


@tool
def read_node_detail(node_id: str) -> dict:
    """Read the full details of a task node by ID.

    Use this to inspect any task node's full description, result, and metadata
    when the context summary isn't enough.

    Args:
        node_id: The TaskNode ID to read.

    Returns:
        Full node details including description, result, status, and criteria.
    """
    from onemancompany.core.vessel import employee_manager
    from onemancompany.core.task_tree import get_tree
    from pathlib import Path

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return {"status": "error", "message": "No agent context."}

    # Find tree_path from current task in schedule
    tree_path = ""
    for entries in employee_manager._schedule.values():
        for e in entries:
            if e.node_id == task_id:
                tree_path = e.tree_path
                break
        if tree_path:
            break

    if not tree_path:
        return {"status": "error", "message": "No project context."}

    tree = get_tree(tree_path)
    node = tree.get_node(node_id)
    if not node:
        return {"status": "error", "message": f"Node {node_id} not found."}

    project_dir = str(Path(tree_path).parent)
    node.load_content(project_dir)

    return {
        "status": "ok",
        "id": node.id,
        "employee_id": node.employee_id,
        "description": node.description,
        "result": node.result,
        "status_phase": node.status,
        "acceptance_criteria": node.acceptance_criteria,
        "node_type": node.node_type,
        "created_at": node.created_at,
        "completed_at": node.completed_at,
    }


@tool
def update_project_team(members: list[dict]) -> dict:
    """Update the team roster for the current project.

    Appends new members to the project's team list. Does not overwrite existing members.

    Args:
        members: List of dicts with 'employee_id' and 'role' keys.

    Returns:
        Confirmation with count of added members.
    """
    from onemancompany.core.agent_loop import _current_vessel, _current_task_id

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return {"status": "error", "message": "No agent context."}

    task = vessel.get_task(task_id)
    if not task or not task.project_dir:
        return {"status": "error", "message": "No project directory in current task."}

    from pathlib import Path
    from datetime import datetime
    import yaml

    project_yaml = Path(task.project_dir) / PROJECT_YAML_FILENAME
    if not project_yaml.exists():
        return {"status": "error", "message": "project.yaml not found."}

    data = yaml.safe_load(project_yaml.read_text(encoding=ENCODING_UTF8)) or {}
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
        encoding=ENCODING_UTF8,
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
        glob_files, grep_search,
        load_skill,
        resume_held_task, update_project_team,
        read_node_detail,
        # Formerly gated — now available to all employees
        bash, use_tool, set_project_budget,
        set_cron, stop_cron_job, setup_webhook, remove_webhook,
        list_automations,
    ]
    for t in _base:
        tool_registry.register(t, ToolMeta(name=t.name, category="base"))

    # Tree tools self-register on import
    from onemancompany.agents import tree_tools as _tt  # noqa: F401

    # Sandbox tools — available to all when sandbox is enabled
    if is_sandbox_enabled():
        for t in SANDBOX_TOOLS:
            tool_registry.register(t, ToolMeta(name=t.name, category="base"))


_register_all_internal_tools()
