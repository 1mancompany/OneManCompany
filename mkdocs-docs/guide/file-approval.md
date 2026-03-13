# File Change Approval

When AI employees modify files, changes go through a review and approval process — just like code review in a real engineering team.

## How It Works

1. **Employee proposes changes** — During task execution, employees may create or modify files
2. **Changes are staged** — File modifications are queued for CEO review, not applied immediately
3. **CEO reviews diffs** — You see exactly what changed, line by line
4. **Batch approval** — Accept or reject changes individually or in batch

## What You See

For each proposed change:

- **File path** — Which file is being modified
- **Diff view** — Added lines (green), removed lines (red), context
- **Employee** — Who made the change and why
- **Task context** — Which task triggered the change

## Approval Actions

| Action | Effect |
| --- | --- |
| **Accept** | Change is applied to the file |
| **Reject** | Change is discarded, employee notified |
| **Accept all** | Batch approve all pending changes |

## Why This Matters

File approval serves as a critical quality gate:

- **Catch errors** — AI employees can make mistakes; review prevents them from shipping
- **Maintain consistency** — Ensure changes align with your codebase standards
- **Stay informed** — As CEO, you always know what's changing in your projects
- **Safety net** — No unauthorized modifications to important files

## Tips

!!! tip "Review Regularly"
    Don't let pending approvals pile up — they can block employee progress on dependent tasks.

!!! tip "Trust But Verify"
    As employees prove reliable through coaching and performance reviews, you may choose to review less frequently. But the safety net is always there.
