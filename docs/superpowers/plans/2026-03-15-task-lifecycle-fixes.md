# Task Lifecycle Fixes Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove simple task_type, unify all tasks to project lifecycle with mandatory review; remove fail_strategy continue; fix adhoc task leaks; improve CEO escalation prompts; convert task queue to show active task nodes.

**Architecture:** Delete the simple/project task_type distinction. All task nodes go through completed → review → accepted → finished. System nodes (CEO root, review nodes) use `node_type` field for special behavior. Frontend simplified: no project selector dropdown, EA auto-generates project names.

**Tech Stack:** Python (FastAPI, LangChain), Vanilla JS frontend, YAML data files

**Reference:** See `docs/superpowers/plans/2026-03-15-task-lifecycle-analysis.md` for the full issue list and target flowchart.

---

## Chunk 1: Remove task_type and auto-skip logic (Issues 1 & 2)

### Task 1: Remove TaskType enum and classify_task_type from task_lifecycle.py

**Files:**
- Modify: `src/onemancompany/core/task_lifecycle.py:25-34` (TaskType enum)
- Modify: `src/onemancompany/core/task_lifecycle.py:145-184` (classify_task_type + keywords)
- Modify: `src/onemancompany/core/task_lifecycle.py:191-221` (TASK_LIFECYCLE_DOC)

- [ ] **Step 1: Delete TaskType enum and classify_task_type**

Delete lines 22-34 (TaskType enum), lines 144-184 (keywords + classify_task_type function), and update TASK_LIFECYCLE_DOC to remove "simple/project" references.

```python
# DELETE: class TaskType, _PROJECT_KEYWORDS, _SIMPLE_KEYWORDS, classify_task_type()

# UPDATE TASK_LIFECYCLE_DOC — remove simple/project distinction:
TASK_LIFECYCLE_DOC = """## Task Lifecycle States
Every task in the system follows this state machine:

| State | Meaning |
|-------|---------|
| pending | 已创建，等待处理 |
| processing | 正在被某员工执行 |
| holding | 等待子任务完成或外部输入 |
| completed | 员工完成执行，等待上级审核 |
| accepted | 上级审核通过 |
| finished | 全部完成，已归档 |
| failed | 执行失败或审核驳回 |
| blocked | 依赖任务失败，无法继续 |
| cancelled | 已取消 |

State flow:
  pending → processing → completed → accepted → finished
                ↕ holding (pause/resume)
  completed → failed (rejection) → processing (retry)

Key distinctions:
- completed = employee says "I'm done" (awaiting review)
- accepted = supervisor says "looks good" (deliverable approved)
- Only accepted/finished unblock downstream dependent tasks

Task tree model:
- 父任务通过 dispatch_child() 分发子任务给员工
- 子任务完成后，系统自动唤醒父任务进行审核
- 父任务通过 accept_child() / reject_child() 审核每个子任务
- 全部子任务通过 → 父任务自动完成并向上汇报
"""
```

- [ ] **Step 2: Verify no import errors**

Run: `.venv/bin/python -c "from onemancompany.core.task_lifecycle import TaskPhase, TASK_LIFECYCLE_DOC; print('OK')"`
Expected: OK

- [ ] **Step 3: Fix all imports of TaskType and classify_task_type**

Search for `TaskType` and `classify_task_type` across the codebase and remove/fix all references:

Known locations:
- `src/onemancompany/core/task_tree.py:28` — imports `TaskType` (remove if unused after field removal)
- `src/onemancompany/api/routes.py:532` — calls `classify_task_type(task).value` (remove call, no longer needed)
- `src/onemancompany/core/vessel.py:1879` — `is_project = node.task_type == "project"` (change to `run_retrospective=True` always, or use `node_type` check)

- [ ] **Step 4: Verify imports**

