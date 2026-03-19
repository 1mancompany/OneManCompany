---
description: "Technical architecture overview of OneManCompany including the Vessel + Talent system, org chart, and agent communication."
---

# Architecture

> Technical reference for developers and contributors.

## Tech Stack

- **Backend**: Python 3.12+ / UV, FastAPI + WebSocket, LangChain (`create_react_agent`)
- **LLM**: OpenRouter API (configurable per employee), Anthropic API (OAuth/API key)
- **Frontend**: Vanilla JS + Canvas 2D pixel art (zero build tools)
- **Infra**: Docker sandbox, MCP server, Watchdog hot-reload
- **Data**: YAML profiles + Markdown workflows + JSON project archives (git-friendly, no database)

## Architecture Overview

```mermaid
graph TB
    CEO["👤 CEO (Human)<br>Browser — CEO Console"]

    subgraph ExecFloor["C-Suite — Founding Executives (Lv.4, Permanent)"]
        HR["HR<br>Hiring / Reviews / Promotions"]
        COO["COO<br>Operations / Assets / Acceptance"]
        EA["EA<br>CEO Quality Gate"]
        CSO["CSO<br>Sales / Clients"]
    end

    subgraph Departments["Departments — Dynamically Hired Employees (Lv.1-3)"]
        Eng["Engineering<br>Engineer / DevOps / QA"]
        Design["Design<br>Designer"]
        Analytics["Analytics<br>Analyst"]
        Marketing["Marketing<br>Marketing"]
        General["General<br>Other Roles"]
    end

    subgraph TalentMkt["Talent Market — Plugin Store"]
        TP["Talent Packages<br>profile / skills / tools<br>functions / agent"]
        MCP["Boss Online<br>MCP Recruitment Server"]
    end

    subgraph CompanyAssets["Company Assets"]
        Tools["Tools & Equipment<br>company/assets/tools/"]
        Rooms["Meeting Rooms<br>company/assets/rooms/"]
        Knowledge["Knowledge Base<br>workflows / SOPs / culture"]
    end

    CEO -->|"commands / approvals"| ExecFloor
    HR -->|"recruit"| TalentMkt
    HR -->|"onboard / offboard"| Departments
    COO -->|"dispatch / accept"| Departments
    COO -->|"register / manage"| CompanyAssets
    EA -->|"review quality"| COO
    CSO -->|"coordinate"| Departments
    Departments -->|"use tools"| CompanyAssets
    TalentMkt -.->|"install talent"| Departments
```

## System Layers

```mermaid
graph TB
    subgraph Presentation["Presentation — Frontend"]
        HTML["index.html<br>3-Column Layout"]
        OfficeJS["office.js<br>Canvas 2D Pixel Art"]
        AppJS["app.js<br>CEO Console + WS Client"]
    end

    subgraph Gateway["Gateway — FastAPI"]
        REST["routes.py<br>/task /hire /fire /state"]
        WSS["websocket.py<br>/ws Real-time Push"]
    end

    subgraph AgentLayer["Agent Layer — LangChain Agents"]
        Runners["Agent Runners<br>BaseAgentRunner / EmployeeAgent / Custom"]
        Founding["Founding Agents<br>HR / COO / EA / CSO"]
        Infra["Agent Infrastructure<br>PromptBuilder / common_tools / onboarding"]
    end

    subgraph CoreEngine["Core Engine"]
        EM["EmployeeManager<br>on-demand task dispatch"]
        EB["EventBus<br>async pub/sub"]
        CS["CompanyState<br>singleton + hot-reload"]
        PA["ProjectArchive<br>lifecycle / cost"]
    end

    subgraph VesselLayer["Vessel Execution Layer"]
        LC["LangChainExecutor<br>company-hosted"]
        CL["ClaudeSessionExecutor<br>self-hosted (CLI)"]
        SL["ScriptExecutor<br>bash scripts"]
    end

    subgraph Persistence["Persistence — company/"]
        EMP["employees/ — profiles, skills, agents"]
        AST["assets/ — tools, rooms"]
        BIZ["business/ — workflows, projects"]
    end

    AppJS -->|"HTTP + WS"| Gateway
    REST --> EM
    WSS --> EB
    EM -->|"dispatch"| VesselLayer
    EM --> EB
    EB --> WSS
    VesselLayer --> Runners
    Founding --> Runners
    Runners --> Infra
    CoreEngine --> Persistence
```

