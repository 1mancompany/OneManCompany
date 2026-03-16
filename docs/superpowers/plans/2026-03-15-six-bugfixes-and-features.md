# Six Bugfixes and Features Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 6 issues: dep resolution not triggering, task tree prompt/result empty, duplicate COO hire tasks, task tree shift on click, employee level/role display, and avatar support on main page.

**Architecture:** Backend fixes in vessel.py (dep resolution), routes.py (duplicate COO task, tree API, avatar), task_tree.py (to_dict). Frontend fixes in task-tree.js (drawer shift), app.js (roster display + avatars), office.js (canvas avatars). Default avatar via company/human_resource/piggy.jpg.

**Tech Stack:** Python/FastAPI backend, Vanilla JS + Canvas 2D frontend

---

## Chunk 1: Backend Fixes (Issues 1, 2, 3)

### Task 1: Fix dependency resolution not triggering from sync tools

**Problem:** `_trigger_dep_resolution()` in vessel.py:195 uses `asyncio.get_running_loop()` which fails when called from sync LangGraph tool functions (accept_child, reject_child, cancel_child in tree_tools.py). Dependent tasks never auto-start.

**Files:**
- Modify: `src/onemancompany/core/vessel.py:195-207`

- [ ] **Step 1: Fix `_trigger_dep_resolution` to use `call_soon_threadsafe` fallback**

The fix: when no running loop is found, use the same pattern as `_schedule_next` — get the loop from the employee_manager and use `call_soon_threadsafe`.

```python
def _trigger_dep_resolution(project_dir: str, tree, node) -> None:
    """Schedule async dependency resolution after a node becomes terminal."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            employee_manager._resolve_dependencies(tree, node, project_dir)
        )
    except RuntimeError:
        # Called from sync tool context (e.g. accept_child) — no event loop.
        # Use the main loop via call_soon_threadsafe, same pattern as _schedule_next.
        main_loop = getattr(employee_manager, "_loop", None)
        if main_loop and main_loop.is_running():
            main_loop.call_soon_threadsafe(
                main_loop.create_task,
                employee_manager._resolve_dependencies(tree, node, project_dir),
            )
            logger.info("Scheduled dep resolution for {} via call_soon_threadsafe", node.id)
        else:
            logger.warning("No event loop available for dep resolution of {}", node.id)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("Could not schedule dep resolution: {}", e)
```

- [ ] **Step 2: Verify `employee_manager._loop` exists**

Check that `_loop` is set on `EmployeeManager`. Search for `self._loop` in vessel.py. If it doesn't exist, find how `_schedule_next` accesses the loop and use the same approach.

Run: `.venv/bin/python -c "from onemancompany.core.vessel import employee_manager; print(hasattr(employee_manager, '_loop'))"`

- [ ] **Step 3: Commit**

```bash
git add src/onemancompany/core/vessel.py
git commit -m "fix: dep resolution now works from sync tool context via call_soon_threadsafe"
```

---

### Task 2: Fix task tree node prompt and result display

**Problem:** `to_dict()` in task_tree.py returns `description_preview` (200 chars) but NOT `description` or `result`. The tree API loads with `skeleton_only=True`, so full content isn't loaded. Frontend references `node.description` and `node.result` which are undefined.

**Files:**
- Modify: `src/onemancompany/api/routes.py:3141-3190` (tree API endpoint)

- [ ] **Step 1: Load content in the tree API and add description/result to response**

In `get_project_tree()`, after loading the tree, call `tree.load_all_content()` to populate description/result. Then in the node dict construction, add these fields:

```python
@router.get("/api/projects/{project_id}/tree")
async def get_project_tree(project_id: str) -> dict:
    """Get the task tree for a project."""
    tree = _load_project_tree_for_api(project_id)
    if tree is None:
        raise HTTPException(status_code=404, detail="Task tree not found")

    # Load full content (description + result) for display
    tree.load_all_content()

    # ... existing employee_info code ...

    nodes = []
    for n in tree._nodes.values():
        d = n.to_dict()
        # Add description and result for frontend display
        d["description"] = n.description or n.description_preview or ""
        d["result"] = n.result or ""
        # ... existing dependency_status code ...
        d["employee_info"] = employee_info.get(n.employee_id, {})
        nodes.append(d)

    return {
        "project_id": tree.project_id,
        "root_id": tree.root_id,
        "nodes": nodes,
    }
```

- [ ] **Step 2: Commit**

