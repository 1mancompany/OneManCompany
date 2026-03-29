# CEO Unified Conversation Model — Design Spec

> Date: 2026-03-29
> Status: Approved by CEO
> Audit reference: `docs/superpowers/specs/2026-03-29-ceo-executor-audit.md`

## Overview

Replace all CEO interaction paths with a unified, per-project conversation model. CEO's interface becomes a multi-session TUI (like Claude Code), where each project has its own conversation session. System messages (employee requests, project reports) and CEO replies flow through the same conversation.

## Core Model

CEO interaction = multi-session conversation:
- Each project has one `CeoSession` (independent conversation history + pending queue)
- "New Task" creates a new project and session
- System pushes messages INTO sessions (employee requests, completion reports)
- CEO replies WITHIN sessions (responses route to the correct handler)

## Architecture

```
CEO TUI
├── Project list (left panel, sorted by pending-first)
│   ├── ● Project A (has pending request)
│   ├── Project B
│   ├── ──────────
│   ├── + New Task
│   └── + Simple Task
│
└── Conversation panel (right, per-project)
    ├── Message history (system + CEO messages)
    └── Input box
```

### Components

#### 1. CeoExecutor (vessel.py, implements Launcher)

Registered for CEO_ID ("00001") at startup via `register()`.

```
execute(task_description, context) -> LaunchResult:
  1. Resolve project_id from context
  2. Build display message from task_description + source employee context
  3. Create asyncio.Future
  4. session = broker.get_or_create_session(project_id)
  5. session.enqueue(CeoInteraction(message, future, node_id, ...))
  6. Broadcast to frontend: new message in session
  7. await future
  8. return LaunchResult(output=ceo_response)
```

When a task is scheduled on CEO (via normal `schedule_node`), the executor:
- Does NOT call any LLM
- Pushes the request into the project's session as a system message
- Waits for CEO to respond in the TUI
- Returns CEO's text as the execution result

#### 2. CeoBroker (new: ceo_broker.py)

Central manager for all CEO sessions.

```python
class CeoBroker:
    sessions: dict[str, CeoSession]  # project_id -> session

    def get_or_create_session(project_id) -> CeoSession
    def handle_input(project_id, text) -> None  # CEO typed something
    def handle_new_task(text, attachments) -> str  # Returns project_id
    def recover() -> None  # Rebuild from disk on restart
    def list_sessions() -> list[SessionSummary]  # For project list UI
```

Singleton, accessible like `employee_manager`.

#### 3. CeoSession (per-project conversation)

```python
class CeoSession:
    project_id: str
    pending: deque[CeoInteraction]  # FIFO queue of system requests
    history: list[dict]  # Persisted conversation messages

    def enqueue(interaction: CeoInteraction) -> None
    def handle_input(text: str) -> None:
        if pending:
            interaction = pending.popleft()
            interaction.future.set_result(text)
            append_to_history("ceo", text)
            broadcast_to_frontend()
        else:
            # No pending request — treat as follow-up instruction
            dispatch_followup(text)

    def push_system_message(message: str, source: str) -> None:
        append_to_history("system", message, source=source)
        broadcast_to_frontend()
```

#### 4. CeoInteraction (data structure)

```python
@dataclass
class CeoInteraction:
    node_id: str              # TaskNode being executed
    tree_path: str
    project_id: str
    source_employee: str      # Who initiated the request
    interaction_type: str     # "ceo_request" | "project_confirm"
    message: str              # Display message for CEO
    future: asyncio.Future    # Resolved when CEO replies
    created_at: str
```

### Routing Logic

```
CEO input arrives with project_id:
  │
  ├─ project_id exists, session has pending queue:
  │   → pop front of queue, resolve Future with CEO's text
  │   → CeoExecutor.execute() returns, node completes normally
  │
  ├─ project_id exists, no pending queue:
  │   → treat as CEO_FOLLOWUP instruction
  │   → create followup node in project tree
  │
  └─ "New Task" (no project_id):
      → create new project via existing _dispatch_ceo_task logic
      → new CeoSession created automatically
```

