# Contributing to OneManCompany

Thanks for your interest in contributing! Whether you're fixing bugs, adding features, improving docs, or building tools — you're welcome here.

## Before You Write Any Code

**Read the [Vibe Coding Guide](vibe-coding-guide.md) first.** It covers our engineering philosophy, architecture patterns, code style, testing rules, and common mistakes to avoid. Both AI coders and human contributors are expected to follow it.

Key principles from the guide:

- **SSOT (Single Source of Truth)** — Disk is truth. No in-memory caches. No data duplication.
- **TDD (Test-Driven Development)** — Write the test first, watch it fail, then implement.
- **Registry-based design** — Use dispatch/registries, not if/elif chains.
- **No silent exceptions** — Always log errors. Re-raise `asyncio.CancelledError`.
- **Don't over-engineer** — Only change what the task requires. No speculative abstractions.

## Getting Started

```bash
# Clone and install
git clone https://github.com/1mancompany/OneManCompany.git
cd OneManCompany
uv venv && uv pip install -e ".[dev]"

# Run tests
.venv/bin/python -m pytest tests/ -x -q

# Start the server (dev mode)
.venv/bin/python -m onemancompany.main

# Install gstack (Claude Code skills for browsing, QA, code review, etc.)
git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack && cd ~/.claude/skills/gstack && ./setup
```

> **Note:** gstack requires [bun](https://bun.sh). If not installed, run `curl -fsSL https://bun.sh/install | bash` first.

## Development Workflow

1. Create a branch from `main`
2. Write tests first (TDD)
3. Implement the feature/fix
4. Run the full test suite — all tests must pass
5. Open a PR against `main`

## What You Can Contribute

- **Build Tools** — Add integrations (APIs, services, platforms)
- **Add Company Features** — Performance dashboards, OKR tracking, employee training
- **Improve the OS** — Core engine, frontend, documentation
- **Share Demos** — Show what your AI company can build
- **Report Issues** — Help us find and fix bugs

## Code Style

- Python: follow existing patterns in the codebase
- Use `loguru.logger`, not `print` or stdlib `logging`
- Mock at the **importing module** level, not the source module
- All test I/O goes to `tmp_path` — never write to the repo

See [vibe-coding-guide.md](vibe-coding-guide.md) for the full style guide.

## License

By contributing, you agree that your contributions will be licensed under the [Apache 2.0 License](LICENSE).
