"""COO Agent — manages the 设备间 (equipment room): tools and meeting rooms.

Equipment (tools + meeting rooms) are stored as YAML in equipment_room/ at project root.
Meeting rooms must be booked via the COO before agents can communicate with each other.
"""

from __future__ import annotations

import uuid

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from onemancompany.agents.base import BaseAgentRunner, make_llm
from onemancompany.agents.common_tools import COMMON_TOOLS
from onemancompany.core.config import EQUIPMENT_DIR, load_equipment
from onemancompany.core.state import MeetingRoom, OfficeTool, company_state

COO_SYSTEM_PROMPT = """You are the COO (Chief Operating Officer) of a startup called "One Man Company".

You manage the company's 设备间 (equipment room), which includes:
1. **Tools & Equipment** — servers, devices, and capabilities the company uses.
2. **Meeting Rooms (会议室)** — employees MUST book a meeting room through you before communicating with each other (including founding employees and the CEO).

## Your Responsibilities:

### Equipment Management
- When the CEO or employees request new tools, call add_equipment() to register them.
- Use list_equipment() to see current tools and meeting rooms.

### Meeting Room Booking (会议室管理)
- When an employee needs to communicate with another employee, they must first request a meeting room.
- Call book_meeting_room() to reserve a room for the requester.
- Call release_meeting_room() when a meeting is done.
- If NO free meeting rooms are available, tell the employee to process other tasks or refine their work first. Do NOT create a new meeting room — the CEO must authorize that.
- Use list_meeting_rooms() to check availability.

### Adding New Meeting Rooms
- Only add new meeting rooms when the CEO explicitly requests it via add_meeting_room().

## Cross-team Collaboration (拉人对齐)
You can call list_colleagues() to see all employees, then call pull_meeting() to organize
a focused meeting with relevant colleagues when you need alignment on operational decisions,
resource allocation, or process improvements.

## File Editing (文件编辑)
You can read and edit any file in the project directory:
- Use read_file() to read file contents, list_directory() to browse directories.
- Use propose_file_edit() to propose changes — the CEO must approve before they take effect.
  Always set proposed_by="COO" when calling propose_file_edit.
- Files are automatically backed up before editing, so changes can be rolled back.

Be concise and action-oriented. Respond in Chinese when possible.
"""


def _load_equipment_from_disk() -> None:
    """Load existing equipment from equipment_room/ directory into company_state."""
    tools_data, rooms_data = load_equipment()

    count = 0
    for tool_id, data in tools_data.items():
        if tool_id not in company_state.tools:
            company_state.tools[tool_id] = OfficeTool(
                id=tool_id,
                name=data.get("name", tool_id),
                description=data.get("description", ""),
                added_by=data.get("added_by", "COO"),
                desk_position=tuple(data.get("desk_position", [5 + (count % 3) * 5, 8 + (count // 3) * 3])),
                sprite=data.get("sprite", "desk_equipment"),
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


# Load persisted equipment on import
_load_equipment_from_disk()


# ===== LangChain tools for the COO agent =====


@tool
def add_equipment(name: str, description: str) -> dict:
    """Add a new tool or device to the company's equipment room (设备间).

    This will appear as equipment in the pixel art office visualization
    and be saved to the equipment_room/ directory.

    Args:
        name: Short name for the equipment (e.g. 'Code Review Bot', 'CI/CD Pipeline')
        description: What this equipment does for the company.

    Returns:
        Confirmation with equipment id and position.
    """
    eq_id = str(uuid.uuid4())[:8]
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
    )
    company_state.tools[eq_id] = office_tool
    company_state.activity_log.append(
        {"type": "tool_added", "name": name, "description": description}
    )

    # Persist to equipment_room/{id}.yaml
    EQUIPMENT_DIR.mkdir(parents=True, exist_ok=True)
    eq_path = EQUIPMENT_DIR / f"{eq_id}.yaml"
    with open(eq_path, "w") as f:
        yaml.dump(
            {
                "name": name,
                "description": description,
                "added_by": "COO",
                "desk_position": list(desk_pos),
                "sprite": "desk_equipment",
            },
            f,
            allow_unicode=True,
            default_flow_style=False,
        )

    return {"status": "success", "id": eq_id, "name": name, "position": list(desk_pos)}


@tool
def list_equipment() -> list[dict]:
    """List all tools and equipment currently in the equipment room (设备间)."""
    items = [
        {"id": t.id, "name": t.name, "description": t.description, "type": "tool"}
        for t in company_state.tools.values()
    ]
    items += [
        {"id": m.id, "name": m.name, "description": m.description, "type": "meeting_room",
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
    # Find a free meeting room
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
                "message": f"会议室 {room.name} 已预约成功。",
            }

    # No free rooms
    company_state.activity_log.append({
        "type": "meeting_denied",
        "requested_by": employee_id,
        "reason": "no_free_rooms",
    })
    return {
        "status": "denied",
        "message": "目前没有空闲的会议室。请先处理其他可执行的任务或完善当前工作，稍后再试。",
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
        return {"status": "error", "message": f"会议室 '{room_id}' 不存在。"}
    if not room.is_booked:
        return {"status": "error", "message": f"会议室 {room.name} 当前未被预约。"}

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
        "message": f"会议室 {room.name} 已释放。",
    }


@tool
def add_meeting_room(name: str, capacity: int = 6, description: str = "") -> dict:
    """Add a new meeting room to the equipment room (CEO authorization required).

    Args:
        name: Name for the meeting room (e.g. '会议室B', '大会议厅').
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
        description=description or f"可容纳{capacity}人的会议室。",
        capacity=capacity,
        position=pos,
        sprite="meeting_room",
    )
    company_state.meeting_rooms[room_id] = room

    # Persist to equipment_room/
    EQUIPMENT_DIR.mkdir(parents=True, exist_ok=True)
    room_path = EQUIPMENT_DIR / f"{room_id}.yaml"
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
        "description": f"新会议室（容量{capacity}人）",
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
    employee_id = "coo"

    def __init__(self) -> None:
        self._agent = create_react_agent(
            model=make_llm(self.employee_id),
            tools=[
                add_equipment,
                list_equipment,
                list_meeting_rooms,
                book_meeting_room,
                release_meeting_room,
                add_meeting_room,
            ] + COMMON_TOOLS,
        )

    async def run(self, task: str) -> str:
        self._set_status("working")
        await self._publish("agent_thinking", {"message": f"COO 分析中: {task[:80]}"})

        prompt = (
            COO_SYSTEM_PROMPT
            + self._get_skills_prompt_section()
            + self._get_culture_wall_prompt_section()
            + self._get_work_principles_prompt_section()
            + self._get_guidance_prompt_section()
        )

        result = await self._agent.ainvoke(
            {"messages": [
                SystemMessage(content=prompt),
                HumanMessage(content=task),
            ]}
        )

        final = result["messages"][-1].content
        self._set_status("idle")
        await self._publish("agent_done", {"role": "COO", "summary": final[:300]})
        return final


coo_agent = COOAgent()
