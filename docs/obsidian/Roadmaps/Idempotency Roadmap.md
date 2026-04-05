---
tags: [roadmap, completed, idempotency]
source: memory/project_idempotency_roadmap.md
status: completed
---

# Idempotency & Anti-Stall Roadmap

**Status: All 3 batches complete.**

Identified 2026-03-25. Fixes for long-running projects getting permanently stuck.

## Batch 1 (高危 + 投入小) — PR #112 ✅
1. HOLDING timeout: `MAX_HOLD_SECONDS=1800` + `holding_timeout_sweep` cron (10m)
2. Cycle detection: `_has_cycle()` + dangling ref validation in `add_child()`
3. Idempotent completion: `_confirm_ceo_report` checks archived state
4. CEO report cleanup: except block pops `_pending_ceo_reports`
5. `save_tree_async` tracking: changed to `spawn_background()`
6. Startup recovery: adds `complete_project()` + `archive_project()`

## Batch 2 (中高危) — PR #119 ✅
7. Dep HOLDING cascade: `holding_timeout_sweep` calls `_trigger_dep_resolution`
8. Schedule zombie cleanup: `cleanup_orphaned_schedule()` + cron (10m)
9. EA HOLDING timeout: covered by Batch 1's `MAX_HOLD_SECONDS`
10. Completion consumer timeout: `asyncio.wait_for(timeout=60s)`
11. Restart orphan recovery: auto-finish parent-resolved COMPLETED children

## Batch 3 (防御性) — PR #120 ✅
12. Atomic tree save: `tempfile.mkstemp()` + `os.replace()`
13. Content file ordering: verified (no change needed)
14. Double-complete guard: FINISHED/ACCEPTED guard (PR #113)

## Related
- [[Task Lifecycle]] — State machine this fixes
- [[Agent Loop]] — Completion consumer
- [[Project Execution]] — Where stalls occurred
