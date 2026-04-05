---
tags: [operations, onboarding, hr]
source: company/human_resource/workflows/onboarding_workflow.md, agents/onboarding.py
---

# Onboarding Workflow

4-phase process, parallel where possible.

## Phases

### Phase 1: Welcome & Introduction (HR)
- Welcome briefing
- Company mission, culture, values

### Phase 2: Meet the Team (depends on Phase 1)
- Team introductions
- New employee background sharing

### Phase 3: Tools & Environment (parallel with Phase 2, depends on Phase 1)
- COO walks through digital workspace
- Tools orientation

### Phase 4: Work Principles (depends on Phases 2 & 3)
- Performance expectations
- Probation milestones
- Review process

## Technical: execute_hire()

1. Allocate employee ID (00006+, skip EXEC_IDS 00002-00005)
2. Copy talent package to `employees/{id}/`
3. Generate nickname (花名) from pool
4. Create `work_principles.md` via LLM
5. Assign department by role
6. Calculate desk position by department layout
7. Register with [[Agent Loop|EmployeeManager]]

## Default Permissions

New employees get: `["company_file_access", "web_search"]`

## Related
- [[Hiring Workflow]] — How employees are found
- [[Vessel System]] — Employee directory structure
