"""Common tools available to ALL employees — default tools every employee has.

The main tool here is `pull_meeting` (pull meeting / sync-up): any employee can pull
relevant colleagues into a meeting room for a focused discussion.
"""

from __future__ import annotations

import asyncio
import json
import re

from langchain_core.tools import tool

from onemancompany.agents.base import make_llm
from onemancompany.core.config import HR_ID, MAX_DISCUSSION_SUMMARY_LEN, MAX_PRINCIPLES_LEN
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.state import company_state


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
def read_file(file_path: str) -> dict:
    """Read the contents of a file under the project directory.

    Can read any project file, including employee profiles, config files, company rules, etc.
    The path can be relative to the project root or an absolute path.

    Args:
        file_path: File path, e.g. "employees/hr/profile.yaml" or "company_rules/xxx.md"

    Returns:
        A dict containing the file contents, or an error message.
    """
    from onemancompany.core.file_editor import _resolve_path

    resolved = _resolve_path(file_path)
    if resolved is None:
        return {"status": "error", "message": f"Invalid path or outside project scope: {file_path}"}
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
def list_directory(dir_path: str = "") -> dict:
    """List files and subdirectories under the project directory.

    Args:
        dir_path: Directory path relative to the project root. An empty string means the project root.

    Returns:
        A dict containing the list of entries.
    """
    from onemancompany.core.file_editor import _resolve_path

    resolved = _resolve_path(dir_path or ".")
    if resolved is None:
        return {"status": "error", "message": f"Invalid path: {dir_path}"}
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
) -> dict:
    """Propose a file edit request (requires CEO approval before execution).

    Can edit any file under the project directory, including:
    - Employee profiles (employees/xxx/profile.yaml)
    - Work principles (employees/xxx/work_principles.md)
    - Skill files (employees/xxx/skills/xxx.md)
    - Company rules (company_rules/xxx.md)
    - Asset configuration (assets/tools/xxx.yaml, assets/rooms/xxx.yaml)
    - Company culture wall (culture_wall.yaml)
    - Any other project file

    After submission, the CEO will see a diff comparison on the frontend.
    The edit is executed automatically once approved.
    The original file is backed up (timestamped) before execution for easy rollback.

    Args:
        file_path: File path relative to the project root
        new_content: The complete new file content after editing
        reason: Explanation for the edit

    Returns:
        Edit request status (pending_approval means submitted and awaiting approval).
    """
    from onemancompany.core.file_editor import propose_edit

    # Determine who is proposing (from the call context, default to unknown)
    # The agent name will be injected by the caller
    result = propose_edit(file_path, new_content, reason, proposed_by=proposed_by or "agent")
    if result["status"] == "error":
        return result

    # Publish event for CEO frontend
    from onemancompany.core.file_editor import pending_file_edits
    edit = pending_file_edits.get(result["edit_id"])
    if edit:
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


@tool
async def pull_meeting(
    topic: str,
    participant_ids: list[str],
    agenda: str = "",
    initiator_id: str = "",
) -> dict:
    """Pull meeting / sync-up — initiate a focused meeting, pulling relevant colleagues to discuss a specific topic.

    Automatically books a meeting room, organizes participants for discussion, and outputs meeting conclusions.
    Any employee can use this tool.

    Args:
        topic: Meeting topic, e.g. "Discuss technical plan for new feature"
        participant_ids: List of colleague IDs who should attend
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
        # Run focused discussion
        llm = make_llm(initiator_id or HR_ID)
        discussion_entries: list[dict] = []

        # Each participant speaks on the topic
        for emp in valid_participants:
            principles_ctx = ""
            if emp.work_principles:
                principles_ctx = f"\nYour work principles:\n{emp.work_principles[:MAX_PRINCIPLES_LEN]}\n"

            prompt = (
                f"You are {emp.name} ({emp.nickname}, Department: {emp.department}, {emp.role}, Lv.{emp.level}).\n"
                f"{principles_ctx}"
                f"You are attending a focused meeting.\n"
                f"Meeting topic: {topic}\n"
            )
            if agenda:
                prompt += f"Meeting agenda: {agenda}\n"
            if discussion_entries:
                recent = discussion_entries[-3:]  # last 3 comments for context
                context = "\n".join(f"  {d['name']}: {d['comment']}" for d in recent)
                prompt += f"\nPrevious comments:\n{context}\n"
            prompt += (
                f"\nBased on your expertise and work principles, share your brief perspective on the meeting topic (2-3 sentences). "
                f"Focus on what you can contribute, any suggestions, or concerns."
            )

            resp = await llm.ainvoke(prompt)
            comment = resp.content
            discussion_entries.append({
                "id": emp.id, "name": emp.name,
                "nickname": emp.nickname, "comment": comment,
            })
            display = emp.nickname or emp.name
            await _chat(room.id, display, emp.role, comment)

        # Synthesize meeting conclusion
        all_comments = "\n".join(
            f"[{d['name']}({d['nickname']})] {d['comment']}"
            for d in discussion_entries
        )
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
        summary_resp = await llm.ainvoke(summary_prompt)
        summary_text = summary_resp.content

        await _chat(room.id, "Meeting Notes", "HR", f"[Meeting Summary] {summary_text[:200]}")

        # Parse action items
        action_items = []
        try:
            json_match = re.search(r'\[.*\]', summary_text, re.DOTALL)
            if json_match:
                action_items = json.loads(json_match.group())
        except (json.JSONDecodeError, AttributeError):
            pass

        company_state.activity_log.append({
            "type": "pull_meeting",
            "topic": topic,
            "initiator": initiator_id,
            "participants": [e.id for e in valid_participants],
            "room": room.name,
        })

        return {
            "status": "completed",
            "room": room.name,
            "topic": topic,
            "participants": [e.nickname or e.name for e in valid_participants],
            "discussion": discussion_entries,
            "summary": summary_text[:MAX_DISCUSSION_SUMMARY_LEN],
            "action_items": action_items,
        }

    finally:
        # Release meeting room
        room.is_booked = False
        room.booked_by = ""
        room.participants = []
        await _publish("meeting_released", {
            "room_id": room.id, "room_name": room.name,
        })


# All common tools that every employee agent gets access to
COMMON_TOOLS = [
    read_file,
    list_directory,
    propose_file_edit,
    list_colleagues,
    pull_meeting,
]
