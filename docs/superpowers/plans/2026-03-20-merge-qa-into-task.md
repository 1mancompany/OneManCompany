# Merge Q&A into Task Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bare-LLM Q&A endpoint with EA agent dispatch, adding a `mode` field to TaskTree that controls dispatch restrictions and retrospective behavior.

**Architecture:** Add `mode: "simple"|"standard"` to TaskTree. Q&A becomes `ceo_submit_task(mode="simple")`. In simple mode, EA dispatch skips O-level restriction and cleanup skips retrospective.

**Tech Stack:** Python (FastAPI, LangChain), vanilla JS frontend, YAML persistence

**Spec:** `docs/superpowers/specs/2026-03-20-merge-qa-into-task-design.md`

**Note:** Spec says mode should flow through `project.yaml` metadata. This plan simplifies: `mode` goes directly from request body to `TaskTree(mode=mode)` in `ceo_submit_task`. No project.yaml indirection needed since the tree is created in the same function.

---

### Task 1: Add `mode` field to TaskTree

**Files:**
- Modify: `src/onemancompany/core/task_tree.py:214-227` (TaskTree.__init__)
- Modify: `src/onemancompany/core/task_tree.py:395-427` (save/load)
- Test: `tests/unit/core/test_task_tree.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/core/test_task_tree.py`:

```python
class TestTaskTreeMode:
    def test_default_mode_is_standard(self):
        tree = TaskTree(project_id="p1")
        assert tree.mode == "standard"

    def test_mode_set_on_init(self):
        tree = TaskTree(project_id="p1", mode="simple")
        assert tree.mode == "simple"

    def test_mode_persisted_in_save(self, tmp_path):
        tree = TaskTree(project_id="p1", mode="simple")
        root = tree.create_root(employee_id="00001", description="test")
        root.set_status(TaskPhase.PROCESSING)
        tree.save(tmp_path / "tree.yaml")
        loaded = TaskTree.load(tmp_path / "tree.yaml")
        assert loaded.mode == "simple"

    def test_load_without_mode_defaults_to_standard(self, tmp_path):
        """Backward compat: old trees without mode field default to standard."""
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="00001", description="test")
        root.set_status(TaskPhase.PROCESSING)
        tree.save(tmp_path / "tree.yaml")
        # Manually strip mode from YAML to simulate old file
        import yaml
        data = yaml.safe_load((tmp_path / "tree.yaml").read_text())
        data.pop("mode", None)
        (tmp_path / "tree.yaml").write_text(yaml.dump(data, allow_unicode=True))
        loaded = TaskTree.load(tmp_path / "tree.yaml")
        assert loaded.mode == "standard"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py::TestTaskTreeMode -v`
Expected: FAIL — `__init__() got an unexpected keyword argument 'mode'`

- [ ] **Step 3: Implement mode field**

In `task_tree.py`, modify `TaskTree.__init__`:
```python
def __init__(self, project_id: str, mode: str = "standard") -> None:
    self.project_id = project_id
    self.mode = mode
    self.root_id: str = ""
    self._nodes: dict[str, TaskNode] = {}
    self.current_branch: int = 0
```

In `save()`, add `mode` to the data dict:
```python
data = {
    "project_id": self.project_id,
    "root_id": self.root_id,
    "current_branch": self.current_branch,
    "mode": self.mode,
    "nodes": [n.to_dict() for n in nodes_snapshot],
}
```

In `load()`, read `mode` with default:
```python
tree.current_branch = data.get("current_branch", 0)
tree.mode = data.get("mode", "standard")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py::TestTaskTreeMode -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/task_tree.py tests/unit/core/test_task_tree.py
git commit -m "feat: add mode field to TaskTree (simple/standard)"
```

---

### Task 2: Conditional EA dispatch restriction

**Files:**
- Modify: `src/onemancompany/agents/tree_tools.py:172-187` (dispatch_child EA check)
- Test: `tests/unit/agents/test_tree_tools.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/agents/test_tree_tools.py`, in `TestEADispatchConstraint`:

