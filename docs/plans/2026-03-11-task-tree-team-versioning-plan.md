# Task Tree Branching, Team Workflow, and Version Management — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix task tree overwriting on CEO follow-up, enforce EA→O-level dispatch hierarchy, upgrade COO project workflow with team assembly and meetings, add project team tracking with bidirectional frontend display, and set up semantic versioning.

**Architecture:** TaskNode/TaskTree gain `branch`/`branch_active` fields; `task_followup` creates new branches; vessel filters by active branch. EA dispatch_child validates caller→target. COO prompt adds 4-phase workflow with new `update_project_team` tool. Frontend renders branch dimming, team sections, and employee project history. python-semantic-release replaces post-commit hook.

**Tech Stack:** Python 3.12, FastAPI, LangChain, D3.js (task tree SVG), vanilla JS, YAML persistence, python-semantic-release

---

## Task 1: Task Tree Branch Model

**Files:**
- Modify: `src/onemancompany/core/task_tree.py`
- Test: `tests/unit/core/test_task_tree.py`

### Step 1: Write failing tests for branch fields on TaskNode

```python
# Add to tests/unit/core/test_task_tree.py

class TestTaskNodeBranch:
    def test_default_branch_values(self):
        node = TaskNode(employee_id="00001", description="test")
        assert node.branch == 0
        assert node.branch_active is True

    def test_branch_in_to_dict(self):
        node = TaskNode(employee_id="00001", description="test", branch=2, branch_active=False)
        d = node.to_dict()
        assert d["branch"] == 2
        assert d["branch_active"] is False

    def test_branch_in_from_dict(self):
        node = TaskNode.from_dict({"branch": 3, "branch_active": False})
        assert node.branch == 3
        assert node.branch_active is False

    def test_from_dict_missing_branch_defaults(self):
        """Backward compat: old YAML without branch fields."""
        node = TaskNode.from_dict({"employee_id": "00001"})
        assert node.branch == 0
        assert node.branch_active is True
```

### Step 2: Run tests to verify they fail

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py::TestTaskNodeBranch -v`
Expected: FAIL — `TaskNode.__init__() got an unexpected keyword argument 'branch'`

### Step 3: Add branch fields to TaskNode

In `src/onemancompany/core/task_tree.py`, add to `TaskNode` dataclass after `timeout_seconds`:

```python
    branch: int = 0
    branch_active: bool = True
```

Add to `to_dict()` return dict:
```python
            "branch": self.branch,
            "branch_active": self.branch_active,
```

### Step 4: Run tests to verify they pass

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py::TestTaskNodeBranch -v`
Expected: PASS

### Step 5: Write failing tests for TaskTree branch management

```python
# Add to tests/unit/core/test_task_tree.py

class TestTaskTreeBranching:
    def test_initial_branch(self):
        tree = TaskTree(project_id="proj1")
        assert tree.current_branch == 0

    def test_new_branch_increments(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c1.status = "accepted"

        new_b = tree.new_branch()
        assert new_b == 1
        assert tree.current_branch == 1

    def test_new_branch_deactivates_old_nodes(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c1.status = "accepted"

        tree.new_branch()

        # Old child deactivated
        assert c1.branch_active is False
        # Root stays active (spans all branches)
        assert root.branch_active is True

    def test_all_children_terminal_filters_active_branch(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c1.status = "accepted"

        # Create new branch — c1 deactivated
        tree.new_branch()

        # Add new child on active branch
        c2 = tree.add_child(root.id, "00011", "B", [])
        c2.branch = tree.current_branch
        c2.branch_active = True

        # c2 is pending (not terminal) but c1 is inactive
        # all_children_terminal should only check active children
        assert tree.all_children_terminal(root.id) is False

        c2.status = "accepted"
        assert tree.all_children_terminal(root.id) is True

    def test_get_active_children(self):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c1.status = "accepted"

        tree.new_branch()

        c2 = tree.add_child(root.id, "00011", "B", [])
        c2.branch = tree.current_branch
        c2.branch_active = True

        active = tree.get_active_children(root.id)
        assert len(active) == 1
        assert active[0].id == c2.id

    def test_branch_persists_in_save_load(self, tmp_path):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        c1 = tree.add_child(root.id, "00010", "A", [])
        c1.status = "accepted"
        tree.new_branch()
        c2 = tree.add_child(root.id, "00011", "B", [])
        c2.branch = tree.current_branch
        c2.branch_active = True

        path = tmp_path / "task_tree.yaml"
        tree.save(path)

        loaded = TaskTree.load(path)
        assert loaded.current_branch == 1
        loaded_c1 = loaded.get_node(c1.id)
        assert loaded_c1.branch_active is False
        loaded_c2 = loaded.get_node(c2.id)
        assert loaded_c2.branch_active is True
        assert loaded_c2.branch == 1
```

