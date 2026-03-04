"""COO Agent — manages company assets: tools and meeting rooms.

Assets are stored as YAML under assets/ at project root:
  - assets/tools/   — equipment with access control
  - assets/rooms/   — meeting rooms (must be booked before agents communicate)
"""

from __future__ import annotations

import asyncio
import uuid

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from onemancompany.agents.base import BaseAgentRunner, make_llm
from onemancompany.agents.common_tools import COMMON_TOOLS
from onemancompany.core.config import COO_ID, MAX_SUMMARY_LEN, PROJECTS_DIR, ROOMS_DIR, SHARED_PROMPTS_DIR, SOP_DIR, STATUS_IDLE, STATUS_WORKING, TOOLS_DIR, WORKFLOWS_DIR, load_assets, migrate_legacy_tool, save_company_culture, save_company_direction, save_workflow, slugify_tool_name
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.state import MeetingRoom, OfficeTool, company_state

# Pending hiring requests awaiting CEO approval.
# { request_id: { role, reason, skills, requested_by, requested_at } }
pending_hiring_requests: dict[str, dict] = {}

COO_SYSTEM_PROMPT = """You are the COO (Chief Operating Officer) of "One Man Company".
You manage operations, assets, and project execution.

## CORE PRINCIPLE — Delegate, Don't Execute
Your job is to PLAN, COORDINATE, REVIEW, and ACCEPT — NOT to write code or create content.
- ALWAYS dispatch_task() implementation work to employees.
- If no suitable employee exists, dispatch to HR to hire one first.
- Only do work yourself as an absolute LAST RESORT.
- For complex tasks: break into sub-tasks, dispatch each to the right employee.

## Delegation Decision Tree
1. Is this a people/HR task? → dispatch_task("00002", ...)
2. Is this implementation work (code, design, writing)? → dispatch_task(best_employee, ...)
3. Can an existing employee handle it? → Check list_colleagues(), then dispatch.
4. No suitable employee? → dispatch_task("00002", "Hire a [role] for [task]")
5. Only administrative/coordination work left? → Handle it yourself.

## Responsibilities

### Task Execution via Delegation
When receiving CEO action plans:
- HR-sourced actions → dispatch_task("00002", ...) immediately.
- COO-sourced actions → find the best employee and dispatch.
- Report a brief summary of all dispatches.

### Asset Management
- New tools: register_asset(name, description, source_project_dir, source_files).
- List/manage: list_tools(), grant_tool_access(), revoke_tool_access().
- All project outputs that become company tools must go through register_asset().

### Meeting Rooms
- book_meeting_room() / release_meeting_room() / list_meeting_rooms().
- No free rooms → tell employee to wait. Do NOT create rooms without CEO authorization.
- add_meeting_room() only when CEO explicitly requests.

### Knowledge Management
- deposit_company_knowledge(category, name, content) to preserve:
  - "workflow": Business processes and procedures
  - "culture": Company values and culture statements
  - "sop": Standard operating procedures
  - "guidance": Shared employee guidance and best practices
  - "direction": Company strategic direction
- Use this for operational insights, process improvements, and lessons learned.
- Tools/equipment still go through register_asset().

### Requesting New Hires
When you identify that the team lacks a capability needed for current or upcoming work:
1. Call `request_hiring(role, reason, desired_skills)` — this sends a request to CEO for approval.
2. CEO will approve or reject. If approved, HR automatically starts recruiting.
3. Do NOT dispatch_task to HR for hiring directly — always go through request_hiring so CEO can approve.

### Project Acceptance (项目验收)
When you receive a "项目验收任务":
1. Read the actual deliverables in the project workspace — do NOT just trust the timeline.
2. For code: check files exist, look at structure, verify it runs or at least looks complete.
3. For documents: read actual content, check completeness against criteria.
4. Score each criterion as PASS/FAIL.
5. All PASS → accept_project(accepted=true, notes="验证详情").
6. Any FAIL → accept_project(accepted=false, notes="具体问题"), the work will be sent back.

## DO NOT
- Do NOT write code or create content yourself — dispatch to employees.
- Do NOT call pull_meeting() with only yourself.
- Do NOT approve projects without actually reading the deliverables.
- Do NOT create meeting rooms without CEO authorization.
- Do NOT dispatch hiring tasks directly to HR — use request_hiring() so CEO can decide.

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
            has_icon = "icon.png" in files
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
                has_icon=has_icon,
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
def remove_tool(tool_id: str) -> dict:
    """Remove a tool/asset from the company and delete its folder from disk.

    Args:
        tool_id: The ID of the tool to remove.

    Returns:
        Confirmation with the removed tool name.
    """
    import shutil

    t = company_state.tools.get(tool_id)
    if not t:
        return {"status": "error", "message": f"Tool not found: {tool_id}"}

    name = t.name
    folder_name = t.folder_name

    # Remove from in-memory state
    del company_state.tools[tool_id]

    # Remove folder from disk
    if folder_name:
        folder = TOOLS_DIR / folder_name
        if folder.exists():
            shutil.rmtree(folder)

    company_state.activity_log.append(
        {"type": "tool_removed", "name": name, "id": tool_id}
    )

    return {"status": "success", "name": name, "id": tool_id}


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
    all_participants = [employee_id] + participants
    # Meetings require at least 2 distinct people
    if len(set(all_participants)) < 2:
        return {
            "status": "denied",
            "message": "A meeting requires at least 2 participants. Do not book a room for one person.",
        }

    for room in company_state.meeting_rooms.values():
        if not room.is_booked:
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


@tool
def request_hiring(role: str, reason: str, desired_skills: list[str] | None = None) -> dict:
    """Request CEO approval to hire a new employee.

    Use this when you identify the team lacks a capability needed for current
    or upcoming work. The request goes to CEO for approval — if approved, HR
    automatically starts recruiting.

    Args:
        role: The role to hire (e.g. "Game Developer", "QA Engineer").
        reason: Why this hire is needed — what gap or demand triggers it.
        desired_skills: Optional list of desired skills/technologies.

    Returns:
        Confirmation that the request was submitted for CEO review.
    """
    from datetime import datetime

    request_id = str(uuid.uuid4())[:8]
    req = {
        "role": role,
        "reason": reason,
        "desired_skills": desired_skills or [],
        "requested_by": COO_ID,
        "requested_at": datetime.now().isoformat(),
    }
    pending_hiring_requests[request_id] = req

    # Publish event for frontend — CEO will see and approve/reject
    asyncio.get_event_loop().create_task(
        event_bus.publish(CompanyEvent(
            type="hiring_request_ready",
            payload={"request_id": request_id, **req},
            agent="COO",
        ))
    )

    return {
        "status": "submitted",
        "request_id": request_id,
        "message": f"Hiring request for '{role}' submitted to CEO for approval.",
    }


@tool
def deposit_company_knowledge(
    category: str,
    name: str,
    content: str,
) -> dict:
    """Deposit company knowledge, process, or culture into the appropriate location.

    Use this to preserve operational insights, processes, and guidelines that
    benefit the entire company — not just tools/equipment (use register_asset for those).

    Args:
        category: Type of knowledge. One of:
            - "workflow": Business process workflow → company/business/workflows/
            - "culture": Company culture value → company/company_culture.yaml
            - "sop": Standard operating procedure → company/operations/sops/
            - "guidance": Shared employee guidance → company/shared_prompts/
            - "direction": Company strategic direction → company/company_direction.yaml
        name: Identifier/title for the knowledge (used as filename for file-based categories).
        content: The knowledge content (markdown for workflows/sops/guidance, plain text for culture/direction).

    Returns:
        Confirmation with category, name, and storage path.
    """
    valid_categories = ("workflow", "culture", "sop", "guidance", "direction")
    if category not in valid_categories:
        return {
            "status": "error",
            "message": f"Invalid category '{category}'. Must be one of: {', '.join(valid_categories)}",
        }

    if category == "workflow":
        save_workflow(name, content)
        path = str(WORKFLOWS_DIR / f"{name}.md")
        company_state.activity_log.append({
            "type": "knowledge_deposited",
            "category": "workflow",
            "name": name,
        })

    elif category == "culture":
        culture_item = {"content": content, "added_by": "COO", "name": name}
        company_state.company_culture.append(culture_item)
        save_company_culture(company_state.company_culture)
        path = "company/company_culture.yaml"
        company_state.activity_log.append({
            "type": "knowledge_deposited",
            "category": "culture",
            "name": name,
        })

    elif category == "sop":
        SOP_DIR.mkdir(parents=True, exist_ok=True)
        sop_path = SOP_DIR / f"{name}.md"
        sop_path.write_text(content, encoding="utf-8")
        path = str(sop_path)
        company_state.activity_log.append({
            "type": "knowledge_deposited",
            "category": "sop",
            "name": name,
        })

    elif category == "guidance":
        SHARED_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
        guidance_path = SHARED_PROMPTS_DIR / f"{name}.md"
        guidance_path.write_text(content, encoding="utf-8")
        path = str(guidance_path)
        company_state.activity_log.append({
            "type": "knowledge_deposited",
            "category": "guidance",
            "name": name,
        })

    elif category == "direction":
        save_company_direction(content)
        company_state.company_direction = content
        path = "company/company_direction.yaml"
        company_state.activity_log.append({
            "type": "knowledge_deposited",
            "category": "direction",
            "name": name,
        })

    return {
        "status": "success",
        "category": category,
        "name": name,
        "path": path,
    }


class COOAgent(BaseAgentRunner):
    role = "COO"
    employee_id = COO_ID

    def __init__(self) -> None:
        self._agent = create_react_agent(
            model=make_llm(self.employee_id),
            tools=[
                register_asset,
                remove_tool,
                list_tools,
                grant_tool_access,
                revoke_tool_access,
                list_assets,
                list_meeting_rooms,
                book_meeting_room,
                release_meeting_room,
                add_meeting_room,
                request_hiring,
                deposit_company_knowledge,
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
            + self._get_dynamic_context_section()
            + self._get_efficiency_guidelines_section()
        )

    async def run(self, task: str) -> str:
        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": f"COO analyzing: {task[:80]}"})

        result = await self._agent.ainvoke(
            {"messages": [
                SystemMessage(content=self._build_full_prompt()),
                HumanMessage(content=task),
            ]}
        )

        final = result["messages"][-1].content
        self._set_status(STATUS_IDLE)
        await self._publish("agent_done", {"role": "COO", "summary": final[:MAX_SUMMARY_LEN]})
        return final


# Singleton removed — agent instances are now created and registered
# in main.py lifespan via PersistentAgentLoop.
