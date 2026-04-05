---
tags: [architecture, task, state-machine]
source: docs/task-system.md, core/task_lifecycle.py
---

# Task Lifecycle

All tasks share a unified `TaskPhase` state machine. No separate machines for simple vs project.

## State Machine

```
pending → processing ⇄ holding → completed → accepted → finished
              ↓                       ↓
           failed ──(retry)──→ processing

pending/holding → blocked (dependency failed)
any non-terminal → cancelled
```

## Status Definitions

| Status | Meaning |
|--------|---------|
| `pending` | Created, waiting to start |
| `processing` | Agent actively executing |
| `holding` | Waiting for child tasks / CEO response |
| `completed` | Done, awaiting supervisor review |
| `accepted` | Supervisor approved (unblocks dependents) |
| `finished` | Archived after retrospective |
| `failed` | Execution failed (retryable) |
| `blocked` | Dependency failed |
| `cancelled` | Cancelled by CEO or supervisor |

## Simple vs Project

Same state machine, different auto-skip behavior:

- **Simple**: `completed` → auto `accepted` → auto `finished`
- **Project**: `completed` → manual `accept_child()` → EA retrospective → `finished`

## Key Rules

1. All transitions MUST go through `transition()` — direct assignment banned
2. `depends_on` field for dependency graph
3. `accepted` / `finished` unblock dependents
4. `fail_strategy`: `"block"` or `"continue"` for dependency handling
5. TASK_LIFECYCLE_DOC injected into all employee system prompts

## Node Types

| Type | Purpose |
|------|---------|
| `TASK` | Normal work task |
| `CEO_PROMPT` | CEO's original instruction (root) |
| `CEO_REQUEST` | Request for CEO input/approval |
| `REVIEW` | Supervisor review of child work |
| `WATCHDOG_NUDGE` | System notification to parent |

## Related
- [[Agent Loop]] — How EmployeeManager processes tasks
- [[Project Execution]] — Full project flow with task trees
- [[Idempotency Roadmap]] — Fixes for stuck tasks
