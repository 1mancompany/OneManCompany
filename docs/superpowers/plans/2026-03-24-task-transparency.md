# Task Transparency & UI Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve task execution visibility with node-level logs, fix project naming, sort projects, fix Work Rules placeholder, and archive meeting room history.

**Architecture:** Node execution logs stored as JSONL per node_id under project dir. Meeting minutes archived on meeting end, room cleared. Frontend loads logs on demand via new API endpoints.

**Tech Stack:** Python (FastAPI, YAML, JSONL), Vanilla JS (Canvas for office, DOM for panels)

---

### Task 1: Project Directory Naming

**Files:**
- Modify: `src/onemancompany/core/project_archive.py:315-344`
- Test: `tests/unit/core/test_project_archive.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/core/test_project_archive.py
def test_create_named_project_format(tmp_path, monkeypatch):
    import onemancompany.core.project_archive as pa
    monkeypatch.setattr(pa, "PROJECTS_DIR", tmp_path)
    slug = pa.create_named_project("PRDBench Project 19")
    parts = slug.split("_")
    assert len(parts) >= 3  # uuid_slug_timestamp
    assert len(parts[0]) == 6  # short uuid hex
    assert parts[-1].isdigit() and len(parts[-1]) == 10  # MMDDHHMMSS
    assert "prdbench" in slug.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/core/test_project_archive.py::test_create_named_project_format -v`

- [ ] **Step 3: Implement**

In `project_archive.py`, change `create_named_project()`:

```python
def create_named_project(name: str) -> str:
    base_slug = _slugify(name)
    ts = datetime.now().strftime("%m%d%H%M%S")
    short_id = uuid.uuid4().hex[:6]
    slug = f"{short_id}_{base_slug}_{ts}"
    counter = 1
    while (PROJECTS_DIR / slug).exists():
        slug = f"{short_id}_{base_slug}_{ts}_{counter}"
        counter += 1
    # ... rest unchanged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/core/test_project_archive.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/project_archive.py tests/unit/core/test_project_archive.py
git commit -m "feat: project dir naming format {uuid}_{slug}_{timestamp}"
```

---

### Task 2: Projects Sidebar Sorting (newest first)

**Files:**
- Modify: `frontend/app.js:3805,5980,6583`

- [ ] **Step 1: Add sort before each project rendering loop**

At line ~3804 (before `for (const p of projects)`):
```javascript
projects.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
```

Same at line ~5979 and ~6582.

- [ ] **Step 2: Verify in browser** — newest project appears at top of sidebar

- [ ] **Step 3: Commit**

```bash
git add frontend/app.js
git commit -m "fix: projects sidebar sorted by created_at descending"
```

---

### Task 3: Work Rules Placeholder Bug

**Files:**
- Modify: `frontend/app.js:4174` and `frontend/index.html:353`

- [ ] **Step 1: Investigate**

Check `frontend/style.css` for `.hidden` class definition. Verify it uses `display: none`. Check if `workflow-placeholder` and `workflow-content` share the same container and whether the placeholder is a sibling or overlay.

- [ ] **Step 2: Fix**

Ensure clicking an SOP hides the placeholder AND shows the content container. The issue may be that `workflow-content` div is not shown when placeholder is hidden. Fix both:

```javascript
document.getElementById('workflow-placeholder').classList.add('hidden');
document.getElementById('workflow-content').classList.remove('hidden');
```

- [ ] **Step 3: Verify in browser** — click SOP, placeholder gone, content fills space

- [ ] **Step 4: Commit**

```bash
git add frontend/app.js frontend/index.html frontend/style.css
git commit -m "fix: Work Rules placeholder hidden after clicking SOP"
```

---

### Task 4: Meeting Room History Archival

**Files:**
- Create: `src/onemancompany/core/meeting_minutes.py`
- Modify: `src/onemancompany/agents/common_tools.py:936-951`
- Modify: `src/onemancompany/core/store.py`
- Modify: `src/onemancompany/api/routes.py`
- Modify: `frontend/office.js`
- Modify: `frontend/app.js`
- Test: `tests/unit/core/test_meeting_minutes.py`

