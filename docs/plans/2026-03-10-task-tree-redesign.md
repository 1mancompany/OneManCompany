# Task Tree Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix follow-up tree overwrite bug and upgrade task tree visuals with animated connections, avatar cards, and status-colored flows.

**Architecture:** Task 1 fixes the backend follow-up endpoint to append to existing trees. Tasks 2-3 upgrade the frontend `task-tree.js` renderer with larger node cards (avatar + name/role + status pill) and animated SVG connection lines that flow/pulse based on child node status. CSS-only animations via `stroke-dasharray` keyframes.

**Tech Stack:** D3.js (existing), SVG animations, CSS keyframes, vanilla JS

---

### Task 1: Fix follow-up tree overwrite

**Files:**
- Modify: `src/onemancompany/api/routes.py:558-567` (task_followup endpoint)

**Step 1: Fix the follow-up to append instead of replace**

Replace lines 558-567 in `task_followup`:

```python
    # Append to existing tree (or create new if none exists)
    tree_path = Path(pdir) / "task_tree.yaml"
    if tree_path.exists():
        tree = TaskTree.load(tree_path, project_id=project_id)
    else:
        tree = TaskTree(project_id=project_id)

    ea_loop = get_agent_loop(EA_ID)
    if not ea_loop:
        raise HTTPException(status_code=503, detail="EA agent not available")

    ea_agent_task = ea_loop.push_task(followup_task, project_id=project_id, project_dir=pdir)

    if tree.root_id:
        # Append as new child under existing root
        child = tree.add_child(
            parent_id=tree.root_id,
            employee_id=EA_ID,
            description=instructions,
        )
        tree.task_id_map[ea_agent_task.id] = child.id
    else:
        # No root yet — create one
        root = tree.create_root(employee_id=EA_ID, description=instructions)
        tree.task_id_map[ea_agent_task.id] = root.id

    _save_project_tree(pdir, tree)
```

Key change: Load existing tree from disk instead of creating empty `TaskTree()`. Add follow-up as child node under root. Previous nodes preserved.

**Step 2: Commit**

```bash
git commit -m "fix: follow-up appends to existing task tree instead of overwriting"
```

---

### Task 2: Upgrade node cards with avatars and status pills

**Files:**
- Modify: `frontend/task-tree.js:14-18` (dimensions), `frontend/task-tree.js:90-138` (node rendering)
- Modify: `frontend/style.css:3760-3800` (tree node styles)

**Step 1: Update dimensions and add SVG defs**

In `task-tree.js`, update constructor dimensions:

```javascript
this.nodeWidth = 220;
this.nodeHeight = 90;
this.levelSep = 100;
this.sibSep = 30;
```

Add status labels map after STATUS_COLORS:

```javascript
static STATUS_LABELS = {
    pending: 'PENDING',
    processing: 'RUNNING',
    completed: 'DONE',
    accepted: 'ACCEPTED',
    failed: 'FAILED',
    cancelled: 'CANCELLED',
    holding: 'HOLDING',
};
```

**Step 2: Rewrite node rendering in `render()`**

Replace the node rendering section (after `treeLayout(root);`, the `nodeGroups` block) with:

```javascript
    const nodeGroups = this.g.selectAll('.tree-node')
        .data(root.descendants())
        .enter()
        .append('g')
        .attr('class', 'tree-node')
        .attr('transform', d => `translate(${d.x}, ${d.y})`)
        .style('cursor', 'pointer')
        .on('click', (event, d) => this.selectNode(d.data));

    // Card background
    nodeGroups.append('rect')
        .attr('x', -this.nodeWidth / 2)
        .attr('y', -this.nodeHeight / 2)
        .attr('width', this.nodeWidth)
        .attr('height', this.nodeHeight)
        .attr('rx', 8)
        .attr('class', 'tree-node-card');

    // Status color bar (left edge)
    nodeGroups.append('rect')
        .attr('x', -this.nodeWidth / 2)
        .attr('y', -this.nodeHeight / 2)
        .attr('width', 4)
        .attr('height', this.nodeHeight)
        .attr('rx', 2)
        .attr('fill', d => TaskTreeRenderer.STATUS_COLORS[d.data.status] || '#666');

    // Avatar circle
    nodeGroups.each(function(d) {
        const g = d3.select(this);
        const info = d.data.employee_info || {};
        const ax = -d.data._nodeWidth / 2 + 24;  // won't work, use constant
        const cx = -(220 / 2) + 24;
        const cy = -8;

        if (info.avatar_url) {
            // Clip path for circular avatar
            const clipId = `clip-${d.data.id}`;
            g.append('clipPath').attr('id', clipId)
                .append('circle').attr('cx', cx).attr('cy', cy).attr('r', 14);
            g.append('image')
                .attr('href', info.avatar_url)
                .attr('x', cx - 14).attr('y', cy - 14)
                .attr('width', 28).attr('height', 28)
                .attr('clip-path', `url(#${clipId})`);
        } else {
            g.append('circle')
                .attr('cx', cx).attr('cy', cy).attr('r', 14)
                .attr('class', 'tree-avatar-fallback');
            g.append('text')
                .attr('x', cx).attr('y', cy + 4)
                .attr('text-anchor', 'middle')
                .attr('class', 'tree-avatar-text')
                .text((info.nickname || info.name || d.data.employee_id || '').slice(0, 2));
        }
    });

    // Name + role
    nodeGroups.append('text')
        .attr('x', -this.nodeWidth / 2 + 46)
        .attr('y', -this.nodeHeight / 2 + 22)
        .attr('class', 'tree-node-name')
        .text(d => {
            const info = d.data.employee_info || {};
            return info.nickname || info.name || d.data.employee_id;
        });

    nodeGroups.append('text')
        .attr('x', -this.nodeWidth / 2 + 46)
        .attr('y', -this.nodeHeight / 2 + 36)
        .attr('class', 'tree-node-role')
        .text(d => {
            const info = d.data.employee_info || {};
            return info.role || '';
        });

    // Description
    nodeGroups.append('text')
        .attr('x', -this.nodeWidth / 2 + 14)
        .attr('y', -this.nodeHeight / 2 + 56)
        .attr('class', 'tree-node-desc')
        .text(d => {
            const desc = d.data.description || '';
            return desc.substring(0, 30) + (desc.length > 30 ? '...' : '');
        });

    // Status pill (rounded rect + text)
    const pills = nodeGroups.append('g')
        .attr('transform', d => `translate(${this.nodeWidth / 2 - 60}, ${this.nodeHeight / 2 - 18})`);

    pills.append('rect')
        .attr('width', 52)
        .attr('height', 14)
        .attr('rx', 7)
        .attr('fill', d => TaskTreeRenderer.STATUS_COLORS[d.data.status] || '#666')
        .attr('opacity', 0.2);

    pills.append('text')
        .attr('x', 26)
        .attr('y', 10)
        .attr('text-anchor', 'middle')
        .attr('class', 'tree-node-pill-text')
        .attr('fill', d => TaskTreeRenderer.STATUS_COLORS[d.data.status] || '#666')
        .text(d => TaskTreeRenderer.STATUS_LABELS[d.data.status] || d.data.status);
```

**Step 3: Update CSS for new node elements**

Add/replace in `style.css` after existing tree styles:

```css
.tree-node-card {
    fill: #12121e;
    stroke: #2a2a3e;
    stroke-width: 1;
    transition: stroke 0.2s;
}