```bash
git add src/onemancompany/api/routes.py
git commit -m "fix: load full content in task tree API so prompt and result display correctly"
```

---

### Task 3: Fix duplicate COO tasks after batch hiring

**Problem:** `batch_hire_candidates()` in routes.py sends TWO tasks to COO:
1. Line 3998: `_notify_coo_hire_ready()` → "新员工就绪通知" (per-employee, when project_id exists)
2. Lines 4040-4052: `_push_adhoc_task(COO_ID, "以下新员工刚入职...")` → "assign_department" (always, for all hires)

Both tasks tell COO to handle the same new employees — redundant.

**Files:**
- Modify: `src/onemancompany/api/routes.py:4039-4052`

- [ ] **Step 1: Skip the assign_department push when `_notify_coo_hire_ready` already fired**

The "新员工就绪通知" is more contextual (includes project info). The "assign_department" is generic. When `_notify_coo_hire_ready` was already called for employees (i.e., coo_ctx has project_id), skip those employees from the assign_department batch. If no employees remain, skip the push entirely.

```python
    # Dispatch COO task to assign departments and roles for new hires
    # Skip employees that already received project-specific COO notification
    hired_entries = [r for r in results if r["status"] == "hired"]
    # Filter out employees already notified via _notify_coo_hire_ready
    notified_ids = set()
    if coo_ctx.get("project_id"):
        for r in hired_entries:
            auth_method_check = pending_candidates.get(batch_id, {}).get(r["candidate_id"], {}).get("auth_method", "api_key")
            if auth_method_check != "oauth":
                notified_ids.add(r["employee_id"])
    unnotified = [r for r in hired_entries if r["employee_id"] not in notified_ids]

    if unnotified:
        emp_lines = "\n".join(
            f"- {r['name']}（{r.get('nickname', '')}）#{r['employee_id']}"
            for r in unnotified
        )
        _push_adhoc_task(
            COO_ID,
            f"以下新员工刚入职，请为他们分配部门和角色。使用 assign_department(employee_id, department, role) 工具逐个分配。\n"
            f"可选部门: Engineering, Design, Analytics, Marketing\n"
            f"角色由你根据员工名称和技能自行判断（如 Engineer, Designer, PM, QA Engineer 等）。\n\n"
            f"{emp_lines}",
        )
```

**Simpler approach:** Since `_notify_coo_hire_ready` already covers the project-hire case (with richer context), and `assign_department` is only useful when no project context exists, just guard the entire block:

```python
    # Only push generic assign_department if there was no project-specific notification
    if not coo_ctx.get("project_id"):
        hired_entries = [r for r in results if r["status"] == "hired"]
        if hired_entries:
            emp_lines = "\n".join(
                f"- {r['name']}（{r.get('nickname', '')}）#{r['employee_id']}"
                for r in hired_entries
            )
            _push_adhoc_task(
                COO_ID,
                f"以下新员工刚入职，请为他们分配部门和角色。使用 assign_department(employee_id, department, role) 工具逐个分配。\n"
                f"可选部门: Engineering, Design, Analytics, Marketing\n"
                f"角色由你根据员工名称和技能自行判断（如 Engineer, Designer, PM, QA Engineer 等）。\n\n"
                f"{emp_lines}",
            )
```

Use the simpler approach — wrap lines 4039-4052 with `if not coo_ctx.get("project_id"):`.

Note: `coo_ctx` is defined earlier as `pending_coo_contexts.pop(batch_id, {})` — re-check its scope. The variable may need to be read from `_pending_project_ctx` or the batch context. Check the exact variable name used earlier in the function.

- [ ] **Step 2: Commit**

```bash
git add src/onemancompany/api/routes.py
git commit -m "fix: avoid duplicate COO task when project-specific hire notification already sent"
```

---

### Task 4: Fix `completed -> completed` transition error

**Problem:** Log shows `Task XXX: illegal transition completed -> completed` at lines 195 and 312. This happens in `_on_child_complete_inner` when it tries to auto-complete a parent that's already completed.

**Files:**
- Modify: `src/onemancompany/core/vessel.py` — `_on_child_complete_inner` around line 1591

- [ ] **Step 1: Guard auto-complete with status check**

Find the line that says "All non-review children of XXX are accepted — auto-completing parent" and add a guard:

```python
# Before auto-completing, check parent isn't already completed
if parent_node.status != "completed":
    parent_node.set_status(TaskPhase.COMPLETED)
```

- [ ] **Step 2: Commit**

```bash
git add src/onemancompany/core/vessel.py
git commit -m "fix: guard parent auto-complete against already-completed status"
```