### Step 6: Run tests to verify they fail

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py::TestTaskTreeBranching -v`
Expected: FAIL — `TaskTree has no attribute 'current_branch'`

### Step 7: Implement TaskTree branching

In `src/onemancompany/core/task_tree.py`:

Add to `TaskTree.__init__`:
```python
        self.current_branch: int = 0
```

Add new methods to `TaskTree`:
```python
    def new_branch(self) -> int:
        """Start a new branch: deactivate non-root nodes, increment counter."""
        self.current_branch += 1
        for node in self._nodes.values():
            if node.id != self.root_id:
                node.branch_active = False
        # Root always stays active
        root = self._nodes.get(self.root_id)
        if root:
            root.branch = self.current_branch
            root.branch_active = True
        return self.current_branch

    def get_active_children(self, node_id: str) -> list[TaskNode]:
        """Get only branch_active children of a node."""
        return [c for c in self.get_children(node_id) if c.branch_active]
```

Update `all_children_terminal` to filter active branch:
```python
    def all_children_terminal(self, node_id: str) -> bool:
        children = self.get_active_children(node_id)
        if not children:
            return True
        return all(c.is_terminal for c in children)
```

Update `has_failed_children`:
```python
    def has_failed_children(self, node_id: str) -> bool:
        return any(c.status == "failed" for c in self.get_active_children(node_id))
```

Update `save()` to persist `current_branch`:
```python
        data = {
            "project_id": self.project_id,
            "root_id": self.root_id,
            "current_branch": self.current_branch,
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "task_id_map": dict(self.task_id_map),
        }
```

Update `load()` to restore `current_branch`:
```python
        tree.current_branch = data.get("current_branch", 0)
```

### Step 8: Run tests to verify they pass

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py -v`
Expected: ALL PASS (including existing tests — `all_children_terminal` change is backward-compatible because branch_active defaults to True)

### Step 9: Commit

```bash
git add src/onemancompany/core/task_tree.py tests/unit/core/test_task_tree.py
git commit -m "feat: add branch model to TaskNode and TaskTree for follow-up isolation"
```

---

## Task 2: task_followup Uses new_branch()

**Files:**
- Modify: `src/onemancompany/api/routes.py` (lines 500-598)
- Test: `tests/unit/api/test_routes.py`

### Step 1: Write failing test for task_followup branching

```python
# Add to tests/unit/api/test_routes.py

class TestTaskFollowupBranching:
    @pytest.mark.asyncio
    async def test_followup_creates_new_branch(self, tmp_path):
        """When CEO follows up on existing tree, old nodes get deactivated and new child is on new branch."""
        from onemancompany.core.task_tree import TaskTree

        # Create a tree with completed first branch
        tree = TaskTree(project_id="test-proj")
        root = tree.create_root("00004", "Original task")
        child = tree.add_child(root.id, "00003", "First pass", [])
        child.status = "accepted"
        root.status = "completed"
        root.result = "Done"

        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        # Simulate followup — load tree, new_branch, add child
        loaded = TaskTree.load(tree_path, project_id="test-proj")
        loaded.new_branch()
        new_child = loaded.add_child(
            parent_id=loaded.root_id,
            employee_id="00004",
            description="Follow-up instructions",
            acceptance_criteria=[],
        )
        new_child.branch = loaded.current_branch
        new_child.branch_active = True
        loaded.save(tree_path)

        # Verify
        reloaded = TaskTree.load(tree_path)
        old_child = reloaded.get_node(child.id)
        assert old_child.branch_active is False

        new = reloaded.get_node(new_child.id)
        assert new.branch_active is True
        assert new.branch == 1

        # Root stays active
        assert reloaded.get_node(reloaded.root_id).branch_active is True

        # all_children_terminal only sees active branch
        active_children = reloaded.get_active_children(reloaded.root_id)
        assert len(active_children) == 1
        assert active_children[0].id == new_child.id
```

### Step 2: Run test to verify it passes (this is a data-model test, should pass with Task 1)

Run: `.venv/bin/python -m pytest tests/unit/api/test_routes.py::TestTaskFollowupBranching -v`
Expected: PASS

### Step 3: Modify task_followup endpoint

In `src/onemancompany/api/routes.py`, change lines 569-577 (the `if tree.root_id:` block):

**Before:**
```python
    if tree.root_id:
        # Append as new child under existing root
        child = tree.add_child(
            parent_id=tree.root_id,
            employee_id=EA_ID,
            description=instructions,
            acceptance_criteria=[],
        )
        tree.task_id_map[ea_agent_task.id] = child.id
```

**After:**
```python
    if tree.root_id:
        # Start new branch — deactivates old nodes
        tree.new_branch()
        # Reset root status for new branch
        root = tree.get_node(tree.root_id)
        if root:
            root.status = "pending"
            root.result = ""
        # Add follow-up child on new branch
        child = tree.add_child(
            parent_id=tree.root_id,
            employee_id=EA_ID,
            description=instructions,
            acceptance_criteria=[],
        )
        child.branch = tree.current_branch
        child.branch_active = True
        tree.task_id_map[ea_agent_task.id] = child.id
```

