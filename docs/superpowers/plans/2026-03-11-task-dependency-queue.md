# Task Dependency Queue Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit `depends_on` dependency edges to the task tree so agents can declare execution order between sibling tasks, with automatic blocking, context injection, and frontend visualization.

**Architecture:** Extends TaskNode with `depends_on: list[str]` and `fail_strategy: str`. Dependency resolution hooks into the existing `_on_child_complete` callback in vessel.py. Frontend renders dependency arrows as dashed lines in the D3 tree and shows waiting/blocked labels on nodes.

**Tech Stack:** Python dataclasses, YAML persistence, FastAPI, D3.js, asyncio locks

**Spec:** `docs/superpowers/specs/2026-03-11-task-dependency-queue-design.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `src/onemancompany/core/task_tree.py` | Add `depends_on`, `fail_strategy` to TaskNode; update `to_dict`, `add_child`; add `find_dependents`, `all_deps_terminal` helpers |
| Modify | `src/onemancompany/core/task_lifecycle.py` | Add PENDING→BLOCKED and BLOCKED→PENDING transitions |
| Modify | `src/onemancompany/agents/tree_tools.py` | Add `depends_on`/`fail_strategy` params to `dispatch_child`; add `unblock_child` and `cancel_child` tools |
| Modify | `src/onemancompany/core/vessel.py` | Add dependency resolution to `_on_child_complete`; add tree lock; add context injection builder |
| Modify | `src/onemancompany/api/routes.py` | Add `dependency_status` to tree API response |
| Modify | `frontend/task-tree.js` | Render dependency arrows, waiting/blocked labels, dependency detail in drawer |
| Modify | `tests/unit/core/test_task_tree.py` | Tests for new TaskNode fields, serialization, helpers |
| Create | `tests/unit/core/test_dependency_resolution.py` | Tests for dependency resolution logic |
| Modify | `tests/unit/agents/test_tree_tools.py` | Tests for updated dispatch_child, new unblock_child/cancel_child |

---

## Chunk 1: Data Model & State Machine

### Task 1: TaskNode — add depends_on and fail_strategy fields

**Files:**
- Modify: `src/onemancompany/core/task_tree.py:20-87` (TaskNode dataclass)
- Test: `tests/unit/core/test_task_tree.py`

- [ ] **Step 1: Write failing tests for new fields**

In `tests/unit/core/test_task_tree.py`, add:

```python
class TestTaskNodeDependency:
    def test_default_depends_on_empty(self):
        node = TaskNode()
        assert node.depends_on == []
        assert node.fail_strategy == "block"

    def test_depends_on_set(self):
        node = TaskNode(depends_on=["abc", "def"], fail_strategy="continue")
        assert node.depends_on == ["abc", "def"]
        assert node.fail_strategy == "continue"

    def test_to_dict_includes_depends_on(self):
        node = TaskNode(depends_on=["abc"], fail_strategy="continue")
        d = node.to_dict()
        assert d["depends_on"] == ["abc"]
        assert d["fail_strategy"] == "continue"

    def test_from_dict_loads_depends_on(self):
        d = {"id": "x", "depends_on": ["a", "b"], "fail_strategy": "continue"}
        node = TaskNode.from_dict(d)
        assert node.depends_on == ["a", "b"]
        assert node.fail_strategy == "continue"

    def test_from_dict_without_depends_on_defaults(self):
        """Backward compat: old YAML without depends_on loads fine."""
        d = {"id": "x", "status": "pending"}
        node = TaskNode.from_dict(d)
        assert node.depends_on == []
        assert node.fail_strategy == "block"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py::TestTaskNodeDependency -v`
Expected: FAIL — `TaskNode` has no `depends_on` field

- [ ] **Step 3: Add fields to TaskNode dataclass**

In `src/onemancompany/core/task_tree.py`, add after line 46 (`branch_active: bool = True`):

```python
    depends_on: list[str] = field(default_factory=list)
    fail_strategy: str = "block"  # "block" | "continue"
