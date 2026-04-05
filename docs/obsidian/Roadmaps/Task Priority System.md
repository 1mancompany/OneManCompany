---
tags: [roadmap, planned, task]
source: memory/project_task_priority_design.md
status: planned
---

# Task Priority System (P1.2)

## Features
- Queue jumping (插队): insert task ahead of pending tasks
- Drag reorder: CEO drag-and-drop task queue reordering
- Cross-employee scheduling: move tasks between employees

## Constraints
- Only PENDING tasks can be reordered
- PROCESSING/HOLDING tasks stay in place
- Frontend interaction design deferred to later phase

## Related
- [[Task Lifecycle]] — Task status that affects reorderability
- [[Task Dispatch]] — How tasks are assigned
