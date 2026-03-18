# Unified Conversation Module Design

> Standardize CEO inbox and 1-on-1 conversation interfaces into a unified backend service + frontend component. Team meeting chat is out of scope for v1 (multi-participant broadcast model incompatible with 1:1 conversation pattern).

## Scope

### v1 (this spec)
- CEO inbox conversations (async, task-driven)
- 1-on-1 meetings (sync, CEO-initiated)
- Unified backend ConversationService + Adapter protocol
- Unified frontend ChatPanel embedded in right panel

### v2 (future)
- Team meeting chat (multi-participant, routine-driven broadcast)
- Meeting minutes generation integration

## Context

Currently two independent 1:1 conversation systems exist:

| System | Backend | Frontend | Storage |
|--------|---------|----------|---------|
| CEO Inbox | `/api/ceo/inbox/*`, `ceo_conversation.py` | `#ceo-conv-overlay` modal | `{project_dir}/conversations/{node_id}.yaml` |
| 1-on-1 Meeting | `/api/oneonone/*` in routes.py | `#oneonone-modal` | `{EMPLOYEES_DIR}/{eid}/oneonone_history.yaml` |

Problems:
- Two separate API surfaces, two modal UIs, inconsistent interaction patterns
- Agent type differences (LangChain vs Claude session) leak into API design
- No unified lifecycle management

## Architecture Overview

```
Frontend: ChatPanel (unified component, embedded in right panel)
    |  REST + WebSocket (async message model)
    v
API: /api/conversation/* (unified endpoints)
    |
    v
ConversationService (lifecycle + disk persistence, stateless reads)
    |  registry dispatch by executor type
    v
Adapter Protocol: LangChainAdapter / ClaudeSessionAdapter
    |
    v
Existing executors (unchanged)
```

## 1. Data Model

### Conversation

```python
@dataclass
class Conversation:
    id: str                    # uuid
    type: str                  # "ceo_inbox" | "oneonone"
    phase: str                 # "created" | "active" | "closing" | "closed"
    employee_id: str           # conversation target
    tools_enabled: bool        # whether agent has access to tools during conversation
    metadata: dict             # scenario-specific (node_id for inbox, etc.)
    created_at: str            # ISO 8601
    closed_at: str | None
```

Note: `tools_enabled` distinguishes conversation modes. 1-on-1 meetings use the full executor pipeline (agent has access to their tools — e.g., HR can run hiring searches during 1-on-1). CEO inbox conversations use plain LLM invocation without tools.

### Message

```python
@dataclass
class Message:
    sender: str                # "ceo" | employee_id
    role: str                  # "CEO" | employee display name
    text: str
    timestamp: str             # ISO 8601
    attachments: list[str]     # file paths
```

### Legacy Field Mapping

| Legacy System | Legacy Fields | Mapped To |
|--------------|---------------|-----------|
| CEO inbox (`ceo_conversation.py`) | `sender`, `text` | `sender` = as-is, `role` = synthesized from employee name, `text` = as-is |
| 1-on-1 history (`store.py`) | `role` ("ceo"/"employee"), `content` | `sender` = mapped from role, `role` = display name, `text` = content |

### Disk Storage

Conversations are stored under their owning context to maintain locality:

```
# CEO inbox (project-scoped, lives with task tree)
{project_dir}/conversations/{conversation_id}/
    meta.yaml          # Conversation metadata
    messages.yaml      # Message list (append-only)

# 1-on-1 (employee-scoped)
{EMPLOYEES_DIR}/{employee_id}/conversations/{conversation_id}/
    meta.yaml
    messages.yaml
```

Migration: existing `{project_dir}/conversations/{node_id}.yaml` flat files readable via compatibility layer. New conversations use the directory structure. `meta.yaml` stores `metadata.node_id` for reverse lookup (node_id -> conversation_id).

### Reverse Lookup

Task tree `ceo_request` nodes store `conversation_id` in their metadata for direct lookup. For legacy nodes without this field, scan `{project_dir}/conversations/*/meta.yaml` for matching `node_id`.

## 2. ConversationService

Core service managing lifecycle and persistence. **Stateless reads** — `get()`, `list_active()`, `get_messages()` always read from disk. No in-memory caching of business data (SSOT principle). The only in-memory state is async queues for active send operations (runtime-only, not persisted).

