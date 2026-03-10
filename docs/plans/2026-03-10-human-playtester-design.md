# Human Playtester Talent Design

## Goal

A company-hosted LangChain agent that bridges task dispatch to a human via Gmail. Sends task email, enters HOLDING, polls for reply, and feeds reply content back upstream as task result.

## Architecture

### Talent Files

```
talent_market/talents/human_playtester/
├── profile.yaml          # company-hosted, OpenRouter
├── manifest.json         # Frontend config: target_email, polling_interval
├── skills/
│   └── playtester/SKILL.md  # Role prompt: email formatting, reply parsing
└── tools/
    └── manifest.yaml     # bash, read, write, ls + gmail tools
```

### HOLDING Mechanism (vessel.py)

Agent returns `__HOLDING:key=value,...` prefix in result. `_execute_task` detects this:
- Sets task to HOLDING instead of COMPLETE
- Parses metadata (thread_id, etc.)
- Starts reply poller cron

Generic mechanism — any agent can use it.

### Reply Polling (cron)

`_setup_reply_poller(employee_id, task)`:
1. Parse `thread_id` from task result
2. Start cron: `start_cron(employee_id, f"reply_{task.id}", interval, description)`
3. Cron dispatches `[reply_poll] Check Gmail thread {thread_id} for task {task_id}`

### Resume Mechanism

New `resume_held_task(employee_id, task_id, result)` on EmployeeManager:
- Updates held task's result and status to COMPLETE
- Triggers task tree callback (notifies parent)
- Persists to disk

Exposed as a base tool `resume_held_task(task_id, result)` callable by agent.

### Execution Flow

```
Task received → read target_email config → gmail_send → return "__HOLDING:thread_id=abc"
  → executor detects → HOLDING + start 1m cron

Cron fires → "[reply_poll]" task → gmail_read_thread → no reply → "no reply yet"
  ... repeat ...
Cron fires → reply found → stop_cron + resume_held_task(task_id, reply) → COMPLETE → upstream notified
```

### Frontend Config (manifest.json)

```json
{
  "id": "human_bridge",
  "title": "Human Bridge",
  "fields": [
    {"key": "target_email", "type": "text", "label": "Human Email Address"},
    {"key": "polling_interval", "type": "text", "label": "Reply Polling Interval", "default": "1m"}
  ]
}
```

### Reused Systems

- Gmail OAuth (add employee ID to gmail tool.yaml allowed_users)
- Task tree callbacks (COMPLETE triggers parent wake)
- Cron system (automation.py)
- Manifest-driven frontend settings
- Task persistence (write-through)

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Hosting | company (LangChain) | Simple logic, fast startup |
| HOLDING pattern | `__HOLDING:` result prefix | Generic, no agent subclass needed |
| Polling | Cron with configurable interval, default 1m | Reuses existing automation system |
| Resume | EmployeeManager.resume_held_task() | Clean task tree integration |
