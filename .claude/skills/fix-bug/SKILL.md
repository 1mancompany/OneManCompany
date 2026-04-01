---
name: fix-bug
description: Fix a bug with systematic debugging, TDD, and PR workflow — no plan needed
user_invocable: true
---

# Fix Bug Workflow

Follow this exact sequence. No plan needed — go straight to debugging.

## 1. Sync main
```
git checkout main && git pull
```

## 2. Systematic Debugging (superpowers:systematic-debugging)
- **DO NOT guess fixes** — follow the debugging protocol
- Phase 1: Read errors, reproduce, check recent changes, gather evidence
- Phase 2: Find working examples, compare
- Phase 3: Form hypothesis, test minimally
- Phase 4: Implement fix with test

## 3. Create branch
- Branch naming: `fix/<bug-description>`
- No worktree needed for simple fixes; use worktree for larger changes

## 4. TDD Fix (superpowers:test-driven-development)
- **Write a failing test that reproduces the bug FIRST**
- Then implement the fix
- The test must pass after the fix
- Run full test suite

## 5. Verify (superpowers:verification-before-completion)
- Run all tests: `.venv/bin/python -m pytest tests/unit/ -x -q`
- Verify the original bug is fixed
- Check no regressions

## 6. Code Review (superpowers:requesting-code-review)
- Dispatch code-reviewer subagent
- Fix ALL issues: Critical, Important, AND Suggestions
- **Verify each finding** — if reviewer is hallucinating (claiming a bug that doesn't exist), gather evidence (read the code, run the test) and reject the finding with reasoning
- Re-run review after fixes until zero issues remain

## 7. Create PR
- Commit with message explaining the root cause
- Push branch and create PR via `gh pr create`
- Return the PR URL to boss
- **DO NOT merge** — wait for boss to say "merge"

## 8. Merge (only when boss says)
- `gh pr merge <number> --admin --merge`
- Clean up: `git checkout main && git pull && git branch -d <branch>`

## Rules
- **Never merge without boss confirmation**
- **Never push directly to main**
- **Always write a regression test** — the bug must never come back
- **No plan needed** — bugs are urgent, go straight to debugging
- **Zero tolerance for code smells:**
  - No dead code (empty functions, unused variables, commented-out blocks)
  - No string literals for values that should be constants/enums
  - No duplicate systems (same data via two paths = Critical defect)
  - No technical debt left behind — fix it now or don't write it
- **Never commit planning materials** — `docs/superpowers/` (specs, plans) is .gitignored. Never `git add -f` to override.
- If 3+ fix attempts fail, stop and question the architecture
