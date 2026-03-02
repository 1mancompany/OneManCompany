"""COO Agent — manages company assets: tools and meeting rooms.

Assets are stored as YAML under assets/ at project root:
  - assets/tools/   — equipment with access control
  - assets/rooms/   — meeting rooms (must be booked before agents communicate)
"""

from __future__ import annotations

import uuid

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from onemancompany.agents.base import BaseAgentRunner, make_llm
from onemancompany.agents.common_tools import COMMON_TOOLS
from onemancompany.core.config import COO_ID, MAX_SUMMARY_LEN, PROJECTS_DIR, ROOMS_DIR, STATUS_IDLE, STATUS_WORKING, TOOLS_DIR, load_assets, migrate_legacy_tool, slugify_tool_name
from onemancompany.core.state import MeetingRoom, OfficeTool, company_state

COO_SYSTEM_PROMPT = """You are the COO (Chief Operating Officer) of a startup called "One Man Company".

You manage the company's assets, which includes:
1. **Tools & Equipment** — servers, devices, and capabilities. Each tool is stored as a folder under assets/tools/ with its own files. Each tool has access control — only authorized employees can use it.
2. **Meeting Rooms** — employees MUST book a meeting room through you before communicating with each other (including founding employees and the CEO).

## Your Responsibilities:

### Tool & Equipment Management (Asset Intake)
- **All new tools must go through the official intake process via register_asset()**.
  This applies to both newly created tools AND tools purchased/produced by projects.
- register_asset() creates a tool folder with metadata (tool.yaml) and optionally copies
  associated files (code, scripts, configs) from a project workspace into the tool folder.
- Use list_tools() to see current tools, their access permissions, and associated files.
- Use grant_tool_access() and revoke_tool_access() to manage who can use each tool.
- By default, new tools are open to everyone (empty allowed_users = open access).

### Meeting Room Booking
- When an employee needs to communicate with another employee, they must first request a meeting room.
- Call book_meeting_room() to reserve a room for the requester.
- Call release_meeting_room() when a meeting is done.
- If NO free meeting rooms are available, tell the employee to process other tasks or refine their work first. Do NOT create a new meeting room — the CEO must authorize that.
- Use list_meeting_rooms() to check availability.

### Adding New Meeting Rooms
- Only add new meeting rooms when the CEO explicitly requests it via add_meeting_room().

## Cross-team Collaboration
You can call list_colleagues() to see all employees, then call pull_meeting() to organize
a focused meeting with relevant colleagues when you need alignment on operational decisions,
resource allocation, or process improvements.

## File Editing
You can read and edit any file in the project directory:
- Use read_file() to read file contents, list_directory() to browse directories.
- Use propose_file_edit() to propose changes — the CEO must approve before they take effect.
  Always set proposed_by="COO" when calling propose_file_edit.
- Files are automatically backed up before editing, so changes can be rolled back.

Be concise and action-oriented.
"""


