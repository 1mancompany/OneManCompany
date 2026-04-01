---
name: add-feat
description: Add a new feature with TDD, worktree isolation, PR workflow, and code review
user_invocable: true
---

# Add Feature Workflow

Follow this exact sequence. Do NOT skip steps.

## 1. Sync main
```
git checkout main && git pull
```

## 2. Brainstorm (superpowers:brainstorming)
- Invoke the brainstorming skill to explore the design
- Present design to CEO for approval before writing any code
- If the feature is simple and CEO says to skip design, proceed directly

## 3. Plan (superpowers:writing-plans)
- Write an implementation plan
- Get CEO approval on the plan

## 4. Create worktree (superpowers:using-git-worktrees)
- Create an isolated git worktree for the feature branch
- Branch naming: `feat/<feature-name>`

## 5. TDD Implementation (superpowers:test-driven-development)
- **Write tests FIRST**, then implement
- For each unit of work:
  1. Write a failing test
  2. Implement the minimum code to pass
  3. Refactor if needed
- Run the full test suite after each change

## 6. Verify (superpowers:verification-before-completion)
- Run all tests: `.venv/bin/python -m pytest tests/unit/ -x -q`
- Verify compilation: `.venv/bin/python -c "from onemancompany.api.routes import router; print('OK')"`
- Check no silent excepts, no print statements

## 7. Create PR
- Commit with descriptive message
- Push branch and create PR via `gh pr create`
- Return the PR URL to CEO
- **DO NOT merge** — wait for CEO to say "merge"

## 8. Code Review (superpowers:requesting-code-review)
- Dispatch code-reviewer subagent
- Fix ALL issues: Critical, Important, AND Suggestions
- **Verify each finding** — if reviewer is hallucinating (claiming a bug that doesn't exist), gather evidence (read the code, run the test) and reject the finding with reasoning
- Re-run review after fixes until zero issues remain
- Push fixes and notify CEO

## 9. Merge (only when CEO says)
- `gh pr merge <number> --admin --merge`
- Clean up: `git checkout main && git pull && git branch -d <branch>`
- If using worktree: remove worktree first

## Rules
- **Never merge without CEO confirmation**
- **Never push directly to main**
- **Always TDD** — tests before implementation
- **Always run full test suite** before committing
- **Zero tolerance for code smells:**
  - No dead code (empty functions, unused variables, commented-out blocks)
  - No string literals for values that should be constants/enums
  - No duplicate systems (same data via two paths = Critical defect)
  - No technical debt left behind — fix it now or don't write it
- **Never commit planning materials** — `docs/superpowers/` (specs, plans) is .gitignored. Never `git add -f` to override. These are local working docs, not repo artifacts.
- Read `vibe-coding-guide.md` before starting if unfamiliar with codebase conventions
