# Brutalist Trace Viewer — Design Spec

## Overview

A two-layer trace viewer for task execution, styled in brutalist/terminal aesthetic to match the pixel art office theme.

## Data Architecture

**Single source of truth:** `nodes/{node_id}/execution.log` (JSONL on disk)

- Tree structure from `task_tree.yaml` (parent-child relationships)
- Per-node execution trace from `execution.log` (timestamped JSONL)
- API: `GET /api/node/{node_id}/logs?tail=500`

## Two-Layer View

### Layer 1: Tree Overview

Shows the task tree hierarchy with status, duration, cost per node.

```
░░ CEO ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 0s
░░ └ EA (玲珑阁) ━━━━━━━━━━━━━━━━━━━━━━━ 2m30s
██ │ ├ COO (铁面侠) ━━━━━━━━━━━━━━━━━━━ 5m12s
██ │ │ ├ 管弦 [COMPLETED] ━━━━━━━━━━━━ 3m20s     ← click
░░ │ │ ├ REVIEW ━━━━━━━━━━━━━━━━━━━━━ 1m40s
▓▓ │ │ └ CEO_REQUEST [PROCESSING] ━━━ ....
░░ │ └ REVIEW ━━━━━━━━━━━━━━━━━━━━━━━ 50s
```

Data per node:
- Employee name/nickname
- Node type icon (task/review/ceo_request/watchdog)
- Status with color block
- Duration (created_at → completed_at)
- Cost ($)

### Layer 2: Node Execution Trace

Detailed step-by-step trace when a node is selected. Reads from `execution.log`.

```
10:04:22 ┃ START ▌ Develop Python Project...
         ┃
10:04:25 ┃ TOOL ▌ bash
         ┃ ╭─ ls -R src/
         ┃ ╰─ → __init__.py A_star.py DP.py...
         ┃
10:04:30 ┃ LLM ▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌
         ┃ Analyzing requirements... (click to expand)
         ┃
10:05:01 ┃ TOOL ▌ write
         ┃ ╭─ main.py (180 lines)
         ┃ ╰─ → OK
         ┃
10:07:42 ┃ RESULT ████████████████████████
         ┃ Project initialized successfully
```

Log types and rendering:

| Type | Display | Color |
|------|---------|-------|
| start | START block with task description | white |
| tool_call + tool_result | Grouped as one TOOL step with ╭╰ | ice blue #4af |
| llm_output | LLM block with fill bar (width = token estimate) | amber #fa4 |
| result | RESULT block with fill bar | green #4a4 |
| error | ERROR block | red #f44 |
| holding | HOLDING with reason | yellow #ff4 |
| resumed | RESUMED | green |
| cancelled | CANCELLED | red |

### Grouping Rules

1. `tool_call` immediately followed by `tool_result` (same tool name) → merge into single TOOL step
2. Duplicate `tool_result` entries (raw + parsed) → show only the parsed one (shorter)
3. Long content (>200 chars) → collapsed by default, "click to expand"
4. `llm_output` → show first 2 lines as summary, rest collapsed

## Visual Style: Brutalist

### Typography
- All monospace (system mono or JetBrains Mono)
- No font size variation except headers
- No italic, no serif

### Colors
- Background: #0a0a0a (near black)
- Primary text: #d4d4d4 (light gray)
- Timestamps: #666
- Tool labels: #4af (ice blue)
- LLM labels: #fa4 (amber)
- Success: #4a4 (dark green)
- Error: #f44 (red)
- Processing: #ff4 (yellow)
- Tree lines/borders: #333

### Layout
- No border-radius anywhere (all sharp corners)
- Box-drawing characters for tree lines (━ ┃ ├ └ ╭ ╰)
- Status shown as inline color blocks `[COMPLETED]`
- Dense layout, minimal padding (2-4px)
- Full-width, no cards — flat structure

### Interactions
- Tree nodes: click to select → loads trace in detail panel
- Trace steps: click to expand/collapse long content
- Auto-scroll to bottom for live traces (running tasks)
- Sticky header with node summary (name, status, cost, duration)

## Embedding Points

The trace viewer component can be embedded in:
1. **Project detail panel** — full view with tree + trace
2. **Employee detail modal** — filtered to that employee's nodes
3. **Task Queue** — click a task → show its tree + trace
4. **Standalone modal** — accessible from task tree visualization (D3)

## Implementation Plan

### Phase 1: Backend (PR #127 — done)
- [x] Single source of truth: node execution.log JSONL
- [x] GET /api/node/{node_id}/logs endpoint
- [x] Taskboard status filtering

### Phase 2: Tree Overview Component
- [ ] New frontend component: `TraceTreeView`
- [ ] Fetches /api/projects/{id}/tree for hierarchy
- [ ] Renders brutalist tree with box-drawing chars
- [ ] Click node → fetches /api/node/{node_id}/logs

### Phase 3: Node Trace Component
- [ ] New frontend component: `NodeTraceView`
- [ ] Parses JSONL log entries
- [ ] Groups tool_call + tool_result
- [ ] Deduplicates raw/parsed tool_result
- [ ] Collapsible long content
- [ ] Live auto-scroll for running tasks

### Phase 4: Integration
- [ ] Embed in project detail panel
- [ ] Embed in employee detail modal
- [ ] Embed in task queue click handler
- [ ] WebSocket live updates (agent_log events append to trace)
