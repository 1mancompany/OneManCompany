---
name: minor-change
description: Small tweaks to existing features — no brainstorming or plan needed, just TDD and PR
user_invocable: true
---

# Minor Change Workflow

For small adjustments to existing features: text changes, style tweaks, config updates, removing dead code, renaming, etc. No brainstorming or plan needed.

## 1. Sync main
```
git checkout main && git pull
```

## 2. Create branch
- Branch naming: `fix/<description>` or `chore/<description>`

## 3. Make the change
- If the change touches logic, write a test first (TDD)
- If purely cosmetic (text, style, config), test is optional
- Run full test suite after changes

## 4. Verify
- Run all tests: `.venv/bin/python -m pytest tests/unit/ -x -q`
- Verify compilation if Python changed

## 5. Code Review (superpowers:requesting-code-review)
- Dispatch code-reviewer subagent before creating PR
- Fix ALL issues: Critical, Important, AND Suggestions
- **Verify each finding** — if reviewer is hallucinating (claiming a bug that doesn't exist), gather evidence (read the code, run the test) and reject the finding with reasoning
- Re-run review after fixes until zero issues remain

## 6. Create PR
- Commit with concise message
- Push and create PR via `gh pr create`
- Return PR URL to boss
- **DO NOT merge** — wait for boss to say "merge"

## 7. Merge (only when boss says)
- `gh pr merge <number> --admin --merge`
- Clean up: `git checkout main && git pull && git branch -d <branch>`

## Rules
- **Never merge without boss confirmation**
- **Never push directly to main**
- **Zero tolerance for code smells:**
  - No dead code (empty functions, unused variables, commented-out blocks)
  - No string literals for values that should be constants/enums
  - No duplicate systems (same data via two paths = Critical defect)
  - No technical debt left behind — fix it now or don't write it
- **Never commit planning materials** — `docs/superpowers/` (specs, plans) is .gitignored. Never `git add -f` to override.
- Keep changes small and focused — one concern per PR