### Step 4: Run existing tests to verify no regressions

Run: `.venv/bin/python -m pytest tests/unit/api/test_routes.py -v`
Expected: PASS

### Step 5: Commit

```bash
git add src/onemancompany/api/routes.py tests/unit/api/test_routes.py
git commit -m "feat: task_followup creates new branch instead of appending to old"
```

---

## Task 3: Vessel _on_task_done Filters Active Branch

**Files:**
- Modify: `src/onemancompany/core/vessel.py` (lines 1388-1438)
- Test: manual verification (vessel tests are integration-heavy)

### Step 1: Update _on_task_done to filter active children

In `src/onemancompany/core/vessel.py`, find the `_on_task_done` method around line 1388:

**Before (line 1389-1390):**
```python
        children = tree.get_children(parent_node.id)
        all_terminal = all(c.is_terminal or c.status == "completed" for c in children)
```

**After:**
```python
        children = tree.get_active_children(parent_node.id)
        all_terminal = all(c.is_terminal or c.status == "completed" for c in children)
```

Also update the review prompt loop (lines 1396-1402) to use only active children:

**Before (line 1396-1402):**
```python
        needs_review = []
        already_accepted = []
        for child in children:
            if child.status == "accepted":
                already_accepted.append(child)
            else:
                needs_review.append(child)
```

This already uses the `children` variable which now comes from `get_active_children`, so no further change needed.

### Step 2: Verify compilation

Run: `.venv/bin/python -c "from onemancompany.core.vessel import EmployeeManager"`
Expected: No errors

### Step 3: Commit

```bash
git add src/onemancompany/core/vessel.py
git commit -m "feat: vessel _on_task_done filters by active branch"
```

---

## Task 4: EA Dispatch Constraint

**Files:**
- Modify: `src/onemancompany/agents/ea_agent.py`
- Modify: `src/onemancompany/agents/tree_tools.py`
- Test: `tests/unit/agents/test_tree_tools.py`

### Step 1: Write failing test for EA dispatch validation

```python
# Add to tests/unit/agents/test_tree_tools.py

class TestEADispatchConstraint:
    def test_ea_cannot_dispatch_to_regular_employee(self):
        """EA (00004) should NOT be able to dispatch to non-O-level employees."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree_with_root(employee_id="00004")
        tree.task_id_map["ea-task-1"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "ea-task-1")

        mock_cs = MagicMock()
        mock_cs.employees = {"00006": MagicMock()}

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.state.company_state", mock_cs),
            ):
                result = dispatch_child.invoke({
                    "employee_id": "00006",
                    "description": "do coding",
                    "acceptance_criteria": ["done"],
                })

            assert result["status"] == "error"
            assert "00002" in result["message"] or "00003" in result["message"] or "O-level" in result["message"].lower() or "COO" in result["message"] or "HR" in result["message"]
        finally:
            _reset_context(tok_v, tok_t)

    def test_ea_can_dispatch_to_coo(self):
        """EA (00004) should be able to dispatch to COO (00003)."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree_with_root(employee_id="00004")
        tree.task_id_map["ea-task-2"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "ea-task-2")

        mock_handle = MagicMock()
        mock_agent_task = MagicMock()
        mock_agent_task.id = "coo-task-1"
        mock_handle.push_task.return_value = mock_agent_task
        mock_em = MagicMock()
        mock_em.get_handle.return_value = mock_handle
        mock_cs = MagicMock()
        mock_cs.employees = {"00003": MagicMock()}

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.state.company_state", mock_cs),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = dispatch_child.invoke({
                    "employee_id": "00003",
                    "description": "manage project",
                    "acceptance_criteria": ["delivered"],
                })

            assert result["status"] == "dispatched"
        finally:
            _reset_context(tok_v, tok_t)

    def test_non_ea_can_dispatch_to_anyone(self):
        """Non-EA employees (e.g. COO 00003) can dispatch to any employee."""
        from onemancompany.agents.tree_tools import dispatch_child

        tree = _make_tree_with_root(employee_id="00003")
        tree.task_id_map["coo-task-3"] = tree.root_id

        vessel, task = _make_vessel_and_task()
        tok_v, tok_t = _set_context(vessel, "coo-task-3")

        mock_handle = MagicMock()
        mock_agent_task = MagicMock()
        mock_agent_task.id = "eng-task-1"
        mock_handle.push_task.return_value = mock_agent_task
        mock_em = MagicMock()
        mock_em.get_handle.return_value = mock_handle
        mock_cs = MagicMock()
        mock_cs.employees = {"00006": MagicMock()}

        try:
            with (
                patch("onemancompany.agents.tree_tools._load_tree", return_value=tree),
                patch("onemancompany.agents.tree_tools._save_tree"),
                patch("onemancompany.core.state.company_state", mock_cs),
                patch("onemancompany.core.vessel.employee_manager", mock_em),
            ):
                result = dispatch_child.invoke({
                    "employee_id": "00006",
                    "description": "write code",
                    "acceptance_criteria": ["works"],
                })

            assert result["status"] == "dispatched"
        finally:
            _reset_context(tok_v, tok_t)
```

