# CEO Inbox & Task Dispatch Design

## Goal

Replace the `report_to_ceo` / `ask_ceo` blocking mechanism with a first-class CEO task inbox. Employees dispatch child tasks to CEO (employee 00001) via the existing task tree. CEO interacts through a real-time chat dialog. This unifies all CEO-employee interaction into one pattern: task dispatch.

## Architecture

### Core Concept

CEO is employee 00001. Any employee can `dispatch_child("00001", description)` to assign work to the CEO. Since CEO is a human, the task node is created with `node_type: "ceo_request"` and status `pending` (the standard initial tree node status). The node blocks downstream dependents via `depends_on` until it reaches terminal status `accepted`.

### What Gets Removed (No Backward Compat)

Delete all code and references for:

- `report_to_ceo()` tool — `common_tools.py`
- `ask_ceo()` tool — `common_tools.py`
- `_ceo_pending` dict, `resolve_ceo_pending()`, `_CeoPendingSnapshot` — `common_tools.py`
- `/api/ceo/respond` endpoint — `routes.py`
- `ceo_report` / `ask_ceo` WebSocket event handlers — `app.js`
- `_showCeoReportDialog()` — `app.js`
- Any snapshot provider for ceo_pending state

### What Gets Added

#### 1. Backend: CEO Task Handling

**Dispatch interception** — When `dispatch_child` targets employee `00001`:
- Create TaskNode in tree as normal (existing tree_tools logic)
- Set `node_type: "ceo_request"` to distinguish from agent-executed tasks
- Keep standard status `pending` — do NOT push to EmployeeManager (CEO is human)
- Publish WebSocket event `ceo_inbox_updated` so frontend refreshes inbox
- The dispatching employee's agent returns; the child is a dependency for siblings that declare `depends_on`

**CEO inbox query** — New endpoint `GET /api/ceo/inbox`:
- Scans active task trees for nodes where `node_type == "ceo_request"` and status not in `_TERMINAL` (`accepted`, `failed`, `cancelled`)
- Returns list of `{project_id, node_id, description, from_employee_id, from_nickname, status, created_at}`
- `from_employee_id` resolved by traversing `parent_id` to find the dispatching employee
- Reads from disk (task tree YAML files) — no in-memory cache (SSOT)

**CEO conversation** — REST endpoints + WebSocket for real-time messaging:
- `POST /api/ceo/inbox/{node_id}/open` — Opens conversation:
  - Activates the dispatching employee's agent in conversation mode (see Section 2)
  - Task node status: `pending` → `processing`
  - Returns initial context (task description, prior messages if resuming)
- `POST /api/ceo/inbox/{node_id}/message` — CEO sends a message:
  - Appends message to conversation log on disk
  - Forwards to employee agent's message queue
  - Agent responds asynchronously → response pushed via WebSocket `ceo_conversation` event
- `POST /api/ceo/inbox/{node_id}/upload` — CEO uploads attachment:
  - Saves file to `{project_dir}/workspace/{original_filename}`
  - Appends message with `attachments` field to conversation log
  - Notifies employee agent via WebSocket (agent can then read the file)
- `POST /api/ceo/inbox/{node_id}/complete` — CEO clicks "完成":
  - Sends termination signal to agent's message queue
  - Agent produces summary, saves as node result
  - Task node status: `processing` → `accepted` (terminal, unblocks dependents)
  - Triggers `_trigger_dep_resolution()`
  - Publishes `ceo_inbox_updated` event

**WebSocket events (server → client only, no bidirectional WS):**
- `{type: "ceo_conversation", node_id, sender: employee_id, text, timestamp}` — employee agent response
- `{type: "ceo_inbox_updated"}` — inbox list changed

**Conversation persistence** (SSOT = disk):
- Messages stored in `{project_dir}/conversations/{node_id}.yaml`
- Format: `[{sender: "ceo"|employee_id, text: "...", timestamp: "...", attachments: [...]}]`
- Each message appended immediately on send (both CEO and agent messages)
- On `open`, load existing messages from disk (supports resume after restart)

#### 2. Employee Agent: Conversation Mode

The conversation loop uses a **per-message invocation pattern**, not a persistent streaming agent:

**Loop design:**
1. CEO opens conversation → backend creates `ConversationSession` object with an `asyncio.Queue`
2. CEO sends message → `POST /api/ceo/inbox/{node_id}/message` → message pushed to queue
3. Conversation loop (running as asyncio task):
   ```
   while True:
     msg = await queue.get()
     if msg is COMPLETE_SIGNAL:
       summary = await agent.ainvoke("Summarize this conversation as a task result")
       break
     response = await agent.ainvoke(msg.text, chat_history=load_history_from_disk())
     append_to_disk(agent_response)
     publish_ws_event(ceo_conversation, response)
   ```
4. Each `ainvoke` is a standalone call with full chat history loaded from disk (SSOT)
5. Agent has standard tools available (read, write, etc.) plus conversation context
6. On COMPLETE_SIGNAL: agent summarizes, result saved to node, loop exits

**Termination:**
- `COMPLETE_SIGNAL` is a sentinel value pushed to the queue
- If agent is mid-`ainvoke` when complete arrives, the current call finishes, then the loop reads the signal and terminates
- Clean shutdown: agent always completes its current response before exiting

**Restart recovery:**
- `ConversationSession` is in-memory only (queue, asyncio task reference)
- On server restart, sessions are lost. Node stays in `processing` status.
- When CEO reopens from inbox, a new `ConversationSession` is created, history loaded from disk
- Agent resumes with full context from persisted messages

**No snapshot provider needed** — conversation state is entirely on disk (messages) plus ephemeral in-memory queue (rebuilt on reopen).

