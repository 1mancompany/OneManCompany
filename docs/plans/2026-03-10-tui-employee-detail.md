# Company-Hosted Employee Detail TUI Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the Task Board and Log panels in the company-hosted employee detail page with a TUI-style interface — Task Board becomes a session list, Log becomes a chat-style conversation view showing LLM input/output as chat bubbles and tool calls as inline blocks.

**Architecture:** Only affects company-hosted employees (hosting == "company"). Self-hosted and remote employees keep their current UI. The center column becomes a session/task list with status indicators. The right column becomes a chat conversation view where `llm_input` logs render as "user" bubbles, `llm_output` as "assistant" bubbles, `tool_call`/`tool_result` as compact inline blocks. Clicking a session in the center column loads that session's logs in the right column.

**Tech Stack:** Vanilla JS, CSS variables, existing API endpoints (`/api/employee/{id}/taskboard`, `/api/employee/{id}/logs`)

---

### Task 1: API — per-task log endpoint

Currently `/api/employee/{id}/logs` returns only the current/most-recent task's logs. We need to load logs for a specific task.

**Files:**
- Modify: `src/onemancompany/api/routes.py:1287-1302` (logs endpoint)
- Test: `tests/unit/api/test_routes.py`

**Step 1: Add optional `task_id` query param to logs endpoint**

In `routes.py`, modify the existing endpoint:

```python
@router.get("/api/employee/{employee_id}/logs")
async def get_employee_logs(employee_id: str, task_id: str = "") -> dict:
    from onemancompany.core.vessel import employee_manager
    board = employee_manager.boards.get(employee_id)
    if not board:
        return {"logs": []}
    if task_id:
        for task in board.tasks:
            if task.id == task_id:
                return {"logs": task.logs[-100:]}
        return {"logs": []}
    # Default: current running or most recent task
    for task in reversed(board.tasks):
        if task.status.value in ("processing", "holding"):
            return {"logs": task.logs[-100:]}
    for task in reversed(board.tasks):
        if task.logs:
            return {"logs": task.logs[-100:]}
    return {"logs": []}
```

**Step 2: Write test**

```python
class TestEmployeeLogsEndpoint:
    def test_logs_with_task_id(self):
        # Setup board with 2 tasks, each with logs
        # Request logs for specific task_id
        # Verify only that task's logs returned
```

**Step 3: Run tests, commit**

```bash
git commit -m "feat: add task_id filter to employee logs endpoint"
```

---

### Task 2: Frontend — Session list (center column, company-hosted only)

Replace the flat task board with a TUI-style session list. Each task is a "session" row showing status indicator, description snippet, timestamp, and cost.

**Files:**
- Modify: `frontend/app.js:1394-1447` (`_renderTaskBoard`)
- Modify: `frontend/style.css:3122-3215` (task board styles)

**Step 1: Add hosting check in openEmployeeDetail**

In `openEmployeeDetail`, after fetching task board, check employee hosting mode. If company-hosted, use the new TUI renderer:

```javascript
// In _fetchTaskBoard success callback, branch on hosting
_renderTaskBoard(tasks) {
    const emp = company_state.employees?.[this.viewingEmployeeId];
    const hosting = emp?.hosting || 'company';
    if (hosting === 'company') {
        this._renderTuiSessionList(tasks);
    } else {
        this._renderClassicTaskBoard(tasks);  // rename current _renderTaskBoard
    }
}
```

**Step 2: Implement `_renderTuiSessionList`**

TUI session list design:
- Each task is a clickable row
- Left: status dot (colored by status)
- Center: task description (first line, truncated to 60 chars)
- Right: timestamp + token count
- Active/selected session highlighted
- Clicking a session loads its logs in the right column

```javascript
_renderTuiSessionList(tasks) {
    const el = document.getElementById('emp-detail-taskboard');
    if (!tasks || !tasks.length) {
        el.innerHTML = '<span class="empty-hint">No sessions</span>';
        return;
    }
    // Only top-level tasks as sessions
    const sessions = tasks.filter(t => !t.parent_id);
    let html = '<div class="tui-session-list">';
    for (const task of sessions) {
        const active = task.id === this._selectedTaskId ? ' active' : '';
        const statusColor = {
            pending: 'var(--pixel-yellow)',
            processing: 'var(--pixel-green)',
            holding: 'var(--pixel-orange)',
            complete: 'var(--pixel-cyan)',
            finished: 'var(--pixel-gray)',
            failed: 'var(--pixel-red)',
            cancelled: 'var(--pixel-gray)',
        }[task.status] || 'var(--text-dim)';
        const desc = (task.description || '').split('\n')[0].substring(0, 60);
        const time = task.created_at ? new Date(task.created_at).toLocaleTimeString('zh-CN', {hour12:false, hour:'2-digit', minute:'2-digit'}) : '';
        const cost = task.estimated_cost_usd ? `$${task.estimated_cost_usd.toFixed(3)}` : '';
        html += `<div class="tui-session-row${active}" data-task-id="${task.id}" onclick="window._selectSession('${task.id}')">`;
        html += `<span class="tui-session-dot" style="background:${statusColor}"></span>`;
        html += `<span class="tui-session-desc">${this._escHtml(desc)}</span>`;
        html += `<span class="tui-session-meta">${time} ${cost}</span>`;
        html += `</div>`;
    }
    html += '</div>';
    el.innerHTML = html;
}
```