```python
def test_ea_can_dispatch_to_regular_employee_in_simple_mode(self):
    """EA can dispatch to non-O-level when tree mode is simple."""
    from onemancompany.agents.tree_tools import dispatch_child

    tree = _make_tree_with_root(employee_id="00004")
    tree.mode = "simple"
    root_id = tree.root_id

    vessel = _make_vessel_and_task()
    tok_v, tok_t = _set_context(vessel, root_id)

    mock_em = _make_mock_em(root_id)

    try:
        with (
            patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
            patch("onemancompany.agents.tree_tools._save_tree"),
            patch("onemancompany.core.store.load_employee", return_value={"id": "00006", "name": "Test"}),
            patch("onemancompany.core.vessel.employee_manager", mock_em),
        ):
            result = dispatch_child.invoke({
                "employee_id": "00006",
                "description": "do coding",
                "acceptance_criteria": ["done"],
            })

        assert result["status"] == "dispatched"
    finally:
        _reset_context(tok_v, tok_t)

def test_ea_still_restricted_in_standard_mode(self):
    """EA cannot dispatch to non-O-level when tree mode is standard (default)."""
    from onemancompany.agents.tree_tools import dispatch_child

    tree = _make_tree_with_root(employee_id="00004")
    tree.mode = "standard"
    root_id = tree.root_id

    vessel = _make_vessel_and_task()
    tok_v, tok_t = _set_context(vessel, root_id)

    mock_em = _make_mock_em(root_id)

    try:
        with (
            patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
            patch("onemancompany.agents.tree_tools._save_tree"),
            patch("onemancompany.core.store.load_employee", return_value={"id": "00006", "name": "Test"}),
            patch("onemancompany.core.vessel.employee_manager", mock_em),
        ):
            result = dispatch_child.invoke({
                "employee_id": "00006",
                "description": "do coding",
                "acceptance_criteria": ["done"],
            })

        assert result["status"] == "error"
    finally:
        _reset_context(tok_v, tok_t)

def test_ea_cannot_dispatch_to_self(self):
    """EA cannot dispatch to itself regardless of mode."""
    from onemancompany.agents.tree_tools import dispatch_child

    tree = _make_tree_with_root(employee_id="00004")
    tree.mode = "simple"
    root_id = tree.root_id

    vessel = _make_vessel_and_task()
    tok_v, tok_t = _set_context(vessel, root_id)

    mock_em = _make_mock_em(root_id)

    try:
        with (
            patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
            patch("onemancompany.agents.tree_tools._save_tree"),
            patch("onemancompany.core.store.load_employee", return_value={"id": "00004", "name": "EA"}),
            patch("onemancompany.core.vessel.employee_manager", mock_em),
        ):
            result = dispatch_child.invoke({
                "employee_id": "00004",
                "description": "self task",
                "acceptance_criteria": ["done"],
            })

        assert result["status"] == "error"
        assert "self" in result["message"].lower() or "cannot" in result["message"].lower()
    finally:
        _reset_context(tok_v, tok_t)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tree_tools.py::TestEADispatchConstraint::test_ea_can_dispatch_to_regular_employee_in_simple_mode tests/unit/agents/test_tree_tools.py::TestEADispatchConstraint::test_ea_still_restricted_in_standard_mode tests/unit/agents/test_tree_tools.py::TestEADispatchConstraint::test_ea_cannot_dispatch_to_self -v`
Expected: first test FAIL (dispatch blocked), second PASS (already works), third FAIL (no self-dispatch guard)

- [ ] **Step 3: Implement conditional restriction**

In `tree_tools.py`, replace lines 172-187:
```python
# EA can only dispatch to O-level executives (unless tree mode is "simple")
from onemancompany.core.config import EA_ID, HR_ID, COO_ID, CSO_ID
if current_node.employee_id == EA_ID:
    # Self-dispatch guard — always blocked
    if employee_id == EA_ID:
        return {
            "status": "error",
            "message": "EA cannot dispatch tasks to itself. Please dispatch to an appropriate team member.",
        }
    # O-level restriction — only in standard mode
    if tree.mode != "simple":
        allowed_targets = {HR_ID, COO_ID, CSO_ID}
        if employee_id not in allowed_targets:
            suggestion = f"COO({COO_ID})"
            return {
                "status": "error",
                "message": (
                    f"EA cannot directly dispatch tasks to {employee_id}. "
                    f"Please dispatch_child to the corresponding O-level executive instead: HR({HR_ID}), COO({COO_ID}), CSO({CSO_ID}). "
                    f"Hint: for development/design/operations tasks, dispatch to {suggestion} to organize team execution. "
                    f"Please immediately re-call dispatch_child with the correct employee_id."
                ),
            }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tree_tools.py::TestEADispatchConstraint -v`
