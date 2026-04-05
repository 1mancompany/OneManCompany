---
tags: [architecture, core]
source: docs/architecture.md
---

# System Overview

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12+ / UV, FastAPI + WebSocket, LangChain (`create_react_agent`) |
| LLM | OpenRouter API (configurable per employee), Anthropic API (OAuth/API key) |
| Frontend | Vanilla JS + Canvas 2D pixel art (zero build tools) |
| Infra | Docker sandbox, MCP server, Watchdog hot-reload |
| Data | YAML profiles + Markdown workflows + JSON archives (git-friendly, no database) |

## Key Modules

| Module | Path | Responsibility |
|--------|------|---------------|
| Config | `core/config.py` | Settings, paths, env loading |
| State | `core/state.py` | CompanyState singleton |
| Events | `core/events.py` | EventBus pub/sub |
| Store | `core/store.py` | Disk read/write, dirty tracking |
| Vessel | `core/vessel.py` | EmployeeManager, task execution |
| Routes | `api/routes.py` | REST + WebSocket endpoints |
| Agents | `agents/` | HR, COO, EA, common tools |

## Organization

```
CEO (Human) → C-Suite Executives (Lv.4, permanent)
                ├── EA  — CEO quality gate
                ├── HR  — Hiring, reviews, promotions
                ├── COO — Operations, assets, acceptance
                └── CSO — Sales, clients
              → Departments (Lv.1-3, dynamically hired)
                ├── Engineering (Engineer/DevOps/QA)
                ├── Design
                ├── Analytics
                └── Marketing
```

## Data Flow

1. CEO sends commands via browser console
2. EA dispatches to appropriate executive
3. Executive creates [[Task Lifecycle|tasks]] and assigns to employees
4. Employees execute via [[Agent Loop|EmployeeManager]]
5. Results flow back up: employee → COO review → EA → CEO
6. Frontend renders via 3-second [[Disk as Single Truth|sync frame]]

## Related
- [[Vessel System]] — Employee = Talent + Vessel
- [[Design Principles]] — Architectural constraints
- [[MCP Tool Bridge]] — Self-hosted integration