#### Step 4a: Meeting minutes storage module

- [ ] **Step 1: Write failing test**

```python
# tests/unit/core/test_meeting_minutes.py
import pytest
from pathlib import Path

def test_archive_meeting(tmp_path, monkeypatch):
    import onemancompany.core.meeting_minutes as mm
    monkeypatch.setattr(mm, "MINUTES_DIR", tmp_path)

    minute_id = mm.archive_meeting(
        room_id="all_hands_room",
        topic="Kickoff",
        project_id="abc123_test_0324120000",
        participants=["00003", "00006"],
        messages=[{"speaker": "COO", "message": "Hello", "time": "12:00:00"}],
        conclusion="Agreed on plan",
    )
    assert minute_id
    doc = mm.load_minute(minute_id)
    assert doc["room_id"] == "all_hands_room"
    assert doc["project_id"] == "abc123_test_0324120000"
    assert len(doc["messages"]) == 1

def test_query_by_project(tmp_path, monkeypatch):
    import onemancompany.core.meeting_minutes as mm
    monkeypatch.setattr(mm, "MINUTES_DIR", tmp_path)

    mm.archive_meeting(room_id="room1", topic="A", project_id="proj1",
                       participants=["00003"], messages=[], conclusion="")
    mm.archive_meeting(room_id="room2", topic="B", project_id="proj2",
                       participants=["00003"], messages=[], conclusion="")

    results = mm.query_minutes(project_id="proj1")
    assert len(results) == 1
    assert results[0]["project_id"] == "proj1"

def test_query_by_employee(tmp_path, monkeypatch):
    import onemancompany.core.meeting_minutes as mm
    monkeypatch.setattr(mm, "MINUTES_DIR", tmp_path)

    mm.archive_meeting(room_id="room1", topic="A", project_id="",
                       participants=["00003", "00006"], messages=[], conclusion="")
    mm.archive_meeting(room_id="room2", topic="B", project_id="",
                       participants=["00008"], messages=[], conclusion="")

    results = mm.query_minutes(employee_id="00006")
    assert len(results) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement `meeting_minutes.py`**

```python
# src/onemancompany/core/meeting_minutes.py
"""Meeting minutes storage — archive and query past meetings."""
from __future__ import annotations
import yaml
from datetime import datetime
from pathlib import Path
from loguru import logger
from onemancompany.core.config import COMPANY_DIR, ENCODING_UTF8

MINUTES_DIR = COMPANY_DIR / "meeting_minutes"

def archive_meeting(
    room_id: str, topic: str, project_id: str,
    participants: list[str], messages: list[dict],
    conclusion: str,
) -> str:
    """Archive a completed meeting. Returns minute_id."""
    MINUTES_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    minute_id = f"{room_id}_{ts}"
    doc = {
        "minute_id": minute_id,
        "room_id": room_id,
        "topic": topic,
        "project_id": project_id,
        "participants": participants,
        "start_time": messages[0]["time"] if messages else "",
        "end_time": messages[-1]["time"] if messages else "",
        "archived_at": datetime.now().isoformat(),
        "messages": messages,
        "conclusion": conclusion,
    }
    path = MINUTES_DIR / f"{minute_id}.yaml"
    path.write_text(yaml.dump(doc, allow_unicode=True, default_flow_style=False), encoding=ENCODING_UTF8)
    return minute_id

def load_minute(minute_id: str) -> dict:
    path = MINUTES_DIR / f"{minute_id}.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding=ENCODING_UTF8)) or {}