Expected: ALL PASS (existing + new tests)

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/agents/tree_tools.py tests/unit/agents/test_tree_tools.py
git commit -m "feat: EA dispatch unrestricted in simple mode, add self-dispatch guard"
```

---

### Task 3: Skip retrospective in simple mode

**Files:**
- Modify: `src/onemancompany/core/vessel.py:2120-2126` (_request_ceo_confirmation)
- Test: `tests/unit/core/test_agent_loop.py`

- [ ] **Step 1: Write failing test**

Follow the existing pattern in `TestRootNodeCompletion` (line 1896). Add to `test_agent_loop.py`:

```python
class TestSimpleModeSkipsRetrospective:
    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_simple_mode_skips_retrospective(self, mock_bus, mock_state):
        """When tree.mode == 'simple', _request_ceo_confirmation passes run_retrospective=False."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}

        mgr = EmployeeManager()

        tree = TaskTree(project_id="p1", mode="simple")
        node = MagicMock()
        node.node_type = NodeType.TASK
        node.description_preview = "test task"
        node.id = "n1"
        node.project_dir = "/tmp/proj"
        node.is_ceo_node = False

        entry = ScheduleEntry(node_id="n1", tree_path="/tmp/proj/task_tree.yaml")

        with (
            patch.object(mgr, "_full_cleanup", new_callable=AsyncMock) as mock_cleanup,
            patch("onemancompany.core.vessel._store") as mock_store,
        ):
            mock_store.load_employee.return_value = {"name": "Test"}
            tree._nodes = {"n1": node}
            tree.get_children = MagicMock(return_value=[])

            await mgr._request_ceo_confirmation("00006", node, tree, entry, "p1")

            mock_cleanup.assert_called_once()
            _, kwargs = mock_cleanup.call_args
            assert kwargs["run_retrospective"] is False

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_standard_mode_runs_retrospective(self, mock_bus, mock_state):
        """When tree.mode == 'standard' and not system node, retrospective runs."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}

        mgr = EmployeeManager()

        tree = TaskTree(project_id="p1", mode="standard")
        node = MagicMock()
        node.node_type = NodeType.TASK
        node.description_preview = "test task"
        node.id = "n1"
        node.project_dir = "/tmp/proj"
        node.is_ceo_node = False

        entry = ScheduleEntry(node_id="n1", tree_path="/tmp/proj/task_tree.yaml")

        with (
            patch.object(mgr, "_full_cleanup", new_callable=AsyncMock) as mock_cleanup,
            patch("onemancompany.core.vessel._store") as mock_store,
        ):
            mock_store.load_employee.return_value = {"name": "Test"}
            tree._nodes = {"n1": node}
            tree.get_children = MagicMock(return_value=[])

            await mgr._request_ceo_confirmation("00006", node, tree, entry, "p1")

            mock_cleanup.assert_called_once()
            _, kwargs = mock_cleanup.call_args
            assert kwargs["run_retrospective"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_agent_loop.py::TestSimpleModeSkipsRetrospective -v`
Expected: FAIL — simple mode still passes `run_retrospective=True`

- [ ] **Step 3: Implement**

In `vessel.py`, modify lines 2120-2126:
```python
# Auto-approve: proceed directly with cleanup
is_system_node = node.node_type in SYSTEM_NODE_TYPES
run_retro = not is_system_node and tree.mode != "simple"
await self._full_cleanup(
    employee_id, node, agent_error=False,
    project_id=project_id,
    run_retrospective=run_retro,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_agent_loop.py::TestSimpleModeSkipsRetrospective -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/vessel.py tests/unit/core/test_agent_loop.py
git commit -m "feat: skip retrospective in simple mode"
```

---

### Task 4: Modify `ceo_submit_task` to accept mode, remove `ceo_qa`

**Files:**
- Modify: `src/onemancompany/api/routes.py:476-537` (delete ceo_qa)
- Modify: `src/onemancompany/api/routes.py:540-655` (ceo_submit_task add mode)
- Modify: `src/onemancompany/core/models.py` (remove CEO_QA)
- Test: `tests/unit/api/test_routes.py`

- [ ] **Step 1: Write failing test**

Add to `tests/unit/api/test_routes.py`:

Follow the existing `TestCeoSubmitTask.test_routes_to_ea_initializes_task_tree` pattern (line 324) which uses `_store_patches(state)` and patches at source module level for deferred imports:

```python
class TestCeoTaskMode:
    async def test_submit_task_default_mode_standard(self):
        """When no mode specified, tree should have mode='standard'."""
        state = _make_state()
        bus = EventBus()
        mock_save_tree = MagicMock()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=MagicMock()), \
             patch("onemancompany.core.project_archive.async_create_project_from_task", new_callable=AsyncMock, return_value=("proj1", "iter_001")), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value="/tmp/proj"), \
             patch("onemancompany.core.vessel._save_project_tree", mock_save_tree), \
             patch("onemancompany.core.vessel.employee_manager", MagicMock()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ceo/task", json={"task": "hello"})

        assert resp.status_code == 200
        mock_save_tree.assert_called_once()
        _, saved_tree = mock_save_tree.call_args[0]
        assert saved_tree.mode == "standard"

    async def test_submit_task_simple_mode(self):
        """When mode='simple', tree should have mode='simple'."""
        state = _make_state()
        bus = EventBus()
        mock_save_tree = MagicMock()

        with patch("onemancompany.api.routes.company_state", state), \
             _store_patches(state), \
             patch("onemancompany.api.routes.event_bus", bus), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=MagicMock()), \
             patch("onemancompany.core.project_archive.async_create_project_from_task", new_callable=AsyncMock, return_value=("proj1", "iter_001")), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value="/tmp/proj"), \
             patch("onemancompany.core.vessel._save_project_tree", mock_save_tree), \
             patch("onemancompany.core.vessel.employee_manager", MagicMock()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ceo/task", json={"task": "hello", "mode": "simple"})

        assert resp.status_code == 200
        mock_save_tree.assert_called_once()
        _, saved_tree = mock_save_tree.call_args[0]
        assert saved_tree.mode == "simple"

    async def test_qa_endpoint_removed(self):
        """The /api/ceo/qa endpoint should no longer exist."""
        with patch("onemancompany.api.routes.company_state", _make_state()), \
             patch("onemancompany.api.routes.event_bus", EventBus()):
            app = _make_test_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/api/ceo/qa", json={"question": "hello"})
        # 404 or 405 — endpoint gone
        assert resp.status_code in (404, 405)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/api/test_routes.py::TestCeoTaskMode -v`
Expected: first two FAIL (no mode handling), third FAIL (endpoint still exists)

- [ ] **Step 3: Implement**

In `routes.py`:

1. **Delete** the entire `ceo_qa` function (lines 476-537)

2. **Modify** `ceo_submit_task` — read mode from body and pass to TaskTree:
```python
mode = body.get("mode", "standard")
if mode not in ("simple", "standard"):
    mode = "standard"
```

Then when creating TaskTree (line 628):
```python
tree = TaskTree(project_id=ctx_id, mode=mode)
```

3. **In `models.py`**, remove `CEO_QA = "ceo_qa"` from EventType enum.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/api/test_routes.py::TestCeoTaskMode -v`
Expected: 3 PASS

- [ ] **Step 5: Run full test suite to check nothing broke**

Run: `.venv/bin/python -m pytest tests/ -x -q --tb=short`
Expected: All pass (any test referencing `ceo_qa` or `CEO_QA` will need updating)

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/api/routes.py src/onemancompany/core/models.py tests/unit/api/test_routes.py
git commit -m "feat: ceo_submit_task accepts mode param, remove ceo_qa endpoint"
```

---

### Task 5: Frontend — remove Q&A, add mode toggle

**Files:**
- Modify: `frontend/index.html:88-91` (remove Q&A option, add mode toggle)
- Modify: `frontend/app.js:5944-5967` (remove Q&A branch, add mode to request)
- Modify: `frontend/app.js:4917` (remove qa cost label)

- [ ] **Step 1: Remove Q&A option from project selector**

In `frontend/index.html`, replace the `__qa__` option (line 90) with a simple mode checkbox or toggle. Add after the project selector:
```html
<label class="mode-toggle" title="Simple mode: EA dispatches directly to employees, no retrospective">
  <input type="checkbox" id="simple-mode-toggle" /> Simple
</label>
```

- [ ] **Step 2: Remove Q&A branch in app.js**

In `frontend/app.js`, delete the `if (projectId === '__qa__')` block (lines 5944-5967).

Add mode to the request body (around line 5969):
```javascript
const reqBody = { task, attachments };
if (projectId) reqBody.project_id = projectId;
const simpleToggle = document.getElementById('simple-mode-toggle');
if (simpleToggle && simpleToggle.checked) reqBody.mode = 'simple';
```

- [ ] **Step 3: Remove qa cost label**

In `frontend/app.js` line 4917, remove `qa:'CEO Q&A',` from `catLabels`.

- [ ] **Step 4: Manual test in browser**

1. Open frontend, verify no Q&A option in project selector
2. Check "Simple" toggle → submit task → verify request body has `mode: "simple"`
3. Uncheck → submit → verify no mode field (defaults to standard)

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/app.js
git commit -m "feat: replace Q&A mode with simple mode toggle in frontend"
```

---

### Task 6: Full test suite verification and cleanup

**Files:**
- Any test files referencing `ceo_qa` or `CEO_QA`

- [ ] **Step 1: Find and fix broken references**

Run: `grep -rn "ceo_qa\|CEO_QA\|__qa__" tests/ src/ frontend/`

Update any tests, WebSocket handlers, or frontend code that reference the removed endpoint, event type, or Q&A option value.

- [ ] **Step 2: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q --tb=short`
Expected: All pass

- [ ] **Step 3: Commit fixes if any**

```bash
git commit -am "fix: update tests for Q&A removal"
```

- [ ] **Step 4: Create PR**

```bash
git push origin investigate/shared-prompt -u
gh pr create --title "feat: merge Q&A into task flow with simple mode" --body "..."
```