.tree-node-card.selected {
    stroke: var(--pixel-green, #00ff88);
    stroke-width: 2;
}

.tree-node:hover .tree-node-card {
    stroke: var(--pixel-cyan, #00ddff);
    stroke-opacity: 0.8;
}

.tree-avatar-fallback {
    fill: #1e1e3a;
    stroke: #444;
    stroke-width: 1;
}

.tree-avatar-text {
    fill: #888;
    font-family: var(--font-pixel, monospace);
    font-size: 10px;
}

.tree-node-name {
    fill: #fff;
    font-family: var(--font-pixel, monospace);
    font-size: 12px;
    font-weight: bold;
}

.tree-node-role {
    fill: #666;
    font-family: var(--font-pixel, monospace);
    font-size: 9px;
}

.tree-node-desc {
    fill: #999;
    font-family: var(--font-pixel, monospace);
    font-size: 10px;
}

.tree-node-pill-text {
    font-family: var(--font-pixel, monospace);
    font-size: 8px;
    font-weight: bold;
    text-transform: uppercase;
}
```

**Step 4: Commit**

```bash
git commit -m "feat: upgraded task tree node cards with avatars, roles, status pills"
```

---

### Task 3: Animated connection lines

**Files:**
- Modify: `frontend/task-tree.js:81-88` (link rendering)
- Modify: `frontend/style.css` (add animation keyframes)

**Step 1: Replace link rendering with status-colored animated paths**

Replace the `.tree-link` rendering block in `render()` with:

```javascript
    // Connection lines — colored and animated by child status
    this.g.selectAll('.tree-link')
        .data(root.links())
        .enter()
        .append('path')
        .attr('class', d => {
            const status = d.target.data.status;
            return `tree-link tree-link-${status}`;
        })
        .attr('d', d3.linkVertical()
            .x(d => d.x)
            .y(d => d.y));
```

**Step 2: Add CSS for connection animations**

Add to `style.css`:

```css
/* Base link style */
.tree-link {
    fill: none;
    stroke-width: 2;
    stroke-linecap: round;
}

/* Status-specific link colors */
.tree-link-processing {
    stroke: #4a9eff;
    stroke-width: 2.5;
    stroke-dasharray: 8 4;
    animation: tree-flow 0.8s linear infinite;
}

.tree-link-holding {
    stroke: #ff8800;
    stroke-width: 2;
    stroke-dasharray: 6 6;
    animation: tree-flow 1.5s linear infinite;
}

.tree-link-pending {
    stroke: #444;
    stroke-dasharray: 3 5;
}

.tree-link-completed {
    stroke: #ffaa00;
    stroke-width: 2;
}

.tree-link-accepted {
    stroke: #00ff88;
    stroke-width: 2;
}

.tree-link-failed {
    stroke: #ff4444;
    stroke-dasharray: 4 4;
}

.tree-link-cancelled {
    stroke: #555;
    stroke-dasharray: 2 4;
    opacity: 0.5;
}

/* Flowing animation for active connections */
@keyframes tree-flow {
    to {
        stroke-dashoffset: -12;
    }
}
```

The `stroke-dasharray` + `stroke-dashoffset` animation creates a flowing particle effect on processing/holding links. Accepted links are solid green. Pending are dotted gray. Failed are dashed red.

**Step 3: Add node entrance animation**

In `task-tree.js` `render()`, after creating nodeGroups, add initial opacity and transition:

```javascript
    // Animate nodes in
    nodeGroups
        .attr('opacity', 0)
        .transition()
        .duration(300)
        .delay((d, i) => i * 50)
        .attr('opacity', 1);
```

**Step 4: Commit**

```bash
git commit -m "feat: animated status-colored connection lines in task tree"
```

---

## Summary

| Task | What | Key Change |
|------|------|------------|
| 1 | Fix follow-up overwrite | Load existing tree, append child under root |
| 2 | Node card upgrade | Avatar + name/role + description + status pill |
| 3 | Animated connections | Status-colored links with CSS flow animation |

**Scope**: `task-tree.js`, `style.css`, `routes.py:task_followup`. No data model changes. No new dependencies.