def query_minutes(
    room_id: str = "", project_id: str = "",
    employee_id: str = "", limit: int = 10,
) -> list[dict]:
    """Query archived minutes with optional filters."""
    if not MINUTES_DIR.exists():
        return []
    results = []
    for f in sorted(MINUTES_DIR.iterdir(), reverse=True):
        if not f.suffix == ".yaml":
            continue
        doc = yaml.safe_load(f.read_text(encoding=ENCODING_UTF8)) or {}
        if room_id and doc.get("room_id") != room_id:
            continue
        if project_id and doc.get("project_id") != project_id:
            continue
        if employee_id and employee_id not in doc.get("participants", []):
            continue
        # Return summary (no messages) for list view
        summary = {k: v for k, v in doc.items() if k != "messages"}
        summary["message_count"] = len(doc.get("messages", []))
        results.append(summary)
        if len(results) >= limit:
            break
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/meeting_minutes.py tests/unit/core/test_meeting_minutes.py
git commit -m "feat: meeting minutes storage module"
```

#### Step 4b: Archive on meeting end + clear room

- [ ] **Step 6: Modify `pull_meeting()` finally block** (`common_tools.py:936-951`)

After room release, before the `return`, archive and clear:

```python
# In finally block, after room release:
# Archive meeting minutes
try:
    from onemancompany.core.meeting_minutes import archive_meeting
    from onemancompany.core.store import load_room_chat, clear_room_chat
    chat_messages = load_room_chat(room.id)
    if chat_messages:
        # Get project_id from current task context
        _proj_id = ""
        try:
            from onemancompany.core.vessel import _current_task_id
            # ... resolve project_id from task tree
        except Exception:
            pass
        archive_meeting(
            room_id=room.id, topic=topic,
            project_id=_proj_id,
            participants=[pid for pid, _ in valid_participants],
            messages=chat_messages,
            conclusion=meeting_conclusion if 'meeting_conclusion' in dir() else "",
        )
        await clear_room_chat(room.id)
except Exception as e:
    logger.warning("Failed to archive meeting minutes: {}", e)
```

- [ ] **Step 7: Add `clear_room_chat()` to `store.py`**

```python
async def clear_room_chat(room_id: str) -> None:
    path = _rooms_dir() / f"{room_id}_chat.yaml"
    async with _get_lock(str(path)):
        _write_yaml(path, [])
    mark_dirty(DirtyCategory.ROOMS)
```

- [ ] **Step 8: Commit**

```bash
git add src/onemancompany/agents/common_tools.py src/onemancompany/core/store.py
git commit -m "feat: archive meeting on end, clear room chat"
```

#### Step 4c: `view_meeting_minutes` employee tool

- [ ] **Step 9: Add tool to `common_tools.py`**

```python
@tool
def view_meeting_minutes(
    room_id: str = "", project_id: str = "",
    employee_id: str = "", limit: int = 5,
) -> dict:
    """View archived meeting minutes. Filter by room, project, or participant."""
    from onemancompany.core.meeting_minutes import query_minutes
    results = query_minutes(room_id=room_id, project_id=project_id,
                           employee_id=employee_id, limit=limit)
    return {"status": "ok", "minutes": results, "count": len(results)}
```

Register in tool registry as BASE_TOOL (all employees can use).

- [ ] **Step 10: Commit**

```bash
git add src/onemancompany/agents/common_tools.py
git commit -m "feat: view_meeting_minutes tool for all employees"
```

#### Step 4d: API endpoints + frontend

- [ ] **Step 11: Add API endpoints in `routes.py`**

```python
@router.get("/api/rooms/{room_id}/minutes")
async def list_room_minutes(room_id: str, limit: int = 20):
    from onemancompany.core.meeting_minutes import query_minutes
    return query_minutes(room_id=room_id, limit=limit)

@router.get("/api/meeting-minutes/{minute_id}")
async def get_minute_detail(minute_id: str):
    from onemancompany.core.meeting_minutes import load_minute
    doc = load_minute(minute_id)
    if not doc:
        raise HTTPException(404, "Meeting minutes not found")
    return doc
