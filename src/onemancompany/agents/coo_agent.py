"""COO Agent — manages company assets: tools and meeting rooms.

Assets are stored as YAML under assets/ at project root:
  - assets/tools/   — equipment with access control
  - assets/rooms/   — meeting rooms (must be booked before agents communicate)
"""

from __future__ import annotations

import asyncio
from loguru import logger
import uuid

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from onemancompany.agents.base import BaseAgentRunner, extract_final_content, make_llm
from onemancompany.core.config import COO_ID, HR_ID, MAX_SUMMARY_LEN, OrgDir, PF_DEPARTMENT, PF_NAME, PF_REMOTE, PF_ROLE, PROJECTS_DIR, ROOMS_DIR, STATUS_IDLE, STATUS_WORKING, TOOL_YAML_FILENAME, TOOLS_DIR, WORKFLOWS_DIR, load_assets, save_company_direction, save_workflow, slugify_tool_name
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.models import EventType
from onemancompany.core.state import MeetingRoom, OfficeTool, company_state
from onemancompany.core.store import append_activity_sync as _append_activity

# Pending hiring requests — now auto-approved; kept for audit/frontend display.
# { hire_id: { role, reason, skills, requested_by, requested_at, ... } }
pending_hiring_requests: dict[str, dict] = {}


# COO operational prompt is now in employees/00003/role_guide.md (loaded by _get_role_identity_section)


def _load_assets_from_disk() -> None:
    """Load existing assets from assets/tools/ and assets/rooms/ into company_state.

    Legacy flat YAML files are auto-migrated to folder-based format on first load.
    """
    tools_data, rooms_data = load_assets()

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
                tool_type=data.get("tool_type", "template"),
                reference_url=data.get("reference_url", ""),
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
                agenda=data.get("agenda", {}),
            )


# Assets are loaded explicitly during startup (main.py lifespan) and hot-reload
# (state.py). No module-level loading here to avoid double-load on import.


# ===== LangChain tools for the COO agent =====


def _persist_tool(t: OfficeTool) -> None:
    """Write a tool's data to assets/tools/{folder_name}/tool.yaml."""
    if not t.folder_name:
        t.folder_name = slugify_tool_name(t.name)
    folder = TOOLS_DIR / t.folder_name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / TOOL_YAML_FILENAME
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
                "tool_type": t.tool_type,
                "reference_url": t.reference_url,
            },
            f,
            allow_unicode=True,
            default_flow_style=False,
        )


