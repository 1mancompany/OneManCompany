---
tags: [roadmap, in-progress, tools]
source: memory/project_tool_reliability_roadmap.md
status: batch-1-done
---

# Tool Reliability Upgrade

Three-batch plan based on Claude Code reliability patterns (2026-03-31).

## Batch 1: System Prompt + Dedicated Tools + Template ✅
- System prompt "use X for Y" tool selection matrix
- `update_work_principles` + `update_guidance` dedicated tools
- Standard tool template at `docs/tool-template.md`
- **Why first:** Highest ROI — one prompt change affects all agents

## Batch 2: Descriptions + Error Format — TODO
- Upgrade 10 adequate/poor tools with detailed descriptions
- Standardize error format: `{"status": "error", "error_code": "...", "message": "...", "hint": "..."}`
- Apply template to all existing tools
- **Why second:** Improves LLM understanding and error recovery

## Batch 3: Validation + Verification — TODO
- Parameter validation on critical tools (employee_id format, file_path, timeout range)
- Schema validation layer before execution
- `"next_step"` field in results: `"Verify by reading the file"`
- **Why third:** Defense-in-depth, depends on Batch 2

## Reference
- 35 tools audited (see tool inventory)
- 7-layer reliability stack: design, instruction, selection, results, orchestration, recovery, guidance

## Related
- [[Coding Standards]] — Tool error handling patterns
- [[Design Principles]] — Systematic design applies to tools too