### Step 2: Run tests to verify they fail

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tree_tools.py::TestEADispatchConstraint -v`
Expected: `test_ea_cannot_dispatch_to_regular_employee` FAILS (dispatch succeeds when it shouldn't)

### Step 3: Add EA dispatch validation to dispatch_child

In `src/onemancompany/agents/tree_tools.py`, add after the employee exists validation (after line 71):

```python
    # EA can only dispatch to O-level executives
    from onemancompany.core.config import EA_ID, HR_ID, COO_ID, CSO_ID
    current_node = tree.get_node(current_node_id)
    if current_node and current_node.employee_id == EA_ID:
        allowed_targets = {HR_ID, COO_ID, CSO_ID}
        if employee_id not in allowed_targets:
            return {
                "status": "error",
                "message": (
                    f"EA不能直接分派任务给 {employee_id}。"
                    f"请分派给对应负责人: HR({HR_ID}), COO({COO_ID}), CSO({CSO_ID})。"
                ),
            }
```

### Step 4: Run tests to verify they pass

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tree_tools.py -v`
Expected: ALL PASS

### Step 5: Update EA system prompt

In `src/onemancompany/agents/ea_agent.py`, replace the routing table:

**Before:**
```python
## Routing Table
| Domain | Route to | Examples |
|--------|----------|----------|
| People/HR | HR (00002) | Hiring, reviews, promotions |
| Operations | COO (00003) | Project execution, engineering |
| Sales | CSO (00005) | Clients, contracts, deals |
| Specific person | Direct employee | "Tell X to do Y" |
```

**After:**
```python
## Routing Table (严格执行 — 只能dispatch给O-level)
| Domain | Route to | Examples |
|--------|----------|----------|
| 人事/招聘/入职/绩效 | HR (00002) | Hiring, reviews, promotions |
| 项目执行/开发/设计/运营 | COO (00003) | Project execution, engineering |
| 销售/市场/客户 | CSO (00005) | Clients, contracts, deals |

**绝对禁止直接dispatch给普通员工 (00006+)。**
即使CEO说"告诉某某做X"，也必须通过对应O-level转达。
系统会拦截直接dispatch给非O-level的请求。
```

### Step 6: Run all tests

Run: `.venv/bin/python -m pytest tests/unit/ -v`
Expected: ALL PASS

### Step 7: Commit

```bash
git add src/onemancompany/agents/tree_tools.py src/onemancompany/agents/ea_agent.py tests/unit/agents/test_tree_tools.py
git commit -m "feat: enforce EA dispatch only to O-level executives"
```

---

## Task 5: COO Project Workflow + update_project_team Tool

**Files:**
- Modify: `src/onemancompany/agents/coo_agent.py`
- Modify: `src/onemancompany/agents/common_tools.py` (add update_project_team)
- Test: `tests/unit/agents/test_common_tools.py` (new test class)

### Step 1: Write failing test for update_project_team

```python
# Add to tests/unit/agents/test_common_tools.py (create if needed)

import yaml
from unittest.mock import MagicMock, patch
from onemancompany.core.vessel import _current_vessel, _current_task_id


class TestUpdateProjectTeam:
    def test_adds_team_members(self, tmp_path):
        from onemancompany.agents.common_tools import update_project_team

        # Create project.yaml
        project_yaml = tmp_path / "project.yaml"
        project_yaml.write_text(yaml.dump({"task": "Build app", "status": "in_progress"}))

        task = MagicMock()
        task.project_dir = str(tmp_path)
        task.project_id = "test-proj"
        board = MagicMock()
        board.get_task.return_value = task
        vessel = MagicMock()
        vessel.board = board

        tok_v = _current_vessel.set(vessel)
        tok_t = _current_task_id.set("task-1")

        try:
            result = update_project_team.invoke({
                "members": [
                    {"employee_id": "00006", "role": "Game Engineer"},
                    {"employee_id": "00007", "role": "PM"},
                ],
            })

            assert result["status"] == "ok"
            assert result["added"] == 2

            # Verify YAML
            data = yaml.safe_load(project_yaml.read_text())
            assert len(data["team"]) == 2
            assert data["team"][0]["employee_id"] == "00006"
            assert data["team"][0]["role"] == "Game Engineer"
            assert "joined_at" in data["team"][0]
        finally:
            _current_vessel.reset(tok_v)
            _current_task_id.reset(tok_t)

    def test_appends_not_overwrites(self, tmp_path):
        from onemancompany.agents.common_tools import update_project_team

        # Pre-existing team
        project_yaml = tmp_path / "project.yaml"
        project_yaml.write_text(yaml.dump({
            "task": "Build app",
            "team": [{"employee_id": "00003", "role": "项目负责人", "joined_at": "2026-03-11T10:00:00"}],
        }))

        task = MagicMock()
        task.project_dir = str(tmp_path)
        task.project_id = "test-proj"
        board = MagicMock()
        board.get_task.return_value = task
        vessel = MagicMock()
        vessel.board = board

        tok_v = _current_vessel.set(vessel)
        tok_t = _current_task_id.set("task-2")

        try:
            result = update_project_team.invoke({
                "members": [{"employee_id": "00006", "role": "Engineer"}],
            })

            data = yaml.safe_load(project_yaml.read_text())
            assert len(data["team"]) == 2
            assert data["team"][0]["employee_id"] == "00003"
            assert data["team"][1]["employee_id"] == "00006"
        finally:
            _current_vessel.reset(tok_v)
            _current_task_id.reset(tok_t)

    def test_no_project_dir_returns_error(self):
        from onemancompany.agents.common_tools import update_project_team

        tok_v = _current_vessel.set(None)
        tok_t = _current_task_id.set("")

        try:
            result = update_project_team.invoke({
                "members": [{"employee_id": "00006", "role": "Engineer"}],
            })
            assert result["status"] == "error"
        finally:
            _current_vessel.reset(tok_v)
            _current_task_id.reset(tok_t)
```