def _load_assets_from_disk() -> None:
    """Load existing assets from assets/tools/ and assets/rooms/ into company_state.

    Legacy flat YAML files are auto-migrated to folder-based format on first load.
    """
    tools_data, rooms_data = load_assets()

    # First pass: migrate any legacy flat YAML tools to folder-based format
    used_slugs: set[str] = set()
    # Collect existing folder names so we don't collide
    if TOOLS_DIR.exists():
        for entry in TOOLS_DIR.iterdir():
            if entry.is_dir():
                used_slugs.add(entry.name)

    legacy_items = [(tid, d) for tid, d in tools_data.items() if d.get("_legacy")]
    for tool_id, data in legacy_items:
        folder_name, _ = migrate_legacy_tool(tool_id, data, used_slugs)
        # Update entry in-place for loading below
        data.pop("_legacy", None)
        data["_folder_name"] = folder_name
        data["_files"] = []
        data["id"] = tool_id

    count = 0
    for tool_id, data in tools_data.items():
        if tool_id not in company_state.tools:
            folder_name = data.get("_folder_name", "")
            files = data.get("_files", [])
            company_state.tools[tool_id] = OfficeTool(
                id=tool_id,
                name=data.get("name", tool_id),
                description=data.get("description", ""),
                added_by=data.get("added_by", "COO"),
                desk_position=tuple(data.get("desk_position", [5 + (count % 3) * 5, 8 + (count // 3) * 3])),
                sprite=data.get("sprite", "desk_equipment"),
                allowed_users=data.get("allowed_users", []),
                files=files,
                folder_name=folder_name,
            )
            count += 1

    for room_id, data in rooms_data.items():
        if room_id not in company_state.meeting_rooms:
            company_state.meeting_rooms[room_id] = MeetingRoom(
                id=room_id,
                name=data.get("name", room_id),
                description=data.get("description", ""),
                capacity=data.get("capacity", 6),
                position=tuple(data.get("position", [1, 8])),
                sprite=data.get("sprite", "meeting_room"),
            )


# Load persisted assets on import, then recompute layout to position them
_load_assets_from_disk()
from onemancompany.core.layout import compute_asset_layout as _compute_asset_layout
_compute_asset_layout(company_state, company_state.office_layout)


# ===== LangChain tools for the COO agent =====


def _persist_tool(t: OfficeTool) -> None:
    """Write a tool's data to assets/tools/{folder_name}/tool.yaml."""
    if not t.folder_name:
        t.folder_name = slugify_tool_name(t.name)
    folder = TOOLS_DIR / t.folder_name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "tool.yaml"
    with open(path, "w") as f:
        yaml.dump(
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "added_by": t.added_by,
                "desk_position": list(t.desk_position),
                "sprite": t.sprite,
                "allowed_users": t.allowed_users,
            },
            f,
            allow_unicode=True,
            default_flow_style=False,
        )


@tool
def register_asset(
    name: str,
    description: str,
    source_project_dir: str = "",
    source_files: list[str] | None = None,
) -> dict:
    """Register a new tool/asset through the official intake process.

    All new tools — whether newly created or produced by a project — must go through
    this intake. Creates a tool folder under assets/tools/{slug_name}/ containing
    tool.yaml and any associated files.

    Args:
        name: Short name for the tool (e.g. 'Code Review Bot', 'CI/CD Pipeline').
        description: What this tool does for the company.
        source_project_dir: (Optional) Absolute path to a project workspace directory.
            If provided, source_files will be copied from this directory into the tool folder.
        source_files: (Optional) List of filenames (relative to source_project_dir) to copy
            into the tool folder. Only used when source_project_dir is provided.

    Returns:
        Confirmation with tool id, folder name, and copied files.
    """
    import shutil
    from pathlib import Path

    eq_id = str(uuid.uuid4())[:8]
    folder_name = slugify_tool_name(name)

    # Handle slug collision with existing folders
    if (TOOLS_DIR / folder_name).exists():
        folder_name = f"{folder_name}_{eq_id}"

    count = len(company_state.tools)
    row = count // 3
    col = count % 3
    desk_pos = (5 + col * 5, 8 + row * 3)

    office_tool = OfficeTool(
        id=eq_id,
        name=name,
        description=description,
        added_by="COO",
        desk_position=desk_pos,
        sprite="desk_equipment",
        allowed_users=[],
        files=[],
        folder_name=folder_name,
    )

    # Create tool folder and write tool.yaml
    _persist_tool(office_tool)

    # Copy files from project workspace if provided
    copied_files: list[str] = []
    if source_project_dir and source_files:
        src_dir = Path(source_project_dir)
        # Security check: source must be under PROJECTS_DIR
        try:
            src_dir.resolve().relative_to(PROJECTS_DIR.resolve())
        except ValueError:
            return {"status": "error", "message": f"Source directory must be under projects/: {source_project_dir}"}

        tool_folder = TOOLS_DIR / folder_name
        for fname in source_files:
            src_file = src_dir / fname
            # Prevent path traversal
            try:
                src_file.resolve().relative_to(src_dir.resolve())
            except ValueError:
                continue
            if src_file.exists() and src_file.is_file():
                dst_file = tool_folder / Path(fname).name
                shutil.copy2(str(src_file), str(dst_file))
                copied_files.append(Path(fname).name)

        office_tool.files = copied_files

    company_state.tools[eq_id] = office_tool
    company_state.activity_log.append(
        {"type": "tool_added", "name": name, "description": description, "folder": folder_name}
    )

    return {
        "status": "success",
        "id": eq_id,
        "name": name,
        "folder_name": folder_name,
        "position": list(desk_pos),
        "files": copied_files,
    }


@tool
def list_tools() -> list[dict]:
    """List all tools and equipment currently in the company's assets."""
    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "added_by": t.added_by,
            "allowed_users": t.allowed_users,
            "access": "restricted" if t.allowed_users else "open",
            "folder_name": t.folder_name,
            "files": t.files,
        }
        for t in company_state.tools.values()
    ]


@tool
def grant_tool_access(tool_id: str, employee_id: str) -> dict:
    """Grant an employee access to a specific tool.

    If the tool currently has open access (empty allowed_users), granting access
    to one employee will restrict it to ONLY that employee. To keep it open while
    also tracking, add all relevant employees.

    Args:
        tool_id: The ID of the tool.
        employee_id: The employee ID to grant access to.

    Returns:
        Updated access list.
    """
    t = company_state.tools.get(tool_id)
    if not t:
        return {"status": "error", "message": f"Tool '{tool_id}' not found."}
    if employee_id not in t.allowed_users:
        t.allowed_users.append(employee_id)
        _persist_tool(t)
    return {
        "status": "success",
        "tool": t.name,
        "allowed_users": t.allowed_users,
    }


@tool
def revoke_tool_access(tool_id: str, employee_id: str) -> dict:
    """Revoke an employee's access to a specific tool.

    If the allowed_users list becomes empty after revocation, the tool
    reverts to open access (everyone can use it).

    Args:
        tool_id: The ID of the tool.
        employee_id: The employee ID to revoke access from.

    Returns:
        Updated access list.
    """
    t = company_state.tools.get(tool_id)
    if not t:
        return {"status": "error", "message": f"Tool '{tool_id}' not found."}
    if employee_id in t.allowed_users:
        t.allowed_users.remove(employee_id)
        _persist_tool(t)
    return {
        "status": "success",
        "tool": t.name,
        "allowed_users": t.allowed_users,
        "access": "restricted" if t.allowed_users else "open",
    }


@tool
def list_assets() -> list[dict]:
    """List all company assets — both tools and meeting rooms."""
    items = [
        {"id": t.id, "name": t.name, "description": t.description,
         "type": "tool", "access": "restricted" if t.allowed_users else "open"}
        for t in company_state.tools.values()
    ]
    items += [
        {"id": m.id, "name": m.name, "description": m.description, "type": "room",
         "capacity": m.capacity, "is_booked": m.is_booked, "booked_by": m.booked_by}
        for m in company_state.meeting_rooms.values()
    ]
    return items


@tool
def list_meeting_rooms() -> list[dict]:
    """List all meeting rooms and their current booking status."""
    return [
        {
            "id": m.id,
            "name": m.name,
            "capacity": m.capacity,
            "is_booked": m.is_booked,
            "booked_by": m.booked_by,
            "participants": m.participants,
        }
        for m in company_state.meeting_rooms.values()
    ]


@tool
def book_meeting_room(employee_id: str, participants: list[str], purpose: str = "") -> dict:
    """Book a meeting room for an employee to communicate with others.

    Employees must book a meeting room before they can communicate with other employees.
    If no rooms are available, the employee should work on other tasks or refine their work.

    Args:
        employee_id: The ID of the employee requesting the room.
        participants: List of employee IDs who will join the meeting.
        purpose: Brief description of the meeting purpose.

    Returns:
        Booking result — success with room details, or denied if no rooms free.
    """
    for room in company_state.meeting_rooms.values():
        if not room.is_booked:
            all_participants = [employee_id] + participants
            if len(all_participants) > room.capacity:
                continue
            room.is_booked = True
            room.booked_by = employee_id
            room.participants = all_participants
            company_state.activity_log.append({
                "type": "meeting_booked",
                "room": room.name,
                "booked_by": employee_id,
                "participants": all_participants,
                "purpose": purpose,
            })
            return {
                "status": "booked",
                "room_id": room.id,
                "room_name": room.name,
                "participants": all_participants,
                "message": f"Meeting room {room.name} booked successfully.",
            }

    company_state.activity_log.append({
        "type": "meeting_denied",
        "requested_by": employee_id,
        "reason": "no_free_rooms",
    })
    return {
        "status": "denied",
        "message": "No meeting rooms available. Please work on other tasks first and try again later.",
    }


@tool
def release_meeting_room(room_id: str) -> dict:
    """Release a meeting room after a meeting is done.

    Args:
        room_id: The ID of the meeting room to release.

    Returns:
        Confirmation of release.
    """
    room = company_state.meeting_rooms.get(room_id)
    if not room:
        return {"status": "error", "message": f"Meeting room '{room_id}' does not exist."}
    if not room.is_booked:
        return {"status": "error", "message": f"Meeting room {room.name} is not currently booked."}

    old_participants = room.participants.copy()
    room.is_booked = False
    room.booked_by = ""
    room.participants = []
    company_state.activity_log.append({
        "type": "meeting_released",
        "room": room.name,
        "participants": old_participants,
    })
    return {
        "status": "released",
        "room_name": room.name,
        "message": f"Meeting room {room.name} released.",
    }


@tool
def add_meeting_room(name: str, capacity: int = 6, description: str = "") -> dict:
    """Add a new meeting room (CEO authorization required).

    Args:
        name: Name for the meeting room (e.g. 'Meeting Room B', 'Main Conference Hall').
        capacity: Maximum number of people.
        description: Brief description of the room.

    Returns:
        Confirmation with room details.
    """
    room_id = f"room_{str(uuid.uuid4())[:6]}"
    room_count = len(company_state.meeting_rooms)
    pos = (1 + room_count * 4, 8)

    room = MeetingRoom(
        id=room_id,
        name=name,
        description=description or f"Meeting room with capacity for {capacity} people.",
        capacity=capacity,
        position=pos,
        sprite="meeting_room",
    )
    company_state.meeting_rooms[room_id] = room

    # Persist to assets/rooms/
    ROOMS_DIR.mkdir(parents=True, exist_ok=True)
    room_path = ROOMS_DIR / f"{room_id}.yaml"
    with open(room_path, "w") as f:
        yaml.dump(
            {
                "name": name,
                "type": "meeting_room",
                "description": room.description,
                "capacity": capacity,
                "position": list(pos),
                "sprite": "meeting_room",
            },
            f,
            allow_unicode=True,
            default_flow_style=False,
        )

    company_state.activity_log.append({
        "type": "tool_added",
        "name": name,
        "description": f"New meeting room (capacity: {capacity})",
    })

    return {
        "status": "success",
        "id": room_id,
        "name": name,
        "capacity": capacity,
        "position": list(pos),
    }


class COOAgent(BaseAgentRunner):
    role = "COO"
    employee_id = COO_ID

    def __init__(self) -> None:
        self._agent = create_react_agent(
            model=make_llm(self.employee_id),
            tools=[
                register_asset,
                list_tools,
                grant_tool_access,
                revoke_tool_access,
                list_assets,
                list_meeting_rooms,
                book_meeting_room,
                release_meeting_room,
                add_meeting_room,
            ] + COMMON_TOOLS,
        )

    def _build_prompt(self) -> str:
        return (
            COO_SYSTEM_PROMPT
            + self._get_skills_prompt_section()
            + self._get_tools_prompt_section()
            + self._get_company_culture_prompt_section()
            + self._get_work_principles_prompt_section()
            + self._get_guidance_prompt_section()
        )

    async def run(self, task: str) -> str:
        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": f"COO analyzing: {task[:80]}"})

        result = await self._agent.ainvoke(
            {"messages": [
                SystemMessage(content=self._build_prompt()),
                HumanMessage(content=task),
            ]}
        )

        final = result["messages"][-1].content
        self._set_status(STATUS_IDLE)
        await self._publish("agent_done", {"role": "COO", "summary": final[:MAX_SUMMARY_LEN]})
        return final


# Singleton removed — agent instances are now created and registered
# in main.py lifespan via PersistentAgentLoop.
