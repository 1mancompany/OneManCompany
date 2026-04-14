# Product Management System

> First-principles design for product and version management in OMC.

## Core Question

Product management answers three questions:
1. **Where are we going?** — Product objective + Key Results (goal layer)
2. **What needs to be done?** — Issue backlog, prioritised (work layer)
3. **Where are we now?** — Issue status flow + Version releases (progress layer)

## Entity Model

### Product

The strategic container. Defines the "why" and measures success.

```yaml
id: prod_abc123
name: "OneManCompany Website"
slug: "omc-website"
status: planning | active | archived
description: "Best AI office simulator showcase"   # the objective
owner_id: "00004"                                  # product owner (employee)
current_version: "0.1.1"
key_results:
  - id: kr_001
    title: "DAU reaches 1000"
    target: 1000
    current: 150
    unit: "DAU"
    history: [...]   # append-only audit trail
```

**Storage**: `business/products/{slug}/product.yaml`

### Issue

The atomic unit of work. Everything flows through Issues.

```yaml
id: issue_def456
product_id: prod_abc123
title: "Homepage loads too slowly"
description: "LCP > 3s, optimise images and JS bundle"
status: backlog | planned | in_progress | in_review | done | released
priority: P0 | P1 | P2 | P3
labels: ["performance", "frontend"]
assignee_id: "00010"
story_points: 5              # estimation (Fibonacci: 1,2,3,5,8,13)
sprint: "2026-W15"           # time window label, not a separate entity
milestone_version: "0.2.0"   # target release version
linked_task_ids: []           # TaskNode IDs from execution
linked_issue_ids: []          # related issues
created_by: "ceo"
resolution: fixed | wontfix | duplicate | by_design
reopened_count: 0
history: [...]                # append-only audit trail
```

**Storage**: `business/products/{slug}/issues/{id}.yaml`

**Status lifecycle** (state machine):

```
backlog ──→ planned ──→ in_progress ──→ in_review ──→ done ──→ released
   ↑                        │                          │
   └────────────────────────┘ (blocked/deprioritised)  │
                                                       ↓
                                                   (reopen) → backlog
```

**Status derivation from TaskNode** (automatic, not manual):

| Condition | Issue Status |
|-----------|-------------|
| Not picked by any Project | `backlog` |
| Picked by Project, TaskNodes pending | `planned` |
| Any linked TaskNode in `processing` | `in_progress` |
| All linked TaskNodes `completed` | `in_review` |
| CEO accepts / auto-accepted | `done` |
| Included in a Version release | `released` |

### Version

An immutable release record. Auto-generated when CEO triggers a release.

```yaml
version: "0.2.0"
released_at: "2026-04-15T14:00:00Z"
changelog: |
  - Fix homepage loading speed (#issue_def456)
  - Add user registration flow (#issue_xyz789)
resolved_issue_ids: [issue_def456, issue_xyz789]
project_ids: [proj_001]
```

**Storage**: `business/products/{slug}/versions/{semver}.yaml`

### Project (existing, extended)

Project is an execution batch. It picks Issues from the backlog and creates a [[Task Lifecycle FSM|TaskTree]] to execute them.

```yaml
project_id: "a1b2c3d4e5f6"
name: "Sprint 15 — Performance"
product_id: "prod_abc123"          # optional: linked product
issue_ids: [issue_def456, issue_ghi789]  # which issues this project addresses
status: active | archived
```

**Key relationship**: Project does NOT own Issues. It references them. One Issue can be addressed across multiple Projects. One Project can address multiple Issues.

## Relationship Diagram

```
Product (optional)
  ├── KR[] ← progress auto-derived from Issue completion
  ├── Issue[] (backlog) ← the atomic work unit
  │     ├── status: derived from linked TaskNode states
  │     ├── sprint: time window label
  │     └── history[]: append-only audit trail
  └── Version[] ← groups done Issues into releases

Project (execution batch)
  ├── product_id (optional → links to Product)
  ├── issue_ids[] (optional → which Issues it solves)
  └── TaskTree → TaskNode[] (actual agent execution)

Standalone Project (no Product)
  └── TaskTree → TaskNode[] (CEO ad-hoc tasks, unchanged)
```

**Issue ↔ TaskNode status sync (automatic)**:
- TaskNode completes → check all linked TaskNodes → derive Issue status
- Issue status changes → append to history
- All Issues in a Project done → eligible for Version release

## Sprint