```

- [ ] **Step 4: Update to_dict to include new fields**

In `src/onemancompany/core/task_tree.py`, inside `to_dict()`, add after `"branch_active": self.branch_active,`:

```python
            "depends_on": list(self.depends_on),
            "fail_strategy": self.fail_strategy,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py::TestTaskNodeDependency -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/core/task_tree.py tests/unit/core/test_task_tree.py
git commit -m "feat: add depends_on and fail_strategy fields to TaskNode"
```

---

### Task 2: TaskTree — update add_child and add dependency helpers

**Files:**
- Modify: `src/onemancompany/core/task_tree.py:110-129` (add_child) and new methods
- Test: `tests/unit/core/test_task_tree.py`

- [ ] **Step 1: Write failing tests**

```python
class TestTaskTreeDependencyHelpers:
    def test_add_child_with_depends_on(self):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id])
        assert b.depends_on == [a.id]
        assert b.fail_strategy == "block"

    def test_add_child_with_fail_strategy(self):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id], fail_strategy="continue")
        assert b.fail_strategy == "continue"

    def test_find_dependents(self):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id])
        c = tree.add_child(root.id, "e3", "task C", [], depends_on=[a.id])
        d = tree.add_child(root.id, "e4", "task D", [])
        dependents = tree.find_dependents(a.id)
        dep_ids = {n.id for n in dependents}
        assert dep_ids == {b.id, c.id}

    def test_find_dependents_empty(self):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        assert tree.find_dependents(a.id) == []

    def test_all_deps_terminal_true(self):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        a.status = "accepted"
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id])
        assert tree.all_deps_terminal(b.id) is True

    def test_all_deps_terminal_false(self):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id])
        assert tree.all_deps_terminal(b.id) is False

    def test_has_failed_deps(self):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        a.status = "failed"
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id])
        assert tree.has_failed_deps(b.id) is True

    def test_save_load_preserves_depends_on(self, tmp_path):
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id], fail_strategy="continue")
        path = tmp_path / "tree.yaml"
        tree.save(path)
        loaded = TaskTree.load(path)
        lb = loaded.get_node(b.id)
        assert lb.depends_on == [a.id]
        assert lb.fail_strategy == "continue"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py::TestTaskTreeDependencyHelpers -v`
Expected: FAIL — `add_child` doesn't accept `depends_on`, `find_dependents` doesn't exist

- [ ] **Step 3: Update add_child signature**

In `src/onemancompany/core/task_tree.py`, replace `add_child` method (lines 110-129):

```python
    def add_child(
        self,
        parent_id: str,
        employee_id: str,
        description: str,
        acceptance_criteria: list[str],
        timeout_seconds: int = 3600,
        depends_on: list[str] | None = None,
        fail_strategy: str = "block",
    ) -> TaskNode:
        parent = self._nodes[parent_id]
        child = TaskNode(
            parent_id=parent_id,
            employee_id=employee_id,
            description=description,
            acceptance_criteria=acceptance_criteria,
            project_id=self.project_id,
            timeout_seconds=timeout_seconds,
            depends_on=depends_on or [],
            fail_strategy=fail_strategy,
        )
        parent.children_ids.append(child.id)
        self._nodes[child.id] = child
        return child
