# Talent Market

The talent market provides structured **talent packages** for hiring and
multiple **connection modes** for running agents.

---

## Talent Package Structure

Each talent lives in `talents/{talent_id}/`:

```
talents/{talent_id}/
├── profile.yaml          # Required — identity + hiring info + system_prompt_template
├── CLAUDE.md             # Optional — Claude CLI project instructions (copied to employee dir on onboarding)
├── manifest.json         # Optional — frontend settings UI + capability declarations
├── launch.sh             # Optional — launch script for self-hosted employees
├── run_worker.py         # Optional — worker entry point for remote employees
├── skills/               # Optional — skills (folder-based, see skill spec below)
│   └── {skill-name}/     # One folder per skill
│       └── SKILL.md      # Skill definition (with YAML frontmatter)
├── agent/                # Optional — agent configuration (additional prompt sections)
│   ├── manifest.yaml     # Prompt section declarations
│   └── prompt_sections/  # Additional prompt fragments
└── tools/                # Optional — tool declarations and custom tools
    ├── manifest.yaml     # Tool manifest (builtin_tools + custom_tools)
    └── *.py              # Custom LangChain @tool implementations
```

> Data class definitions are in `talent_spec.py`, with field descriptions for all files.

### `profile.yaml` (Required)

Talent identity information, displayed to the CEO during HR recruitment.

| Field | Type | Description |
|---|---|---|
| `id` | str | Talent unique identifier, matches directory name |
| `name` | str | Display name (e.g. "Coding Talent") |
| `description` | str | Talent description text |
| `role` | str | Role type (Engineer, Designer, QA, etc.), determines onboarding department |
| `remote` | bool | `true` = remote work (no desk assigned), `false` = on-site |
| `hosting` | str | Hosting mode: `company` / `self` / `remote` (see connection modes below) |
| `auth_method` | str | Authentication method: `api_key` / `oauth` / `cli` / `none` |
| `api_provider` | str | LLM provider (`openrouter`, `anthropic`, etc.) |
| `llm_model` | str | Default LLM model identifier |
| `temperature` | float | Default inference temperature |
| `image_model` | str | Image generation model (optional, used by Designer) |
| `hiring_fee` | float | Hiring fee |
| `salary_per_1m_tokens` | float | Salary per million tokens (0 = auto-calculate by model) |
| `skills` | list | Skill identifier list, corresponds to folder names under `skills/` |
| `tools` | list | Tool name list |
| `personality_tags` | list | Personality tags (for HR matching) |
| `system_prompt_template` | str | Talent Persona prompt (see detailed description below) |

### `system_prompt_template` Writing Guidelines

`system_prompt_template` is the talent's **soul prompt** — it defines the talent's core capabilities,
work style, and thinking framework. On onboarding, it is written to `employees/{id}/prompts/talent_persona.md`
and injected as the **Talent Persona** layer across all prompt paths.

#### Prompt Layering Protocol

The employee's final system prompt is assembled by priority (ascending):

```
Priority 10: Identity       — "You are Xiao Ming (nickname), Manager in Product Dept (Lv.3)"
                               [Auto-generated from employee profile]
Priority 12: Talent Persona  — Content of system_prompt_template
                               [From talent profile, fixed at onboarding]
Priority 15: Work Approach   — Work methodology (generic or custom)
Priority 30: Skills          — Full content of skills/*.md
Priority 35: Tools           — Authorized tool list + usage instructions
Priority 40: Direction       — Company strategic direction
Priority 45: Culture         — Company culture guidelines
Priority 50: Principles      — Employee personal work principles (updated after CEO 1-on-1)
Priority 55: Guidance        — CEO guidance notes
Priority 70: Context         — Current time, team status, active tasks
Priority 80: Efficiency      — Efficiency guidelines
```

`system_prompt_template` sits **after Identity, before Skills**.
It does not need to repeat identity information (name/role/department) — those are provided by the Identity layer.
It should focus on: how this talent **thinks**, **works**, and **what it excels at**.

#### Writing Tips

1. **Do not include identity info** — No need for "You are {name}" or "Your role is {role}",
   the Identity layer already provides this