Sprint is NOT a separate entity. It is a **label** on Issues (`sprint: "2026-W15"`).

Why: separate Sprint entities add management overhead without value in a small-team AI system. The time window label gives grouping and filtering without lifecycle management.

Filtering: "Show me all Issues in Sprint 2026-W15" = `list_issues(sprint="2026-W15")`.

## History (Audit Trail)

Every Issue and KR carries an append-only `history` list (capped at 100 entries):

```yaml
history:
  - timestamp: "2026-04-14T10:00:00"
    field: "status"
    old_value: "backlog"
    new_value: "in_progress"
    changed_by: "00010"
  - timestamp: "2026-04-14T11:30:00"
    field: "priority"
    old_value: "P2"
    new_value: "P0"
    changed_by: "ceo"
```

History is embedded in the entity's YAML file, not a separate file. This keeps the "disk is single source of truth" principle: one file = one entity = complete state + history.

## Periodic Health Check

A [[Snapshot and Hot Reload|system cron]] (`product_health_check`, every 30 minutes) runs for all active Products:

1. **Issue status sync** — for each in-progress Issue, check linked TaskNode states, derive correct Issue status, auto-close if all done
2. **KR progress check** — for KRs below 50% progress, auto-create a P2 Issue alert (skip if alert already exists)
3. **Stale issue detection** — Issues in `in_progress` for >7 days without TaskNode activity get flagged

## Agent Tools

Agents interact with the Product system through [[MCP Tool Bridge|LangChain tools]]:

| Tool | What it does |
|------|-------------|
| `create_product_tool` | Create Product with OKR |
| `create_product_issue` | Create Issue in backlog |
| `update_product_issue` | Update Issue fields |
| `close_product_issue` | Close Issue with resolution |
| `get_product_context_tool` | Get Product objective + KR + active Issues |
| `list_product_issues_tool` | List/filter Issues |
| `update_kr_progress_tool` | Update KR current value |

## Event-Driven Triggers

| Event | Trigger | Action |
|-------|---------|--------|
| `ISSUE_CREATED` (P0/P1) | Auto | Create Project, assign to EA |
| `ISSUE_ASSIGNED` | CEO assigns | Create Project for assignee |
| `AGENT_DONE` (with product) | TaskNode completes | Close linked Issues, release Version |
| `KR_UPDATED` | Periodic check | Create P2 Issue if KR < 50% |
| `VERSION_RELEASED` | Project completion | Broadcast to frontend |

## Context Injection

When an agent executes a task linked to a Product (via Project's `product_id`), [[Architecture Walkthrough|vessel.py]] injects product context into the task prompt:

```
=== Product: OMC Website (v0.1.1) ===
Objective: Best AI office simulator showcase

Key Results:
  - DAU reaches 1000: 150/1000 DAU (15%)

Active Issues (2):
  - [P0] Homepage loads too slowly (issue_def456)
  - [P1] SEO optimisation (issue_ghi789)
=== End Product Context ===
```

This gives agents awareness of the product goal and outstanding issues while executing tasks.

## Frontend

### Left panel (Products view)
Product-grouped view replacing the old Projects list:
```
Product Name (v0.1.1) [...]
  ▸ OKR Progress
  ▸ Issues (3 open)
  ▸ Projects (2)
Standalone (3)
  project cards...
```

### Product detail modal
Three tabs:
- **Overview** — inline-editable name, objective, owner, status, KR list with progress bars, version history
- **Issues** — filterable list, create/close/reopen, expandable cards with history, inline-edit all fields
- **Projects** — linked projects list, click to open project detail

### CEO task input
Dropdown to select Product when submitting tasks. Links the created Project to the Product.

## Design Principles

1. **Issue is the atomic work unit** — everything traces back to Issues
2. **Status derived, not manually set** — Issue status auto-computed from TaskNode states
3. **Product is optional** — standalone Projects work unchanged
4. **Sprint is a label, not an entity** — avoids overhead
5. **History is embedded** — one file = complete entity state
6. **Disk is truth** — no in-memory caching, YAML on disk
7. **No duplicate systems** — Issue lifecycle does not duplicate TaskNode lifecycle; they connect via `linked_task_ids`

## Related
- [[Task Lifecycle FSM]] — TaskNode state machine that drives Issue status
- [[DAG Task Execution]] — how TaskTrees execute within Projects
- [[Architecture Walkthrough]] — end-to-end request lifecycle
- [[Repo Architecture]] — file structure and tech stack