```

- [ ] **Step 12: Frontend — meeting minutes button on room wall** (`office.js`)

In `drawMeetingRoom()`, after room label rendering (~line 1110), add a small "Minutes" icon:

```javascript
// Meeting minutes icon (book icon) — only when room is not in use
if (!roomData.is_booked) {
    const iconX = px + rw - 14, iconY = py - 2;
    this._rect(iconX, iconY, 8, 6, '#4466aa');
    this._rect(iconX + 1, iconY + 1, 2, 4, '#fff');
    // Store click region for hit detection
    this._clickRegions.push({
        x: iconX, y: iconY, w: 8, h: 6,
        action: 'meeting_minutes', room_id: roomData.id,
    });
}
```

- [ ] **Step 13: Frontend — meeting minutes panel** (`app.js`)

Add handler for meeting minutes click → fetch `/api/rooms/{room_id}/minutes` → render list panel. Click item → fetch `/api/meeting-minutes/{minute_id}` → render chat history.

- [ ] **Step 14: Commit**

```bash
git add src/onemancompany/api/routes.py frontend/office.js frontend/app.js
git commit -m "feat: meeting minutes API + frontend panel"
```

---

### Task 5: Node-level Execution Log

**Files:**
- Modify: `src/onemancompany/core/vessel.py:639-654,2860-2869`
- Modify: `src/onemancompany/agents/base.py:614-615`
- Modify: `src/onemancompany/api/routes.py`
- Modify: `frontend/task-tree.js:461-516`
- Modify: `frontend/style.css`
- Test: `tests/unit/core/test_node_execution_log.py`

#### Step 5a: Node-level log writer

- [ ] **Step 1: Write failing test**

```python
# tests/unit/core/test_node_execution_log.py
def test_append_node_log(tmp_path):
    from onemancompany.core.vessel import _append_node_execution_log
    import json

    project_dir = str(tmp_path)
    _append_node_execution_log(project_dir, "node123", "tool_call", "dispatch_child({...full args...})")

    log_path = tmp_path / "nodes" / "node123" / "execution.log"
    assert log_path.exists()
    line = json.loads(log_path.read_text().strip())
    assert line["type"] == "tool_call"
    assert "full args" in line["content"]  # NOT truncated
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement `_append_node_execution_log()`** in `vessel.py`

```python
def _append_node_execution_log(project_dir: str, node_id: str, log_type: str, content: str) -> None:
    """Append full-content log entry to node-level execution log (JSONL)."""
    if not project_dir:
        return
    import json
    log_dir = Path(project_dir) / "nodes" / node_id
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "execution.log"
    try:
        entry = json.dumps({
            "ts": datetime.now().isoformat(),
            "type": log_type,
            "content": content,
        }, ensure_ascii=False) + "\n"
        with open(path, "a", encoding=ENCODING_UTF8) as f:
            f.write(entry)
    except Exception as exc:
        logger.debug("Failed to write node execution log: {}", exc)
```

- [ ] **Step 4: Hook into `_log_node()`**

Add call to `_append_node_execution_log()` in `_log_node()`. Need to resolve `project_dir` from the current task entry.

- [ ] **Step 5: Run test to verify it passes**

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/core/vessel.py tests/unit/core/test_node_execution_log.py
git commit -m "feat: node-level execution log writer (JSONL, full content)"
```

#### Step 5b: Remove truncation in streaming

- [ ] **Step 7: In `base.py run_streamed`**, change tool_result logging

Line ~614: remove the `[:300]` truncation in `last_tool_results`:
```python
# Before: last_tool_results.append(f"{name} → {result_str[:300]}")
# After:  last_tool_results.append(f"{name} → {result_str}")
```

The `on_log("tool_result", ...)` already passes full content. The truncation is only in `last_tool_results` used for fallback output extraction.

- [ ] **Step 8: Commit**

```bash
git add src/onemancompany/agents/base.py
git commit -m "fix: remove 300-char truncation on tool results in execution log"
```

#### Step 5c: API endpoint

- [ ] **Step 9: Add `GET /api/task-tree/{node_id}/logs`** in `routes.py`

```python
@router.get("/api/task-tree/{node_id}/logs")
async def get_node_execution_logs(node_id: str):
    """Load execution logs for a task node from disk."""
    import json
    # Find the node across all project trees
    node, tree, project_dir = _find_node_in_trees(node_id)
    log_path = Path(project_dir) / "nodes" / node_id / "execution.log"
    if not log_path.exists():
        return []
    logs = []
    for line in log_path.read_text(encoding="utf-8").strip().split("\n"):
        if line:
            try:
                logs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return logs
