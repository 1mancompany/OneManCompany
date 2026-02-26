"""Common tools available to ALL employees — 每个员工默认拥有的工具。

The main tool here is `pull_meeting` (拉人对齐): any employee can pull
relevant colleagues into a meeting room for a focused discussion.
"""

from __future__ import annotations

import asyncio
import json
import re

from langchain_core.tools import tool

from onemancompany.agents.base import make_llm
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
    """读取项目目录下的文件内容。

    可以读取任何项目文件，包括员工档案、配置文件、规章制度等。
    路径可以是相对于项目根目录的相对路径，也可以是绝对路径。

    Args:
        file_path: 文件路径，如 "employees/hr/profile.yaml" 或 "company_rules/xxx.md"

    Returns:
        包含文件内容的 dict，或错误信息。
    """
    from onemancompany.core.file_editor import _resolve_path

    resolved = _resolve_path(file_path)
    if resolved is None:
        return {"status": "error", "message": f"路径不合法或超出项目范围: {file_path}"}
    if not resolved.exists():
        return {"status": "error", "message": f"文件不存在: {file_path}"}
    if not resolved.is_file():
        return {"status": "error", "message": f"不是文件: {file_path}"}
    try:
        content = resolved.read_text(encoding="utf-8")
        return {"status": "ok", "path": file_path, "content": content}
    except Exception as e:
        return {"status": "error", "message": f"读取失败: {e}"}


@tool
def list_directory(dir_path: str = "") -> dict:
    """列出项目目录下的文件和子目录。

    Args:
        dir_path: 目录路径，相对于项目根目录。空字符串表示项目根目录。

    Returns:
        包含文件列表的 dict。
    """
    from onemancompany.core.file_editor import _resolve_path

    resolved = _resolve_path(dir_path or ".")
    if resolved is None:
        return {"status": "error", "message": f"路径不合法: {dir_path}"}
    if not resolved.exists() or not resolved.is_dir():
        return {"status": "error", "message": f"目录不存在: {dir_path}"}
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
        return {"status": "error", "message": f"读取目录失败: {e}"}


@tool
async def propose_file_edit(
    file_path: str,
    new_content: str,
    reason: str,
    proposed_by: str = "",
) -> dict:
    """提出文件编辑请求（需要CEO审批后才会执行）。

    可以编辑项目目录下的任何文件，包括：
    - 员工档案 (employees/xxx/profile.yaml)
    - 工作准则 (employees/xxx/work_principles.md)
    - 技能文件 (employees/xxx/skills/xxx.md)
    - 规章制度 (company_rules/xxx.md)
    - 设备配置 (equipment_room/xxx.yaml)
    - 公司文化墙 (culture_wall.yaml)
    - 其他任何项目文件

    编辑请求提交后，CEO会在前端看到变更对比，审批通过后自动执行。
    执行前会自动备份原文件（按时间戳），方便回滚。

    Args:
        file_path: 文件路径，相对于项目根目录
        new_content: 修改后的完整文件内容
        reason: 编辑原因说明

    Returns:
        编辑请求状态（pending_approval 表示已提交等待审批）。
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
    """列出所有同事信息，用于确定该拉谁开会。

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
    """拉人对齐 — 发起一个聚焦会议，拉取相关同事讨论特定议题。

    会自动预约会议室，组织参会人员进行讨论，并输出会议结论。
    任何员工都可以使用此工具。

    Args:
        topic: 会议主题，例如"讨论新功能技术方案"
        participant_ids: 需要参会的同事ID列表
        agenda: 可选的会议议程
        initiator_id: 发起人ID（自动填充，可留空）

    Returns:
        会议结果，包含讨论摘要和行动项。
    """
    # Validate participants
    valid_participants = []
    for pid in participant_ids:
        emp = company_state.employees.get(pid)
        if emp:
            valid_participants.append(emp)

    if not valid_participants:
        return {"status": "error", "message": "没有找到有效的参会人员。请检查员工ID。"}

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
            "message": "目前没有空闲的会议室，请稍后再试或先处理其他工作。",
        }

    # Publish booking event
    await _publish("meeting_booked", {
        "room_id": room.id,
        "room_name": room.name,
        "participants": room.participants,
    })

    initiator_name = "发起人"
    if initiator_id:
        ini_emp = company_state.employees.get(initiator_id)
        if ini_emp:
            initiator_name = ini_emp.nickname or ini_emp.name

    await _chat(room.id, initiator_name, "employee",
                f"大家好，我发起了这个会议。主题：{topic}")

    if agenda:
        await _chat(room.id, initiator_name, "employee", f"议程：{agenda}")

    try:
        # Run focused discussion
        llm = make_llm(initiator_id or "hr")
        discussion_entries: list[dict] = []

        # Each participant speaks on the topic
        for emp in valid_participants:
            principles_ctx = ""
            if emp.work_principles:
                principles_ctx = f"\n你的工作准则:\n{emp.work_principles[:300]}\n"

            prompt = (
                f"你是 {emp.name}（{emp.nickname}，部门: {emp.department}，{emp.role}，Lv.{emp.level}）。\n"
                f"{principles_ctx}"
                f"你正在参加一个聚焦会议。\n"
                f"会议主题: {topic}\n"
            )
            if agenda:
                prompt += f"会议议程: {agenda}\n"
            if discussion_entries:
                recent = discussion_entries[-3:]  # last 3 comments for context
                context = "\n".join(f"  {d['name']}: {d['comment']}" for d in recent)
                prompt += f"\n之前的发言:\n{context}\n"
            prompt += (
                f"\n请根据你的专业领域和工作准则，就会议主题发表简要观点（2-3句话中文）。"
                f"重点关注你能贡献什么、有什么建议或担忧。"
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
            f"你是会议记录员，请总结以下聚焦会议的讨论结果。\n\n"
            f"会议主题: {topic}\n"
            f"参会人员: {', '.join(e.nickname or e.name for e in valid_participants)}\n\n"
            f"讨论内容:\n{all_comments}\n\n"
            f"请输出:\n"
            f"1. 会议结论（2-3句话）\n"
            f"2. 行动项（JSON数组格式）: "
            f'[{{"assignee": "负责人", "action": "具体行动"}}]\n'
            f"请用中文回答。"
        )
        summary_resp = await llm.ainvoke(summary_prompt)
        summary_text = summary_resp.content

        await _chat(room.id, "会议记录", "HR", f"[会议总结] {summary_text[:200]}")

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
            "summary": summary_text[:500],
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


# All common tools that every employee agent gets
COMMON_TOOLS = [
    read_file,
    list_directory,
    propose_file_edit,
    list_colleagues,
    pull_meeting,
]