---

## Chunk 2: Frontend Fixes (Issues 4, 5, 6)

### Task 5: Fix task tree area shift on first click

**Problem:** When a tree node is clicked, `selectNode()` removes `.hidden` from the drawer (280px), which shrinks the SVG canvas and shifts all nodes left.

**Files:**
- Modify: `frontend/style.css:4321-4333`
- Modify: `frontend/task-tree.js:305-315`

- [ ] **Step 1: Change drawer to overlay instead of taking layout space**

Make the drawer use `position: absolute` so it overlays rather than pushing the tree:

In `frontend/style.css`, change `.project-tree-layout` to `position: relative`, and change `.project-tree-drawer` to overlay:

```css
.project-tree-layout {
    display: flex;
    height: 100%;
    gap: 0;
    position: relative;
}

.project-tree-drawer {
    width: 280px;
    position: absolute;
    right: 0;
    top: 0;
    bottom: 0;
    overflow-y: auto;
    padding: 16px;
    background: #111;
    border-left: 1px solid var(--pixel-green-dim, #1a3a2a);
    z-index: 10;
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/style.css
git commit -m "fix: task tree drawer overlays instead of shifting layout"
```

---

### Task 6: Add employee level prefix and role underneath on roster

**Problem:** Roster cards show employee name without level prefix, and no separate role/title line below.

**Files:**
- Modify: `frontend/app.js:677-680`

- [ ] **Step 1: Add level prefix to name and role as subtitle**

Change the roster card HTML in `updateRoster()`:

```javascript
      const levelPrefix = emp.level ? `L${emp.level} ` : '';
      card.innerHTML = `
        <div class="roster-info">
          <div class="roster-name">${roleIcon} ${levelPrefix}${emp.name} ${nn}${guidanceBadge}${remoteBadge}${probationBadge}${pipBadge}</div>
          <div class="roster-role"><span class="roster-empnum">${empNum}</span> ${title}</div>
          <div class="roster-quarter">${(emp.skills || []).slice(0, 3).join(', ')}</div>
        </div>
        <div class="roster-score${scoreClass}">${latestScore}</div>
      `;
```

Key changes:
- Add `L${emp.level}` prefix before employee name
- Move skills to the quarter line (or a new subtitle)
- Keep title on the role line without skills clutter

- [ ] **Step 2: Commit**

```bash
git add frontend/app.js
git commit -m "feat: show employee level prefix and role on roster cards"
```

---

### Task 7: Avatar support on main page + default piggy.jpg

**Problem:** Employee avatars only show in detail modal and task tree, not on the main roster or office canvas. Need default avatar (piggy.jpg) for employees without custom avatars, and use it as founding employee default on install.

**Files:**
- Modify: `frontend/app.js:649-685` (roster cards — add avatar img)
- Modify: `frontend/office.js:553-615` (canvas — use avatar image instead of pixel face when available)
- Modify: `src/onemancompany/api/routes.py:5109-5127` (employees API — add `has_avatar` field)
- Modify: `src/onemancompany/api/routes.py:3206+` (avatar GET endpoint — serve piggy.jpg as fallback)
- Verify: `company/human_resource/piggy.jpg` exists

- [ ] **Step 1: Modify avatar GET endpoint to serve default piggy.jpg when no custom avatar**

In the avatar GET endpoint (around line 3206), instead of returning 404 when avatar doesn't exist, serve the default `company/human_resource/piggy.jpg`:

```python
@router.get("/api/employees/{employee_id}/avatar")
async def get_employee_avatar(employee_id: str):
    """Get employee avatar image, falling back to default piggy."""
    from onemancompany.core.config import get_company_dir
    emp_dir = Path(get_company_dir()) / "employees" / employee_id
    avatar_path = emp_dir / "avatar.png"
    if not avatar_path.exists():
        avatar_path = emp_dir / "avatar.jpg"
    if not avatar_path.exists():
        avatar_path = emp_dir / "avatar.jpeg"
    if avatar_path.exists():
        return FileResponse(avatar_path)
    # Fallback to default piggy
    default = Path(get_company_dir()) / "human_resource" / "piggy.jpg"
    if default.exists():
        return FileResponse(default)
    raise HTTPException(status_code=404, detail="No avatar found")
```

- [ ] **Step 2: Add avatar to roster cards**

In `frontend/app.js` `updateRoster()`, add an avatar `<img>` before the roster-info div:

```javascript
      card.innerHTML = `
        <img class="roster-avatar" src="/api/employees/${emp.id}/avatar?t=${Date.now()}"
             onerror="this.style.display='none'" />
        <div class="roster-info">
          <div class="roster-name">${roleIcon} ${levelPrefix}${emp.name} ${nn}${guidanceBadge}${remoteBadge}${probationBadge}${pipBadge}</div>
          <div class="roster-role"><span class="roster-empnum">${empNum}</span> ${title}</div>
          <div class="roster-quarter">${(emp.skills || []).slice(0, 3).join(', ')}</div>
        </div>
        <div class="roster-score${scoreClass}">${latestScore}</div>
      `;
```

The `.roster-avatar` style already exists (24x24, pixelated). Add `border-radius: 50%` and `object-fit: cover`:

```css
.roster-avatar {
    width: 24px;
    height: 24px;
    border-radius: 50%;
    object-fit: cover;
    image-rendering: pixelated;
}
```

- [ ] **Step 3: Use avatar on office canvas instead of pixel face**

In `frontend/office.js`, preload avatar images and draw them on the canvas when available. In `drawCharacter()`, check if an avatar image is loaded for this employee. If so, draw it clipped as a circle instead of the pixel face.

Add avatar image cache to OfficeRenderer (similar to existing `_toolIcons`):

```javascript
// In constructor or init
this._avatarImages = {};

// Load avatars when employees are updated
_loadAvatars(employees) {
    for (const emp of employees) {
        if (!this._avatarImages[emp.id]) {
            const img = new Image();
            img.src = `/api/employees/${emp.id}/avatar`;
            img.onload = () => {
                this._avatarImages[emp.id] = img;
            };
            // Set to null initially so we don't retry
            this._avatarImages[emp.id] = null;
        }
    }
}
```

In `drawCharacter()`, before the pixel face drawing code (lines 597-615), check for avatar:

```javascript
    const avatarImg = this._avatarImages?.[data.id];
    if (avatarImg) {
        // Draw circular clipped avatar instead of pixel face
        ctx.save();
        ctx.beginPath();
        ctx.arc(px + 12, baseY - 6, 8, 0, Math.PI * 2);
        ctx.clip();
        ctx.drawImage(avatarImg, px + 4, baseY - 14, 16, 16);
        ctx.restore();
    } else {
        // ... existing pixel face code ...
    }
```

- [ ] **Step 4: Copy piggy.jpg as default avatar for founding employees during install**

Find the install/setup code that creates founding employees (00002-00005) and copy piggy.jpg as their default avatar. This may be in the CLI setup or the onboarding code. Search for where employee directories are created.

- [ ] **Step 5: Add CEO avatar to roster card**

Update the CEO card at the top of `updateRoster()` to also show the default avatar:

```javascript
    const ceoCard = document.createElement('div');
    ceoCard.className = 'roster-card';
    ceoCard.innerHTML = `
      <img class="roster-avatar" src="/api/employees/00001/avatar?t=${Date.now()}"
           onerror="this.style.display='none'" />
      <div class="roster-info">
        <div class="roster-name" style="color: #ffd700;">👑 CEO (You)</div>
        <div class="roster-role"><span class="roster-empnum">#00001</span> Chief Executive Officer</div>
      </div>
    `;
```

- [ ] **Step 6: Commit**

```bash
git add frontend/app.js frontend/office.js frontend/style.css src/onemancompany/api/routes.py
git commit -m "feat: avatar on main page + office canvas, default piggy.jpg fallback"
```

---

## Implementation Notes

### Issue 1 (dep resolution): The key insight is that `_schedule_next` (vessel.py:690) already uses `call_soon_threadsafe` successfully. We replicate the same pattern for `_trigger_dep_resolution`.

### Issue 2 (task tree content): `TaskTree.load()` defaults to `skeleton_only=True`, which only stores `description_preview` (200 chars). Calling `load_all_content()` in the API loads full description+result from `nodes/{id}.yaml` files.

### Issue 3 (duplicate COO): The `_notify_coo_hire_ready` path provides richer project-aware context. The `assign_department` path is a generic fallback. Gate it on `not coo_ctx.get("project_id")`.

### Issue 4 (tree shift): Pure CSS fix — make drawer `position: absolute` to overlay rather than consume flex space.

### Issue 5 (level/role): Simple template change in roster card HTML.

### Issue 6 (avatars): Three layers — API endpoint fallback to piggy.jpg, roster card `<img>`, canvas image preloading + circular clip drawing.