2. **State capability focus** — Describe the talent's core expertise and work style
3. **Provide behavioral guidance** — How to use skills, how to make decisions, output standards
4. **Keep it concise** — 2-5 sentences is ideal, do not exceed one paragraph. Skill details belong in skills/*.md
5. **Use second person** — Start with "You"

#### Examples

**Good examples** (focused on capability + behavior):

```yaml
# Product Manager talent
system_prompt_template: >
  You are equipped with 46 professional PM frameworks covering user stories,
  PRDs, positioning, discovery, prioritization, roadmapping, and financial
  analysis. Use your skills library to select the right framework for each
  product challenge. Ground analysis in frameworks, not generic advice.
  When unsure which framework to apply, ask clarifying questions first.
```

```yaml
# Full-stack Engineer talent
system_prompt_template: >
  You specialize in full-stack development with deep expertise in Python,
  TypeScript, and cloud infrastructure. Write production-ready code with
  tests. Prefer simple, maintainable solutions over clever abstractions.
  Always verify assumptions by reading existing code before proposing changes.
```

```yaml
# Data Analyst talent
system_prompt_template: >
  You excel at turning raw data into actionable insights. Use SQL for
  data extraction, Python for analysis, and clear visualizations to
  communicate findings. Always validate data quality before drawing
  conclusions. Present results with confidence intervals where applicable.
```

**Bad examples**:

```yaml
# Bad — repeats identity layer info
system_prompt_template: >
  You are a senior product manager named Alice in the Product Department.
  Your role is Manager and you work at level 3.

# Bad — too vague, no specific capability or behavioral guidance
system_prompt_template: >
  You are a helpful assistant. Be professional and do your best.

# Bad — too long, stuffs skill details into prompt
system_prompt_template: >
  You are a PM who can write user stories using the Mike Cohn format
  with Gherkin-style acceptance criteria. The format is: As a [user],
  I want to [action], so that [outcome]. Acceptance criteria follow
  the Given-When-Then pattern: Given [context], When [event], Then
  [expected result]. You can also create PRDs with the following
  sections: Overview, Problem Statement, Goals, User Stories...
  [300 more words]
```

#### Relationship Between CLAUDE.md and system_prompt_template

| File | Purpose | Effective Path |
|------|---------|----------------|
| `system_prompt_template` | Concise persona prompt | Company-hosted (LangChain) + Self-hosted PromptBuilder |
| `CLAUDE.md` | Complete project-level instructions | Self-hosted Claude CLI auto-discovery (CLAUDE.md in cwd) |

- If the talent comes from a GitHub repo, `CLAUDE.md` is saved as-is to the talent directory,
  copied to `employees/{id}/CLAUDE.md` on onboarding for Claude CLI auto-loading
- `system_prompt_template` should always exist independently of `CLAUDE.md` —
  it is a concise persona definition, while `CLAUDE.md` may contain detailed operational manuals

### `manifest.json` (Optional)

Drives frontend settings UI and capability declarations. Legacy employees without manifest use default UI.

```json
{
  "id": "claude-code-onsite",
  "name": "Claude Code Engineer",
  "version": "1.0.0",
  "role": "Engineer",
  "hosting": "self",
  "settings": {
    "sections": [
      {
        "id": "connection",
        "title": "Connection",
        "fields": [
          {"key": "oauth", "type": "oauth_button", "label": "Anthropic Login", "provider": "anthropic"},
          {"key": "llm_model", "type": "text", "label": "Model", "default": "claude-sonnet-4-20250514"},
          {"key": "temperature", "type": "number", "label": "Temperature", "default": 0.7, "min": 0, "max": 2, "step": 0.1}
        ]
      }
    ]
  },
  "prompts": {"skills": ["skills/*/SKILL.md"]},
  "tools": {"builtin": ["sandbox_execute_code"], "custom": ["tools/my_tool.py"]},
  "platform_capabilities": ["file_upload", "websocket"]
}
```

**Settings Field Types**:

| type | Renders | Additional Properties |
|---|---|---|
| `text` | Single-line text input | — |
| `secret` | Password input (masked) | — |
| `number` | Number input | `min`, `max`, `step` |
| `select` | Single-select dropdown | `options` or `options_from` |
| `multi_select` | Multi-select dropdown | `options` or `options_from` |
| `toggle` | Toggle switch | — |
| `textarea` | Multi-line text | — |
| `oauth_button` | OAuth login button | `provider` (e.g. `"anthropic"`) |
| `color` | Color picker | — |
| `file` | File upload | — |
| `readonly` | Read-only display | `value_from` (data source, e.g. `"api:sessions"`) |

### `tools/manifest.yaml` (Optional)

```yaml
builtin_tools:          # Platform built-in tool names
  - sandbox_execute_code
  - sandbox_run_command
