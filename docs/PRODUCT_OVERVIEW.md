# One Man Company — Product Overview

> A single human CEO runs an entire AI-powered company through a pixel-art office interface.

---

## System Architecture

```
                          +------------------+
                          |    Human CEO     |
                          | (Browser Client) |
                          +--------+---------+
                                   |
                          WebSocket + REST API
                                   |
                  +----------------+----------------+
                  |                                 |
         +--------v---------+            +----------v-----------+
         |  OneManCompany   |            |    Talent Market      |
         |  Backend (8000)  |   MCP/API  |    Platform (9000)    |
         |                  +<---------->+                       |
         |  FastAPI + WS    |            |  FastAPI + React SPA  |
         +--------+---------+            +----------+-----------+
                  |                                 |
     +------------+------------+            +-------+-------+
     |            |            |            |               |
  +--v--+     +--v--+     +--v--+     +---v----+     +----v----+
  | HR  |     | COO |     | EA  |     |Registry|     |Git Repos|
  |00002|     |00003|     |00004|     | .json  |     | (clone) |
  +-----+     +-----+     +-----+     +--------+     +---------+
     |            |            |
  +--v--+     +--v--+     +--v--+
  | CSO |     |Hired|     |Hired|
  |00005|     |Emps |     |Emps |
  +-----+     +-----+     +-----+
```

---

## I. CEO — The Only Human

CEO is the sole human operator. All other "employees" are LLM agents. CEO controls the company through the browser UI at `http://localhost:8000`.

### CEO Control Points

```
                        +-------------------+
                        |     CEO Console   |
                        | (Browser at :8000)|
                        +---------+---------+
                                  |
            +---------------------+---------------------+
            |                     |                     |
    +-------v-------+    +-------v-------+    +--------v--------+
    |  Task & Project|    |  People Mgmt  |    | Company Config  |
    |  Management    |    |               |    |                 |
    +-------+-------+    +-------+-------+    +--------+--------+
            |                     |                     |
    +-------+-------+    +-------+-------+    +--------+--------+
    | - Submit task  |    | - Hire/fire   |    | - Culture       |
    | - Q&A mode     |    | - 1-on-1 guide|    | - Direction     |
    | - Follow-up    |    | - Set OKRs    |    | - Workflows     |
    | - Abort        |    | - Config model|    | - Tool mgmt     |
    | - Approve/deny |    | - Set API key |    | - Dashboard     |
    | - Review edits |    | - Review perf |    | - Hot reload    |
    +----------------+    +---------------+    +-----------------+
```

### 1. Task & Project Management

| Action | How | Notes |
|--------|-----|-------|
| Submit task | Input box + Send | Routes to EA, EA dispatches autonomously |
| Q&A mode | Select "Q&A" in dropdown | Direct LLM answer, no project created |
| Follow-up | Click project → "Follow-up" | Adds instructions to running project |
| Abort project | Click project → "Stop" | Force-cancels all subtasks |
| Approve report | "Approve" button on report popup | Unblocks employee waiting for CEO |
| Request revision | "Revise" button + feedback | Employee reworks based on CEO notes |
| Review file edits | Resolution modal | Approve/reject/defer each file change |

### 2. People Management

| Action | How | Notes |
|--------|-----|-------|
| Initiate hiring | Submit task "hire an engineer" | EA → HR → candidate shortlist → CEO selects |
| Interview candidate | Click candidate card | Chat with candidate before hiring |
| Approve COO hire request | Hiring request modal | COO proposes need, CEO decides |
| Fire employee | Employee detail → "Fire" | Cannot fire Lv.4+ founding members |
| Rehire ex-employee | Ex-employee wall → "Rehire" | Restores at Lv.1 |
| 1-on-1 guidance | Toolbar → 1-on-1 button | Chat session, guidance saved to profile |
| Set OKRs | Employee detail → OKRs | Tracked but not auto-enforced |
| Configure LLM | Employee detail → Settings | Model, provider, API key, hosting mode |
| Trigger review | Submit "performance review" | HR evaluates all eligible employees |

### 3. Company Configuration

| Action | How | Notes |
|--------|-----|-------|
| Set company culture | Toolbar → Culture | List of principles, injected into all agent prompts |
| Set company direction | Toolbar → Direction | Vision statement, can "enrich" via LLM |
| Edit workflows | Toolbar → Workflows | Markdown-based SOP definitions |
| Manage tools | Toolbar → Tool count | OAuth login, credentials, env vars, templates |
| View costs | Toolbar → Dashboard | Per-project and overhead spending |
| Force reload | Toolbar → Reload | Re-read all config from disk |
| Apply code update | Admin → Apply update | Hot-reload backend changes |

