# Frontend-Backend Communication Architecture

> First-principles design for extreme efficiency and extreme user experience.

## Two Principles

1. **Extreme Efficiency** — Minimum data transfer, zero latency, zero redundancy
2. **Extreme UX** — Instant feedback, always-fresh state, no loading spinners

## The Rule

**Every piece of data has exactly ONE path. No duplicates. No polling.**

If polling exists, it means the backend is missing a WS event. Polling is a design defect, not a feature.

---

## Channel Selection: Who Initiates?

| Initiator | Direction | Channel | Why |
|-----------|-----------|---------|-----|
| **User action** (click, submit, configure) | Frontend → Backend | **REST** | Request-response with confirmation |
| **System state change** (task done, employee working) | Backend → Frontend | **WS push** | Proactive, zero latency |

### REST — Confirmation Channel for User Intent

```
CEO clicks → REST POST/PUT/DELETE → Server processes → Returns result → Frontend updates
```

**Use REST for:**
- Submit task, send message, hire/fire, change settings
- Delete/archive/rename project
- Switch agent family, change model, set API key
- Any operation where the user needs atomic success/failure confirmation

**Characteristics:** User-triggered, needs confirmation, one-shot, idempotent preferred.

**REST is NEVER for:** Fetching live state that changes independently of user actions.

### WS — Real-time State Mirror

```
Backend state changes → WS pushes complete payload → Frontend renders directly
```

**Frontend NEVER polls for state. WS pushes it.**

---

## WS Push Layers

### Layer 1: Real-time Stream (millisecond latency)

Data that flows continuously during task execution.

| Event | Payload | Frontend Action |
|-------|---------|-----------------|
| `agent_log` | `{employee_id, task_id, log: {ts, type, content}}` | Append to xterm log viewer |
| `meeting_chat` | `{room_id, sender, text}` | Append to chat panel |
| `ceo_conversation` | `{node_id, sender, text}` | Append to inbox chat |
| `conversation_message` | `{conv_id, sender, text}` | Append to 1-on-1 chat |

**Rendering:** Direct append to DOM. No REST. No clear-and-rebuild.

### Layer 2: Event Notifications (second latency)

Discrete state transitions that the CEO needs to see immediately.

| Event | Payload | Frontend Action |
|-------|---------|-----------------|
| `agent_task_update` | `{employee_id, node: {full node dict}}` | Update task card in-place |
| `employee_status_change` | `{employee_id, status, task_summary}` | Update roster badge + office sprite |
| `cron_status_change` | `{employee_id, cron_name, running}` | Update cron list if viewing |
| `employee_hired` | `{employee data}` | Add to roster |
| `employee_fired` | `{employee_id}` | Remove from roster |
| `ceo_inbox_updated` | `{}` | Refresh inbox count + list |
| `candidates_ready` | `{full candidate data}` | Render selection modal |
| `onboarding_progress` | `{items, batch_id}` | Update progress modal |
| `ceo_report` | `{report data}` | Show report modal |
| `tree_update` | `{node data}` | Update tree view in-place |
| `background_task_update` | `{task_id, status, output_tail}` | Update bg task panel |

**Rendering:** In-place DOM update using payload data. No REST re-fetch.

### Layer 3: Bulk Sync (3-second tick)

Low-frequency aggregate data where individual change events are impractical.

| Dirty Category | What Changed | Frontend Action |
|----------------|-------------|-----------------|
| `employees` | Profile edits, avatar changes, dept reassignment | REST fetch `/api/employees` |
| `rooms` | Room config changes | REST fetch `/api/rooms` |
| `tools` | Tool registration | REST fetch `/api/tools` |
| `office_layout` | Desk position changes | REST fetch `/api/state` |

**Mechanism:** Backend marks category dirty → 3s tick pushes `state_changed: [categories]` → Frontend fetches only dirty categories.

**Layer 3 is the ONLY place where WS triggers REST.** All other layers push complete data.

---

## REST — When It IS Used

### 1. User-Initiated Write Operations

Every CEO action that modifies state:

```
POST   /api/ceo/task              — Submit task
POST   /api/employee/{id}/fire    — Fire employee
PUT    /api/employee/{id}/hosting — Switch agent family
DELETE /api/projects/{id}         — Delete project
POST   /api/ceo/inbox/{id}/message — Send inbox message
```

Response confirms success/failure. Frontend updates optimistically or waits for WS event.

### 2. Initial Data Load (One-Shot)

When a view opens for the first time, load historical data:

```
GET /api/bootstrap              — App startup (one-time)
GET /api/employee/{id}          — Open employee detail (one-time)
GET /api/employee/{id}/logs     — Load log history (one-time, then WS appends)
GET /api/projects/{id}          — Open project detail (one-time)
GET /api/ceo/inbox              — Load inbox (one-time, then WS updates)
```

After initial load, ALL updates come through WS. No re-fetching.

### 3. On-Demand Deep Data

Large payloads that are only needed on explicit user action:

```
GET /api/projects/{id}/tree         — View full task tree
GET /api/node/{id}/logs             — View specific node logs (trace viewer)
GET /api/employee/{id}/progress-log — View progress history
GET /api/meeting-minutes/{id}       — View meeting minutes
```

These are user-triggered reads, not state synchronization.

---

## Anti-Patterns (Forbidden)

### 1. Polling

```javascript
// ❌ FORBIDDEN
setInterval(() => fetch('/api/employee/{id}/taskboard'), 3000);

// ✅ CORRECT
ws.on('agent_task_update', (p) => updateTaskCard(p.node));
```

### 2. WS Event Triggers REST Fetch (Layer 1 & 2)

```javascript
// ❌ FORBIDDEN (except Layer 3 bulk sync)
ws.on('agent_log', () => fetch('/api/employee/{id}/logs'));

// ✅ CORRECT
ws.on('agent_log', (p) => xterm.appendLog(p.log));
```

### 3. Same Data, Two Paths

```javascript
// ❌ FORBIDDEN
ws.on('agent_task_update', () => fetchTaskBoard());  // path A
setInterval(() => fetchTaskBoard(), 3000);            // path B

// ✅ CORRECT — one path only
ws.on('agent_task_update', (p) => renderTaskCard(p.node));
```

### 4. Full Re-render on Incremental Update

```javascript
// ❌ FORBIDDEN
ws.on('agent_log', () => { clearTerminal(); renderAllLogs(); });

// ✅ CORRECT
ws.on('agent_log', (p) => appendSingleLog(p.log));
```

---

## Defects — All Fixed

| # | Defect | Was | Fix | Status |
|---|--------|-----|-----|--------|
| 1 | Employee taskboard | WS + 3s REST poll | In-place render from WS payload | **Fixed** |
| 2 | Employee logs | WS + 3s REST poll | One-time initial load, WS append | **Fixed** |
| 3 | Background task detail | WS + 3s poll | In-place update from WS payload | **Fixed** |
| 4 | Cron list | No WS event | Added `cron_status_change` Layer 2 event | **Fixed** |
| 5 | Task tree | 3s REST poll | Removed polling, WS `tree_update` only | **Fixed** |

---

## Summary

```
User action     → REST (write + confirm)
Initial load    → REST (one-time read)
Live state      → WS push (zero polling)
Bulk sync       → WS tick → REST fetch (Layer 3 only)
Polling         → Design defect. Fix the backend.
Duplicate paths → Critical defect. Fix immediately.
```

*Built with [OneManCompany](https://github.com/1mancompany/OneManCompany) — The AI Operating System for One-Person Companies*