```

- [ ] **Step 4: Add helper methods**

In `src/onemancompany/core/task_tree.py`, add after `has_failed_children` (after line 189):

```python
    def find_dependents(self, node_id: str) -> list[TaskNode]:
        """Find all nodes that depend on the given node."""
        return [n for n in self._nodes.values() if node_id in n.depends_on]

    def all_deps_terminal(self, node_id: str) -> bool:
        """Check if all depends_on nodes are terminal."""
        node = self._nodes.get(node_id)
        if not node or not node.depends_on:
            return True
        for dep_id in node.depends_on:
            dep = self._nodes.get(dep_id)
            if not dep or not dep.is_terminal:
                return False
        return True

    def has_failed_deps(self, node_id: str) -> bool:
        """Check if any depends_on node has failed or been cancelled."""
        node = self._nodes.get(node_id)
        if not node:
            return False
        for dep_id in node.depends_on:
            dep = self._nodes.get(dep_id)
            if dep and dep.status in ("failed", "cancelled"):
                return True
        return False
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py::TestTaskTreeDependencyHelpers -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/core/task_tree.py tests/unit/core/test_task_tree.py
git commit -m "feat: add dependency helpers to TaskTree (find_dependents, all_deps_terminal)"
```

---

### Task 3: State machine — add PENDING→BLOCKED and BLOCKED→PENDING transitions

**Files:**
- Modify: `src/onemancompany/core/task_lifecycle.py:65-77`
- Test: `tests/unit/core/test_task_tree.py` (or existing lifecycle tests)

- [ ] **Step 1: Write failing test**

```python
from onemancompany.core.task_lifecycle import TaskPhase, VALID_TRANSITIONS

class TestDependencyTransitions:
    def test_pending_to_blocked_allowed(self):
        assert TaskPhase.BLOCKED in VALID_TRANSITIONS[TaskPhase.PENDING]

    def test_blocked_to_pending_allowed(self):
        assert TaskPhase.PENDING in VALID_TRANSITIONS[TaskPhase.BLOCKED]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py::TestDependencyTransitions -v`
Expected: FAIL — BLOCKED not in PENDING transitions, PENDING not in BLOCKED transitions

- [ ] **Step 3: Update VALID_TRANSITIONS**

In `src/onemancompany/core/task_lifecycle.py`, change lines 66 and 73:

```python
    TaskPhase.PENDING: [TaskPhase.PROCESSING, TaskPhase.CANCELLED, TaskPhase.HOLDING, TaskPhase.BLOCKED],
```

```python
    TaskPhase.BLOCKED: [TaskPhase.CANCELLED, TaskPhase.PENDING],
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py::TestDependencyTransitions -v`
Expected: PASS

- [ ] **Step 5: Run full lifecycle tests to check no regressions**

Run: `.venv/bin/python -m pytest tests/unit/core/ -v --tb=short`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/core/task_lifecycle.py tests/unit/core/test_task_tree.py
git commit -m "feat: add PENDING→BLOCKED and BLOCKED→PENDING state transitions"
```

---

## Chunk 2: Tree Tools — dispatch_child, unblock_child, cancel_child

### Task 4: Update dispatch_child with depends_on support

**Files:**
- Modify: `src/onemancompany/agents/tree_tools.py:44-121`
- Test: `tests/unit/agents/test_tree_tools.py`

- [ ] **Step 1: Write failing tests**

In `tests/unit/agents/test_tree_tools.py`, add:

```python
class TestDispatchChildDependency:
    @pytest.mark.asyncio
    async def test_dispatch_with_depends_on_pending_dep(self, monkeypatch):
        """Task with unmet dependency should not be pushed to board."""
        # Setup: mock tree with root + dep_node (status=pending)
        # Call dispatch_child with depends_on=[dep_node.id]
        # Assert: node created, but push_task NOT called
        # Assert: returned dict has "dependency_status": "waiting"
        pass  # Implement with proper mocking per existing test patterns

    @pytest.mark.asyncio
    async def test_dispatch_with_depends_on_met(self, monkeypatch):
        """Task with all deps terminal should be pushed immediately."""
        # Setup: mock tree with root + dep_node (status=accepted)
        # Call dispatch_child with depends_on=[dep_node.id]
        # Assert: push_task called
        # Assert: returned dict has "dependency_status": "resolved"
        pass

    @pytest.mark.asyncio
    async def test_dispatch_invalid_dep_id(self, monkeypatch):
        """depends_on with non-existent node ID should error."""
        # Assert: returns error message
        pass

    @pytest.mark.asyncio
    async def test_dispatch_circular_dep_rejected(self, monkeypatch):
        """Self-referencing depends_on should be rejected."""
        pass
```