### Step 2: Run tests to verify they fail

Run: `.venv/bin/python -m pytest tests/unit/agents/test_common_tools.py::TestUpdateProjectTeam -v`
Expected: FAIL — `ImportError: cannot import name 'update_project_team'`

### Step 3: Implement update_project_team tool

In `src/onemancompany/agents/common_tools.py`, add:

```python
@tool
def update_project_team(members: list[dict]) -> dict:
    """Update the team roster for the current project.

    Appends new members to the project's team list. Does not overwrite existing members.

    Args:
        members: List of dicts with 'employee_id' and 'role' keys.

    Returns:
        Confirmation with count of added members.
    """
    from onemancompany.core.vessel import _current_vessel, _current_task_id

    vessel = _current_vessel.get()
    task_id = _current_task_id.get()
    if not vessel or not task_id:
        return {"status": "error", "message": "No agent context."}

    task = vessel.board.get_task(task_id)
    if not task or not task.project_dir:
        return {"status": "error", "message": "No project directory in current task."}

    from pathlib import Path
    from datetime import datetime
    import yaml

    project_yaml = Path(task.project_dir) / "project.yaml"
    if not project_yaml.exists():
        return {"status": "error", "message": "project.yaml not found."}

    data = yaml.safe_load(project_yaml.read_text(encoding="utf-8")) or {}
    team = data.get("team", [])

    now = datetime.now().isoformat()
    for m in members:
        team.append({
            "employee_id": m["employee_id"],
            "role": m.get("role", ""),
            "joined_at": now,
        })

    data["team"] = team
    project_yaml.write_text(
        yaml.dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    return {"status": "ok", "added": len(members), "total": len(team)}
```

Register the tool:
```python
from onemancompany.core.tool_registry import tool_registry, ToolMeta
tool_registry.register(update_project_team, ToolMeta(name="update_project_team", category="base"))
```

### Step 4: Run tests to verify they pass

Run: `.venv/bin/python -m pytest tests/unit/agents/test_common_tools.py::TestUpdateProjectTeam -v`
Expected: PASS

### Step 5: Update COO system prompt

In `src/onemancompany/agents/coo_agent.py`, add after the "## 团队组建与人员调度" section, replace it with:

```python
## 项目执行流程 (复杂项目必须遵循，简单任务可跳过阶段2-3)

### 阶段1 — 分析项目
- 理解EA的需求，评估复杂度和所需技能
- 决定是否需要组建团队（简单单人任务可直接dispatch）

### 阶段2 — 组建团队
- list_colleagues() 查看可用人员及其技能和当前负载
- update_project_team(members=[{employee_id, role}]) 注册团队成员
- 可在后续阶段追加成员（验收/整改/受阻时）

### 阶段3 — 团队对齐
- pull_meeting(attendees=团队全员) 讨论:
  - 项目目标和范围
  - 验收标准
  - 分工计划和时间线
- 会议结论写入项目工作区

### 阶段4 — 分派执行
- 按计划 dispatch_child() 分配子任务
- 每个子任务必须有明确的验收标准（来自阶段3讨论结果）
- PM可以做：项目规划、市场调研、竞品分析、文档撰写、进度跟踪
- Engineer做：代码开发、技术实现、测试
```

### Step 6: Verify compilation

Run: `.venv/bin/python -c "from onemancompany.agents.coo_agent import COOAgent"`
Expected: No errors

### Step 7: Commit

```bash
git add src/onemancompany/agents/common_tools.py src/onemancompany/agents/coo_agent.py tests/unit/agents/test_common_tools.py
git commit -m "feat: add update_project_team tool and COO 4-phase project workflow"
```

---

## Task 6: Employee Projects API Endpoint

**Files:**
- Modify: `src/onemancompany/api/routes.py`
- Test: `tests/unit/api/test_routes.py`

### Step 1: Write failing test

