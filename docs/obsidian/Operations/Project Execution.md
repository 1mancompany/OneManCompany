---
tags: [operations, project, workflow]
source: company/operations/sops/project_execution_sop.md, company/human_resource/workflows/project_intake_workflow.md
---

# Project Execution

## Intake → Delivery Flow

1. **CEO Instruction** → EA creates `CEO_PROMPT` root node
2. **EA Dispatch** → EA analyzes, creates task tree, assigns to employees
3. **Employee Execution** → Each employee works their subtask via [[Agent Loop]]
4. **Incremental Review** → Completed children trigger immediate supervisor review (no deadlock)
5. **Parent Auto-Complete** → All children accepted → parent auto-promotes
6. **CEO Confirmation** → `CEO_REQUEST` node for final approval
7. **Retrospective** → EA runs retrospective (project tasks only)
8. **Archive** → Project archived to `project_archive/`

## Task Tree Structure

```
CEO_PROMPT (root, 00001)
  └── TASK (EA, 00002) — "Build feature X"
        ├── TASK (Engineer, 00010) — "Backend API"
        ├── TASK (Designer, 00011) — "UI mockups"
        ├── REVIEW (EA, 00002) — auto-generated
        └── CEO_REQUEST (CEO, 00001) — "Confirm completion"
```

## Key Mechanisms

| Mechanism | Purpose |
|-----------|---------|
| Dependency graph | `depends_on` field, `accepted`/`finished` unblocks |
| Incremental review | Prevents dep-chain deadlocks (A→B) |
| Auto-accept orphans | Completed nodes with resolved parents |
| HOLDING timeout | 30 min max, cron sweeps every 10 min |
| Atomic tree save | `tempfile.mkstemp()` + `os.replace()` |

## Related
- [[Task Lifecycle]] — State machine details
- [[Task Dispatch]] — How tasks are assigned
- [[Agent Loop]] — Execution internals
- [[Idempotency Roadmap]] — Edge case fixes