Run: `.venv/bin/python -c "from onemancompany.api.routes import router; print('OK')"`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/task_lifecycle.py src/onemancompany/core/task_tree.py src/onemancompany/api/routes.py src/onemancompany/core/vessel.py
git commit -m "refactor: remove TaskType enum and classify_task_type"
```

---

### Task 2: Remove task_type field from TaskNode

**Files:**
- Modify: `src/onemancompany/core/task_tree.py:47` (field definition)
- Modify: `src/onemancompany/core/task_tree.py:158` (to_dict serialization)

- [ ] **Step 1: Remove task_type field from TaskNode dataclass**

In `task_tree.py`, delete line 47:
```python
# DELETE this line:
task_type: str = "simple"         # "simple" | "project"
```

- [ ] **Step 2: Remove task_type from to_dict()**

In `task_tree.py:158`, remove the `"task_type": self.task_type,` line from `to_dict()`.

- [ ] **Step 3: Handle deserialization of existing YAML files**

`from_dict()` uses `__dataclass_fields__` filtering — after removing the field, old YAML with `task_type` key will be silently ignored. No code change needed, but verify:

Run: `.venv/bin/python -c "from onemancompany.core.task_tree import TaskNode; n = TaskNode.from_dict({'id':'test','employee_id':'00001','description':'test','task_type':'simple'}); print('OK, no crash')"`
Expected: OK, no crash

- [ ] **Step 4: Commit**

```bash
git add src/onemancompany/core/task_tree.py
git commit -m "refactor: remove task_type field from TaskNode"
```

---

### Task 3: Remove auto-skip logic from vessel.py

**Files:**
- Modify: `src/onemancompany/core/vessel.py:1082-1087` (post-task auto-skip)
- Modify: `src/onemancompany/core/vessel.py:1200-1210` (resume auto-skip)
- Modify: `src/onemancompany/core/vessel.py:1732` (review node task_type assignment)
- Modify: `src/onemancompany/core/vessel.py:1879` (retrospective gate)

- [ ] **Step 1: Remove auto-skip in _execute_task**

At `vessel.py:1082-1087`, change:
```python
# BEFORE:
node.set_status(TaskPhase.COMPLETED)
# Auto-skip simple tasks: completed → accepted → finished
# so dependency resolution treats them as resolved.
if node.task_type == "simple":
    node.set_status(TaskPhase.ACCEPTED)
    node.set_status(TaskPhase.FINISHED)
save_tree_async(entry.tree_path)

# AFTER:
node.set_status(TaskPhase.COMPLETED)
save_tree_async(entry.tree_path)
```

- [ ] **Step 2: Remove auto-skip in resume_held_task**

At `vessel.py:1200-1210`, same pattern — remove the `if node.task_type == "simple"` block. Keep only:
```python
node.set_status(TaskPhase.COMPLETED)
node.completed_at = datetime.now().isoformat()
save_tree_async(tree_path)
```

- [ ] **Step 3: Remove task_type assignment on review nodes**

At `vessel.py:1732`, delete:
```python
# DELETE this line:
review_node.task_type = "simple"
```

Review nodes are identified by `node_type == "review"`, not `task_type`. But we need to ensure review nodes still auto-complete after the reviewer finishes. Check: review nodes complete → `_on_child_complete_inner` checks `node_type == "review"` at line 1614 → returns early (doesn't trigger another review of the review). This is correct — review nodes are handled by `node_type`, not `task_type`.

**However**, without auto-skip, review nodes will now stop at `completed` and wait for someone to accept them. We need review nodes to auto-complete. Add a `node_type`-based auto-skip:

At `vessel.py:1082` (after `node.set_status(TaskPhase.COMPLETED)`), add:
```python
node.set_status(TaskPhase.COMPLETED)
# System nodes auto-skip review: they don't need to be reviewed themselves
if node.node_type in ("review", "ceo_request"):
    node.set_status(TaskPhase.ACCEPTED)
    node.set_status(TaskPhase.FINISHED)