```python
# Add to tests/unit/api/test_routes.py

class TestEmployeeProjects:
    @pytest.mark.asyncio
    async def test_get_employee_projects(self, tmp_path):
        """GET /api/employees/{id}/projects returns projects the employee participated in."""
        import yaml
        from pathlib import Path

        # Create mock project dirs with team data
        proj1_dir = tmp_path / "proj1"
        proj1_dir.mkdir()
        (proj1_dir / "project.yaml").write_text(yaml.dump({
            "task": "Build game",
            "status": "completed",
            "team": [
                {"employee_id": "00006", "role": "Engineer", "joined_at": "2026-03-11T10:00:00"},
                {"employee_id": "00003", "role": "COO", "joined_at": "2026-03-11T10:00:00"},
            ],
        }))

        proj2_dir = tmp_path / "proj2"
        proj2_dir.mkdir()
        (proj2_dir / "project.yaml").write_text(yaml.dump({
            "task": "Design UI",
            "status": "in_progress",
            "team": [
                {"employee_id": "00007", "role": "Designer", "joined_at": "2026-03-11T11:00:00"},
            ],
        }))

        from onemancompany.api.routes import _scan_employee_projects
        projects = _scan_employee_projects("00006", str(tmp_path))
        assert len(projects) == 1
        assert projects[0]["task"] == "Build game"
        assert projects[0]["role_in_project"] == "Engineer"
```

### Step 2: Run test to verify it fails

Run: `.venv/bin/python -m pytest tests/unit/api/test_routes.py::TestEmployeeProjects -v`
Expected: FAIL — `ImportError: cannot import name '_scan_employee_projects'`

### Step 3: Implement endpoint

In `src/onemancompany/api/routes.py`, add helper function and endpoint:

```python
def _scan_employee_projects(employee_id: str, projects_dir: str = "") -> list[dict]:
    """Scan all project.yaml files for projects where employee_id is in team."""
    from pathlib import Path
    from onemancompany.core.config import PROJECTS_DIR
    import yaml

    base = Path(projects_dir) if projects_dir else PROJECTS_DIR
    results = []
    if not base.exists():
        return results

    for pdir in base.iterdir():
        if not pdir.is_dir():
            continue
        pyaml = pdir / "project.yaml"
        if not pyaml.exists():
            continue
        try:
            data = yaml.safe_load(pyaml.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        team = data.get("team", [])
        for member in team:
            if member.get("employee_id") == employee_id:
                results.append({
                    "project_id": pdir.name,
                    "task": data.get("task", ""),
                    "status": data.get("status", ""),
                    "role_in_project": member.get("role", ""),
                    "joined_at": member.get("joined_at", ""),
                })
                break

    return results


@router.get("/api/employees/{employee_id}/projects")
async def get_employee_projects(employee_id: str) -> list[dict]:
    """Get list of projects an employee participated in."""
    return _scan_employee_projects(employee_id)
```

### Step 4: Run tests

Run: `.venv/bin/python -m pytest tests/unit/api/test_routes.py::TestEmployeeProjects -v`
Expected: PASS

### Step 5: Commit

```bash
git add src/onemancompany/api/routes.py tests/unit/api/test_routes.py
git commit -m "feat: add employee projects API endpoint"
```

---

## Task 7: API — Include team in project detail and tree response

**Files:**
- Modify: `src/onemancompany/api/routes.py`

### Step 1: Verify project detail already returns team field

The project detail endpoint returns raw YAML data, so the `team` field will be included automatically once projects have team data. No code change needed for project detail.

### Step 2: Verify tree endpoint includes branch fields

Check that `/api/projects/{id}/tree` returns node data including `branch` and `branch_active`. Since `TaskNode.to_dict()` now includes these fields (from Task 1), the tree endpoint will automatically include them. Verify:

Run: `.venv/bin/python -c "from onemancompany.core.task_tree import TaskNode; n = TaskNode(); d = n.to_dict(); assert 'branch' in d and 'branch_active' in d; print('OK')"`
Expected: `OK`

### Step 3: Commit (if any changes needed, otherwise skip)

No commit needed — auto-included via data model changes.

---

## Task 8: Frontend — Task Tree Branch Rendering

**Files:**
- Modify: `frontend/task-tree.js`
- Modify: `frontend/style.css`

### Step 1: Update TaskTreeRenderer to render branch styling

In `frontend/task-tree.js`, update the `render()` method.

After building the D3 hierarchy and laying out the tree (around line 89), add branch info classes to links:

**Replace the link rendering (lines 92-102):**
```javascript
        // Connection lines — colored by child status, dashed for inactive branch
        this.g.selectAll('.tree-link')
            .data(root.links())
            .enter()
            .append('path')
            .attr('class', d => {
                const status = d.target.data.status;
                const active = d.target.data.branch_active !== false;
                return `tree-link tree-link-${status}${active ? '' : ' tree-link-inactive'}`;
            })
            .attr('d', d3.linkVertical()
                .x(d => d.x)
                .y(d => d.y))
            .attr('stroke-width', d => d.target.data.branch_active !== false ? 2.5 : 1);
```