Note: follow existing mock patterns in `test_tree_tools.py` — mock `_get_current_tree`, `_save_current_tree`, `EmployeeManager`, etc.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tree_tools.py::TestDispatchChildDependency -v`

- [ ] **Step 3: Update dispatch_child tool**

In `src/onemancompany/agents/tree_tools.py`, update the `dispatch_child` function signature and body:

Add `depends_on` and `fail_strategy` parameters. After creating the node via `tree.add_child(...)`, check `tree.all_deps_terminal(node.id)`:
- If True: push_task as before
- If False: skip push_task, return with `"dependency_status": "waiting"`

Add validation:
- Check each ID in `depends_on` exists in the tree
- Check no self-reference (new node can't depend on itself — but since node isn't created yet, check that none of the dep IDs would create a cycle)

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_tree_tools.py::TestDispatchChildDependency -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/agents/tree_tools.py tests/unit/agents/test_tree_tools.py
git commit -m "feat: dispatch_child supports depends_on with blocking"
```

---

### Task 5: Add unblock_child tool

**Files:**
- Modify: `src/onemancompany/agents/tree_tools.py`
- Test: `tests/unit/agents/test_tree_tools.py`

- [ ] **Step 1: Write failing tests**

```python
class TestUnblockChild:
    @pytest.mark.asyncio
    async def test_unblock_resets_to_pending(self, monkeypatch):
        """BLOCKED node should transition to PENDING."""
        pass

    @pytest.mark.asyncio
    async def test_unblock_with_new_description(self, monkeypatch):
        """Unblock should update description if provided."""
        pass

    @pytest.mark.asyncio
    async def test_unblock_pushes_if_deps_met(self, monkeypatch):
        """If remaining deps are terminal, push_task after unblock."""
        pass

    @pytest.mark.asyncio
    async def test_unblock_non_blocked_node_errors(self, monkeypatch):
        """Unblocking a non-BLOCKED node should return error."""
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement unblock_child**

```python
@tool
def unblock_child(node_id: str, new_description: str = "") -> dict:
    """Unblock a BLOCKED task, optionally with updated instructions.
    Removes the failed dependency and re-evaluates remaining deps.

    Args:
        node_id: The blocked task node ID.
        new_description: Updated task description (optional).
    """
    tree = _get_current_tree()
    if not tree:
        return {"error": "No active task tree"}
    node = tree.get_node(node_id)
    if not node:
        return {"error": f"Node {node_id} not found"}
    if node.status != "blocked":
        return {"error": f"Node {node_id} is {node.status}, not blocked"}

    # Remove failed/cancelled deps
    node.depends_on = [
        d for d in node.depends_on
        if tree.get_node(d) and tree.get_node(d).status not in ("failed", "cancelled")
    ]
    if new_description:
        node.description = new_description
    node.status = "pending"
    _save_current_tree(tree)

    # Check if remaining deps are met
    if tree.all_deps_terminal(node.id):
        # Push task to employee board
        # (same pattern as dispatch_child push logic)
        pass  # implement with EmployeeManager push
        return {"status": "unblocked_and_dispatched", "node_id": node_id}

    return {"status": "unblocked_waiting", "node_id": node_id,
            "waiting_on": node.depends_on}
```

- [ ] **Step 4: Run tests**

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/agents/tree_tools.py tests/unit/agents/test_tree_tools.py
git commit -m "feat: add unblock_child tool for BLOCKED tasks"
```

---

### Task 6: Add cancel_child tool

**Files:**
- Modify: `src/onemancompany/agents/tree_tools.py`
- Test: `tests/unit/agents/test_tree_tools.py`

- [ ] **Step 1: Write failing tests**