```

- [ ] **Step 10: Commit**

```bash
git add src/onemancompany/api/routes.py
git commit -m "feat: GET /api/task-tree/{node_id}/logs endpoint"
```

#### Step 5d: Frontend — execution log in task tree detail

- [ ] **Step 11: Add execution log section** to `task-tree.js _renderNodeDetail()`

After the detail-meta section (line ~514), add:

```javascript
<div class="detail-section">
    <h4 class="detail-log-toggle" data-node-id="${node.id}" style="cursor:pointer">
        ▶ Execution Log
    </h4>
    <div class="detail-log-content hidden" id="node-log-${node.id}">
        <div class="detail-log-loading">Loading...</div>
    </div>
</div>
```

Add click handler to toggle + fetch:

```javascript
// In selectNode() or after render:
document.querySelectorAll('.detail-log-toggle').forEach(el => {
    el.addEventListener('click', async () => {
        const nodeId = el.dataset.nodeId;
        const content = document.getElementById(`node-log-${nodeId}`);
        if (content.classList.contains('hidden')) {
            content.classList.remove('hidden');
            el.textContent = '▼ Execution Log';
            // Fetch logs
            const logs = await fetch(`/api/task-tree/${nodeId}/logs`).then(r => r.json());
            content.innerHTML = logs.map(log => `
                <div class="node-log-entry node-log-${log.type}">
                    <span class="node-log-ts">${log.ts?.substring(11, 19) || ''}</span>
                    <span class="node-log-type">${log.type}</span>
                    <pre class="node-log-content">${this._escapeHtml(log.content || '')}</pre>
                </div>
            `).join('') || '<div class="node-log-empty">No logs available</div>';
        } else {
            content.classList.add('hidden');
            el.textContent = '▶ Execution Log';
        }
    });
});
```

- [ ] **Step 12: Add CSS** in `style.css`

```css
.node-log-entry { padding: 4px 6px; border-bottom: 1px solid #1a1a2e; font-size: 11px; }
.node-log-ts { color: #666; margin-right: 6px; }
.node-log-type { font-weight: bold; margin-right: 8px; }
.node-log-content { white-space: pre-wrap; max-height: 200px; overflow-y: auto; margin: 2px 0; }
.node-log-tool_call .node-log-type { color: #4488ff; }
.node-log-tool_result .node-log-type { color: #888; }
.node-log-llm_output .node-log-type { color: #44cc88; }
.node-log-error .node-log-type { color: #ff4444; }
.node-log-empty { color: #666; padding: 8px; }
```

- [ ] **Step 13: Commit**

```bash
git add frontend/task-tree.js frontend/style.css
git commit -m "feat: execution log viewer in task tree detail panel"
```

---

### Task 6: Final Integration Test

- [ ] **Step 1: Run full test suite**

```bash
.venv/bin/python -m pytest tests/ -x -q
```

- [ ] **Step 2: Manual browser test**
- Create a project → verify new naming format
- Check sidebar → newest project at top
- Click Work Rules → SOP → placeholder gone
- Run a task → click node in tree → expand execution log → see full tool calls
- After meeting ends → room chat cleared → click Minutes icon → see archived meeting

- [ ] **Step 3: Create PR**

```bash
git push -u origin feat/task-transparency
gh pr create --title "feat: task transparency, meeting minutes, project naming, UI fixes"
```
