# Session State (Working Memory)

## Current Focus
P1 System Optimization — improving runtime efficiency, idempotency, and user control.

## P0 Status (ALL DONE)
- P0.1 store.py atomic writes — PR #176 merged
- P0.2 CEO conversation async lock — PR #177 merged  
- P0.3 abort paths use safe_cancel — PR #178 merged

## P1 Queue (TODO)
- P1.1 Employee pause/resume (abort is too destructive)
- P1.2 Task priority system (pure FIFO is insufficient)
- P1.3 Real-time task progress (CEO blind during execution)
- P1.4 Inject instructions into running tasks

## Active Skills
- self-improving-agent (semantic + episodic memory initialized)
- ontology (knowledge graph with 7 tasks tracked)
- proactive-agent (WAL protocol active)

## Key Decisions
- All PRs require CEO explicit "merge" — never auto-merge
- Code review mandatory on ALL changes (add-feat, fix-bug, minor-change)
- TDD: tests before implementation
- Use /add-feat, /fix-bug, /minor-change skills for all work

## CEO Preferences
- Cyberpunk aesthetic for terminal UI
- Systematic design, not patches
- InquirerPy for all interactive prompts
- Agent family = hosting field (company/self/openclaw)