#### 3. Frontend Changes

**Settings → Toolbar**:
- Remove `SETTINGS` collapsible section from right sidebar (`index.html`)
- Add ⚙ button to the toolbar in the center panel header
- Click ⚙ → floating dropdown panel (position: absolute, z-index above canvas) with API settings content
- Click outside or click ⚙ again → close panel

**CEO Inbox (replaces Settings position)**:
- New collapsible section `📥 CEO INBOX` in right sidebar, between CEO Console and Activity Log
- Header shows count badge: number of non-terminal CEO request nodes (red if > 0)
- Each item: left orange border, status icon (⏸ pending / 🔄 processing), employee nickname, task description preview (truncated)
- Click item → open conversation dialog
- Refreshes on `ceo_inbox_updated` WebSocket events

**Conversation Dialog (center modal)**:
- Full-screen overlay with semi-transparent backdrop
- Center modal (80% width, 70% height) with pixel-art styling consistent with app
- Header: `📥 来自 {nickname} 的任务请求` + close button (X)
- Task description bar (collapsible)
- Chat area: scrollable message list, employee messages left-aligned (colored by role), CEO messages right-aligned (gold)
- Input area: text input + Send button (green) + Complete button (orange)
- Send: POST to `/api/ceo/inbox/{node_id}/message`, appears immediately in chat (optimistic)
- Employee response: arrives via WebSocket `ceo_conversation` event, appended to chat
- Complete: confirmation prompt → POST to complete endpoint → close dialog → inbox refreshes
- Close (X): dialog closes but conversation stays open (task remains `processing`), can reopen from inbox
- Attachments: CEO can attach files via 📎 button or drag-and-drop
  - `POST /api/ceo/inbox/{node_id}/upload` — accepts multipart file upload
  - File saved to `{project_dir}/workspace/{original_filename}` (the project's shared workspace folder)
  - Message entry in conversation log includes `attachments: [{filename, path}]`
  - Employee agent can read uploaded files via standard file tools (they already have workspace access)
  - Frontend shows attachment as clickable filename in the chat message

#### 4. Data Flow

```
Employee agent calls dispatch_child("00001", "需要确认技术方案")
  → tree_tools creates TaskNode(assignee="00001", node_type="ceo_request", status="pending")
  → saves to task_tree.yaml on disk
  → publishes ceo_inbox_updated via WebSocket
  → returns to employee agent (child is a blocking dependency for siblings)

CEO sees inbox badge → clicks task item
  → POST /api/ceo/inbox/{node_id}/open
  → backend creates ConversationSession, starts conversation loop
  → node status → processing (saved to disk)
  → dialog opens with task context + any prior messages

CEO types message → POST /api/ceo/inbox/{node_id}/message
  → backend appends to conversations/{node_id}.yaml
  → pushes to ConversationSession queue
  → agent.ainvoke() with full history
  → agent response appended to conversations/{node_id}.yaml
  → WebSocket ceo_conversation event → CEO sees response

CEO clicks "完成"
  → POST /api/ceo/inbox/{node_id}/complete
  → COMPLETE_SIGNAL pushed to queue
  → agent summarizes conversation as result
  → node status → accepted (terminal, saved to disk)
  → _trigger_dep_resolution() unblocks waiting siblings
  → dialog closes, inbox refreshes
```

#### 5. Edge Cases

- **Server restart during open conversation**: Node stays `processing` on disk. CEO reopens from inbox → new ConversationSession created, history loaded from disk. Agent resumes with full context.
- **Employee agent errors during ainvoke**: Standard error handling (log, don't swallow). Error message pushed to CEO via WebSocket `ceo_conversation_error` event. CEO can send another message to retry, or complete the task.
- **Multiple CEO tasks**: Each is independent. CEO can have multiple inbox items. Only one conversation dialog open at a time in frontend. Backend supports concurrent ConversationSessions.
- **CEO closes dialog without completing**: Task stays `processing`. Inbox shows it with 🔄 icon. CEO can reopen anytime. ConversationSession stays alive in memory until server restarts or CEO completes.
- **Agent mid-call when CEO completes**: Current ainvoke finishes, response is sent, then loop reads COMPLETE_SIGNAL and terminates cleanly.

#### 6. Files Affected

**Delete/gut:**
- `src/onemancompany/agents/common_tools.py` — remove `report_to_ceo`, `ask_ceo`, `_ceo_pending`, `resolve_ceo_pending`, `_CeoPendingSnapshot`
- `src/onemancompany/api/routes.py` — remove `/api/ceo/respond` endpoint
- `frontend/app.js` — remove `_showCeoReportDialog`, `ceo_report`/`ask_ceo` event handlers

**Modify:**
- `src/onemancompany/agents/tree_tools.py` — intercept dispatch to "00001", set `node_type="ceo_request"`, skip EmployeeManager push
- `src/onemancompany/api/routes.py` — add `/api/ceo/inbox`, `/api/ceo/inbox/{node_id}/open`, `/api/ceo/inbox/{node_id}/message`, `/api/ceo/inbox/{node_id}/upload`, `/api/ceo/inbox/{node_id}/complete`
- `src/onemancompany/core/vessel.py` — no change needed (conversation loop is separate from vessel executors)
- `frontend/index.html` — move settings to toolbar, add inbox section
- `frontend/app.js` — settings floating panel, inbox rendering, conversation dialog, WebSocket handlers
- `frontend/style.css` — styles for inbox section, conversation dialog, settings floating panel, toolbar button

**New:**
- `src/onemancompany/core/ceo_conversation.py` — `ConversationSession` class, message persistence (append/load YAML), conversation loop (asyncio task with queue + per-message ainvoke), session registry (in-memory dict of active sessions)
