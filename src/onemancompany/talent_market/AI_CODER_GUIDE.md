# Talent Development Guide for AI Coders

> A complete talent development guide for AI Coders (Claude, GPT, etc.).
> After reading this document, you should be able to independently create a talent package.

---

## Quick Start — Minimum Viable Talent

```
talents/my_talent/
├── profile.yaml        # Required
├── launch.sh           # Recommended (company-hosted execution entry point)
└── skills/
    └── core_skill.md   # At least one
```

```yaml
# profile.yaml
id: my_talent
name: My Talent Name
description: One-line description of this talent's capabilities.
role: Engineer                    # Engineer / Designer / Manager / QA / ...
remote: false
hosting: company                  # company / self / remote
auth_method: api_key
api_provider: openrouter
llm_model: google/gemini-3.1-pro-preview
temperature: 0.7
hiring_fee: 0.50
salary_per_1m_tokens: 0
skills:
  - core_skill                    # Corresponds to skills/core_skill.md
tools: []
personality_tags:
  - efficient
system_prompt_template: >
  You specialize in [domain]. [Work style]. [Decision-making approach].
```

That's all you need. The platform will auto-fill missing configuration on onboarding.

---

## Directory Structure (Full Version)

```
talents/{talent_id}/
├── profile.yaml              # Required — identity, model, skills, tools
├── launch.sh                 # Recommended — task execution script
├── manifest.json             # Optional — frontend settings UI (OAuth, parameter tuning)
├── CLAUDE.md                 # Optional — Claude CLI project instructions
├── skills/                   # Recommended — skill knowledge base
│   └── *.md                  # Each file = one skill, content injected into system prompt
├── tools/                    # Optional — tool declarations
│   ├── manifest.yaml         # Tool manifest
│   └── *.py                  # Custom LangChain @tool
├── functions/                # Optional — tools contributed to the company
│   ├── manifest.yaml
│   └── *.py
└── vessel/                   # Optional — advanced agent configuration
    ├── vessel.yaml
    └── prompt_sections/*.md
```

---

## profile.yaml Field Reference

### Identity Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | str | Yes | Unique identifier, matches directory name |
| `name` | str | Yes | Display name |
| `description` | str | Yes | Description shown to CEO during recruitment |
| `role` | str | Yes | Role type, determines onboarding department |

Role-to-department mapping:

| role | Department |
|------|------------|
| Engineer | R&D Department |
| Designer | Creative Design Department |
| Manager | Operations Management Department |
| QA | Quality Assurance Department |
| Other | General Affairs Department |

### Model Fields

| Field | Type | Description |
|-------|------|-------------|
| `api_provider` | str | LLM provider: `openrouter`, `anthropic`, `openai` |
| `llm_model` | str | Model ID (e.g. `claude-sonnet-4-20250514`) |
| `temperature` | float | Inference temperature 0-2 |
| `image_model` | str | Image generation model (Designer-only, optional) |

### Deployment Fields

| Field | Description |
|-------|-------------|
| `hosting: company` | Platform-hosted — runs `launch.sh` via `SubprocessExecutor` |
| `hosting: self` | Self-hosted — Claude CLI independent process |
| `hosting: remote` | Remote — external worker via HTTP polling |
| `auth_method: api_key` | Use API key to call LLM |
| `auth_method: cli` | Use locally logged-in CLI credentials |
| `auth_method: oauth` | OAuth PKCE login |
| `auth_method: none` | No authentication needed |

### Capability Fields

| Field | Type | Description |
|-------|------|-------------|
| `skills` | list | Skill ID list, corresponds to `skills/*.md` filenames |
| `tools` | list | Available tool name list |
| `personality_tags` | list | Personality tags (for HR matching) |
| `system_prompt_template` | str | Soul prompt — defines how the talent thinks |

---

## system_prompt_template Writing Guide

This is the talent's **core personality definition**. It is injected at prompt priority 12 (after Identity, before Skills).

### Prompt Layering

```
Priority 10: Identity        — "You are Xiao Ming, Manager in Product Dept" (system-generated)
Priority 12: Talent Persona  — <- system_prompt_template goes here
Priority 30: Skills           — Full content of skills/*.md
Priority 35: Tools            — Authorized tool list
Priority 50: Work Principles  — Personal work principles after CEO 1-on-1
Priority 70: Context          — Current time, team status
```

### Rules

1. **Do not include identity info** — Identity layer already provides name/role/department
2. **State capability focus** — What this talent excels at, how it works
3. **Provide behavioral guidance** — How to use skills, how to make decisions
4. **Keep it concise** — 2-5 sentences, no more than one paragraph
5. **Use second person** — Start with "You"

### Examples