save_tree_async(entry.tree_path)
```

Same pattern in `resume_held_task`.

- [ ] **Step 4: Fix retrospective gate**

At `vessel.py:1879`, change:
```python
# BEFORE:
is_project = node.task_type == "project"
await self._full_cleanup(
    employee_id, node, agent_error=False,
    project_id=project_id,
    run_retrospective=is_project,
)

# AFTER — always run retrospective for non-system nodes:
is_system_node = node.node_type in ("review", "ceo_request", "ceo_prompt")
await self._full_cleanup(
    employee_id, node, agent_error=False,
    project_id=project_id,
    run_retrospective=not is_system_node,
)
```

- [ ] **Step 5: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.core.vessel import employee_manager; print('OK')"`
Expected: OK

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/core/vessel.py
git commit -m "refactor: remove auto-skip logic, use node_type for system node behavior"
```

---

### Task 4: Clean up tree_tools.py

**Files:**
- Modify: `src/onemancompany/agents/tree_tools.py:225` (child.task_type assignment)

- [ ] **Step 1: Remove task_type assignment in dispatch_child**

At `tree_tools.py:225`, delete:
```python
# DELETE these lines:
# Project tree children default to "project" type for EA review
child.task_type = "project"
```

The `task_type` field no longer exists on `TaskNode`.

- [ ] **Step 2: Verify**

Run: `.venv/bin/python -c "from onemancompany.agents.tree_tools import dispatch_child; print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add src/onemancompany/agents/tree_tools.py
git commit -m "refactor: remove task_type assignment from dispatch_child"
```

---

### Task 5: Remove fail_strategy="continue" (Issue 2)

**Files:**
- Modify: `src/onemancompany/core/task_tree.py:67` (field definition)
- Modify: `src/onemancompany/core/task_tree.py:233` (add_child parameter)
- Modify: `src/onemancompany/core/vessel.py:1766` (dep resolution logic)
- Modify: `src/onemancompany/agents/tree_tools.py` (dispatch_child parameter)

- [ ] **Step 1: Remove fail_strategy field from TaskNode**

At `task_tree.py:67`, delete:
```python
# DELETE:
fail_strategy: str = "block"  # "block" | "continue"
```

Remove from `to_dict()` (line 173) and from `add_child()` parameter (line 233).

- [ ] **Step 2: Simplify dep resolution in vessel.py**

At `vessel.py:1766`, change:
```python
# BEFORE:
if tree.has_failed_deps(dep_node.id) and dep_node.fail_strategy == "block":

# AFTER — always block on failed deps:
if tree.has_failed_deps(dep_node.id):
```

- [ ] **Step 3: Remove fail_strategy from dispatch_child signature**

In `tree_tools.py`, remove `fail_strategy` parameter from `dispatch_child()` function signature and the corresponding `fail_strategy=fail_strategy` in the `tree.add_child()` call.

- [ ] **Step 4: Verify**

Run: `.venv/bin/python -c "from onemancompany.core.vessel import employee_manager; from onemancompany.agents.tree_tools import dispatch_child; print('OK')"`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/task_tree.py src/onemancompany/core/vessel.py src/onemancompany/agents/tree_tools.py
git commit -m "refactor: remove fail_strategy field, always block on failed deps"
```

---

## Chunk 2: Frontend simplification (Issues 1 & 5)

### Task 6: Remove project-select dropdown, simplify CEO input

**Files:**
- Modify: `frontend/index.html:86-93` (project-select dropdown)
- Modify: `frontend/app.js:785-797` (project selector event listener)
- Modify: `frontend/app.js:5064-5077` (submitTask project logic)
- Modify: `frontend/app.js:5667-5689` (loadActiveProjects)

- [ ] **Step 1: Replace project-select dropdown in index.html**

