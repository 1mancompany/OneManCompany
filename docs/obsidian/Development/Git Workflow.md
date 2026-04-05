---
tags: [development, git, workflow]
source: memory/feedback_git_workflow.md, memory/feedback_merge_approval.md
---

# Git Workflow

## Iron Rules

1. **All changes via PR** — never push directly to main
2. **Never self-merge** — wait for CEO to explicitly say "merge"
3. **Branch naming**: `feat/<name>`, `fix/<name>`, `chore/<name>`
4. **One concern per PR** — keep changes small and focused

## PR Flow

1. `git checkout main && git pull`
2. `git checkout -b feat/my-feature`
3. Implement with TDD (see [[Testing Guide]])
4. Run full test suite
5. `git push -u origin feat/my-feature`
6. `gh pr create --title "..." --body "..."`
7. Wait for CI to pass
8. Wait for CEO approval
9. `gh pr merge <number> --admin --merge`
10. Cleanup: `git checkout main && git pull && git branch -d feat/my-feature`

## Never Commit

- Planning materials (`docs/superpowers/` is .gitignored)
- Secrets (`.env`, credentials)
- Large binaries

## Pre-commit Hook

The repo has a pre-commit hook that runs the full test suite. If tests fail, the commit is rejected.

## Related
- [[Merge Approval]] — CEO approval policy
- [[Testing Guide]] — What to verify before committing
