# Task Tree Redesign — Design Document

## Problems

1. **Follow-up overwrites tree**: `task_followup` endpoint creates a new empty `TaskTree()` and overwrites `task_tree.yaml`, losing all previous nodes.
2. **Task queue status inaccurate**: Already fixed — now aggregates from tree node statuses with priority ordering.
3. **Visual quality low**: Node cards are minimal (just text), connections are plain, no avatars or role info displayed prominently.

## Design

### Data Layer Fix

**Follow-up appends, not replaces.** When CEO sends a follow-up instruction to an existing project:
- Load existing tree from disk
- Create new child node under root (not a new tree)
- Map new task to the new child node
- Save — existing nodes preserved

### Visual Upgrade (Paperclip-inspired)

**Node card redesign:**
- Larger cards (220×90px)
- Left: circular avatar (or initials fallback)
- Top-right: display name (nickname) + role badge
- Center: task description (truncated)
- Bottom: status pill (colored background + text) + cost indicator
- Left border: 4px status color bar (keep current pattern)

**Animated connections:**
- SVG `<path>` with smooth cubic bezier curves
- `stroke-dasharray` + `stroke-dashoffset` animation for "flowing" effect on active connections (processing status)
- Static connections for completed/accepted (solid green)
- Dimmed connections for failed/cancelled (gray dashed)

**Connection color by child status:**
- processing → animated blue flow
- pending → gray dotted
- completed → solid amber
- accepted → solid green
- failed → solid red
- cancelled → gray dashed

**Transitions:**
- New nodes fade in + slide down on `addNode()`
- Status changes trigger brief pulse animation on the node card

### No Changes To

- Task tree data model (TaskNode fields)
- D3.js dependency (already works well)
- Detail drawer (right panel on click)
- Tree API endpoints