```python
class ConversationService:
    async def create(type: str, employee_id: str, **metadata) -> Conversation
    async def send_message(conv_id: str, sender: str, text: str) -> None
    async def close(conv_id: str, wait_hooks: bool = False) -> dict
    async def get(conv_id: str) -> Conversation        # reads from disk
    async def list_active(type: str | None = None) -> list[Conversation]  # scans disk
    async def get_messages(conv_id: str) -> list[Message]  # reads from disk
```

### send_message flow (async)

1. Persist CEO message to disk (`messages.yaml` append)
2. Broadcast `conversation_message` event for CEO message (frontend renders immediately)
3. Resolve employee executor type
4. Registry dispatch to corresponding adapter (async, non-blocking)
5. Adapter builds full conversation history + context, calls executor
6. Persist agent reply to disk
7. Broadcast `conversation_message` event for agent reply

**`POST /api/conversation/{conv_id}/message` returns immediately with `{status: "sent"}`**. Agent reply arrives asynchronously via WebSocket `conversation_message` event. This matches the existing CEO inbox pattern and avoids HTTP timeout issues with slow LLM responses (Claude session can take 60+ seconds).

### close flow

1. Set phase -> `closing`, persist to disk
2. Registry dispatch `on_close` hook by conversation type
3. Hook behavior depends on `wait_hooks` parameter:
   - `wait_hooks=True` (1-on-1): Hook runs synchronously, result included in response. CEO sees reflection notes + work_principles update confirmation.
   - `wait_hooks=False` (inbox): Hook runs async, response returns immediately. Summary generated in background.
4. Set phase -> `closed`, persist to disk
5. Broadcast `conversation_phase` event

### Startup Recovery

On server start, `ConversationService.recover()` scans disk for `phase: "active"` or `phase: "closing"` conversations:
- `active` conversations: no action needed (stateless, next `send_message` works normally)
- `closing` conversations: re-run close hooks (idempotent)

## 3. Adapter Protocol + Registry

### Protocol

```python
class ConversationAdapter(Protocol):
    async def send(
        self, conversation: Conversation, messages: list[Message], new_message: Message
    ) -> str:
        """Send message to agent with full history, return agent reply text.

        Args:
            conversation: Conversation metadata (includes tools_enabled flag)
            messages: Full conversation history (for context injection)
            new_message: The latest CEO message
        """
        ...

    async def on_create(self, conversation: Conversation) -> None:
        """Optional initialization when conversation starts."""
        ...

    async def on_close(self, conversation: Conversation) -> None:
        """Optional adapter-level cleanup (release resources, close connections).
        This is separate from close hooks — adapter.on_close handles transport cleanup,
        close hooks handle business logic (summary, reflection, etc.)."""
        ...
```

Note: `send()` receives the full message history because neither adapter maintains conversation state internally. Each call is stateless — the adapter must inject history into the prompt/context.

### Implementations

| Adapter | Executor Type | send() Logic |
|---------|--------------|-------------|
| `LangChainAdapter` | company-hosted | Build system prompt + chat history -> `executor.execute()` (tools_enabled=True: full tool access via `create_react_agent`; False: plain LLM invoke) -> return result |
| `ClaudeSessionAdapter` | self-hosted | Build full-history prompt -> `executor.execute()` (routes through `ClaudeSessionExecutor` which manages daemon lifecycle) -> return result |

**Critical: Both adapters route through `executor.execute()`**, not raw `run_claude_session()` or `create_react_agent` directly. This preserves the existing executor pipeline behavior — agents retain access to their tools during conversation (e.g., HR can run hiring searches during a 1-on-1). The adapter's job is to format the conversation context and history, then delegate execution to the existing executor infrastructure.

**ClaudeSessionAdapter details:**
- `executor.execute()` internally calls `run_claude_session()` which manages the daemon lifecycle, per-employee locking, and crash recovery
- The daemon is a single-turn request/response model (send prompt, wait for `result` NDJSON) — NOT a persistent multi-turn session
- Full conversation history is formatted into each prompt (same pattern as current `ceo_conversation.py` `_build_agent_and_invoke`)
- Lock is per-employee-per-project (keyed by `employee_id:project_id`), see Concurrency section for implications

### Registry

```python
_adapter_registry: dict[str, type[ConversationAdapter]] = {}

def register_adapter(executor_type: str):
    def decorator(cls):
        _adapter_registry[executor_type] = cls
        return cls
    return decorator

@register_adapter("langchain")
class LangChainAdapter: ...

@register_adapter("claude_session")
class ClaudeSessionAdapter: ...
```

### Close Hooks Registry

