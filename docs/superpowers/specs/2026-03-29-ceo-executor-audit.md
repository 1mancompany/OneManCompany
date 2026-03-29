# CEO Interaction Audit — Complete Pathway Map

> Generated 2026-03-29. Reference doc for CeoExecutor unified conversation redesign.
> Check off each item as it's migrated to the new system.

## OUTPUT (CEO -> System) — 5 paths

| # | Path | Entry Point | Current Implementation | Migrated? |
|---|------|-------------|------------------------|-----------|
| 1 | **Submit new task** | `POST /api/ceo/task` (routes.py:465-525) | Creates CEO_PROMPT + EA child | [ ] |
| 2 | **Reply to CEO_REQUEST** | `POST /api/ceo/inbox/{id}/open` -> `/message` -> `/complete` (routes.py:5926-6230) | ConversationSession dialog | [ ] |
| 3 | **Confirm project completion** | `POST /api/ceo/report/{pid}/confirm` (routes.py:6303-6335) | `_confirm_ceo_report` | [ ] |
| 4 | **Follow-up instruction** | `POST /api/iteration/{id}/continue` (routes.py:2860-2905) | Creates CEO_FOLLOWUP node | [ ] |
| 5 | **1-on-1 meeting** | `POST /api/conversation/create` type=oneonone | conversation_adapters | [ ] |

## INPUT (System -> CEO) — 4 paths

| # | Path | Trigger | Current Implementation | Migrated? |
|---|------|---------|------------------------|-----------|
| A | **CEO_INBOX_UPDATED** | dispatch_child(CEO_ID) / circuit breaker | WebSocket event | [ ] |
| B | **CEO_REPORT** | `_request_ceo_confirmation` (vessel.py:2608-2677) | WebSocket event + 120s auto-confirm | [ ] |
| C | **CEO_TASK_SUBMITTED** | After CEO submits task | WebSocket event (activity log) | [ ] |
| D | **EA auto-reply** | CEO response timeout | `_ea_auto_reply` decides on behalf of CEO | [ ] |

## Special-case code to refactor

| Location | Special Logic | Migrated? |
|----------|---------------|-----------|
| `tree_tools.py:165-180` | `dispatch_child(CEO_ID)` bypasses schedule_node, uses dedicated path | [ ] |
| `vessel.py:2608-2677` | `_request_ceo_confirmation` + 120s auto-confirm timer | [ ] |
| `vessel.py:2679-2690` | `_ceo_report_auto_confirm` timer task | [ ] |
| `vessel.py:2706-2750` | `_confirm_ceo_report` advances CEO_PROMPT | [ ] |
| `ceo_conversation.py` (entire module) | Independent ConversationSession system | [ ] |
| `routes.py` 6 CEO endpoints | inbox CRUD + report confirm | [ ] |
| `conversation_hooks.py:63-77` | `_close_ceo_inbox` hook | [ ] |

## EventTypes to consolidate

| EventType | File:Line | Current Use | Keep/Replace? |
|-----------|-----------|-------------|---------------|
| `CEO_TASK_SUBMITTED` | models.py:93 | Activity log when CEO submits task | [ ] |
| `CEO_INBOX_UPDATED` | models.py:94 | Inbox refresh when CEO_REQUEST created | [ ] |
| `CEO_REPORT` | models.py:95 | Project completion report | [ ] |

## NodeTypes (keep as-is, handled by CeoExecutor)

| NodeType | Meaning | CeoExecutor behavior |
|----------|---------|---------------------|
| `CEO_PROMPT` | Root container when CEO submits task | Skip execution (container) |
| `CEO_FOLLOWUP` | CEO follow-up in existing tree | Route as new instruction |
| `CEO_REQUEST` | Employee escalation to CEO | FIFO queue, wait for CEO reply |

## No changes needed

| Location | Reason |
|----------|--------|
| 1-on-1 meetings (routine.py) | Independent flow, not task-based |
| CEO roster display (frontend) | Pure UI |
| task-tree.js CEO node rendering | Display only |
| FOUNDING_IDS config | Unchanged |
| layout.py CEO_ID import | Color coding only |
| agents/base.py:513 CEO escalation prompt | Agents still use dispatch_child("00001") |

## Key files to modify

| File | Changes |
|------|---------|
| `src/onemancompany/core/vessel.py` | Add CeoExecutor; register at startup; remove auto-confirm timer path |
| `src/onemancompany/core/ceo_broker.py` (new) | FIFO queue, routing logic, Future resolution |
| `src/onemancompany/core/ceo_conversation.py` | Refactor or replace — ConversationSession merges into CeoBroker |
| `src/onemancompany/agents/tree_tools.py` | dispatch_child(CEO_ID) uses normal schedule_node path |
| `src/onemancompany/api/routes.py` | Unified CEO conversation endpoint; deprecate separate inbox/report endpoints |
| `src/onemancompany/core/task_persistence.py` | Recovery rebuilds CeoBroker pending queue from trees |
| `frontend/app.js` | TUI conversation view |
