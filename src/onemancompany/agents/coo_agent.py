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
from onemancompany.core.config import COO_ID, MAX_SUMMARY_LEN, PROJECTS_DIR, ROOMS_DIR, SHARED_PROMPTS_DIR, SOP_DIR, STATUS_IDLE, STATUS_WORKING, TOOLS_DIR, WORKFLOWS_DIR, load_assets, migrate_legacy_tool, save_company_direction, save_workflow, slugify_tool_name
from onemancompany.core.events import CompanyEvent, event_bus
from onemancompany.core.state import MeetingRoom, OfficeTool, company_state
from onemancompany.core.store import append_activity_sync as _append_activity

# Pending hiring requests awaiting CEO approval.
# { request_id: { role, reason, skills, requested_by, requested_at } }
pending_hiring_requests: dict[str, dict] = {}

COO_SYSTEM_PROMPT = """You are the COO (Chief Operating Officer) of "One Man Company".
You manage operations, assets, and project execution.

## CORE PRINCIPLE — Delegate, Don't Execute
Your job is to PLAN, COORDINATE, REVIEW, and ACCEPT — NOT to write code or create content.
- ALWAYS dispatch_child() implementation work to employees.
- If no suitable employee exists, dispatch to HR to hire one first.
- Only do work yourself as an absolute LAST RESORT.
- For complex tasks: break into sub-tasks, dispatch each to the right employee.

## Delegation Decision Tree
1. Is this a people/HR task? → dispatch_child("00002", ...)
2. Is this implementation work (code, design, writing)? → dispatch_child(best_employee, ...)
3. Need project planning, market research, or documentation? → dispatch to PM
4. Can an existing employee handle it? → Check list_colleagues(), then dispatch.
5. No suitable employee? → dispatch_child("00002", "Hire a [role] for [task]")
6. Only administrative/coordination work left? → Handle it yourself.

## 项目执行流程 (复杂项目必须遵循，简单任务可跳过阶段2-3)

### 阶段1 — 分析项目
- 理解EA的需求，评估复杂度和所需技能
- 决定是否需要组建团队（简单单人任务可直接dispatch）

### 阶段2 — 组建团队
- list_colleagues() 查看可用人员及其技能和当前负载
- update_project_team(members=[{employee_id, role}]) 注册团队成员
- 可在后续阶段追加成员（验收/整改/受阻时）

### 阶段3 — 团队对齐
- pull_meeting(attendees=团队全员) 讨论:
  - 项目目标和范围
  - 验收标准
  - 分工计划和时间线
- 会议结论写入项目工作区

### 阶段4 — 分派执行
- 按计划 dispatch_child() 分配子任务
- 每个子任务必须有明确的验收标准（来自阶段3讨论结果）
- PM可以做：项目规划、市场调研、竞品分析、文档撰写、进度跟踪
- Engineer做：代码开发、技术实现、测试

## Responsibilities

### Task Execution via Delegation
When receiving CEO action plans:
- HR-sourced actions → dispatch_child("00002", ...) immediately.
- COO-sourced actions → find the best employee and dispatch.
- Report a brief summary of all dispatches.

### Asset Management
- New tools: register_asset(name, description, tool_type, source_project_dir, source_files, reference_url).
- List/manage: list_tools(), grant_tool_access(), revoke_tool_access().
- All project outputs that become company tools must go through register_asset().

### 工具注册标准（严格执行）

**工具的定义**：工具是原子性的、可复用的功能单元，用于加快效率或完成特殊功能。

**什么是工具**：
- 可执行脚本（自动化发布、构建、部署等）
- API交互模块（与外部服务通信）
- 沙箱/运行时环境
- 项目管理/查询工具

**什么不是工具（严禁注册）**：
- 参考代码/示例代码 — 这是文档，不是工具
- 游戏模板/代码脚手架 — 这是项目产物，留在项目目录里
- 文档模板 — 这是知识，用 deposit_company_knowledge 存
- 同一功能的多个副本 — 一个功能只应有一个工具
- 只有描述没有实际可执行内容的空壳

**注册前必须自查**：
1. 这个东西能直接运行/调用吗？不能 → 不是工具
2. 公司已有类似功能的工具吗？有 → 不要重复注册
3. 这只是某个项目的源码吗？是 → 留在项目目录，不要注册为工具

**类型要求**：
- tool_type="script"：必须包含真实可执行的 .py/.sh 文件，系统会验证语法
- tool_type="reference"：外部服务引用，必须有 reference_url

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
3. Do NOT dispatch_child to HR for hiring directly — always go through request_hiring so CEO can approve.

### Child Task Review (子任务验收)
When all your dispatched children complete, the system wakes you with a review prompt:
1. Read the actual deliverables — do NOT just trust the result summaries.
2. For code: check files exist, verify structure and completeness.
3. For documents: read actual content, check against acceptance criteria.
4. Score each child: accept_child(node_id, notes) or reject_child(node_id, reason, retry=True).
5. All accepted → your task auto-completes and reports up.

## 项目规划（Plan Mode — 复杂项目必用）

收到复杂任务后，你必须先进入"规划模式"：只做分析和设计，不做执行。
规划完成后通过 write() 保存计划文档到 project workspace，再开始 dispatch_child()。

### Step 1: 现状调研（Read-Only Analysis）
在做任何决策前，先充分了解现状。调研分两个维度：

**1a. 内部盘点**
- list_colleagues() 盘点团队能力、各员工技能栈和当前负载
- 用 read / ls 检查公司已有资产（工具、文档、代码库）
- 查看相关项目历史（复用已有成果，避免重复造轮子）
- 识别缺口：缺人？缺工具？缺技术栈？缺依赖资源？

**1b. 市场与用户调研**
- **SOTA 分析**：当前这个领域最先进的技术/方案是什么？行业最佳实践是什么？
- **竞品分析**：市面上最好的竞品有哪些？它们的核心优势和不足分别是什么？我们的差异化机会在哪里？
- **用户痛点**：目标用户最大的痛苦是什么？现有方案解决不了什么问题？哪些需求被严重低估？
- **用户爽点**：什么功能/体验能让用户眼前一亮？什么能产生口碑传播和自发推荐？
- 将调研结论写入 plan.md 的「背景」章节，作为后续所有设计决策的依据

### Step 2: 设计实施方案（Architectural Design）
基于调研结果，产出详细的结构化方案。方案必须回答：

**2a. 目标与范围**
- 项目要解决什么问题？最终交付物是什么？
- MVP 范围：哪些是 must-have，哪些是 nice-to-have？
- 不做什么：明确排除项，防止范围蔓延

**2b. 技术/执行方案**
- 技术选型及理由（为什么选A不选B）
- 关键架构决策和权衡取舍
- 与现有系统/代码的集成方式
- 已知风险和应对策略

**2c. 任务拆解与依赖图**
每个子任务必须具体到可直接执行：
- 明确 assignee（哪个员工）+ 所需技能
- 明确输入依赖（需要哪些前置任务的产出）
- 明确交付物（文件名、格式、存放路径）
- 预估工作量（简单/中等/复杂）

**2d. 分阶段编排（Phase Plan）**
- Phase 1：基础建设 — 独立的、无依赖的工作先行
- Phase 2：核心实现 — 依赖 P1 产出的主体工作
- Phase 3：集成测试 — 组装、联调、质量验证
- Phase 4（可选）：发布准备 — 部署、文档、推广物料
- 每个 phase 标注预期持续时间和关键里程碑

**2e. 验收标准（Acceptance Criteria）**
- 每条标准必须可验证（能通过具体操作确认 pass/fail）
- 区分功能性标准（"能做到X"）和质量性标准（"性能达到Y"）
- 包含最终用户视角的端到端验证

### Step 3: 保存计划文档
通过 write() 将完整方案持久化到 project workspace：
- plan.md 是团队所有人的单一事实来源（Single Source of Truth）
- 计划文档包含：背景、目标、技术方案、任务分配表、阶段甘特图、验收标准
- 验收标准同步写入项目 acceptance_criteria

### Step 4: 执行调度
- 计划保存后，才开始 dispatch_child() 分发子任务
- 每个 dispatch 的 task_description 引用 plan.md 中对应的章节
- 员工收到任务后可以 read("plan.md") 了解完整上下文

### 简单任务豁免
判断标准：单人 + 单一交付物 + 无需技术选型 → 跳过 Plan Mode，直接 dispatch_child()。
复杂度判断：涉及 2+ 人 或 2+ 交付物 或 需要技术选型 → 必须走 Plan Mode。

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
    _reject_keywords = ["参考代码", "参考", "reference code", "模板", "template",
                        "脚手架", "scaffold", "示例", "example", "sample"]
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
    """Request CEO approval to hire a new employee.

    Use this when you identify the team lacks a capability needed for current
    or upcoming work. The request goes to CEO for approval — if approved, HR
    automatically starts recruiting.

    Args:
        role: The role to hire (e.g. "Game Developer", "QA Engineer").
            This role will override the talent's profile role on hire.
        reason: Why this hire is needed — what gap or demand triggers it.
        department: Target department (e.g. "Engineering", "Design").
            If empty, auto-determined from role mapping.
        desired_skills: Optional list of desired skills/technologies.

    Returns:
        Confirmation that the request was submitted for CEO review.
    """
    from datetime import datetime
    from onemancompany.core.agent_loop import _current_vessel, _current_task_id

    # Capture project context from COO's current task
    project_id = ""
    project_dir = ""
    caller_loop = _current_vessel.get()
    caller_task_id = _current_task_id.get()
    if caller_loop and caller_task_id:
        caller_task = caller_loop.board.get_task(caller_task_id)
        if caller_task:
            project_id = caller_task.project_id or caller_task.original_project_id
            project_dir = caller_task.project_dir or caller_task.original_project_dir

    request_id = str(uuid.uuid4())[:8]
    req = {
        "role": role,
        "department": department,
        "reason": reason,
        "desired_skills": desired_skills or [],
        "requested_by": COO_ID,
        "requested_at": datetime.now().isoformat(),
        "project_id": project_id,
        "project_dir": project_dir,
    }
    pending_hiring_requests[request_id] = req

    # Note: hiring flow tracking is now handled by the task tree.
    # The hiring request event is sufficient for frontend tracking.

    # Publish event for frontend — CEO will see and approve/reject.
    # LangChain tools run in a thread pool, so use run_coroutine_threadsafe
    # with the main event loop stored on EmployeeManager.
    from onemancompany.core.vessel import employee_manager
    coro = event_bus.publish(CompanyEvent(
        type="hiring_request_ready",
        payload={"request_id": request_id, **req},
        agent="COO",
    ))
    loop = getattr(employee_manager, "_event_loop", None)
    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, loop)
    else:
        try:
            asyncio.get_event_loop().run_until_complete(coro)
        except RuntimeError:
            logger.warning("No event loop available — hiring_request_ready event will be missed")

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
        _append_activity({
            "type": "knowledge_deposited",
            "category": "workflow",
            "name": name,
        })

    elif category == "culture":
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
        path = "company/company_culture.yaml"
        _append_activity({
            "type": "knowledge_deposited",
            "category": "culture",
            "name": name,
        })

    elif category == "sop":
        SOP_DIR.mkdir(parents=True, exist_ok=True)
        sop_path = SOP_DIR / f"{name}.md"
        sop_path.write_text(content, encoding="utf-8")
        path = str(sop_path)
        _append_activity({
            "type": "knowledge_deposited",
            "category": "sop",
            "name": name,
        })

    elif category == "guidance":
        SHARED_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
        guidance_path = SHARED_PROMPTS_DIR / f"{name}.md"
        guidance_path.write_text(content, encoding="utf-8")
        path = str(guidance_path)
        _append_activity({
            "type": "knowledge_deposited",
            "category": "guidance",
            "name": name,
        })

    elif category == "direction":
        save_company_direction(content)
        path = "company/company_direction.yaml"
        _append_activity({
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


@tool
async def assign_department(employee_id: str, department: str) -> dict:
    """Assign or change an employee's department.

    Updates the employee's department, recalculates their desk position
    based on the department zone, and adjusts tool permissions.

    Args:
        employee_id: The employee number (e.g. "00008").
        department: Target department name (e.g. "Engineering", "Design",
            "Analytics", "Marketing").

    Returns:
        dict with status, employee_id, department, desk_position.
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

    old_dept = emp_data.get("department", "General")
    if old_dept == department:
        return {
            "status": "no_change",
            "employee_id": employee_id,
            "department": department,
            "message": f"{emp_data.get('name', employee_id)} is already in {department}",
        }

    # Compute new desk position within the target department zone
    is_remote = emp_data.get("remote", False)
    if is_remote:
        desk_pos = [-1, -1]
    else:
        desk_pos = list(get_next_desk_for_department(company_state, department))

    # Update tool permissions for new department
    new_tool_perms = list(DEFAULT_TOOL_PERMISSIONS.get(
        department, DEFAULT_TOOL_PERMISSIONS_FALLBACK
    ))

    await _store.save_employee(employee_id, {
        "department": department,
        "desk_position": desk_pos,
        "tool_permissions": new_tool_perms,
    })

    # Recompute office layout
    compute_layout(company_state)

    _append_activity({
        "type": "department_changed",
        "employee_id": employee_id,
        "name": emp_data.get("name", employee_id),
        "from_department": old_dept,
        "to_department": department,
    })

    await event_bus.publish(CompanyEvent(
        type="state_snapshot", payload={}, agent="COO",
    ))

    logger.info("Department assigned: {} → {} for {}",
                old_dept, department, employee_id)

    return {
        "status": "ok",
        "employee_id": employee_id,
        "name": emp_data.get("name", ""),
        "department": department,
        "desk_position": desk_pos,
        "previous_department": old_dept,
    }


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

    def _customize_prompt(self, pb) -> None:
        pb.add("role", COO_SYSTEM_PROMPT, priority=10)

    async def run(self, task: str) -> str:
        self._set_status(STATUS_WORKING)
        await self._publish("agent_thinking", {"message": f"COO analyzing: {task[:80]}"})

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
