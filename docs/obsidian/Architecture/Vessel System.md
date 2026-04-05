---
tags: [architecture, vessel, talent]
source: docs/vessel-system.md
---

# Vessel System

**Core metaphor**: Talent (pilot) + Vessel (mech) = Employee (active unit)

## Employee Directory

```
employees/{id}/
├── profile.yaml          # Identity: name, role, department, llm_model
├── vessel/
│   ├── vessel.yaml       # DNA: runner, hooks, limits, capabilities
│   └── prompt_sections/  # Prompt fragments
├── skills/               # Talent skills (markdown)
└── progress.log          # Working memory (cross-task context)
```

## Vessel Harness — 6 Protocols

| Harness | Responsibility |
|---------|---------------|
| `ExecutionHarness` | Executor protocol (execute / is_ready) |
| `TaskHarness` | Task queue (push / get_next / cancel) |
| `EventHarness` | Logging and event publishing |
| `StorageHarness` | Progress log and history persistence |
| `ContextHarness` | Prompt / context assembly |
| `LifecycleHarness` | Pre/post task hook invocation |

## Hosting Modes

| Mode | Runner | Managed by |
|------|--------|-----------|
| **company** | LangChainExecutor | Platform (auto start/stop) |
| **self** | ClaudeSessionExecutor | User (Claude CLI) |
| **openclaw** | SubprocessExecutor | Platform (graph engine) |
| **remote** | HTTP polling | External server |

## Talent → Employee Flow

1. HR searches [[Hiring Workflow|Talent Market]]
2. CEO selects candidate from shortlist
3. `execute_hire()` allocates employee ID (≥00006, skip EXEC_IDS)
4. Copies talent package to `employees/{id}/`
5. Generates nickname, work_principles, department assignment
6. Registers with [[Agent Loop|EmployeeManager]]

## Profile Fields

`id`, `name`, `nickname`(花名), `level`, `department`, `role`, `skills`, `llm_model`, `temperature`, `api_provider`, `hosting`, `auth_method`, `tool_permissions`

## Related
- [[System Overview]] — Where vessels fit in the architecture
- [[Agent Loop]] — How EmployeeManager dispatches to vessels
- [[MCP Tool Bridge]] — Tool access for self-hosted vessels