**Update node card rendering (around line 114-120):**
```javascript
        nodeGroups.append('rect')
            .attr('x', -this.nodeWidth / 2)
            .attr('y', -this.nodeHeight / 2)
            .attr('width', this.nodeWidth)
            .attr('height', this.nodeHeight)
            .attr('rx', 8)
            .attr('class', d => {
                const active = d.data.branch_active !== false;
                return `tree-node-card${active ? '' : ' tree-node-inactive'}`;
            });
```

Add branch label to inactive nodes, after the status pill (around line 206):
```javascript
        // Branch label for inactive nodes
        nodeGroups.filter(d => d.data.branch_active === false)
            .append('text')
            .attr('x', -this.nodeWidth / 2 + 14)
            .attr('y', -this.nodeHeight / 2 + 72)
            .attr('class', 'tree-branch-label')
            .text(d => `Branch ${d.data.branch}`);
```

### Step 2: Add CSS for branch rendering

In `frontend/style.css`, add:

```css
/* Task tree — inactive branch styling */
.tree-node-inactive {
    opacity: 0.4;
    stroke-dasharray: 4 3;
}

.tree-link-inactive {
    stroke-dasharray: 6 4;
    opacity: 0.35;
}

.tree-branch-label {
    font-size: 8px;
    fill: var(--text-dim);
    font-style: italic;
}
```

### Step 3: Test manually by loading frontend

Start server and open frontend, navigate to a project with task tree to verify styling compiles.

### Step 4: Commit

```bash
git add frontend/task-tree.js frontend/style.css
git commit -m "feat: frontend task tree branch rendering with inactive node dimming"
```

---

## Task 9: Frontend — Project Detail Team Section

**Files:**
- Modify: `frontend/app.js` (in `_loadIterationDetail`)
- Modify: `frontend/style.css`

### Step 1: Add team section to project detail tab

In `frontend/app.js`, in the `_loadIterationDetail` method, after the cost section (around line 5182) and before the "Build full panel HTML" line, add:

```javascript
        // Team section
        const team = doc.team || [];
        if (team.length > 0) {
          detailHtml += `<div style="font-size:7px;color:var(--pixel-cyan);margin:8px 0 3px;">Team (${team.length})</div>`;
          detailHtml += `<div class="project-team-list">`;
          for (const m of team) {
            const empId = m.employee_id || '';
            const role = m.role || '';
            const joined = (m.joined_at || '').substring(0, 10);
            detailHtml += `<div class="project-team-member" data-emp-id="${this._escHtml(empId)}">`;
            detailHtml += `<img src="/api/employees/${empId}/avatar" class="project-team-avatar" onerror="this.style.display='none'" />`;
            detailHtml += `<div class="project-team-info">`;
            detailHtml += `<span class="project-team-name">${this._escHtml(empId)}</span>`;
            detailHtml += `<span class="project-team-role">${this._escHtml(role)}</span>`;
            detailHtml += `</div></div>`;
          }
          detailHtml += `</div>`;
        }
```

After the panel is rendered, bind click handlers for team members (add after file click handler binding, around line 5235):

```javascript
        // Bind team member click → open employee detail
        panel.querySelectorAll('.project-team-member').forEach(el => {
          el.addEventListener('click', () => {
            const empId = el.dataset.empId;
            const emp = this.employees.find(e => e.id === empId);
            if (emp) this.openEmployeeDetail(emp);
          });
        });
```

### Step 2: Add CSS

In `frontend/style.css`:

```css
/* Project team section */
.project-team-list {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
}

.project-team-member {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 3px 6px;
    background: var(--bg-dark);
    border: 1px solid var(--border);
    border-radius: 4px;
    cursor: pointer;
    font-size: 5px;
}

.project-team-member:hover {
    border-color: var(--pixel-cyan);
}

.project-team-avatar {
    width: 16px;
    height: 16px;
    border-radius: 50%;
}

.project-team-info {
    display: flex;
    flex-direction: column;
}

.project-team-name {
    color: var(--pixel-green);
    font-size: 6px;
}

.project-team-role {
    color: var(--text-dim);
    font-size: 5px;
}
```

### Step 3: Commit

```bash
git add frontend/app.js frontend/style.css
git commit -m "feat: project detail team section with clickable members"
```

---

## Task 10: Frontend — Employee Project History

**Files:**
- Modify: `frontend/app.js` (in `openEmployeeDetail`)
- Modify: `frontend/index.html` (add container in employee modal)
- Modify: `frontend/style.css`

### Step 1: Add project history container to employee modal HTML

In `frontend/index.html`, find the employee modal content area. Add a new section after the existing sections (guidance notes or similar):

```html
<div class="emp-section" id="emp-detail-projects-section">
  <div class="emp-section-title">Project History</div>
  <div id="emp-detail-projects" class="emp-detail-projects">
    <span class="empty-hint">Loading...</span>
  </div>
</div>
```