### 4. Approval & Oversight

CEO only needs to intervene when:

```
  Employee working
       |
       v
  Is this risky?  ----NO----> EA completes autonomously
       |                      (CEO gets notification)
      YES
       |
       v
  report_to_ceo(action_required=True)
       |
       v
  +----+----+
  | CEO     |
  | reviews |
  +----+----+
       |
   +---+---+
   |       |
Approve  Revise
   |       |
   v       v
 Done    Employee
         reworks
```

**What requires CEO approval:**
- Financial decisions (budgets, purchases, pricing)
- Personnel decisions (hire, fire, promote)
- External-facing commitments (client comms, public announcements)
- Irreversible actions (delete data, deploy production)
- File edits outside employee workspace (via Resolution system)

**What EA handles autonomously:**
- Routine operations (emails, queries, scheduling)
- Clear-cut task routing
- Low-risk internal work
- Status reporting

---

## II. OneManCompany — Features

### Core Architecture

```
+------------------------------------------------------------------+
|                         OneManCompany                              |
|                                                                   |
|  +------------------+  +------------------+  +-----------------+  |
|  |   Agent System   |  |   Task System    |  | Employee System |  |
|  |                  |  |                  |  |                 |  |
|  | HR (hiring/perf) |  | TaskTree (tree)  |  | Hire/onboard    |  |
|  | COO (operations) |  | TaskPhase (FSM)  |  | Level (1-5)     |  |
|  | EA (routing)     |  | TaskType (S/P)   |  | Performance     |  |
|  | CSO (sales)      |  | Persistence      |  | Probation/PIP   |  |
|  | Employee (generic)|  | Dependencies     |  | Skills/SKILL.md |  |
|  +------------------+  +------------------+  +-----------------+  |
|                                                                   |
|  +------------------+  +------------------+  +-----------------+  |
|  |  Project System  |  |   Tool System    |  | Vessel System   |  |
|  |                  |  |                  |  |                 |  |
|  | Named projects   |  | ToolRegistry     |  | On-demand exec  |  |
|  | Iterations       |  | MCP bridge       |  | LangChain exec  |  |
|  | Workspace        |  | Permissions      |  | Claude CLI exec |  |
|  | Cost tracking    |  | Asset tools      |  | Script exec     |  |
|  +------------------+  +------------------+  +-----------------+  |
|                                                                   |
|  +------------------+  +------------------+  +-----------------+  |
|  | File Editor      |  |  Workflow Engine |  |  Pixel Art UI   |  |
|  |                  |  |                  |  |                 |  |
|  | Propose → Review |  | Markdown SOPs    |  | Canvas 2D       |  |
|  | Free zones       |  | Step handlers    |  | Sprites + desks |  |
|  | Resolution batch |  | Hiring/onboard   |  | Live WebSocket  |  |
|  | Backup + apply   |  | Review/offboard  |  | 3-column layout |  |
|  +------------------+  +------------------+  +-----------------+  |
|                                                                   |
|  +------------------+  +------------------+  +-----------------+  |
|  |  Meeting System  |  |  Plugin System   |  | Snapshot/Reload |  |
|  |                  |  |                  |  |                 |  |
|  | pull_meeting()   |  | Kanban board     |  | @snapshot_provider|
|  | Room booking     |  | Timeline view    |  | Graceful restart|  |
|  | Live chat stream |  | Custom plugins   |  | Task persistence|  |
|  | Meeting reports  |  | YAML manifest    |  | Hot reload tiers|  |
|  +------------------+  +------------------+  +-----------------+  |
+------------------------------------------------------------------+
```

### Agent Hierarchy

```
                    +----------+
                    |   CEO    |
                    | (Human)  |
                    +----+-----+
                         |
                    +----v-----+
                    |    EA    |  Task routing & oversight
                    |  (00004) |  Autonomous for simple tasks
                    +----+-----+
                         |
          +--------------+--------------+
          |              |              |
     +----v-----+  +----v-----+  +----v-----+
     |    HR    |  |   COO    |  |   CSO    |
     |  (00002) |  |  (00003) |  |  (00005) |
     +----+-----+  +----+-----+  +----------+
          |              |
     Hiring         +----+----+
     Reviews        |         |
     Onboarding   Hired     Hired
                  Engineers  Designers
                    ...       ...
```

