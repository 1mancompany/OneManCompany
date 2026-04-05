---
tags: [architecture, agent, execution]
source: core/vessel.py, MEMORY.md
---

# Agent Loop (EmployeeManager)

The **EmployeeManager** is the sole execution entry point for all employee work. No persistent while-loop — tasks dispatch on-demand.

## Two Execution Paths

### 1. Employee Tasks
`_execute_task` → `_post_task_cleanup` → `_full_cleanup`

### 2. System Operations
`schedule_system_task` (watchdog, cron, nudges)

## Launcher Protocol

| Launcher | Hosting | How |
|----------|---------|-----|
| `LangChainExecutor` | company | Direct LangChain agent invocation |
| `ClaudeSessionExecutor` | self | Claude CLI with `--print` and MCP config |
| `SubprocessExecutor` | openclaw/remote | Subprocess or HTTP polling |

## Retrospective (复盘)

Only triggers when:
1. Task type is `project` (not `simple`)
2. EA flags `needs_retrospective=True`

## Progress Log

File-based at `{employee_dir}/progress.log`. Cross-task context — survives between tasks.

## Completion Flow (project)

1. Child node completes → `_on_child_complete` (queued, serial)
2. All children resolved → parent auto-promotes to FINISHED
3. `is_project_complete()` → True → create CEO_REQUEST confirm node
4. CEO confirms → `_full_cleanup` → archive

## Key Internals

- `_running_tasks`: employee_id → asyncio.Task
- `_completion_queue`: serial consumer for tree mutations (no concurrent modification)
- `schedule_node()` / `_schedule_next()`: task dispatch
- `is_idle()`: checks both `_running_tasks` and `_system_tasks`

## Snapshot & Recovery

- `save_task_queue()` / `restore_task_queue()` on restart
- `_trigger_graceful_restart()`: saves snapshot → `os.execv`
- Hot reload: Tier 1 (data), Tier 1.5 (frontend), Tier 2 (backend restart)

## Related
- [[Task Lifecycle]] — State machine for task phases
- [[Vessel System]] — Launcher types and hosting modes
- [[Idempotency Roadmap]] — Fixes for completion edge cases
