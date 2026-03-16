# Getting Started

This guide walks you through your first launch to completing your first task as CEO.

## Installation

You only need **Node.js 16+** and **Git**. Everything else is installed automatically.

```bash
npx @1mancompany/onemancompany
```

First run automatically:

1. Installs **UV** (fast Python package manager)
2. Installs **Python 3.12** via UV (isolated, no system changes)
3. Clones the repository
4. Creates venv and installs dependencies
5. Launches the setup wizard

## Setup Wizard

The wizard guides you through:

1. **OpenRouter API Key** — Required. Powers your AI employees via LLM. Get one at [openrouter.ai](https://openrouter.ai)
2. **Default Model** — Browse and select from available models. Each employee can later be assigned a different model.
3. **Server Configuration** — Host and port (defaults: `0.0.0.0:8000`)
4. **Optional Keys** — Anthropic API key, SkillMarket API key, Talent Market API key. Press Enter to skip any you don't have.

!!! tip "Talent Market Recommended"
    Register at [one-man-company.com](https://one-man-company.com) to get a Talent Market API key. This lets you hire community-verified AI employees.

## Your First Session

After setup, open `http://localhost:8000` in your browser. You'll see:

- **Left panel** — Employee roster showing your founding team
- **Center** — Pixel-art office with your AI employees at their desks
- **Right panel** — CEO console for commands, task management, and approvals

### Your Founding Team

Four executives are ready on Day 1:

| Employee | Role |
| --- | --- |
| **EA** | Routes tasks, quality gate |
| **HR** | Hiring, performance reviews, promotions |
| **COO** | Operations, task dispatch, acceptance |
| **CSO** | Sales, client relations |

### Give Your First Task

Type a task in the CEO console:

> "Build a simple puzzle game"

Watch what happens:

1. **EA** receives and routes the task
2. **COO** breaks it down into subtasks
3. If you need more people, **HR** searches the Talent Market
4. Employees work autonomously, holding meetings when needed
5. Work goes through review and quality gates
6. You get notified to approve the final result

## Starting Again

```bash
npx @1mancompany/onemancompany
```

If there's a new version, it updates automatically. If the service is already running, you'll be asked whether to stop and re-setup.

## Reconfiguration

```bash
# Re-run setup wizard
npx @1mancompany/onemancompany init

# Custom port
npx @1mancompany/onemancompany --port 8080
```

## Uninstall

```bash
npx @1mancompany/onemancompany uninstall
```

Stops the running service and deletes the entire installation directory. Requires confirmation.

## Configuration Files

| File | Purpose |
| --- | --- |
| `.onemancompany/.env` | API keys (OpenRouter, Anthropic, etc.) |
| `.onemancompany/config.yaml` | App config (Talent Market URL, etc.) |
| Browser Settings panel | Frontend preferences |

## Next Steps

- [Execution Modes](execution-modes.md) — Switch between Company Hosted Agent and Claude Code
- [Task Management](task-management.md) — Learn the full task lifecycle
- [Hiring](hiring.md) — Expand your team from the Talent Market