At `index.html:86-93`, replace:
```html
<!-- BEFORE: -->
<div class="ceo-project-selector">
  <select id="project-select">
    <option value="">&#128203; Simple Task</option>
    <option value="__new__">&#10133; Create New Project...</option>
    <option value="__qa__">&#128172; Q&amp;A Mode (no project)</option>
  </select>
  <input id="new-project-name" class="hidden" placeholder="Project name..." />
</div>

<!-- AFTER: -->
<div class="ceo-project-selector">
  <select id="project-select">
    <option value="">&#128203; New Task</option>
    <option value="__qa__">&#128172; Q&amp;A Mode</option>
  </select>
</div>
```

Keep the select element (for Q&A mode) and active project list (loaded dynamically). Remove `__new__` option and `new-project-name` input.

- [ ] **Step 2: Simplify submitTask in app.js**

At `app.js:5064-5077`, remove the `__new__` / project name logic:
```javascript
// BEFORE:
const projectId = projectSelect ? projectSelect.value : '';
let projectName = '';
if (projectId === '__new__') {
    const nameInput = document.getElementById('new-project-name');
    projectName = nameInput ? nameInput.value.trim() : '';
    if (!projectName) { alert('Enter project name'); return; }
}
...
if (projectName) reqBody.project_name = projectName;

// AFTER:
const projectId = projectSelect ? projectSelect.value : '';
// No more project name input — EA auto-generates project names
```

- [ ] **Step 3: Remove project selector change listener**

At `app.js:785-797`, remove the event listener that toggles `new-project-name` visibility:
```javascript
// DELETE this block:
projectSelect.addEventListener('change', () => {
    if (projectSelect.value === '__new__') {
        newProjectName.classList.remove('hidden');
        newProjectName.focus();
    } else {
        newProjectName.classList.add('hidden');
        newProjectName.value = '';
    }
});
```

- [ ] **Step 4: Update loadActiveProjects**

In `loadActiveProjects()`, the dynamic options for existing projects should still be loaded. Just verify it still works after removing `__new__` option. No code change needed.

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/app.js
git commit -m "feat: simplify CEO input — remove project selector, keep Q&A mode"
```

---

### Task 7: Convert task queue to show active task nodes (Issue 5)

**Files:**
- Modify: `frontend/app.js` (task queue rendering)
- Modify: `src/onemancompany/api/routes.py` (add API endpoint for active nodes)

- [ ] **Step 1: Find current task queue implementation**

Search `app.js` for task queue/panel rendering code. Identify the data source (API endpoint) and rendering function.

- [ ] **Step 2: Add backend API for active task nodes**

Add endpoint `GET /api/tasks/active` in `routes.py` that scans all project trees for nodes with `status == "processing"`:

```python
@router.get("/api/tasks/active")
async def get_active_tasks() -> dict:
    """Return all currently processing task nodes across all projects."""
    from onemancompany.core.project_archive import get_project_dir
    from onemancompany.core.task_tree import get_tree

    active = []
    projects_dir = Path(COMPANY_DIR) / "projects"
    if not projects_dir.exists():
        return {"tasks": []}

    for pdir in projects_dir.iterdir():
        if not pdir.is_dir():
            continue
        tree_path = pdir / "task_tree.yaml"
        if not tree_path.exists():
            continue
        tree = get_tree(str(tree_path))
        if not tree:
            continue
        for node in tree._nodes.values():
            if node.status == "processing":
                active.append({
                    "node_id": node.id,
                    "employee_id": node.employee_id,
                    "description": node.description[:200],
                    "project_id": tree.project_id,
                    "project_dir": str(pdir),
                    "created_at": node.created_at,
                })
    return {"tasks": active}