custom_tools:           # .py module names in same directory (without suffix)
  - custom_build        # -> exports @tool function from tools/custom_build.py
```

### `skills/{name}/SKILL.md` (Optional — Skill Specification)

Skills use a **folder-based** structure, one directory per skill, with `SKILL.md` as the core definition file.
The loose `skills/*.md` file format is no longer supported.

```
skills/
├── ontology/
│   ├── SKILL.md              # Required — skill definition (with YAML frontmatter)
│   ├── references/           # Optional — reference documents
│   └── scripts/              # Optional — helper scripts
├── proactive-agent/
│   └── SKILL.md
└── work-principles/
    └── SKILL.md
```

#### SKILL.md Format

```markdown
---
name: Ontology
description: Typed knowledge graph for structured agent memory
autoload: true          # true = full text injected into prompt; false/omitted = listed in catalog only, loaded on demand
---

# Ontology

Skill body content...
```

| Frontmatter Field | Type | Description |
|---|---|---|
| `name` | str | Skill display name (uses folder name if omitted) |
| `description` | str | One-line description (appears in skill catalog) |
| `autoload` | bool | `true` = always injected into prompt; `false`/omitted = employee loads on demand via `load_skill` tool |

#### Skill Loading Mechanism

Follows Claude's skill specification:

1. **Autoloaded skills** (`autoload: true`) — Full text injected into system prompt, suitable for work principles and other always-active content
2. **Catalog skills** (default) — Only name + description shown in prompt, employee calls `load_skill("skill-name")` tool to get full text

> **Development guideline**: All new talents must use folder-based skills. The onboarding flow (`onboarding.py`)
> automatically copies the talent's `skills/` to the employee directory and injects three default skills
> (ontology, proactive-agent, self-improving-agent).

---

## Connection Modes

The platform supports three connection modes, determined by the `hosting` field in `profile.yaml`.

### 1. Company-Hosted (`hosting: "company"`)

**Company-hosted** — the most common mode. The platform runs the `launch.sh` script via `SubprocessExecutor`.

```yaml
# profile.yaml
hosting: company          # Or leave empty, this is the default
auth_method: api_key      # Use API key to call LLM
api_provider: openrouter  # LLM provider
llm_model: google/gemini-3.1-pro-preview-customtools
```

**How it works**:
- The platform uses `SubprocessExecutor` to run `launch.sh` as a **foreground process**
- Each task = one `launch.sh` invocation, task prompt written to a temp file and passed via `OMC_TASK_DESCRIPTION_FILE` environment variable
- Script outputs result JSON to stdout, logs to stderr
- Timeout and cancellation managed by the platform (SIGTERM -> 30s -> SIGKILL)
- If no custom `launch.sh` exists, the platform falls back to `LangChainLauncher`
- Company tools available via MCP stdio protocol (optional)

**Authentication configuration**:
- `auth_method: api_key` — Use `{"type": "secret", "key": "api_key"}` field in manifest
- `auth_method: none` — No authentication needed (free models or pre-configured provider)

**Use cases**: Standard AI employees using OpenRouter, OpenAI, and similar APIs

### 2. Self-Hosted (`hosting: "self"`)

**Self-hosted** — the employee brings its own runtime environment, running as an independent process.

```yaml
# profile.yaml
hosting: self
auth_method: oauth        # OAuth PKCE login
api_provider: anthropic
llm_model: claude-sonnet-4-20250514
```

**How it works**:
- The platform uses `ClaudeSessionLauncher` to start `claude --print` CLI processes on demand
- Each task = one CLI invocation, process exits after task completion
- Session context maintained via `sessions.json` (subsequent calls within the same project use `--resume`)
- If the talent provides a `launch.sh`, it is copied to the employee directory on onboarding

**Authentication configuration**:
- `auth_method: oauth` — Use `{"type": "oauth_button", "provider": "anthropic"}` field in manifest,
  triggers Anthropic OAuth PKCE flow, token stored in employee configuration
- `auth_method: cli` — Uses locally logged-in Claude CLI credentials, no additional configuration needed

**Files additionally generated on onboarding**:
- `connection.json` — Contains `employee_id`, `company_url`, `talent_id`
- `launch.sh` (copied from talent) — Launch script
- `sessions.json` (generated at runtime) — Session records

**Use cases**: Claude Code CLI, local AI tools, and other employees with their own runtime environment

### 3. Remote (`hosting: "remote"` / `remote: true`)

**Remote** — the employee runs on an external node and communicates with the company via HTTP.

```yaml
# profile.yaml
remote: true
hosting: remote           # Can be omitted, auto-inferred when remote: true
```

**How it works**:
- The employee runs a worker process on an external machine (inheriting `RemoteWorkerBase`)
- Polls for tasks, submits results, and sends heartbeats via HTTP
- The platform does not manage its process lifecycle, only provides a task queue
- Skills and tools are not copied locally — the remote worker brings its own

**Files additionally generated on onboarding**:
- `connection.json` — Read by the worker on startup, contains company URL and other connection info

**Use cases**: AI workers running on remote servers, inference nodes on GPU clusters

---

## Connection Mode Comparison

| | Company | Self-Hosted | Remote |
|---|---|---|---|
| **hosting value** | `company` (default) | `self` | `remote` |
| **Process management** | `SubprocessExecutor` foreground process | Platform starts CLI on demand | Externally self-managed |
| **Launcher** | `SubprocessExecutor` (with launch.sh) / `LangChainLauncher` (fallback) | `ClaudeSessionLauncher` | None (HTTP task queue) |
| **LLM calls** | launch.sh calls LLM API internally | CLI brings own credentials/OAuth | Worker calls LLM independently |
| **Authentication** | api_key / none | oauth / cli | — |
| **skills/tools copied** | Yes | Yes | No |
| **Desk assignment** | Yes | Yes | No (remote flag) |
| **connection.json** | No | Yes | Yes |
| **launch.sh** | Recommended (foreground mode) | Optional (background mode) | No |
| **Session management** | Stateless (one process per task) | sessions.json | Worker self-managed |
| **Timeout/cancellation** | Platform-managed (SIGTERM->SIGKILL) | Platform-managed | Worker self-managed |

---

## Remote Worker Protocol

Remote workers communicate with the company via four HTTP endpoints:

| Endpoint | Method | Description |
|---|---|---|
| `/api/remote/register` | POST | Worker registration |
| `/api/remote/tasks/{employee_id}` | GET | Poll for pending tasks |
| `/api/remote/results` | POST | Submit task results |
| `/api/remote/heartbeat` | POST | Heartbeat keep-alive |

### Flow

1. **Register** — After startup, worker POSTs to `/api/remote/register` with `employee_id`, callback URL, and capability list
2. **Poll** — Periodically GETs `/api/remote/tasks/{employee_id}`, returns `TaskAssignment` when tasks are available
3. **Execute** — Worker executes the task in its own environment
4. **Submit** — POSTs `TaskResult` to `/api/remote/results`
5. **Heartbeat** — Periodically POSTs to `/api/remote/heartbeat` to report alive status

### Data Models

See `remote_protocol.py`: `RemoteWorkerRegistration`, `TaskAssignment`, `TaskResult`, `HeartbeatPayload`

---

## Writing launch.sh

`launch.sh` is the core execution entry point for company-hosted employees. The platform runs this script
as a **foreground process** via `SubprocessExecutor`, unlike the background worker mode for self-hosted.

> Template file: `company/assets/tools/launch_template.sh`

### Two launch.sh Modes

| | Company-Hosted (Foreground) | Self-Hosted (Background) |
|---|---|---|
| **Execution** | `SubprocessExecutor` direct invocation | Copied to employee dir on onboarding, manually started |
| **Lifecycle** | One process per task, exits on completion | Long-running background, polls task queue |
| **Task source** | `OMC_TASK_DESCRIPTION_FILE` (temp file path) | HTTP polling `/api/remote/tasks/` |
| **Result output** | stdout JSON | HTTP POST `/api/remote/results` |
| **Timeout/cancellation** | Platform-managed (SIGTERM -> 30s -> SIGKILL) | Self-managed |
| **PID management** | Not needed | `worker.pid` file |

**This section only describes Company-Hosted foreground mode.** For Self-Hosted background mode, see each talent's `launch.sh` implementation.

### Calling Convention

```
SubprocessExecutor invocation:
    bash launch.sh <employee_dir>

Arguments:
    $1 = employee_dir  (e.g. company/human_resource/employees/00010/)

Environment variables (auto-injected):
    OMC_EMPLOYEE_ID      — Employee ID
    OMC_TASK_ID          — Task ID
    OMC_PROJECT_ID       — Project ID
    OMC_PROJECT_DIR      — Project working directory (cwd)
    OMC_TASK_DESCRIPTION_FILE — Path to temp file containing task prompt
    OMC_SERVER_URL       — Backend URL (http://localhost:8000)
    OMC_MAX_ITERATIONS   — Max agent iteration count (default 20)

Output:
    stdout -> JSON (single line only)
    stderr -> Logs (debug only)
    exit 0 -> Success    exit non-zero -> Failure
```

### stdout JSON Format

```json
{
  "output": "Task result text",
  "model": "google/gemini-3.1-pro-preview",
  "input_tokens": 1234,
  "output_tokens": 567
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `output` | string | Yes | Task execution result (plain text) |
| `model` | string | No | LLM model identifier used |
| `input_tokens` | int | No | Input token count |
| `output_tokens` | int | No | Output token count |

If stdout is not valid JSON, `SubprocessExecutor` will return the raw text as `output`.

### Timeout and Cancellation

- Default timeout is 3600s (1 hour), adjustable by parent task via `dispatch_child(timeout_seconds=...)`
- On timeout or manual cancellation, the platform sends **SIGTERM** to the process
- If not exited within 30 seconds, force **SIGKILL**
- Scripts should use `trap cleanup EXIT` to respond to SIGTERM and clean up child processes

```bash
cleanup() {
    # Clean up MCP server and other child processes
    if [ -n "${MCP_PID:-}" ] && kill -0 "$MCP_PID" 2>/dev/null; then
        kill "$MCP_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT
```

### Using MCP Tools

Company-hosted employees can access company tools via MCP stdio protocol (`dispatch_child`, `accept_child`,
`reject_child`, `list_colleagues`, etc.).

```bash
# Start MCP server as coprocess
PROJECT_ROOT="$(cd "$EMPLOYEE_DIR/../../../.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

coproc MCP_PROC {
    exec "$PYTHON" -m onemancompany.tools.mcp.server 2>/dev/null
}
MCP_PID=$MCP_PROC_PID

# MCP_PROC[0] = stdout fd (read)
# MCP_PROC[1] = stdin fd (write)
# Communicate with MCP server via JSON-RPC 2.0 protocol
```

The MCP server filters available tools based on employee permissions (`tool_permissions`).

### Complete Example

```bash
#!/usr/bin/env bash
set -euo pipefail

EMPLOYEE_DIR="${1:?Usage: launch.sh <employee_dir>}"
EMPLOYEE_DIR="$(cd "$EMPLOYEE_DIR" && pwd)"
PROJECT_ROOT="$(cd "$EMPLOYEE_DIR/../../../.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

cleanup() { :; }
trap cleanup EXIT

>&2 echo "[launch.sh] Employee=${OMC_EMPLOYEE_ID} Task=${OMC_TASK_ID}"

# Call LLM (OpenRouter example)
RESULT=$(curl -s https://openrouter.ai/api/v1/chat/completions \
    -H "Authorization: Bearer ${OPENROUTER_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{
      \"model\": \"google/gemini-3.1-pro-preview\",
      \"messages\": [{\"role\": \"user\", \"content\": $(echo "$OMC_TASK_DESCRIPTION" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}]
    }")

# Parse response
OUTPUT=$(echo "$RESULT" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r["choices"][0]["message"]["content"])')
MODEL=$(echo "$RESULT" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r.get("model",""))' 2>/dev/null || echo "")
IN_TOKENS=$(echo "$RESULT" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r.get("usage",{}).get("prompt_tokens",0))' 2>/dev/null || echo "0")
OUT_TOKENS=$(echo "$RESULT" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r.get("usage",{}).get("completion_tokens",0))' 2>/dev/null || echo "0")

# Output JSON (only this single line to stdout)
python3 -c "
import json, sys
print(json.dumps({
    'output': sys.argv[1],
    'model': sys.argv[2],
    'input_tokens': int(sys.argv[3]),
    'output_tokens': int(sys.argv[4]),
}))
" "$OUTPUT" "$MODEL" "$IN_TOKENS" "$OUT_TOKENS"
```

### Best Practices

1. **Write all logs to stderr** — stdout is only for final JSON output, use `>&2 echo "..."` for logging
2. **Respond to SIGTERM** — Use `trap cleanup EXIT` to clean up child processes and temp files
3. **Do not daemonize** — Script must run in foreground until completion, do not use `nohup` or `&`
4. **Use python3 for JSON** — Avoid JSON format errors from shell string concatenation
5. **set -euo pipefail** — Exit immediately on any command failure, avoid silent errors
6. **Environment variables are context** — Do not hardcode task info, read from `OMC_*` environment variables

---

## Creating a New Talent

### Minimum Viable Package (Company-Hosted)

```
talents/my_talent/
├── profile.yaml      # Required
├── launch.sh         # Recommended — foreground task execution script / Ralph-style agent loop
└── skills/
    └── my-skill/
        └── SKILL.md  # Folder-based skill
```

```yaml
# profile.yaml
id: my_talent
name: My Talent
description: A new talent for the company.
role: Engineer
remote: false
hosting: company
auth_method: api_key
api_provider: openrouter
llm_model: ''
temperature: 0.7
hiring_fee: 0.50
skills:
  - my-skill
personality_tags:
  - efficient
system_prompt_template: >
  You are a skilled engineer. Complete tasks efficiently.
```

### Full Package (Self-Hosted with manifest)

```
talents/my_self_hosted/
├── profile.yaml
├── manifest.json
├── launch.sh
├── skills/
│   ├── coding/
│   │   └── SKILL.md
│   └── review/
│       └── SKILL.md
└── tools/
    ├── manifest.yaml
    └── my_custom_tool.py
```

### Steps

1. Create a directory under `talents/`
2. Write `profile.yaml` (required fields: `id`, `name`, `role`, `skills`)
3. Write `system_prompt_template` — focus on capability positioning and behavioral guidance, do not repeat identity info
4. Create folder-based skills under `skills/` (`{name}/SKILL.md`, with YAML frontmatter)
5. Write `launch.sh` — reference the `company/assets/tools/launch_template.sh` template
   - Company-hosted: foreground mode, get task from `OMC_*` env vars, JSON output to stdout
   - Self-hosted: background mode, nohup to start worker, write PID file
6. If custom tools are needed, create `tools/manifest.yaml` + `.py` files
7. If custom settings UI is needed, create `manifest.json`
8. If there are Claude CLI project instructions, place them in `CLAUDE.md`

> **Required**: All skills must use the folder-based format (`skills/{name}/SKILL.md`), the loose file format is no longer supported.
> Three default skills (ontology, proactive-agent, self-improving-agent) are automatically injected on onboarding.

---

## Standalone Execution

Company-hosted talents support **running independently outside the company system**. During onboarding,
`onboarding.py` generates a self-contained `run.py` in the employee directory (from the `standalone_runner.py`
template), containing a complete LangChain ReAct agent, skill loading, and built-in tools.

### Prerequisites

```bash
pip install langchain-openai langgraph pyyaml
# If using Anthropic:
pip install langchain-anthropic
```

### Standalone Launch Steps

```bash
# 1. Generate run.py in the talent directory (one-time, or auto-generated on onboarding)
cd talents/general-assistant
python -c "
from onemancompany.talent_market.standalone_runner import generate_run_py
generate_run_py('.', 'General Assistant', 'standalone')
"

# 2. Configure API Key (choose one)
#    Option A: Environment variable
export OPENROUTER_API_KEY=sk-or-v1-xxx
#    Option B: Set api_key field in profile.yaml

# 3. Configure model (set llm_model in profile.yaml)
#    e.g. anthropic/claude-sonnet-4, google/gemini-2.5-pro, etc.
```

### Three Execution Modes

| Mode | Command | Use Case |
|------|---------|----------|
| **Single task** | `python run.py "Analyze project structure"` | One-off Q&A or operations |
| **Pipe input** | `echo "Write a proposal" \| python run.py` | Script/CI integration |
| **Agent Loop** | `./launch.sh [max_iterations]` | Complex multi-step tasks, auto-iterates until completion |

### Agent Loop Mode (Ralph Style)

`launch.sh` uses a [Ralph](https://github.com/snarktank/ralph)-style iterative execution model:

```
┌─────────────────────────────────────────┐
│  Read task.txt or TASK env variable     │
└──────────────┬──────────────────────────┘
               ▼
┌─────────────────────────────────────────┐
│  Iteration N: invoke run.py             │
│  - Inject previous progress.log as ctx  │
│  - Append output to progress.log        │
└──────────────┬──────────────────────────┘
               ▼
         ┌───────────┐
         │ Output     │──── Yes ──→ Exit (success)
         │ contains   │
         │ <done>     │
         │ COMPLETE   │
         │ </done>?   │
         └─────┬─────┘
               │ No
               ▼
         ┌───────────┐
         │ Reached    │──── Yes ──→ Exit (incomplete)
         │ max        │
         │ iterations?│
         └─────┬─────┘
               │ No
               └──→ Next iteration
```

```bash
# Example: execute a complex task with agent loop
echo "Refactor this project's test framework to achieve 80% coverage" > task.txt
OPENROUTER_API_KEY=sk-or-v1-xxx ./launch.sh 20
```

### run.py Built-in Tools

When running standalone, the agent has the following built-in tools (no company backend required):

| Tool | Description |
|------|-------------|
| `read_file(path)` | Read file contents (50KB limit) |
| `write_file(path, content)` | Write/create files |
| `list_dir(path)` | List directory contents |
| `bash(command)` | Execute shell commands (120s timeout) |
| `load_skill(name)` | Load skill full text on demand |

### Self-Containment Requirement

> **Development guideline**: Each talent must be self-contained. All file dependencies needed for
> standalone execution (profile.yaml, skills/, tools/, etc.) must be within the talent's own directory.
> No dependencies on files outside the talent directory are allowed. After `run.py` is generated,
> it can run on any machine — just install Python dependencies and configure the API key.

### Developer Checklist

When creating a new talent, confirm the following standalone execution support:

- [ ] `profile.yaml` includes complete `api_provider` and `llm_model` configuration
- [ ] Skills use folder-based format (`skills/{name}/SKILL.md`), with YAML frontmatter
- [ ] `tools/manifest.yaml` declares required built-in tools
- [ ] `launch.sh` supports Ralph-style agent loop (reference `general-assistant/launch.sh`)
- [ ] `generate_run_py()` correctly generates a runnable `run.py`
- [ ] All dependency files are within the talent directory, no external path references

---

## Extending `RemoteWorkerBase`

`remote_worker_base.py` provides the abstract base class for remote workers:

```python
from onemancompany.talent_market.remote_worker_base import RemoteWorkerBase
from onemancompany.talent_market.remote_protocol import TaskAssignment, TaskResult


class MyCodingWorker(RemoteWorkerBase):
    def setup_tools(self) -> list:
        return [my_sandbox_tool, my_web_search_tool]

    async def process_task(self, task: TaskAssignment) -> TaskResult:
        return TaskResult(
            task_id=task.task_id,
            employee_id=self.employee_id,
            status="completed",
            output="Task done!",
        )


import asyncio
worker = MyCodingWorker(
    company_url="http://localhost:8000",
    employee_id="00010",
    capabilities=["coding", "web_research"],
)
asyncio.run(worker.start())
```

The base class automatically handles registration, task polling, and heartbeat loops — just implement `setup_tools()` and `process_task()`.