### Task Lifecycle

```
CEO submits task
      |
      v
  EA analyzes
      |
      v
  dispatch_child() ──────────────┐
      |                          |
      v                          v
  Employee works            Employee works
      |                          |
      v                          v
  complete                   complete
      |                          |
      +──────────┬───────────────+
                 |
                 v
         EA reviews all
                 |
         +───────+───────+
         |               |
     accept_child    reject_child
         |               |
         v               v
   More work?        Retry / fail
         |
    +----+----+
    |         |
   YES        NO
    |         |
    v         v
 dispatch   report_to_ceo()
  more         |
            +--+--+
            |     |
         Simple  Risky
            |     |
            v     v
         Notify  Block for
         CEO     CEO approval
```

### Task Types

| Type | Description | CEO Approval | Retrospective |
|------|-------------|:---:|:---:|
| **Simple** | Single action (send email, query info) | No | No |
| **Project** | Multi-step delivery (dev, design) | Only if risky | Optional (EA decides) |

### Employee Levels

```
Lv.5 ── CEO (human, permanent)
Lv.4 ── Founding (HR/COO/EA/CSO, permanent, cannot be fired)
Lv.3 ── Senior (max normal level, 3 consecutive 3.75 quarters)
Lv.2 ── Mid (promoted from Lv.1)
Lv.1 ── Junior (new hires, probation period)
```

### Hosting Modes

| Mode | Description | Managed By |
|------|-------------|------------|
| `company` | LangChain agent on server | OMC backend (EmployeeManager) |
| `self` | External process (Claude CLI) | Employee's own machine, heartbeat monitored |
| `remote` | Remote worker via task queue | Pulls tasks from API, pushes results |

### Key Features Summary

- **Pixel Art Office** — Real-time 2D canvas with employee sprites, desks, meeting rooms
- **LangChain Agents** — Each employee is a `create_react_agent` with role-specific tools
- **Task Tree** — Hierarchical task decomposition with parent-child relationships
- **File Edit Approval** — Employees propose edits, CEO reviews in batch (Resolution system)
- **MCP Tool Bridge** — Expose company tools to Claude CLI via MCP stdio server
- **Performance System** — Quarterly reviews, probation, PIP, promotion (3.25/3.5/3.75 scoring)
- **Meeting System** — `pull_meeting()` tool for multi-agent synchronous discussion
- **Plugin System** — Extensible project views (Kanban, Timeline)
- **Hot Reload** — Tier 1 (data) / Tier 1.5 (frontend) / Tier 2 (backend graceful restart)
- **Snapshot Persistence** — Task queues survive server restarts
- **Cost Dashboard** — Per-project LLM token usage and USD cost tracking
- **Workflow Engine** — Markdown-defined SOPs parsed into executable steps

---

## III. Talent Market — Features

### Architecture

```
+------------------------------------------------------------------+
|                        Talent Market                              |
|                                                                   |
|  +-----------+    +-------------+    +--------------------------+ |
|  | React SPA |    | FastAPI     |    | MCP Server               | |
|  | (Vite+TS) |    | REST API   |    | (SSE at /mcp + stdio)    | |
|  |           |    | 12 endpoints|    | 3 tools                  | |
|  | Browse    |    |            |    | search_candidates        | |
|  | Detail    |    | Talents    |    | get_talent_info          | |
|  | Add Repo  |    | Repos      |    | list_available_talents   | |
|  +-----------+    | Search     |    +--------------------------+ |
|                   +------+------+                                 |
|                          |                                        |
|                   +------v------+                                 |
|                   |  Registry   |                                 |
|                   | (JSON file) |                                 |
|                   +------+------+                                 |
|                          |                                        |
|              +-----------+-----------+                            |
|              |                       |                            |
|       +------v------+       +-------v-------+                    |
|       | Local talents|       | Git Repos     |                    |
|       | (talents/)   |       | (shallow clone)|                   |
|       +-------------+       +---------------+                    |
+------------------------------------------------------------------+
```

### How OMC Connects to Talent Market

```
  OMC HR Agent                    Talent Market
  (00002)                         (port 9000)
      |                               |
      |   MCP: search_candidates()    |
      +------------------------------>|
      |                               |
      |   Ranked talent list          |
      |<------------------------------+
      |                               |
      |   MCP: get_talent_info(id)    |
      +------------------------------>|
      |                               |
      |   Full profile + manifest     |
      |<------------------------------+
      |                               |
      v
  HR shortlists → CEO selects → execute_hire()
                                   |
                              Clone talent dir
                              Copy assets to employee
                              Register agent loop
```