**Step 3: Wire up session selection**

```javascript
window._selectSession = function(taskId) {
    const app = window.appController;
    app._selectedTaskId = taskId;
    // Re-render session list to update active state
    app._fetchTaskBoard(app.viewingEmployeeId);
    // Load this task's logs
    app._fetchExecutionLogs(app.viewingEmployeeId, taskId);
};
```

Update `_fetchExecutionLogs` to accept optional `taskId`:

```javascript
_fetchExecutionLogs(empId, taskId) {
    let url = `/api/employee/${empId}/logs`;
    if (taskId) url += `?task_id=${taskId}`;
    fetch(url).then(r => r.json()).then(data => {
        const hosting = company_state.employees?.[empId]?.hosting;
        if (hosting === 'company') {
            this._renderTuiConversation(data.logs || []);
        } else {
            this._renderExecutionLogs(data.logs || []);
        }
    }).catch(() => {});
}
```

**Step 4: CSS for session list**

```css
.tui-session-list {
    display: flex;
    flex-direction: column;
    gap: 1px;
}
.tui-session-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 6px;
    cursor: pointer;
    border-left: 2px solid transparent;
    transition: background 0.1s;
}
.tui-session-row:hover {
    background: var(--bg-panel-alt);
}
.tui-session-row.active {
    background: var(--bg-panel-alt);
    border-left-color: var(--pixel-green);
}
.tui-session-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
}
.tui-session-desc {
    flex: 1;
    font-size: 6px;
    color: var(--pixel-white);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.tui-session-meta {
    font-size: 5px;
    color: var(--text-dim);
    white-space: nowrap;
}
```

**Step 5: Commit**

```bash
git commit -m "feat: TUI session list for company-hosted employee detail"
```

---

### Task 3: Frontend — Chat conversation view (right column, company-hosted only)

Replace the flat log viewer with a chat-style conversation for company-hosted employees.

**Files:**
- Modify: `frontend/app.js` (add `_renderTuiConversation`)
- Modify: `frontend/style.css` (add chat bubble styles)

**Step 1: Implement `_renderTuiConversation`**

Log types to render:
- `llm_input` → user/system message bubble (left-aligned, dim)
- `llm_output` → assistant bubble (left-aligned, green tint)
- `tool_call` → compact inline block (monospace, yellow border)
- `tool_result` → compact inline block (monospace, gray border)
- `start` / `end` → system divider line
- `error` / `retry` → error block (red)
- `result` → final result block (blue border)
- Other types → plain text with timestamp

```javascript
_renderTuiConversation(logs) {
    const el = document.getElementById('emp-detail-logs');
    if (!logs || !logs.length) {
        el.innerHTML = '<span class="empty-hint">No conversation</span>';
        return;
    }
    const prevScroll = el.scrollTop;
    const wasAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 20;

    let html = '<div class="tui-chat">';
    for (const log of logs) {
        const ts = log.timestamp ? new Date(log.timestamp).toLocaleTimeString('zh-CN', {hour12:false}) : '';
        const raw = log.content || '';
        const type = log.type || '';

        if (type === 'start' || type === 'end') {
            html += `<div class="tui-chat-divider"><span>${this._escHtml(type === 'start' ? '● Session Start' : '○ Session End')}</span></div>`;
        } else if (type === 'llm_input') {
            html += `<div class="tui-chat-bubble tui-input">`;
            html += `<div class="tui-chat-meta">${ts} · input</div>`;
            html += `<div class="tui-chat-text">${this._escHtml(raw)}</div>`;
            html += `</div>`;
        } else if (type === 'llm_output') {
            html += `<div class="tui-chat-bubble tui-output">`;
            html += `<div class="tui-chat-meta">${ts} · output</div>`;
            html += `<div class="tui-chat-text">${this._escHtml(raw)}</div>`;
            html += `</div>`;
        } else if (type === 'tool_call') {
            html += `<div class="tui-chat-tool tui-tool-call">`;
            html += `<span class="tui-tool-icon">⚙</span> ${this._escHtml(raw)}`;
            html += `</div>`;
        } else if (type === 'tool_result') {
            const truncated = raw.length > 300 ? raw.substring(0, 300) + '...' : raw;
            html += `<div class="tui-chat-tool tui-tool-result">`;
            html += `<span class="tui-tool-icon">→</span> ${this._escHtml(truncated)}`;
            html += `</div>`;
        } else if (type === 'error' || type === 'retry') {
            html += `<div class="tui-chat-error">${ts} ${this._escHtml(raw)}</div>`;
        } else if (type === 'result') {
            html += `<div class="tui-chat-bubble tui-result">`;
            html += `<div class="tui-chat-meta">${ts} · result</div>`;
            html += `<div class="tui-chat-text">${this._escHtml(raw)}</div>`;
            html += `</div>`;
        } else {
            html += `<div class="tui-chat-line"><span class="tui-chat-ts">${ts}</span> ${this._escHtml(raw)}</div>`;
        }
    }
    html += '</div>';
    el.innerHTML = html;

    if (wasAtBottom) {
        el.scrollTop = el.scrollHeight;
    } else {
        el.scrollTop = prevScroll;
    }
}
```