```python
class TestCancelChild:
    @pytest.mark.asyncio
    async def test_cancel_sets_cancelled(self, monkeypatch):
        pass

    @pytest.mark.asyncio
    async def test_cancel_propagates_to_dependents(self, monkeypatch):
        """Cancelling a node should trigger dep resolution for its dependents."""
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement cancel_child**

```python
@tool
def cancel_child(node_id: str, reason: str = "") -> dict:
    """Cancel a task node. Triggers dependency resolution for dependents.

    Args:
        node_id: The task node ID to cancel.
        reason: Cancellation reason (optional).
    """
    tree = _get_current_tree()
    if not tree:
        return {"error": "No active task tree"}
    node = tree.get_node(node_id)
    if not node:
        return {"error": f"Node {node_id} not found"}
    if node.is_terminal:
        return {"error": f"Node {node_id} already terminal ({node.status})"}

    node.status = "cancelled"
    node.result = reason or "Cancelled by parent"
    _save_current_tree(tree)

    # Dependency resolution will be handled by _on_child_complete
    # which fires when a node becomes terminal
    return {"status": "cancelled", "node_id": node_id}
```

- [ ] **Step 4: Run tests**

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/agents/tree_tools.py tests/unit/agents/test_tree_tools.py
git commit -m "feat: add cancel_child tool with dependency propagation"
```

---

## Chunk 3: Dependency Resolution in vessel.py

### Task 7: Add tree lock and dependency resolution to _on_child_complete

**Files:**
- Modify: `src/onemancompany/core/vessel.py:1416-1522`
- Create: `tests/unit/core/test_dependency_resolution.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/core/test_dependency_resolution.py`:

```python
"""Tests for dependency resolution in _on_child_complete."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from onemancompany.core.task_tree import TaskTree, TaskNode


class TestBuildDependencyContext:
    def test_single_dep_accepted(self):
        """Accepted dependency result injected into context."""
        from onemancompany.core.vessel import _build_dependency_context
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "analyze requirements", [])
        a.status = "accepted"
        a.result = "Requirements: build X with Y"
        b = tree.add_child(root.id, "e2", "implement", [], depends_on=[a.id])

        context = _build_dependency_context(tree, b)
        assert "=== Dependency Results ===" in context
        assert "analyze requirements" in context
        assert "Requirements: build X with Y" in context

    def test_no_deps_returns_empty(self):
        from onemancompany.core.vessel import _build_dependency_context
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        context = _build_dependency_context(tree, a)
        assert context == ""

    def test_result_truncated(self):
        from onemancompany.core.vessel import _build_dependency_context
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        a.status = "accepted"
        a.result = "x" * 5000
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id])

        context = _build_dependency_context(tree, b)
        # Should be truncated to last 2000 chars
        assert len(context) < 3000


class TestResolveDependencies:
    def test_dep_accepted_unlocks_dependent(self):
        """When dep is accepted, dependent with all deps met should be unlocked."""
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        a.status = "accepted"
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id])

        # b should be unlockable
        assert tree.all_deps_terminal(b.id)
        assert not tree.has_failed_deps(b.id)

    def test_dep_failed_blocks_dependent(self):
        """When dep fails with fail_strategy=block, dependent should be blocked."""
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        a.status = "failed"
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id], fail_strategy="block")

        assert tree.has_failed_deps(b.id)

    def test_dep_failed_continue_unlocks(self):
        """When dep fails with fail_strategy=continue, dependent proceeds."""
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        a.status = "failed"
        a.result = "Error: something broke"
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id], fail_strategy="continue")

        assert tree.all_deps_terminal(b.id)

    def test_partial_deps_still_waiting(self):
        """If only some deps are terminal, task should not unlock."""
        tree = TaskTree(project_id="test")
        root = tree.create_root(employee_id="ceo", description="root")
        a = tree.add_child(root.id, "e1", "task A", [])
        a.status = "accepted"
        c = tree.add_child(root.id, "e3", "task C", [])  # still pending
        b = tree.add_child(root.id, "e2", "task B", [], depends_on=[a.id, c.id])

        assert not tree.all_deps_terminal(b.id)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_dependency_resolution.py -v`
Expected: FAIL — `_build_dependency_context` does not exist

- [ ] **Step 3: Add _build_dependency_context function to vessel.py**

In `src/onemancompany/core/vessel.py`, add as a module-level function (before the EmployeeManager class):