```

- [ ] **Step 3: Update frontend task queue to use active nodes API**

Replace the task queue data source to call `/api/tasks/active` and render cards with employee name, description, project. On click → open project task tree and select the node.

```javascript
// In the task queue refresh function:
async refreshTaskQueue() {
    const resp = await fetch('/api/tasks/active');
    const data = await resp.json();
    const panel = document.getElementById('task-panel-list');
    panel.innerHTML = '';
    for (const t of data.tasks || []) {
        const card = document.createElement('div');
        card.className = 'task-card processing';
        const empName = this.employeeNames?.[t.employee_id] || t.employee_id;
        card.innerHTML = `
            <div class="task-header"><span class="task-employee">${empName}</span></div>
            <div class="task-desc">${this._escHtml(t.description)}</div>
        `;
        card.style.cursor = 'pointer';
        card.addEventListener('click', () => this._openTaskInBoard(t.project_id, t.node_id));
        panel.appendChild(card);
    }
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/app.js src/onemancompany/api/routes.py
git commit -m "feat: task queue shows active processing nodes instead of adhoc tasks"
```

---

## Chunk 3: Agent prompt updates (Issues 1 & 4)

### Task 8: Update EA prompt — remove simple/project, add auto-naming

**Files:**
- Modify: `src/onemancompany/agents/ea_agent.py:63-67` (Simple vs Project section)
- Modify: `src/onemancompany/agents/ea_agent.py` (add project naming instruction)

- [ ] **Step 1: Remove Simple vs Project section from EA prompt**

Replace lines 63-67:
```python
# BEFORE:
## Simple vs Project Tasks
- **Simple**: 单一操作任务 — 发邮件、查信息等. ...
- **Project**: 多步骤交付任务 — 开发、设计等. ...

# AFTER:
## Task Completion
All tasks go through review. After you complete your work or after all dispatched children
are accepted, your supervisor (or CEO) reviews and accepts your deliverable.
Do NOT assume any task will auto-complete — always ensure quality before marking done.
```

- [ ] **Step 2: Add project auto-naming instruction to EA prompt**

Add to the EA prompt (after the Task Flow section):
```python
## Project Naming
When you receive a NEW task from CEO (not a followup to an existing project):
- Analyze the CEO's request and generate a concise project name (2-6 words, Chinese or English matching CEO's language)
- Call set_project_name(name) to set it
- Do NOT ask CEO for a project name — generate it yourself based on the task content
- Examples: "官网视频制作", "Q2 Marketing Campaign", "员工培训体系搭建"
```

- [ ] **Step 3: Commit**

```bash
git add src/onemancompany/agents/ea_agent.py
git commit -m "feat: update EA prompt — remove simple/project, add auto project naming"
```

---

### Task 9: Expand CEO escalation guidance in base.py (Issue 4)

**Files:**
- Modify: `src/onemancompany/agents/base.py:355-358` (CEO communication section)

- [ ] **Step 1: Expand the CEO escalation prompt**

At `base.py:357`, replace the single line:
```python
# BEFORE:
"- **CEO communication**: Use dispatch_child(\"00001\", description) to escalate issues to CEO.\n"

