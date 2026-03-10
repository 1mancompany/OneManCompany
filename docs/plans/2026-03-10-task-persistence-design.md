# Task Persistence Design

## Goal

Persist employee tasks to disk (per-employee `tasks/` folder) with write-through on every state change. On any restart (graceful or crash), fully restore task state and auto-resume unfinished tasks.

## Architecture

### Storage Structure

```
employees/{id}/tasks/
├── {task_id}.yaml        # active tasks (PENDING/PROCESSING/HOLDING/COMPLETE)
└── archive/
    └── {task_id}.yaml    # terminal tasks (FINISHED/FAILED/BLOCKED/CANCELLED)
```

Each file is a full YAML serialization of `AgentTask`.

### Write-Through

Every task state change immediately writes to disk:

- `push_task()` → create `{task_id}.yaml`
- Status transitions → update file in place
- Terminal state reached → move file to `archive/`

Implementation: a single `_persist_task(employee_id, task)` function called at every state change point in `EmployeeManager`.

### Startup Recovery

```
lifespan startup:
  1. Scan all employees/{id}/tasks/*.yaml (skip archive/)
  2. Deserialize to AgentTask
  3. PROCESSING tasks → reset to PENDING (can't resume mid-execution)
  4. Push into employee's AgentTaskBoard
  5. Call _schedule_next() to auto-start execution
```

### Removal of Old System

- Remove `save_task_queue()` / `restore_task_queue()`
- Remove `company/.task_queue.json` usage
- Graceful restart no longer needs separate task queue save

### Snapshot System

Unchanged. `_CompanyStateSnapshot` handles frontend display state and activity log — orthogonal to task execution recovery.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Persistence strategy | Write-through | Maximum safety, no data loss |
| Old task queue system | Remove | New system fully replaces it |
| Terminal tasks | Archive subfolder | Queryable history without clutter |
| Interrupted PROCESSING tasks | Reset to PENDING | LangChain agents can't resume mid-execution |