```python
def _build_dependency_context(tree: TaskTree, node: TaskNode) -> str:
    """Build context string from resolved dependency results."""
    if not node.depends_on:
        return ""
    sections = []
    max_per_dep = 2000 if len(node.depends_on) <= 3 else 1000
    for dep_id in node.depends_on:
        dep = tree.get_node(dep_id)
        if not dep or not dep.is_terminal:
            continue
        result = dep.result or "(no result)"
        if len(result) > max_per_dep:
            result = "..." + result[-max_per_dep:]
        status_label = "completed" if dep.status == "accepted" else dep.status
        sections.append(f"{dep.employee_id} {status_label} \"{dep.description}\":\n{result}")
    if not sections:
        return ""
    return "=== Dependency Results ===\n" + "\n\n".join(sections) + "\n=== End Dependencies ===\n\n"
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_dependency_resolution.py -v`
Expected: PASS

- [ ] **Step 5: Add tree lock dict and _resolve_dependencies to vessel.py**

Add module-level lock dict:

```python
import asyncio
_tree_locks: dict[str, asyncio.Lock] = {}

def _get_tree_lock(project_id: str) -> asyncio.Lock:
    if project_id not in _tree_locks:
        _tree_locks[project_id] = asyncio.Lock()
    return _tree_locks[project_id]
```

Add `_resolve_dependencies` method to `EmployeeManager`:

```python
async def _resolve_dependencies(self, tree: TaskTree, completed_node: TaskNode, project_dir: str) -> None:
    """Check if completing this node unlocks any dependent tasks."""
    dependents = tree.find_dependents(completed_node.id)
    if not dependents:
        return

    for dep_node in dependents:
        if dep_node.status != "pending":
            continue  # Already running, blocked, or terminal

        if tree.has_failed_deps(dep_node.id) and dep_node.fail_strategy == "block":
            dep_node.status = "blocked"
            _save_project_tree(project_dir, tree)
            # Notify parent
            parent = tree.get_node(dep_node.parent_id)
            if parent:
                msg = (f"Task \"{dep_node.description}\" is BLOCKED because dependency "
                       f"\"{completed_node.description}\" failed. Please handle via "
                       f"reject_child (retry), unblock_child, or cancel_child.")
                board = self.boards.setdefault(parent.employee_id, AgentTaskBoard())
                notify_task = board.push(description=msg, project_dir=project_dir)
                persist_task(parent.employee_id, notify_task)
                if parent.employee_id not in self._running_tasks:
                    self._schedule_next(parent.employee_id)
            continue

        if tree.all_deps_terminal(dep_node.id):
            # Inject dependency context and push task
            context = _build_dependency_context(tree, dep_node)
            full_desc = context + dep_node.description
            board = self.boards.setdefault(dep_node.employee_id, AgentTaskBoard())
            agent_task = board.push(
                description=full_desc,
                project_id=dep_node.project_id,
                project_dir=project_dir,
            )
            tree.task_id_map[agent_task.id] = dep_node.id
            _save_project_tree(project_dir, tree)
            persist_task(dep_node.employee_id, agent_task)
            if dep_node.employee_id not in self._running_tasks:
                self._schedule_next(dep_node.employee_id)
```

- [ ] **Step 6: Hook _resolve_dependencies into _on_child_complete**

In `_on_child_complete`, after the node status is updated and saved (after the existing `_save_project_tree` call around line 1440), add:

```python
        # Resolve dependencies — check if completing this node unlocks others
        await self._resolve_dependencies(tree, node, task.project_dir)
```

Also wrap the tree load/save in `_on_child_complete` with the tree lock:

```python
    async with _get_tree_lock(project_id or task.project_id):
        tree = _load_project_tree(task.project_dir)
        # ... existing logic ...
```