### Step 2: Fetch and render project history in openEmployeeDetail

In `frontend/app.js`, in `openEmployeeDetail()`, after line 1343 (`this._fetchCronList(emp.id);`), add:

```javascript
    // Fetch project history
    this._fetchEmployeeProjects(emp.id);
```

Add new method:

```javascript
  _fetchEmployeeProjects(employeeId) {
    const container = document.getElementById('emp-detail-projects');
    if (!container) return;
    container.innerHTML = '<span class="empty-hint">Loading...</span>';

    fetch(`/api/employees/${employeeId}/projects`)
      .then(r => r.json())
      .then(projects => {
        if (!projects || projects.length === 0) {
          container.innerHTML = '<span class="empty-hint">No project history</span>';
          return;
        }
        let html = '';
        for (const p of projects) {
          const statusCls = p.status === 'completed' ? 'pixel-green' : 'pixel-yellow';
          html += `<div class="emp-project-item" data-project-id="${this._escHtml(p.project_id)}">`;
          html += `<div class="emp-project-task">${this._escHtml(p.task || p.project_id)}</div>`;
          html += `<div class="emp-project-meta">`;
          html += `<span class="emp-project-role">${this._escHtml(p.role_in_project)}</span>`;
          html += `<span style="color:var(--${statusCls});">${this._escHtml(p.status)}</span>`;
          html += `</div></div>`;
        }
        container.innerHTML = html;

        // Bind click → open project detail
        container.querySelectorAll('.emp-project-item').forEach(el => {
          el.addEventListener('click', () => {
            const pid = el.dataset.projectId;
            this.closeEmployeeDetail();
            this._openProjectFromId(pid);
          });
        });
      })
      .catch(() => {
        container.innerHTML = '<span class="empty-hint">Failed to load</span>';
      });
  }

  _openProjectFromId(projectId) {
    // Open project detail — reuse existing iteration detail loader
    this._loadIterationDetail(projectId, projectId);
    const detailEl = document.getElementById('project-detail');
    if (detailEl) detailEl.classList.remove('hidden');
  }
```

### Step 3: Add CSS

In `frontend/style.css`:

```css
/* Employee project history */
.emp-detail-projects {
    max-height: 100px;
    overflow-y: auto;
}

.emp-project-item {
    padding: 3px 4px;
    border-bottom: 1px solid var(--border);
    cursor: pointer;
    font-size: 5px;
}

.emp-project-item:hover {
    background: var(--bg-dark);
}

.emp-project-task {
    color: var(--pixel-white);
    font-size: 6px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.emp-project-meta {
    display: flex;
    justify-content: space-between;
    color: var(--text-dim);
    font-size: 5px;
    margin-top: 1px;
}

.emp-project-role {
    color: var(--pixel-cyan);
}
```

### Step 4: Commit

```bash
git add frontend/app.js frontend/index.html frontend/style.css
git commit -m "feat: employee modal project history with bidirectional navigation"
```

---

## Task 11: Version Management — python-semantic-release

**Files:**
- Modify: `pyproject.toml`
- Delete: `.git/hooks/post-commit`
- Create: `CHANGELOG.md` (empty placeholder)

### Step 1: Add python-semantic-release to dev dependencies

In `pyproject.toml`, update the `[dependency-groups]` section:

```toml
[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "pytest-timeout>=2.3",
    "python-semantic-release>=9",
]
```

### Step 2: Add semantic-release config

In `pyproject.toml`, add at the end:

```toml
[tool.semantic_release]
version_toml = ["pyproject.toml:project.version"]
branch = "main"
changelog_file = "CHANGELOG.md"
build_command = ""
commit_message = "chore(release): {version}"
```

### Step 3: Remove post-commit hook

```bash
rm .git/hooks/post-commit
```

### Step 4: Remove README changelog markers (if present)

Check if `README.md` has changelog markers and remove them:

```bash
grep -n 'CHANGELOG_START\|CHANGELOG_END' README.md
```

If found, remove the lines and the content between them.

### Step 5: Install dev dependencies

```bash
.venv/bin/pip install "python-semantic-release>=9"
```

### Step 6: Verify semantic-release works

```bash
.venv/bin/semantic-release version --print
```

Expected: Outputs the current or next version number without error.

### Step 7: Commit

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore: set up python-semantic-release with conventional commits"
```

---

## Summary of All Commits

1. `feat: add branch model to TaskNode and TaskTree for follow-up isolation`
2. `feat: task_followup creates new branch instead of appending to old`
3. `feat: vessel _on_task_done filters by active branch`
4. `feat: enforce EA dispatch only to O-level executives`
5. `feat: add update_project_team tool and COO 4-phase project workflow`
6. `feat: add employee projects API endpoint`
7. `feat: frontend task tree branch rendering with inactive node dimming`
8. `feat: project detail team section with clickable members`
9. `feat: employee modal project history with bidirectional navigation`
10. `chore: set up python-semantic-release with conventional commits`