```yaml
# Good example
system_prompt_template: >
  You specialize in full-stack development with deep expertise in Python,
  TypeScript, and cloud infrastructure. Write production-ready code with
  tests. Prefer simple, maintainable solutions over clever abstractions.
  Always verify assumptions by reading existing code before proposing changes.

# Good example
system_prompt_template: >
  You are equipped with 46 professional PM frameworks. Use your skills
  library to select the right tool for each challenge. Ground analysis
  in frameworks, not generic advice.

# Bad example — repeats identity
system_prompt_template: >
  You are a senior engineer named Alice in the Engineering Department.

# Bad example — too vague
system_prompt_template: >
  You are a helpful assistant. Be professional and do your best.
```

---

## Skills Writing Guide

### File Convention

- Path: `skills/{skill_id}.md`
- `skill_id` must appear in the `skills` list in `profile.yaml`
- Content is directly injected into the system prompt, so it should be **declarative knowledge**, not executable code

### Structure Template

```markdown
# Skill Name

## Purpose
One-line description: what this skill does and when to use it.

## Key Concepts
Core frameworks, definitions, mental models.
- Use lists or tables
- Define potentially confusing terms
- Include "what it's NOT" (anti-patterns)

## Application
Step-by-step guidance for specific scenarios.
- Write as instructions the agent can directly execute
- Use numbered steps for sequential operations
- Mark decision points and branching logic

## Examples
Real cases demonstrating the skill's application.
- Show "good" vs "bad" comparisons
- Be specific, not generic

## Common Pitfalls
Common mistakes and their consequences.
- Name failure patterns
- Explain consequences
- Provide correction methods
```

### Key Points

- **Concise** — Agent's context window is limited, keep each skill under 500 lines
- **Actionable** — Write as instructions, not a textbook
- **Self-contained** — Do not assume the agent has external knowledge

---

## launch.sh Writing Guide

### Company-Hosted (Foreground Mode)

The platform invokes via `SubprocessExecutor`, one process per task:

```
bash launch.sh <employee_dir>
```

**Environment variables (auto-injected):**

| Variable | Description |
|----------|-------------|
| `OMC_EMPLOYEE_ID` | Employee ID |
| `OMC_TASK_ID` | Task ID |
| `OMC_PROJECT_ID` | Project ID |
| `OMC_PROJECT_DIR` | Project working directory |
| `OMC_TASK_DESCRIPTION_FILE` | Path to temp file containing the task prompt |
| `OMC_SERVER_URL` | Backend URL |
| `OMC_MAX_ITERATIONS` | Max agent iteration count (default 20) |

**Output convention:**
- `stdout` -> Single line of JSON only (result)
- `stderr` -> Logs
- `exit 0` -> Success, non-zero -> Failure

```json
{"output": "Task result", "model": "model-id", "input_tokens": 100, "output_tokens": 50}
```

**Timeout/cancellation:** Platform-managed. SIGTERM -> 30s -> SIGKILL. Respond with `trap cleanup EXIT`.

**Template:** Reference `company/assets/tools/launch_template.sh`

### Self-Hosted (Background Mode)

Copied to employee directory on onboarding, manually started:

```bash
#!/usr/bin/env bash
EMPLOYEE_DIR="${1:?Usage: launch.sh <employee_dir>}"
# ... start worker ...
nohup "$PYTHON" "$WORKER_SCRIPT" "$EMPLOYEE_DIR" > "$LOG_FILE" 2>&1 &
echo $! > "$EMPLOYEE_DIR/worker.pid"
```

---

## Tools Development

### Using Platform Built-in Tools

Just declare them in the `tools` list in `profile.yaml`:

```yaml
tools:
  - sandbox_execute_code
  - sandbox_run_command
```

### Custom LangChain Tools

1. Create `tools/manifest.yaml`:
```yaml
builtin_tools:
  - sandbox_execute_code
custom_tools:
  - my_analyzer
```

2. Create `tools/my_analyzer.py`:
```python
from langchain_core.tools import tool

@tool
def my_analyzer(code: str) -> str:
    """Analyze code quality and return suggestions."""
    # Implementation logic
    return f"Analysis: {len(code)} chars, looks good."
```

### Contributing Company-Level Tools (functions/)

If your tool should be **shared with other employees**:

1. Create `functions/manifest.yaml`:
```yaml
functions:
  - name: "shared_tool"
    description: "A tool everyone can use"
    scope: "company"        # "company" = company-wide, "personal" = self only
```

2. Create `functions/shared_tool.py` (same format as above)

Automatically installed to `company/assets/tools/shared_tool/` on onboarding.

---

## manifest.json — Frontend Settings UI

Only create when you need a custom settings interface. Supported field types:

| type | Renders | Use Case |
|------|---------|----------|
| `text` | Single-line input | Model name, URL |
| `secret` | Password input | API key |
| `number` | Number input | Temperature, timeout |
| `select` | Dropdown select | Model list |
| `toggle` | Toggle switch | Enable/disable features |
| `oauth_button` | OAuth button | Third-party login |
| `readonly` | Read-only display | Status info |

```json
{
  "id": "my-talent",
  "name": "My Talent",
  "version": "1.0.0",
  "settings": {
    "sections": [
      {
        "id": "connection",
        "title": "Connection",
        "fields": [
          {"key": "api_key", "type": "secret", "label": "API Key", "required": true},
          {"key": "temperature", "type": "number", "label": "Temperature", "default": 0.7, "min": 0, "max": 2, "step": 0.1}
        ]
      }
    ]
  }
}
```

---

## vessel.yaml — Advanced Agent Configuration

Only create when you need to customize agent behavior:

```yaml
# vessel/vessel.yaml
runner:
  module: ""                    # Custom runner module (.py under vessel/)
  class_name: ""                # BaseAgentRunner subclass name
hooks:
  module: ""                    # Hooks module
  pre_task: ""                  # Pre-task callback
  post_task: ""                 # Post-task callback
context:
  prompt_sections:              # Additional prompt injection
    - file: prompt_sections/guide.md
      name: guide
      priority: 40              # 10-80, lower = earlier in prompt
  inject_progress_log: true     # Inject historical progress log
  inject_task_history: true     # Inject task history
limits:
  max_retries: 3
  task_timeout_seconds: 600     # Default timeout
```

---

## What Happens During Onboarding

When the CEO approves hiring, `onboarding.py` executes the following steps in order:

```
1. Assign employee ID (e.g. 00042)
2. Create employee directory company/human_resource/employees/00042/
3. Copy skills/*.md -> employee directory/skills/
4. Copy manifest.json, CLAUDE.md (if they exist)
5. Copy launch.sh, heartbeat.sh (if they exist, chmod +x)
6. Copy vessel/ or agent/ configuration
7. Register tool permissions (custom_tools from tools/manifest.yaml)
8. Install functions/ (copy to company central tools directory)
9. Generate nickname (martial arts style Chinese nickname)
10. Write work_principles.md (initial work principles)
11. Write system_prompt_template -> prompts/talent_persona.md
12. Register with EmployeeManager (start accepting tasks)
```

**Key:** After onboarding, the talent directory is no longer referenced. All runtime files are in the employee directory.

---

## Three Hosting Mode Comparison

| | Company | Self-Hosted | Remote |
|---|---|---|---|
| **Execution** | `SubprocessExecutor` foreground | Claude CLI subprocess | External HTTP polling |
| **Task delivery** | Environment variables | CLI arguments | HTTP API |
| **Result return** | stdout JSON | CLI output | HTTP POST |
| **Timeout/cancellation** | Platform SIGTERM->SIGKILL | Platform-managed | Worker self-managed |
| **launch.sh** | Foreground mode (recommended) | Background mode | Not needed |
| **skills copied** | Yes | Yes | No |
| **tools copied** | Yes | Yes | No |
| **Typical use case** | OpenRouter/OpenAI employees | Claude Code employees | GPU cluster inference nodes |

---

## MCP Tool Access

Company-hosted employees can access company tools via MCP stdio protocol:

```bash
# Start MCP server in launch.sh
PROJECT_ROOT="$(cd "$EMPLOYEE_DIR/../../../.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

coproc MCP_PROC {
    exec "$PYTHON" -m onemancompany.tools.mcp.server 2>/dev/null
}
# Communicate with MCP server via JSON-RPC 2.0
```

Available tools (depends on permissions):
- `dispatch_child` — Dispatch child tasks
- `accept_child` / `reject_child` — Accept/reject child tasks
- `list_colleagues` — List colleagues
- `pull_meeting` — Pull people into alignment meeting

---

## Checklist — Pre-Release Check

- [ ] `profile.yaml` has `id`, `name`, `role`, `skills` fields
- [ ] Every skill listed in `profile.yaml` has a corresponding `.md` file under `skills/`
- [ ] `system_prompt_template` does not repeat identity info, 2-5 sentences
- [ ] `launch.sh` (if present) starts with `set -euo pipefail`
- [ ] `launch.sh` writes logs to stderr, result JSON to stdout
- [ ] `.py` files under `tools/` all have `@tool` decorators
- [ ] `manifest.json` (if present) has `id` matching `profile.yaml`'s `id`
- [ ] No hardcoded paths (use `$1` and environment variables)
- [ ] No leftover test data or placeholders
