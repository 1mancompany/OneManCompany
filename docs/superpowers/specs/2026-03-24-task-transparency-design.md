# Task Transparency & UI Improvements — Design Spec

## Overview

Five improvements to task execution visibility and project organization:

1. **Node-level execution log** — full agent work process persisted per task node
2. **Project directory naming** — `{short_uuid}_{slug}_{timestamp}` format
3. **Work Rules placeholder bug** — hide "← Select a workflow to view" after click
4. **Projects sidebar sorting** — newest first
5. **Meeting room history archival** — meeting ends → chat archived to meeting minutes, room cleared

---

## 1. Node-level Execution Log

### Problem

- Execution logs only in employee-level `execution.log` (all tasks mixed)
- In-memory buffer lost after task completes
- Task tree detail panel shows no execution logs
- Tool call args/results truncated to 300 chars

### Design

**Storage**: `{project_dir}/nodes/{node_id}/execution.log` (JSONL)

Each line is a JSON object:
```json
{
  "ts": "2026-03-24T17:03:40.298Z",
  "type": "tool_call",
  "content": "dispatch_child({\"employee_id\": \"00006\", ...})",
  "meta": {}
}
```

Log types: `start`, `llm_input`, `llm_output`, `tool_call`, `tool_result`, `result`, `error`, `retry`, `holding`, `cancelled`

**Full content — no truncation**:
- `llm_input`: full system+user message
- `llm_output`: full assistant response
- `tool_call`: tool name + full JSON args
- `tool_result`: full tool return value

**Backend changes**:
- `vessel.py _log_node()`: additionally write to `{project_dir}/nodes/{node_id}/execution.log`
- `base.py run_streamed on_log()`: pass full content (remove 300-char truncation on tool_result)
- New API: `GET /api/task-tree/{node_id}/logs` — reads JSONL from disk, returns JSON array
- Employee-level `execution.log` kept as-is (summary, backward compat)

**Frontend changes**:
- `task-tree.js _renderNodeDetail()`: add "Execution Log" collapsible section at bottom
- Click to expand → fetch `GET /api/task-tree/{node_id}/logs`
- Color coding: tool_call (blue), tool_result (gray), llm_output (green), error (red)
- Long content: collapsed by default, click to expand individual entries

---

## 2. Project Directory Naming

### Problem

Current format: `{slugified_name}-{MMDDHHMMSS}` (e.g. `项目信息-0324201740`)
- Chinese chars get slugified poorly
- No stable ID component
- Hard to identify projects at a glance

### Design

New format: `{short_uuid}_{slug}_{MMDDHHMMSS}`
- Example: `a3f2b1_prdbench-project-19_0324201740`
- `short_uuid`: `uuid.uuid4().hex[:6]` (6 chars)
- `slug`: `_slugify(name)` (existing function)
- `MMDDHHMMSS`: compact timestamp

**Changes**:
- `project_archive.py create_named_project()`: change slug format
- `project_id` in `project.yaml` matches directory name
- Existing projects not migrated — old format still loads fine

---

## 3. Work Rules Placeholder Bug

### Problem

Clicking an SOP shows content in right panel, but "← Select a workflow to view" placeholder still visible, taking up space.

### Current Code

- `index.html:353`: `<div id="workflow-placeholder">← Select a workflow to view</div>`
- `app.js:4174`: `classList.add('hidden')` — already hides it
- But the workflow content container and placeholder overlap or layout is wrong

### Fix

- Check if `workflow-placeholder` is actually being hidden correctly on click
- If it is hidden but still takes space, check CSS (`display: none` vs `visibility: hidden`)
- The `hidden` class should use `display: none` — verify in `style.css`

---

## 4. Projects Sidebar Sorting

### Problem

Projects listed in arbitrary order (backend iteration order). New projects not at top.

### Fix

- Frontend `app.js`: sort projects by `created_at` descending before rendering
- Apply to all 3 rendering locations: modal list (3805), panel list (5980), dropdown (6583)
- One-liner: `projects.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))`

---

## 5. Meeting Room History Archival

### Problem

- Meeting room chat history persists across meetings — next meeting sees old messages
- Old messages may pollute agent context (LLM reads stale chat)
- No way to review past meeting minutes after room is reused

### Design

**On meeting end** (`pull_meeting` returns):
- Archive current chat to `{company_dir}/meeting_minutes/{room_id}_{YYYYMMDD_HHMMSS}.yaml`
  - Contains: room_id, topic, participants, start_time, end_time, messages[], conclusion
- Clear the room chat file (`{rooms_dir}/{room_id}_chat.yaml` → empty list)
- Room is clean for next meeting

**Meeting minutes storage**:
```yaml
room_id: all_hands_room
topic: "Kickoff for PRDBench Project 18"
project_id: "a3f2b1_prdbench-project-18_0324201640"
participants: ["00003", "00006", "00008"]
start_time: "2026-03-24T20:16:46"
end_time: "2026-03-24T20:22:19"
messages:
  - speaker: "铁面侠"
    role: "COO"
    message: "Hello everyone..."
    time: "20:16:46"
  # ...
conclusion: "Team agreed on architecture-first approach..."
```

**Frontend — Meeting Minutes button on room wall**:
- Office canvas: clickable "Meeting Minutes" icon/button on the meeting room wall
- Click opens a panel listing past meetings for that room (sorted newest first)
- Click a meeting → shows full archived chat + conclusion

**Employee tool — `view_meeting_minutes`**:
- New tool in `common_tools.py`: `view_meeting_minutes(room_id?, project_id?, employee_id?, limit=5)`
- Filters: by room, by project (if meeting was during a project task), by participant employee
- Returns recent meeting minutes matching any combination of filters
- Employees can reference past meetings for context

**Backend changes**:
- `common_tools.py pull_meeting()`: after meeting ends, archive chat + clear room
- New: `common_tools.py view_meeting_minutes()` tool
- New: `store.py archive_meeting()` / `load_meeting_minutes()`
- New API: `GET /api/rooms/{room_id}/minutes` — list archived meetings
- New API: `GET /api/rooms/{room_id}/minutes/{minute_id}` — get one meeting's full content

**Frontend changes**:
- `office.js`: meeting minutes icon on room wall (clickable)
- `app.js`: meeting minutes panel (list + detail view)

---

## Implementation Order

1. **Project naming** (smallest, isolated change)
2. **Projects sorting** (frontend one-liner)
3. **Work Rules bug** (frontend fix)
4. **Meeting room archival** (backend + frontend + tool)
5. **Node execution log** (largest: backend storage + API + frontend rendering)

## Files Changed

| File | Changes |
|------|---------|
| `src/onemancompany/core/project_archive.py` | `create_named_project` naming format |
| `src/onemancompany/core/vessel.py` | `_log_node` write to node-level file |
| `src/onemancompany/agents/base.py` | `on_log` remove truncation |
| `src/onemancompany/api/routes.py` | New `GET /api/task-tree/{node_id}/logs` endpoint |
| `frontend/task-tree.js` | `_renderNodeDetail` add execution log section |
| `frontend/app.js` | Projects sort + work rules placeholder fix |
| `frontend/style.css` | Execution log entry styling |
| `src/onemancompany/agents/common_tools.py` | `pull_meeting` archive on end + `view_meeting_minutes` tool |
| `src/onemancompany/core/store.py` | `archive_meeting()` / `load_meeting_minutes()` |
| `frontend/office.js` | Meeting minutes icon on room wall |