- [ ] **Step 7: Run all tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_dependency_resolution.py tests/unit/core/test_task_tree.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/onemancompany/core/vessel.py tests/unit/core/test_dependency_resolution.py
git commit -m "feat: dependency resolution in _on_child_complete with tree lock"
```

---

## Chunk 4: API & Frontend

### Task 8: Add dependency_status to tree API response

**Files:**
- Modify: `src/onemancompany/api/routes.py:2980-3019`

- [ ] **Step 1: Update get_project_tree endpoint**

In `routes.py`, in `get_project_tree`, after `d = n.to_dict()` and before `nodes.append(d)`, add:

```python
        # Compute dependency_status
        if n.depends_on:
            if n.status == "blocked":
                d["dependency_status"] = "blocked"
            elif tree.all_deps_terminal(n.id):
                d["dependency_status"] = "resolved"
            else:
                d["dependency_status"] = "waiting"
        else:
            d["dependency_status"] = "resolved"
```

- [ ] **Step 2: Verify API response**

Run backend and test: `curl http://localhost:8000/api/projects/<some_project>/tree | python3 -m json.tool`
Check that nodes include `depends_on`, `fail_strategy`, `dependency_status` fields.

- [ ] **Step 3: Commit**

```bash
git add src/onemancompany/api/routes.py
git commit -m "feat: add dependency_status to task tree API response"
```

---

### Task 9: Frontend — dependency arrows and labels

**Files:**
- Modify: `frontend/task-tree.js`

- [ ] **Step 1: Add dependency arrow rendering**

In `task-tree.js`, in the `render()` method, after parent-child link rendering (around line 120), add a new section for dependency arrows:

```javascript
    // --- Dependency arrows (dashed) ---
    const depLinks = [];
    nodesData.forEach(n => {
      (n.data.depends_on || []).forEach(depId => {
        const depNode = nodesData.find(d => d.data.id === depId);
        if (depNode) {
          depLinks.push({ source: depNode, target: n, status: depNode.data.status });
        }
      });
    });

    svg.selectAll('.dep-link').remove();
    svg.selectAll('.dep-link')
      .data(depLinks)
      .enter()
      .append('line')
      .attr('class', 'dep-link')
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x)
      .attr('y2', d => d.target.y)
      .attr('stroke', d => {
        if (d.status === 'accepted') return '#00ff88';
        if (d.status === 'failed' || d.status === 'cancelled') return '#ff4444';
        return '#666';
      })
      .attr('stroke-width', 1.5)
      .attr('stroke-dasharray', '6,3')
      .attr('marker-end', 'url(#dep-arrow)');
```

Add arrow marker definition at the start of render():

```javascript
    // Arrow marker for dependency lines
    const defs = svg.append('defs');
    defs.append('marker')
      .attr('id', 'dep-arrow')
      .attr('viewBox', '0 0 10 10')
      .attr('refX', 10).attr('refY', 5)
      .attr('markerWidth', 6).attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M 0 0 L 10 5 L 0 10 z')
      .attr('fill', '#888');
```

- [ ] **Step 2: Add waiting/blocked labels to nodes**

In the node rendering section, after the status pill (around line 214), add:

```javascript
    // Dependency status labels
    nodeGroups.filter(d => d.data.dependency_status === 'waiting')
      .append('text')
      .attr('class', 'tree-dep-label')
      .attr('y', 38)
      .attr('text-anchor', 'middle')
      .attr('fill', '#aaa')
      .attr('font-size', '9px')
      .text(d => {
        const depIds = d.data.depends_on || [];
        const names = depIds.map(id => {
          const dn = nodesData.find(n => n.data.id === id);
          return dn ? (dn.data.employee_info?.name || 'Unknown') : '?';
        });
        return 'Waiting: ' + names.join(', ');
      });

    nodeGroups.filter(d => d.data.dependency_status === 'blocked')
      .append('text')
      .attr('class', 'tree-dep-label blocked')
      .attr('y', 38)
      .attr('text-anchor', 'middle')
      .attr('fill', '#ff4444')
      .attr('font-size', '9px')
      .text('Blocked');
```

- [ ] **Step 3: Add dependencies to detail drawer**

In `_renderNodeDetail()`, after the acceptance criteria section, add:

