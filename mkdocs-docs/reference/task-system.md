# Task Status System

All tasks share a unified `TaskPhase` state machine:

```
pending → processing ⇄ holding → completed → accepted → finished
              ↓                       ↓
           failed ──(retry)──→ processing

pending/holding → blocked (dependency failed)
any non-terminal → cancelled
```

| Status       | Meaning                                   |
| ------------ | ----------------------------------------- |
| `pending`    | Created, waiting to start                 |
| `processing` | Agent actively executing                  |
| `holding`    | Waiting for child tasks / CEO response    |
| `completed`  | Done, awaiting supervisor review          |
| `accepted`   | Supervisor approved (unblocks dependents) |
| `finished`   | Archived after retrospective              |
| `failed`     | Execution failed (retryable)              |
| `blocked`    | Dependency failed                         |
| `cancelled`  | Cancelled by CEO or supervisor            |

**Simple vs Project tasks** use the same state machine. The difference is auto-skip:

- **Simple**: `completed` → auto `accepted` → auto `finished`
- **Project**: `completed` → manual review → EA retrospective → `finished`

All transitions enforced through `transition()` in `task_lifecycle.py`.
