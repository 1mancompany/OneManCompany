---
autoload: false
description: "Task lifecycle state machine — states, transitions, and task tree model. Use when you need to understand task states, dispatch subtasks, or review completions. Do NOT use for domain-specific procedures — this only covers the universal state machine."
---

## Task Lifecycle States
Every task in the system follows this state machine:

| State | Meaning |
|-------|---------|
| pending | Created, waiting to be processed |
| processing | Actively being executed by an employee |
| holding | Waiting for subtasks to complete or external input |
| completed | Employee finished execution, awaiting supervisor review |
| accepted | Supervisor approved the deliverable |
| finished | Fully done, archived |
| failed | Execution failed or supervisor rejected |
| blocked | Dependency task failed, cannot proceed |
| cancelled | Cancelled |

State flow:
  pending → processing → completed → accepted → finished
                ↕ holding (pause/resume)
  completed → failed (rejection) → processing (retry)

Key distinctions:
- completed = employee says "I'm done" (awaiting review)
- accepted = supervisor says "looks good" (deliverable approved)
- Only accepted/finished unblock downstream dependent tasks

Task tree model:
- Parent tasks dispatch subtasks to employees via dispatch_child()
- When a subtask completes, the system automatically wakes the parent task for review
- Parent tasks review each subtask via accept_child() / reject_child()
- All subtasks accepted → parent task auto-completes and reports upward