```python
_close_hooks: dict[str, Callable[..., Awaitable[dict | None]]] = {}

def register_close_hook(conv_type: str):
    ...

@register_close_hook("ceo_inbox")
async def _close_ceo_inbox(conv) -> dict | None:
    # 1. Generate conversation summary (LLM)
    # 2. Update task node status (processing -> completed -> accepted)
    # 3. Trigger dependency resolution (_trigger_dep_resolution)
    # 4. Auto-resume held parent tasks if applicable
    # Non-blocking hook (wait_hooks=False) — runs in background

@register_close_hook("oneonone")
async def _close_oneonone(conv) -> dict | None:
    # 1. Generate reflection notes (LLM)
    # 2. Update work_principles
    # 3. Save 1-on-1 notes to employee dir
    # Returns: {reflection: str, principles_updated: bool}
    # Blocking hook (wait_hooks=True) — CEO waits for result
```

## 4. Unified REST API

### Endpoints

```
POST   /api/conversation/create             # Create conversation
GET    /api/conversation/{conv_id}           # Get conversation metadata
GET    /api/conversation/{conv_id}/messages  # Get message history
POST   /api/conversation/{conv_id}/message   # Send message (returns immediately, reply via WebSocket)
POST   /api/conversation/{conv_id}/upload    # Upload file attachment (multipart)
POST   /api/conversation/{conv_id}/close     # End conversation (?wait_hooks=true for blocking)
GET    /api/conversations?type=&phase=       # List (filter by type/phase)
```

### WebSocket Events

New event types added to `EventType` literal in `events.py`:

| Event | Payload | Trigger |
|-------|---------|---------|
| `conversation_message` | `{conv_id, sender, role, text, timestamp}` | New message (CEO or agent) |
| `conversation_phase` | `{conv_id, phase, type}` | Lifecycle change |

Events go through `event_bus` (consistent with project architecture), not direct `ws_manager.broadcast()`.

**Migration note:** Current CEO inbox events (`ceo_conversation`, `ceo_inbox_updated`) bypass `event_bus` and broadcast directly via `ws_manager.broadcast()`. The migration must route these through `event_bus` for consistency. During transition, both legacy (`ws_manager.broadcast`) and new (`event_bus`) events are emitted. Legacy events removed after frontend migration.

### Legacy API Compatibility

Existing `/api/ceo/inbox/*` and `/api/oneonone/*` endpoints kept temporarily, internally rewired to call ConversationService. Removed after frontend fully migrated.

## 5. Concurrency

### Same-Employee Concurrent Conversations

`ClaudeSessionExecutor` uses a per-employee-per-project lock (`claude_session.py:_get_session_lock`, keyed by `employee_id:project_id`). This means:

- Different conversation types with the same employee use different `project_id` values (see below), so they get **separate locks and can execute concurrently**
- Within the same conversation type (same project_id), messages serialize — only one processes at a time
- Different employees are fully independent
- Company-hosted (LangChain) employees have no lock constraint — each invoke is independent

**UX handling:** If CEO sends a message while a previous one is still processing, the frontend shows a "typing..." indicator. Messages queue naturally — no special handling needed since `send_message` is async.

### Different Conversation Types with Same Employee

Each conversation type uses a different `project_id` context, resulting in separate daemon processes and separate locks:
- `ceo_inbox`: uses the task's project_id
- `oneonone`: uses a synthetic `oneonone-{employee_id}` project_id

This means CEO can chat with the same self-hosted employee via inbox AND 1-on-1 simultaneously without blocking. Each conversation gets its own daemon process with its own context.

## 6. Frontend: ChatPanel + Right Panel

### ChatPanel Component

Generic chat renderer, stateless:

```javascript
class ChatPanel {
    constructor(containerEl) { ... }

    render(messages, conversation) { ... }    // Full render
    appendMessage(message) { ... }            // Incremental (WebSocket)
    setInputEnabled(enabled) { ... }          // Hide input when closed
    showTyping(show) { ... }                  // Agent typing indicator
    onSend(callback) { ... }                  // CEO sends message
    onClose(callback) { ... }                 // CEO ends conversation
}
```

Rendering rules:
- CEO messages left-aligned, agent messages right-aligned
- Pixel-style bubbles, 7px monospace font (existing CSS variables)
- Attachment display (filename + icon)
- Conversation type label at top (`1-on-1` / `Inbox`)

### Right Panel as Context-Aware Router

Registry-based panel dispatch:

```javascript
const _panelRenderers = {
    conversation: (ctx) => this._showChat(ctx.conversationId),
    employee:     (ctx) => this._showEmployeeInfo(ctx.employeeId),
    dashboard:    ()    => this._showDashboard(),
};

class RightPanel {
    show(context) {
        const type = context.conversationId ? 'conversation'
                   : context.employeeId     ? 'employee'
                   :                          'dashboard';
        _panelRenderers[type](context);
    }
}
```

### Interaction Entry Points

| Trigger | Behavior |
|---------|----------|
| Click employee in pixel office | Right panel shows employee info + "Start 1-on-1" button |
| Click "Start 1-on-1" | `POST /api/conversation/create` -> right panel switches to ChatPanel |
| Click ceo_request node in task tree | Right panel opens corresponding inbox conversation (via node metadata `conversation_id`) |
| Conversation ends | Right panel returns to employee info |

### Deprecated Components (remove after migration)

- `#ceo-conv-overlay` modal
- `#oneonone-modal`

## 7. Error Handling

| Scenario | Handling |
|----------|---------|
| Adapter send timeout | 60s timeout (Claude session can be slow), show error in ChatPanel ("Agent not responding, please retry"), conversation stays active |
| Claude daemon disconnect | ClaudeSessionAdapter auto-resumes via `run_claude_session()`, transparent to CEO |
| LangChain invoke failure | Log error + return friendly error message as agent reply, don't change conversation phase |
| Close hook failure (non-blocking) | `logger.exception`, phase still progresses to closed |
| Close hook failure (blocking, 1-on-1) | `logger.exception`, return error in response, phase set to closed (CEO can retry via re-open if needed) |
| Disk write failure | `logger.exception`, raise to caller (disk failure is critical — no silent swallowing) |

Core principle: **conversations never get stuck due to agent errors**. CEO can always close or wait for recovery.

## 8. Impact on Existing Features

### Affected Systems

| Feature | Impact | Risk |
|---------|--------|------|
| CEO inbox conversations | Old API rewired to ConversationService, storage path migration | Medium — must handle legacy `{node_id}.yaml` flat files |
| 1-on-1 meetings | Old endpoint wrapped by ConversationService | Medium — work_principles update logic must fully migrate to close hook |
| Team meeting chat | **Unchanged in v1** | None |
| Task tree ceo_request nodes | Click opens right panel ChatPanel instead of modal, node stores `conversation_id` | Low — frontend routing change |
| Pixel office employee click | New "Start 1-on-1" entry point added, existing behavior unchanged | Low |
| WebSocket event consumers | Old + new events sent in parallel during transition | Low |
| `employee.status = "in_meeting"` | Unchanged, set by ConversationService create/close | Low |

### Regression Test Checklist

| Test | Verification |
|------|-------------|
| Legacy inbox data read | Pre-migration `{node_id}.yaml` files load correctly via compatibility layer |
| CEO inbox full flow | Create -> multi-turn chat -> close -> summary generated -> task node status updated |
| 1-on-1 full flow | Initiate -> chat -> end -> reflection notes generated -> work_principles updated |
| Concurrent conversations | CEO has inbox + 1-on-1 with same employee, no data cross-contamination, messages serialize correctly |
| Server restart recovery | Active conversations survive restart, messages not lost, closing conversations re-run hooks |
| Legacy API compat | `/api/ceo/inbox/*` and `/api/oneonone/*` still work during transition |
| Right panel switching | Click between employees/conversations, panel state correct |
| WebSocket event delivery | Both old and new event types received during transition |
| Team meeting unaffected | Meeting chat still works through existing `routine.py` path |

## 9. Testing Strategy

| Layer | What | How |
|-------|------|-----|
| ConversationService | Lifecycle transitions, disk persistence, message append, recovery | Unit test, tmp_path isolation |
| Adapter registry | Registration + dispatch correctness | Unit test |
| LangChainAdapter | Context construction + history injection, mock executor | Unit test |
| ClaudeSessionAdapter | History formatting, mock `run_claude_session()` | Unit test |
| Close hooks | Per-type hook trigger + side effects (summary, reflection, principles) | Unit test, mock LLM |
| REST API | End-to-end CRUD + async message flow | Integration test, mock adapter |
| Legacy compat | Old API endpoints still work after rewiring | Integration test |
| Concurrency | Same-employee message serialization | Integration test |
| Frontend ChatPanel | Rendering, WebSocket message append, typing indicator | Manual verification |
| Regression | All items from regression checklist above | Integration test |
