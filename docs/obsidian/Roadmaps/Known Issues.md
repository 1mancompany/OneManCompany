---
tags: [roadmap, bugs, active]
source: memory/bugs_to_fix.md
updated: 2026-04-05
---

# Known Issues

## Active

### #8 — Task tree node text overflow
- Text exceeds node area, needs auto-wrapping
- Node sizes don't need to be uniform
- **Status**: 待修复

### #11 — Standardize TUI module
- Need unified TUI standard for 1-1 meetings and CEO inbox display/conversation
- **Status**: 待开发

### #13 — Debug mode full prompt printing
- All tasks should print full LLM prompt (no history) and response in `--debug` mode
- **Status**: 待修复

## Resolved
- ~~#1 Task details don't show follow-up tasks~~ — 忽略
- ~~#2 Batch onboarding should be concurrent~~ — 忽略
- ~~#3 Onboarding too slow (nickname LLM)~~ — PR #17
- ~~#4 Vessel missing board attribute~~ — resolved
- ~~#5 HR stuck after onboarding~~ — nickname fix
- ~~#6 Self-hosted provider/model config~~ — resolved
- ~~#7 No avatars in task tree~~ — resolved
- ~~#9 Shortlist not grouped by role~~ — resolved
- ~~#10 Shortlist candidate details incomplete~~ — resolved
- ~~#12 Department area colors need refresh~~ — resolved

## Related
- [[Idempotency Roadmap]] — Systemic fixes
- [[Tool Reliability Roadmap]] — Tool-specific fixes
