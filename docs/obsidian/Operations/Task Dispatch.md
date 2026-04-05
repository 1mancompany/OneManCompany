---
tags: [operations, task, dispatch]
source: company/operations/sops/task_dispatch_and_acceptance_sop.md, company/operations/sops/ea_dispatch_authority_sop.md
---

# Task Dispatch

## Dispatch Authority

| Role | Can dispatch to | Scope |
|------|----------------|-------|
| CEO | EA, any executive | Direct instruction |
| EA | Any employee | Project decomposition |
| COO | Department employees | Operational tasks |
| HR | New hires | Onboarding tasks |

## Assignment Flow

1. EA analyzes CEO instruction
2. EA decomposes into subtasks with acceptance criteria
3. Each subtask assigned to best-fit employee by role/skills
4. Employee's task queue receives the task
5. [[Agent Loop|EmployeeManager]] dispatches when employee is idle

## Acceptance

- **Simple tasks**: auto-accepted on completion
- **Project tasks**: supervisor reviews → `accept_child()` or `reject_child()`
- **Rejected tasks**: can retry (re-dispatch) or abandon

## Common Tools

Every agent has:
- `list_colleagues()` — find employees by role/department
- `pull_meeting()` (拉人对齐) — create ad-hoc meeting

## Related
- [[Project Execution]] — Full project flow
- [[Task Lifecycle]] — Status transitions
- [[COO Role]] — COO's dispatch authority