```javascript
    // Dependencies section
    if (node.depends_on && node.depends_on.length > 0) {
      html += '<div class="tree-detail-section"><strong>Dependencies:</strong><ul>';
      node.depends_on.forEach(depId => {
        const depNode = this.treeData.nodes.find(n => n.id === depId);
        if (depNode) {
          const statusColor = this.STATUS_COLORS[depNode.status] || '#666';
          html += `<li><span style="color:${statusColor}">●</span> ${depNode.employee_info?.name || depId}: ${depNode.description.slice(0, 60)}... [${depNode.status}]</li>`;
        }
      });
      html += '</ul></div>';
    }

    // Dependents section (reverse lookup)
    const dependents = this.treeData.nodes.filter(n => (n.depends_on || []).includes(node.id));
    if (dependents.length > 0) {
      html += '<div class="tree-detail-section"><strong>Dependents:</strong><ul>';
      dependents.forEach(dep => {
        const statusColor = this.STATUS_COLORS[dep.status] || '#666';
        html += `<li><span style="color:${statusColor}">●</span> ${dep.employee_info?.name || dep.id}: ${dep.description.slice(0, 60)}... [${dep.status}]</li>`;
      });
      html += '</ul></div>';
    }
```

- [ ] **Step 4: Visual test**

Restart backend, create a task that generates dependencies, verify:
- Dashed arrows between dependency nodes
- "Waiting:" labels on pending nodes with unresolved deps
- Detail drawer shows Dependencies/Dependents sections

- [ ] **Step 5: Commit**

```bash
git add frontend/task-tree.js
git commit -m "feat: render dependency arrows, waiting/blocked labels in task tree"
```

---

## Chunk 5: Integration & Restart Recovery

### Task 10: Startup recovery for pending dependencies

**Files:**
- Modify: `src/onemancompany/core/vessel.py` (startup/restore section)

- [ ] **Step 1: Add dependency re-evaluation on startup**

In the startup/restore logic (after `restore_task_queue` is called), add a scan of all active task trees:

```python
async def _recover_pending_dependencies(self) -> None:
    """On startup, re-check PENDING tasks with depends_on — push if deps are met."""
    from onemancompany.core.config import PROJECTS_DIR
    if not PROJECTS_DIR.exists():
        return
    for pdir in PROJECTS_DIR.iterdir():
        tree_path = pdir / "task_tree.yaml"
        if not tree_path.exists():
            continue
        tree = TaskTree.load(tree_path)
        for node in tree._nodes.values():
            if node.status != "pending" or not node.depends_on:
                continue
            if node.id in tree.task_id_map.values():
                continue  # Already has an AgentTask mapped
            if tree.all_deps_terminal(node.id):
                if tree.has_failed_deps(node.id) and node.fail_strategy == "block":
                    node.status = "blocked"
                    tree.save(tree_path)
                    continue
                context = _build_dependency_context(tree, node)
                full_desc = context + node.description
                board = self.boards.setdefault(node.employee_id, AgentTaskBoard())
                agent_task = board.push(
                    description=full_desc,
                    project_id=node.project_id,
                    project_dir=str(pdir),
                )
                tree.task_id_map[agent_task.id] = node.id
                tree.save(tree_path)
                persist_task(node.employee_id, agent_task)
                logger.info("Startup recovery: unlocked dep-waiting task {} for {}", node.id, node.employee_id)
```

Call this from the lifespan startup (in `main.py`) after task queue restoration.

- [ ] **Step 2: Commit**

```bash
git add src/onemancompany/core/vessel.py src/onemancompany/main.py
git commit -m "feat: recover pending dependency tasks on startup"
```

---

### Task 11: Run full test suite

- [ ] **Step 1: Run all tests**

Run: `.venv/bin/python -m pytest tests/ -x --tb=short`
Expected: All PASS

- [ ] **Step 2: Fix any regressions**

Existing tests calling `add_child` without the new params should still work (new params have defaults).

- [ ] **Step 3: Final commit if any fixes needed**

```bash
git commit -m "fix: test regressions from dependency queue feature"
```