@tool
def register_asset(
    name: str,
    description: str,
    tool_type: str = "template",
    source_project_dir: str = "",
    source_files: list[str] | None = None,
    reference_url: str = "",
) -> dict:
    """Register a new tool/asset through the official intake process.

    All new tools — whether newly created or produced by a project — must go through
    this intake. Creates a tool folder under assets/tools/{slug_name}/ containing
    tool.yaml and any associated files.

    Args:
        name: Short name for the tool (e.g. 'Code Review Bot', 'CI/CD Pipeline').
        description: What this tool does for the company.
        tool_type: Type of tool — "script" (executable code/automation),
            or "reference" (external service link). Do NOT register templates or
            reference code as tools — use deposit_company_knowledge() instead.
        source_project_dir: (Optional) Absolute path to a project workspace directory.
            If provided, source_files will be copied from this directory into the tool folder.
        source_files: (Optional) List of filenames (relative to source_project_dir) to copy
            into the tool folder. Only used when source_project_dir is provided.
        reference_url: (Optional) URL for reference-type tools pointing to external services.

    Returns:
        Confirmation with tool id, folder name, and copied files.
    """
    import ast
    import shutil
    from pathlib import Path

    # Reject reference code / templates / non-tool items
    _reject_keywords = ["reference code", "template",
                        "scaffold", "example", "sample"]
    name_lower = name.lower()
    desc_lower = description.lower()
    for kw in _reject_keywords:
        if kw in name_lower or kw in desc_lower:
            return {"status": "error", "message": f"Rejected: '{kw}' found in name/description. "
                    "Reference code, templates, and examples are NOT tools. "
                    "Use deposit_company_knowledge() for knowledge, or keep in project directory."}

    # Reject duplicates — check existing tools for similar names
    existing_names = [t.name.lower() for t in company_state.tools]
    if name_lower in existing_names:
        return {"status": "error", "message": f"Rejected: tool '{name}' already exists. Do not register duplicates."}

    # Validate by tool_type
    if tool_type == "script":
        if not source_files:
            return {"status": "error", "message": "script-type tools must have source_files with .py or .sh files"}
        has_executable = any(f.endswith(('.py', '.sh')) for f in source_files)
        if not has_executable:
            return {"status": "error", "message": "script-type tools must include at least one .py or .sh file"}
        # Validate Python syntax
        if source_project_dir:
            for f in source_files:
                if f.endswith('.py'):
                    src = Path(source_project_dir) / f
                    if src.exists():
                        try:
                            ast.parse(src.read_text())
                        except SyntaxError as e:
                            return {"status": "error", "message": f"Python syntax error in {f}: {e}"}
    elif tool_type == "reference":
        if not reference_url:
            return {"status": "error", "message": "reference-type tools must have a reference_url"}

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
        tool_type=tool_type,
        reference_url=reference_url,
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
                logger.warning("Path traversal blocked: %s", fname)
                continue
            if src_file.exists() and src_file.is_file():
                dst_file = tool_folder / Path(fname).name
                shutil.copy2(str(src_file), str(dst_file))
                copied_files.append(Path(fname).name)

        office_tool.files = copied_files

    company_state.tools[eq_id] = office_tool
    _append_activity(
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

    _append_activity(
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
    from onemancompany.core.store import load_rooms
    items += [
        {"id": m.get("id", ""), "name": m.get("name", ""), "description": m.get("description", ""),
         "type": "room", "capacity": m.get("capacity", 6),
         "is_booked": m.get("is_booked", False), "booked_by": m.get("booked_by", "")}
        for m in load_rooms()
    ]
    return items


@tool
def list_meeting_rooms() -> list[dict]:
    """List all meeting rooms and their current booking status."""
    from onemancompany.core.store import load_rooms
    return [
        {
            "id": m.get("id", ""),
            "name": m.get("name", ""),
            "capacity": m.get("capacity", 6),
            "is_booked": m.get("is_booked", False),
            "booked_by": m.get("booked_by", ""),
            "participants": m.get("participants", []),
        }
        for m in load_rooms()
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
            from onemancompany.core.store import save_room
            try:
                asyncio.get_running_loop().create_task(save_room(room.id, {
                    "is_booked": True,
                    "booked_by": employee_id,
                    "participants": all_participants,
                }))
            except RuntimeError:
                logger.debug("No event loop for save_room in book_meeting_room")
            _append_activity({
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

    _append_activity({
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
    from onemancompany.core.store import save_room
    try:
        asyncio.get_running_loop().create_task(save_room(room_id, {
            "is_booked": False,
            "booked_by": "",
            "participants": [],
        }))
    except RuntimeError:
        logger.debug("No event loop for save_room in release_meeting_room")
    _append_activity({
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

    _append_activity({
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
def request_hiring(
    role: str,
    reason: str,
    department: str = "",
    desired_skills: list[str] | None = None,
) -> dict:
    """Request to hire a new employee. Auto-approved — HR starts recruiting immediately.

    Use this when you identify the team lacks a capability needed for current
    or upcoming work. Returns a hire_id for tracking the hiring flow.

    Args:
        role: The role to hire (e.g. "Game Developer", "QA Engineer").
            This role will override the talent's profile role on hire.
        reason: Why this hire is needed — what gap or demand triggers it.
        department: Target department (e.g. "Engineering", "Design").
            If empty, auto-determined from role mapping.
        desired_skills: Optional list of desired skills/technologies.

    Returns:
        hire_id that you MUST use in __HOLDING:hire_id=<hire_id> to wait for completion.
    """
    from datetime import datetime
    from onemancompany.core.agent_loop import _current_vessel, _current_task_id

    # Capture project context from COO's current task
    project_id = ""
    project_dir = ""
    caller_loop = _current_vessel.get()
    caller_task_id = _current_task_id.get()
    if caller_loop and caller_task_id:
        caller_task = caller_loop.get_task(caller_task_id)
        if caller_task:
            project_id = caller_task.project_id or caller_task.original_project_id
            project_dir = caller_task.project_dir or caller_task.original_project_dir

    hire_id = str(uuid.uuid4())[:8]
    req = {
        "role": role,
        "department": department,
        "reason": reason,
        "desired_skills": desired_skills or [],
        "requested_by": COO_ID,
        "requested_at": datetime.now().isoformat(),
        "project_id": project_id,
        "project_dir": project_dir,
        "hire_id": hire_id,
        "auto_approved": True,
    }
    pending_hiring_requests[hire_id] = req

    # Auto-approved — dispatch HR child via project tree (if in tree context),
    # otherwise fallback to adhoc task.
    from onemancompany.core.vessel import employee_manager
    from onemancompany.core.config import HR_ID

    skills_str = ", ".join(desired_skills or [])
    jd = f"Hire {role}"
    if department:
        jd += f" (Department: {department})"
    if skills_str:
        jd += f" (Required skills: {skills_str})"
    jd += f"\nReason: {reason}"

    # Try dispatch_child to keep hiring in the project tree
    from onemancompany.agents.tree_tools import dispatch_child
    result = dispatch_child.invoke({
        "employee_id": HR_ID,
        "description": jd,
        "acceptance_criteria": [f"Successfully hired {role}", "New employee onboarding completed"],
    })

    if result.get("status") in ("dispatched", "dispatched_waiting"):
        logger.info("[hiring] Auto-approved hire_id={} role='{}' → HR child node in tree", hire_id, role)
    else:
        # No tree context — fallback to adhoc task
        from onemancompany.api.routes import _push_adhoc_task
        _push_adhoc_task(HR_ID, jd)
        logger.info("[hiring] Auto-approved hire_id={} role='{}' → HR adhoc task (no tree ctx)", hire_id, role)

    from onemancompany.api.routes import _pending_coo_hire_queue
    _pending_coo_hire_queue.append({
        "hire_id": hire_id,
        "role": role,
        "department": department,
        "project_id": project_id,
        "project_dir": project_dir,
        "reason": reason,
    })

    # Publish event for frontend notification (informational, no approval needed)
    coro = event_bus.publish(CompanyEvent(
        type=EventType.HIRING_REQUEST_READY,
        payload={"hire_id": hire_id, **req},
        agent="COO",
    ))
    loop = getattr(employee_manager, "_event_loop", None)
    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, loop)

    return {
        "status": "auto_approved",
        "hire_id": hire_id,
        "message": (
            f"Hiring '{role}' has been auto-approved. HR is starting recruitment. hire_id={hire_id}\n"
            f"WARNING: Hiring is an async process. The new employee is not yet available.\n"
            f"You must immediately output __HOLDING:hire_id={hire_id} to pause the current task.\n"
            f"The system will automatically wake you after the new employee is onboarded. Do NOT force-start without sufficient staff."
        ),
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

    Categories and their disk locations (use OrgDir enum values):
      - "workflow": Workflows, SOPs, and operational guidance → saved as {name}.md under the workflows directory
      - "culture": Company culture values → saved to company_culture.yaml
      - "direction": Company strategic direction → saved to company_direction.yaml

    The tool will return the exact disk path where the content was saved.

    Args:
        category: One of: "workflow", "culture", "direction".
            "workflow" covers all operational docs: workflows, SOPs, and guidance.
        name: Identifier/title (used as filename: {name}.md for workflow).
        content: The knowledge content (markdown for workflow, plain text for culture/direction).

    Returns:
        Confirmation with category, name, and storage path (absolute).
    """
    valid_categories = tuple(d.value for d in OrgDir)
    if category not in valid_categories:
        return {
            "status": "error",
            "message": f"Invalid category '{category}'. Must be one of: {', '.join(valid_categories)}",
        }

    if category == OrgDir.WORKFLOW:
        save_workflow(name, content)
        path = str(WORKFLOWS_DIR / f"{name}.md")

    elif category == OrgDir.CULTURE:
        culture_item = {"content": content, "added_by": "COO", "name": name}
        from onemancompany.core.store import load_culture as _load_culture, save_culture as _save_culture
        import asyncio as _asyncio
        items = _load_culture()
        items.append(culture_item)
        try:
            _loop = _asyncio.get_running_loop()
            _loop.create_task(_save_culture(items))
        except RuntimeError:
            logger.debug("No event loop for culture persist")
        path = str(OrgDir.CULTURE.disk_path)

    elif category == OrgDir.DIRECTION:
        save_company_direction(content)
        path = str(OrgDir.DIRECTION.disk_path)

    _append_activity({
        "type": "knowledge_deposited",
        "category": category,
        "name": name,
    })

    return {
        "status": "success",
        "category": category,
        "name": name,
        "path": path,
    }


@tool
async def assign_department(employee_id: str, department: str, role: str = "") -> dict:
    """Assign or change an employee's department and role.

    Updates the employee's department (and optionally role), recalculates
    their desk position based on the department zone, and adjusts tool permissions.

    For new hires, ALWAYS provide both department and role.

    Args:
        employee_id: The employee number (e.g. "00008").
        department: Target department name (e.g. "Engineering", "Design",
            "Analytics", "Marketing").
        role: The employee's role/title (e.g. "Engineer", "Designer", "PM",
            "QA Engineer"). Required for new hires.

    Returns:
        dict with status, employee_id, department, role, desk_position.
    """
    from onemancompany.core import store as _store
    from onemancompany.core.config import (
        DEFAULT_TOOL_PERMISSIONS, DEFAULT_TOOL_PERMISSIONS_FALLBACK,
        ROLE_DEPARTMENT_MAP,
    )
    from onemancompany.core.layout import compute_layout, get_next_desk_for_department

    emp_data = _store.load_employee(employee_id)
    if not emp_data:
        return {"status": "error", "error": f"Employee {employee_id} not found"}

    old_dept = emp_data.get(PF_DEPARTMENT, "General")
    old_role = emp_data.get(PF_ROLE, "")
    no_dept_change = old_dept == department
    no_role_change = not role or old_role == role

    if no_dept_change and no_role_change:
        return {
            "status": "no_change",
            "employee_id": employee_id,
            "department": department,
            "role": old_role,
            "message": f"{emp_data.get(PF_NAME, employee_id)} already has department={department}, role={old_role}",
        }

    updates: dict = {}

    if not no_dept_change:
        # Compute new desk position within the target department zone
        is_remote = emp_data.get(PF_REMOTE, False)
        if is_remote:
            desk_pos = [-1, -1]
        else:
            desk_pos = list(get_next_desk_for_department(company_state, department))

        # Update tool permissions for new department
        new_tool_perms = list(DEFAULT_TOOL_PERMISSIONS.get(
            department, DEFAULT_TOOL_PERMISSIONS_FALLBACK
        ))
        updates.update({"department": department, "desk_position": desk_pos, "tool_permissions": new_tool_perms})

    if role and not no_role_change:
        updates["role"] = role
        # Register custom role if needed
        from onemancompany.core.state import ROLE_TITLES
        if role not in ROLE_TITLES:
            ROLE_TITLES[role] = role
        if role not in ROLE_DEPARTMENT_MAP and department:
            ROLE_DEPARTMENT_MAP[role] = department

    await _store.save_employee(employee_id, updates)

    # Recompute office layout if department changed
    if not no_dept_change:
        compute_layout(company_state)

    activity_type = "department_changed" if not no_dept_change else "role_changed"
    _append_activity({
        "type": activity_type,
        "employee_id": employee_id,
        "name": emp_data.get(PF_NAME, employee_id),
        "from_department": old_dept,
        "to_department": department,
        "from_role": old_role,
        "to_role": role or old_role,
    })

    await event_bus.publish(CompanyEvent(
        type=EventType.STATE_SNAPSHOT, payload={}, agent="COO",
    ))

    final_role = role or old_role
    logger.info("Assigned {} → dept={}, role={} for {}",
                old_dept, department, final_role, employee_id)

    result = {
        "status": "ok",
        "employee_id": employee_id,
        "name": emp_data.get(PF_NAME, ""),
        "department": department,
        "role": final_role,
    }
    if not no_dept_change:
        result["desk_position"] = desk_pos
        result["previous_department"] = old_dept
    if role and not no_role_change:
        result["previous_role"] = old_role
    return result


def _register_coo_tools() -> None:
    from onemancompany.core.tool_registry import ToolMeta, tool_registry

    for t in [
        register_asset, remove_tool, list_tools,
        grant_tool_access, revoke_tool_access,
        list_assets, list_meeting_rooms, book_meeting_room,
        release_meeting_room, add_meeting_room,
        request_hiring, deposit_company_knowledge,
        assign_department,
    ]:
        tool_registry.register(t, ToolMeta(name=t.name, category="role", allowed_roles=["COO"]))


_register_coo_tools()


class COOAgent(BaseAgentRunner):
    role = "COO"
    employee_id = COO_ID

    def __init__(self) -> None:
        from onemancompany.core.tool_registry import tool_registry

        self._agent = create_react_agent(
            model=make_llm(self.employee_id),
            tools=tool_registry.get_proxied_tools_for(self.employee_id),
        )

    def _get_role_identity_section(self) -> str:
        from onemancompany.core.config import EMPLOYEES_DIR, ENCODING_UTF8
        guide_path = EMPLOYEES_DIR / self.employee_id / "role_guide.md"
        if guide_path.exists():
            return guide_path.read_text(encoding=ENCODING_UTF8)
        return ""

    def _customize_prompt(self, pb) -> None:
        pass  # All COO prompt content is in role_guide.md

    async def run(self, task: str) -> str:
        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": f"COO analyzing: {task}"})

        result = await self._agent.ainvoke(
            {"messages": [
                SystemMessage(content=self._build_full_prompt()),
                HumanMessage(content=task),
            ]}
        )

        self._extract_and_record_usage(result)
        final = extract_final_content(result)
        self._set_status(STATUS_IDLE)
        await self._publish("agent_done", {"role": "COO", "summary": final[:MAX_SUMMARY_LEN]})
        return final


# Singleton removed — agent instances are now created and registered
# in main.py lifespan via PersistentAgentLoop.


# Snapshot provider for coo_hiring removed — Task 13.
# pending_hiring_requests is transient; retained in-memory only.