## Operating Modes

### Mode A: CEO-Driven — Internal Operations

```mermaid
sequenceDiagram
    actor CEO
    participant FE as Frontend
    participant API as routes.py
    participant EM as EmployeeManager
    participant Officer as COO / HR
    participant Worker as Employee Agent
    participant EA as EA Agent

    CEO->>FE: Submit task (CEO Console)
    FE->>API: POST /task
    API->>EM: push_task(officer_id, task)
    EM->>Officer: execute via Executor
    Officer->>EM: dispatch_task(worker, subtask)
    EM->>Worker: execute via Executor
    Worker->>EM: task completed
    EM->>Officer: push acceptance review
    Officer->>Officer: verify acceptance criteria
    EM->>EA: push EA review
    EA->>EA: final quality check
    EA-->>CEO: notify completion
```

### Mode B: Internet Task Orders — External Services (Planned)

External clients submit tasks via Sales API → CSO evaluates → internal team delivers. The company operates as a service provider.

## Module Index

| Layer        | Module               | Role                                             |
| ------------ | -------------------- | ------------------------------------------------ |
| **Entry**    | `main.py`            | FastAPI app, lifespan                            |
| **API**      | `routes.py`          | REST endpoints                                   |
| **API**      | `websocket.py`       | WS real-time push                                |
| **Agents**   | `base.py`            | `BaseAgentRunner`, `EmployeeAgent`               |
| **Agents**   | `hr_agent.py`        | Hiring, reviews, promotions                      |
| **Agents**   | `coo_agent.py`       | Operations, assets, acceptance                   |
| **Agents**   | `ea_agent.py`        | CEO quality gate                                 |
| **Agents**   | `cso_agent.py`       | Sales pipeline                                   |
| **Agents**   | `common_tools.py`    | Shared tools (dispatch, meeting, file ops)       |
| **Agents**   | `prompt_builder.py`  | Composable prompt system                         |
| **Agents**   | `onboarding.py`      | Hire flow + talent install                       |
| **Agents**   | `termination.py`     | Fire flow + cleanup                              |
| **Core**     | `config.py`          | Paths, constants, config loaders                 |
| **Core**     | `state.py`           | `CompanyState` singleton, hot-reload             |
| **Core**     | `events.py`          | Async `EventBus` pub/sub                         |
| **Core**     | `vessel.py`          | `Vessel`, `EmployeeManager`, `Executor` protocol |
| **Core**     | `vessel_config.py`   | `VesselConfig` (DNA) load/save/migrate           |
| **Core**     | `vessel_harness.py`  | 6 Harness protocols                              |
| **Core**     | `routine.py`         | Post-task workflow dispatch                      |
| **Core**     | `workflow_engine.py` | Markdown → `WorkflowDefinition`                  |
| **Core**     | `project_archive.py` | Project CRUD, cost tracking                      |
| **Core**     | `layout.py`          | Office grid allocation                           |
| **Talent**   | `talent_spec.py`     | `TalentPackage`, `AgentManifest`                 |
| **Talent**   | `boss_online.py`     | MCP recruitment server                           |
| **Infra**    | `tools/sandbox/`     | Docker code execution                            |
| **Infra**    | `claude_session.py`  | Claude CLI session management                    |
| **Frontend** | `index.html`         | 3-column layout                                  |
| **Frontend** | `office.js`          | Canvas 2D pixel art renderer                     |
| **Frontend** | `app.js`             | CEO console, WebSocket handler                   |

## Design Philosophy

1. **Systematic Design, Not Patching** — Every change is structural. No `if id == "special_case"`.
2. **Registry/Dispatch over if-elif** — Data-driven patterns everywhere.
3. **Complete Data Packages** — Every state is serializable, recoverable, registered, and terminable.
4. **No Silent Exceptions** — Always log. Always re-raise `CancelledError`.
5. **Disk = Single Source of Truth** — No in-memory caching of business data.
6. **Zero Idle** — No `while True` polling. Event-driven, on-demand execution.
7. **Git-Friendly Persistence** — YAML + Markdown + JSON. `git diff`, `git blame`, `git revert`.
8. **Minimal Complexity** — Three similar lines > premature abstraction.
