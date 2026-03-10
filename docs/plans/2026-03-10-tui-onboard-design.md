# TUI Onboarding Design

**Goal:** Create a `onemancompany init` command that bootstraps `.onemancompany/` from source `company/` via an interactive TUI wizard.

**Architecture:** Single-file wizard (`onboard.py`) using `rich` for terminal UI. Separate CLI entry point from the server start command.

**Tech:** Python + rich (Prompt, Confirm, Console, Panel, Progress)

---

## CLI Entry Points

```toml
[project.scripts]
onemancompany = "onemancompany.main:run"
onemancompany-init = "onemancompany.onboard:main"
```

- `onemancompany init` → wizard
- `onemancompany` → server (if no `.onemancompany/`, prints hint to run init first)

## Wizard Flow

1. **Welcome** — Logo, brief description
2. **LLM Config** — OpenRouter API key (password input), default model (select from presets)
3. **Server Config** — Host (default 0.0.0.0), Port (default 8000), with enter-to-skip
4. **Optional Config** — Confirm each: Anthropic API key, FastSkills API key
5. **Execute** — Copy company/, write .env, write config.yaml, generate MCP configs (with spinner)
6. **Done** — Summary + hint to start server

## Files

- **New:** `src/onemancompany/onboard.py`
- **Modify:** `pyproject.toml` (rich dep + script entry)
- **Modify:** `src/onemancompany/main.py` (_bootstrap_data_dir → print hint + exit if missing)

## Out of Scope

- Non-interactive mode
- API connectivity validation
- Reset/reconfigure flow