# AFTER:
"- **CEO escalation**: Use dispatch_child(\"00001\", description) to request CEO help. "
"Escalate when:\n"
"  - You need to purchase something (API keys, SaaS subscriptions, domains, etc.)\n"
"  - You need actions outside the system (manual approval, signing contracts, legal compliance)\n"
"  - You need external accounts or access permissions created\n"
"  - The task exceeds your capabilities and cannot be delegated to another employee\n"
"  - The task involves external commitments or brand representation\n"
"  - You are blocked and no available tool or colleague can unblock you\n"
```

- [ ] **Step 2: Verify**

Run: `.venv/bin/python -c "from onemancompany.agents.base import EmployeeAgent; print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add src/onemancompany/agents/base.py
git commit -m "feat: expand CEO escalation guidance for all employees"
```

---

## Chunk 4: Fix adhoc task leaks (Issue 3)

> **Note**: Issue 3 (adhoc task bypass) is the most complex change. It requires modifying the hiring flow to use dispatch_child within the project tree instead of creating standalone adhoc tasks. This task should be done carefully and may need further analysis of the hiring flow state machine. The three call sites in the issue list (`coo_agent.py:850`, `routes.py:3651`, `routes.py:4059`) need to be converted to use the project tree context.

### Task 10: Audit and fix adhoc task calls in project context

**Files:**
- Modify: `src/onemancompany/agents/coo_agent.py:850` (request_hiring)
- Modify: `src/onemancompany/api/routes.py:3651` (_notify_coo_hire_ready)
- Modify: `src/onemancompany/api/routes.py:4059` (_do_batch_hire COO notification)

- [ ] **Step 1: Read the three call sites in detail**

Read the full context (50 lines around each call site) to understand the current data flow and what project/tree context is available.

- [ ] **Step 2: Fix request_hiring in coo_agent.py**

The COO's `request_hiring` currently calls `_push_adhoc_task(HR_ID, jd)`. Instead, it should:
1. Use `dispatch_child(HR_ID, jd)` — this creates an HR child node in the current project tree
2. Return `__HOLDING:hire_id=...` so the COO node enters holding state while waiting for HR

The COO already has tree context (project_dir, task_id) available via closure variables in the tool factory.

- [ ] **Step 3: Fix hire-ready callback in routes.py:3651**

`_notify_coo_hire_ready` currently calls `_push_adhoc_task(COO_ID, ...)`. Instead, it should:
1. Find the COO's HOLDING node in the project tree (by matching hire_id in the holding metadata)
2. Call `resume_held_task(coo_employee_id, node_id, result)` to resume it

- [ ] **Step 4: Fix batch-hire callback in routes.py:4059**

Same pattern as Step 3 — resume the COO's HOLDING node instead of creating a new adhoc task.

- [ ] **Step 5: Verify the three legitimate adhoc calls are untouched**

Confirm these still use `_push_adhoc_task` (they have no project tree context):
- `routes.py:1045` — CEO meeting room → COO
- `routes.py:1246` — quarterly review → HR
- `routes.py:4731` — outsource contract → CSO

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/agents/coo_agent.py src/onemancompany/api/routes.py
git commit -m "fix: hiring flow uses project tree instead of adhoc tasks"
```

---

## Chunk 5: Backend support for EA auto-naming

### Task 11: Add set_project_name tool and API

**Files:**
- Modify: `src/onemancompany/api/routes.py` (add rename endpoint)
- Modify: `src/onemancompany/agents/tree_tools.py` (add set_project_name tool)

- [ ] **Step 1: Check if project rename API already exists**

Search routes.py for existing project rename/update endpoint.

- [ ] **Step 2: Add or modify project rename API**

Add `PATCH /api/projects/{project_id}/name` endpoint:
```python
@router.patch("/api/projects/{project_id}/name")
async def rename_project(project_id: str, body: dict) -> dict:
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    from onemancompany.core.project_archive import rename_project
    rename_project(project_id, name)
    return {"status": "ok", "name": name}
```

- [ ] **Step 3: Add set_project_name as EA tool**

In `tree_tools.py`, add a tool that calls the rename API internally:
```python
@tool
def set_project_name(name: str) -> dict:
    """Set the display name for the current project. Call this when you first receive a new CEO task."""
    # Implementation: update project metadata
```

- [ ] **Step 4: Add frontend editable project name**

In the project detail view, make the project name clickable/editable. On blur/enter, call `PATCH /api/projects/{id}/name`.

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/api/routes.py src/onemancompany/agents/tree_tools.py frontend/app.js
git commit -m "feat: add set_project_name tool for EA auto-naming, CEO can edit in UI"
```

---

## Post-Implementation Verification

After all tasks are complete:

- [ ] **Full import check**: `.venv/bin/python -c "from onemancompany.api.routes import router; from onemancompany.core.vessel import employee_manager; print('ALL OK')"`
- [ ] **Start server**: `.venv/bin/python -m onemancompany` — verify no startup errors
- [ ] **Manual test**: Submit a CEO task, verify EA receives it, dispatches children, children complete at `completed` (not auto-skipping to finished), review triggers correctly
- [ ] **Verify Q&A mode still works**: Select Q&A in frontend, submit question, get answer
