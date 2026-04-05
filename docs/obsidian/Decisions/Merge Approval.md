---
tags: [decision, git, workflow]
source: memory/feedback_merge_approval.md, memory/feedback_git_workflow.md
---

# Merge Approval Policy

**Never self-merge. Wait for CEO to explicitly say "merge".**

## Rules

1. All modifications must go through PR — no direct push to main
2. After creating PR, return URL to CEO and wait
3. Only merge when CEO explicitly says "merge"
4. Use `gh pr merge <number> --admin --merge`
5. Clean up branch after merge

## Why

CEO wants full control over what ships. Self-merging bypasses review.

## Related
- [[Git Workflow]] — Full PR flow