### Talent Format

```
my-talent/
├── profile.yaml        # Required ── id, name, role, skills, hosting
├── manifest.json       # Optional ── UI settings sections for employee detail page
├── skills/             # Optional ── SKILL.md files (autoloaded or on-demand)
├── tools/              # Optional ── tool configs + LangChain modules
│   └── manifest.yaml
├── CLAUDE.md           # Optional ── Claude Code agent instructions
├── launch.sh           # Optional ── self-hosted employee launcher
└── heartbeat.sh        # Optional ── health check for self-hosted
```

### Claude Agent Compatibility

Repos structured as Claude Code agents (with `CLAUDE.md` but no `profile.yaml`) are auto-detected and mapped:

```
Claude Agent Repo                    Talent Market Format
─────────────────                    ────────────────────
CLAUDE.md             ──────>        profile.yaml (inferred)
.mcp.json env vars    ──────>        manifest.json settings (secret fields)
skills/               ──────>        skills/ (preserved)
launch.sh             ──────>        hosting: "self"
run_worker.py         ──────>        hosting: "remote"
README.md first para  ──────>        description
```

### Frontend Pages

| Page | Path | Features |
|------|------|----------|
| **Browse** | `/` | Search bar, role filter chips, talent card grid |
| **Detail** | `/talent/:id` | Profile, skills, tools, manifest settings, pricing |
| **Add** | `/add` | Input repo URL → scan → detect talents → fill profile form |

### API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/talents` | List talents (filter by role/skills) |
| GET | `/api/talents/:id` | Get talent detail |
| GET | `/api/talents/:id/files` | File tree of talent directory |
| GET | `/api/talents/:id/file` | Read specific file |
| GET | `/api/talents/:id/detect` | Detect Claude agent format |
| POST | `/api/talents/:id/profile` | Save edited profile |
| GET | `/api/repos` | List registered repos |
| POST | `/api/repos` | Add repo by URL |
| DELETE | `/api/repos` | Remove repo |
| POST | `/api/repos/sync` | Re-sync repo |
| POST | `/api/search` | Search talents by query |

### MCP Tools (for OMC HR Agent)

| Tool | Args | Returns |
|------|------|---------|
| `search_candidates` | job_description, count | Ranked list with relevance scores |
| `get_talent_info` | talent_id | Full talent entry (profile + manifest) |
| `list_available_talents` | role?, skills? | Filtered talent summaries |

---

## IV. End-to-End Flow

```
 1. CEO opens browser (localhost:8000)
          |
 2. CEO types: "Hire a game developer"
          |
 3. EA receives task, dispatches to HR
          |
 4. HR calls Talent Market MCP ──> search_candidates("game developer")
          |
 5. Talent Market returns ranked results from registry
          |
 6. HR shortlists top candidates ──> presents to CEO
          |
 7. CEO interviews and selects candidate
          |
 8. execute_hire() ──> clone talent assets ──> create employee folder
          |
 9. New employee appears in pixel art office
          |
10. CEO types: "Build a Roblox game"
          |
11. EA dispatches to COO ──> COO dispatches to new game dev employee
          |
12. Employee works (LangChain agent with tools)
          |
13. Employee proposes file edits ──> Resolution for CEO review
          |
14. CEO approves edits ──> files applied
          |
15. Employee completes ──> EA accepts ──> CEO notified
          |
16. Project archived with cost report
```

---

## V. Quick Reference

### Start Services

```bash
# OneManCompany (port 8000)
cd OneManCompany && bash start.sh

# Talent Market (port 9000)
cd talentmarket && bash start.sh
```

### Key Directories

```
OneManCompany/
├── .onemancompany/              # Runtime data (created by init wizard)
│   ├── .env                     # API keys, server config
│   ├── company/
│   │   ├── human_resource/employees/   # Employee profiles + skills
│   │   ├── assets/tools/               # Company tools
│   │   ├── business/projects/          # Project archives
│   │   └── business/workflows/         # SOP markdown files
│   └── config.yaml              # App config (hot reload, etc.)
├── src/onemancompany/           # Python backend
├── frontend/                    # Vanilla JS pixel art UI
└── start.sh                     # One-click launcher

talentmarket/
├── src/talentmarket/            # Python backend
├── frontend/                    # React + Vite + TypeScript
├── talents/                     # Built-in talent packages
├── registry.json                # Talent registry
└── start.sh                     # One-click launcher
```
