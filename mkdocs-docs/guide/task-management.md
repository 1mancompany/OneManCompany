---
description: "How CEO task delegation, quality gates, and the task lifecycle work in OneManCompany."
---

# Task Management

OneManCompany uses a hierarchical task system with dependency tracking, quality gates, and CEO approval at every level.

## Task Types

| Type | Description | Approval |
| --- | --- | --- |
| **Simple** | Single-step tasks | Auto-accepted on completion |
| **Project** | Multi-step with subtasks | Requires manual acceptance + retrospective |

## Task Lifecycle

Every task follows a unified status flow:

```
pending → processing ⇄ holding → completed → accepted → finished
```

- **pending** — Task created, waiting to be picked up
- **processing** — Employee is actively working
- **holding** — Paused, waiting for input or dependency
- **completed** — Work done, awaiting review
- **accepted** — CEO/manager approved the output
- **finished** — Fully closed (after retrospective for projects)

Error states:

- **failed** — Retryable, can go back to processing
- **blocked** — Dependency failed
- **cancelled** — Terminated

## Creating Tasks

As CEO, type your request in the console:

> "Design a landing page for our new product"

The EA routes it to the right person. For complex requests, the COO breaks it into a task tree with dependencies.

## Task Tree

Projects are automatically decomposed into subtask hierarchies:

```
Build a puzzle game (Project)
├── Design game mechanics (Simple)
├── Create art assets (Simple)
│   └── blocked by: Design game mechanics
├── Implement game logic (Simple)
│   └── blocked by: Design game mechanics
├── QA testing (Simple)
│   └── blocked by: Implement game logic, Create art assets
└── Polish and ship (Simple)
    └── blocked by: QA testing
```

Dependencies are tracked automatically — subtasks unblock when their dependencies reach `accepted` or `finished`.

## Reviewing Work

When an employee completes a task:

1. You receive a notification in the CEO console
2. Review the deliverable and any file changes
3. **Accept** to approve, or **reject** with feedback for revision
4. Rejected tasks go back to the employee for iteration

## Quality Gates

Every level has a quality gate:

- **Employee** → COO reviews subtask output
- **COO** → EA reviews project-level deliverable
- **EA** → CEO gives final approval

## Retrospectives

Project-type tasks trigger an automatic retrospective after acceptance:

- What went well
- What could improve
- Lessons learned

These insights are distilled into employee work principles and company knowledge base, driving continuous improvement.