### Project List UI State

Each project in the left panel shows:
- `●` (dot indicator) — has pending CeoInteraction(s) needing CEO attention
- No indicator — running normally, CEO can still send follow-up instructions
- Sorted: pending-first, then by recency

## Migration Plan

### Paths replaced by CeoExecutor + CeoBroker

| Current path | New path |
|-------------|----------|
| `dispatch_child(CEO_ID)` special case in tree_tools.py | Normal `schedule_node` → CeoExecutor → CeoBroker session |
| Circuit breaker CEO escalation (vessel.py:2413-2482) | Normal `schedule_node` → CeoExecutor → CeoBroker session |
| `_request_ceo_confirmation` + 120s auto-confirm timer | CeoExecutor scheduled for project confirm → CeoBroker session |
| `POST /api/ceo/inbox/{id}/open,message,complete,dismiss` | `POST /api/ceo/session/{project_id}/message` (unified) |
| `POST /api/ceo/report/{pid}/confirm` | CEO replies in session (same input path) |
| `POST /api/ceo/task` | `POST /api/ceo/session/new` or "New Task" button |
| `ceo_conversation.py` ConversationSession | Replaced by CeoSession |

### Code to remove after migration

- `ceo_conversation.py` — entire module (ConversationSession, _ea_auto_reply, etc.)
- `vessel.py` — `_request_ceo_confirmation`, `_ceo_report_auto_confirm`, `_confirm_ceo_report`, `CEO_REPORT_CONFIRM_DELAY`, `_pending_ceo_reports`
- `routes.py` — 6 CEO inbox endpoints, report confirm endpoint
- `conversation_hooks.py` — `_close_ceo_inbox` hook
- `tree_tools.py:165-180` — CEO_ID special path in dispatch_child

### Code to keep as-is

- 1-on-1 meetings (independent system, not task-based)
- CEO roster display (pure UI)
- task-tree.js CEO node rendering (display only)
- `agents/base.py` CEO escalation prompt (agents still call dispatch_child("00001"))

### EventTypes

| Event | Action |
|-------|--------|
| `CEO_TASK_SUBMITTED` | Keep — activity log |
| `CEO_INBOX_UPDATED` | Replace with `CEO_SESSION_MESSAGE` (per-session message event) |
| `CEO_REPORT` | Remove — project reports go through CeoSession |

### Persistence

Session history persisted as YAML per project:
```
{project_dir}/ceo_session.yaml
```

Contains conversation messages (system + CEO), not pending Futures (those are in-memory, rebuilt from tree state on restart).

### Restart Recovery

`CeoBroker.recover()` called during startup:
1. Scan all non-archived project trees
2. For each tree, find PENDING CEO_REQUEST/CEO_PROMPT nodes assigned to CEO_ID
3. Rebuild CeoInteraction for each → enqueue in corresponding CeoSession
4. Project confirm: find trees where `is_project_complete()` and CEO_PROMPT is COMPLETED → enqueue confirm interaction

### EA Auto-Reply

EA auto-reply behavior changes:
- Currently: EA replies on CEO's behalf after timeout in ConversationSession
- New: configurable per-session timeout in CeoBroker; if CEO doesn't respond within N seconds, CeoBroker auto-resolves the Future with EA's decision
- Implementation: `CeoSession.set_auto_reply(timeout_seconds)` starts a timer per pending interaction

## Success Criteria

1. All CEO interactions go through TUI conversation (zero separate endpoints)
2. Each project has independent conversation context
3. Pending requests are FIFO within each session
4. System survives restart — pending queue rebuilt from tree state
5. No duplicate systems — old paths fully removed
6. All existing tests pass + new tests for CeoExecutor, CeoBroker, CeoSession