**Step 2: CSS for chat bubbles**

```css
.tui-chat {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 4px;
}

.tui-chat-divider {
    text-align: center;
    font-size: 5px;
    color: var(--text-dim);
    padding: 4px 0;
    border-bottom: 1px solid var(--border);
}

.tui-chat-bubble {
    padding: 4px 6px;
    border-radius: 4px;
    font-size: 6px;
    line-height: 1.5;
    max-width: 95%;
    word-break: break-word;
}

.tui-chat-bubble.tui-input {
    background: var(--bg-panel-alt);
    border-left: 2px solid var(--pixel-cyan);
}

.tui-chat-bubble.tui-output {
    background: var(--bg-panel-alt);
    border-left: 2px solid var(--pixel-green);
}

.tui-chat-bubble.tui-result {
    background: var(--bg-panel-alt);
    border-left: 2px solid var(--pixel-blue);
}

.tui-chat-meta {
    font-size: 5px;
    color: var(--text-dim);
    margin-bottom: 2px;
}

.tui-chat-text {
    color: var(--pixel-white);
    white-space: pre-wrap;
}

.tui-chat-tool {
    font-family: monospace;
    font-size: 5px;
    padding: 2px 6px;
    color: var(--text-dim);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.tui-chat-tool.tui-tool-call {
    color: var(--pixel-yellow);
}

.tui-tool-icon {
    margin-right: 2px;
}

.tui-chat-error {
    font-size: 5px;
    color: var(--pixel-red);
    padding: 2px 6px;
}

.tui-chat-line {
    font-size: 5px;
    color: var(--pixel-white);
    padding: 1px 6px;
}

.tui-chat-ts {
    color: var(--text-dim);
}
```

**Step 3: Update section titles for company-hosted**

In `index.html`, change the section titles conditionally, or in JS after modal opens:

```javascript
// In openEmployeeDetail, after fetching emp data:
if (emp.hosting === 'company') {
    document.querySelector('.emp-detail-center .emp-detail-section-title').textContent = 'Sessions';
    document.querySelector('.emp-detail-right .emp-detail-section-title').textContent = 'Conversation';
} else {
    document.querySelector('.emp-detail-center .emp-detail-section-title').textContent = 'Task Board';
    document.querySelector('.emp-detail-right .emp-detail-section-title').textContent = 'Execution Log';
}
```

**Step 4: Auto-select active session**

When opening the detail, auto-select the currently running or most recent task:

```javascript
// In _renderTuiSessionList, if no _selectedTaskId, auto-select
if (!this._selectedTaskId || !sessions.find(t => t.id === this._selectedTaskId)) {
    const active = sessions.find(t => t.status === 'processing' || t.status === 'holding')
                || sessions[sessions.length - 1];
    if (active) {
        this._selectedTaskId = active.id;
        this._fetchExecutionLogs(this.viewingEmployeeId, active.id);
    }
}
```

**Step 5: Clear _selectedTaskId on modal close**

```javascript
// In close modal handler:
this._selectedTaskId = null;
```

**Step 6: Commit**

```bash
git commit -m "feat: TUI chat conversation view for company-hosted employees"
```

---

### Task 4: Polling update — selective refresh

Update the polling mechanism to only refresh the selected session's logs instead of always fetching the latest.

**Files:**
- Modify: `frontend/app.js:1492-1507` (`_startTaskBoardPolling`)

**Step 1: Update polling to pass taskId**

```javascript
_startTaskBoardPolling(empId) {
    this._stopTaskBoardPolling();
    this._taskBoardPollTimer = setInterval(() => {
        if (this.viewingEmployeeId === empId) {
            this._fetchTaskBoard(empId);
            this._fetchExecutionLogs(empId, this._selectedTaskId || '');
        }
    }, 3000);
}
```

**Step 2: Commit**

```bash
git commit -m "feat: polling passes selected session taskId for targeted log refresh"
```

---

## Summary

| Task | What | Key Change |
|------|------|------------|
| 1 | API: per-task log endpoint | `task_id` query param on `/api/employee/{id}/logs` |
| 2 | Session list (center column) | TUI session rows with status dot, click to select |
| 3 | Chat conversation (right column) | Chat bubbles for LLM I/O, inline tool blocks |
| 4 | Polling: targeted refresh | Pass `_selectedTaskId` to log fetcher |

**Scope**: Company-hosted employees ONLY. Self-hosted and remote keep existing UI unchanged.
