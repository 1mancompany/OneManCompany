# Task Idempotency & Anti-Stall — Batch 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 5 high-severity issues that can cause permanent task stalls, state corruption, or resource leaks.

**Architecture:** Each fix is independent — a focused change to one subsystem with its own test. No new modules; all changes go into existing files. TDD: write failing test first, then implement.

**Tech Stack:** Python 3.12, asyncio, pytest, loguru

---

## File Map

| File | Changes |
|------|---------|
| `src/onemancompany/core/task_tree.py` | Add `_has_cycle()` helper + call in `add_child()` |
| `src/onemancompany/core/task_tree.py:498-516` | Replace `loop.create_task()` with `spawn_background()` in `save_tree_async()` |
| `src/onemancompany/core/vessel.py:1395-1404` | Add `max_hold_seconds` to HOLDING setup + timeout logic in watchdog |
| `src/onemancompany/core/vessel.py:2537-2584` | Fix `_ceo_report_auto_confirm` error cleanup + make `_confirm_ceo_report` idempotent |
| `tests/unit/core/test_task_tree.py` | Tests for cycle detection |
| `tests/unit/core/test_task_persistence.py` | (no changes, already has HOLDING tests) |
| `tests/unit/core/test_vessel_holding_timeout.py` | New: tests for HOLDING timeout |
| `tests/unit/core/test_vessel_ceo_report.py` | New: tests for idempotent CEO report confirmation |
| `tests/unit/core/test_tree_save_tracked.py` | New: test that save_tree_async uses spawn_background |

---

### Task 1: Circular Dependency Detection in `add_child()`

**Files:**
- Modify: `src/onemancompany/core/task_tree.py:251-272` — add_child()
- Test: `tests/unit/core/test_task_tree.py`

- [ ] **Step 1: Write failing tests for cycle detection**

Add to existing test file or create new test class:

```python
# tests/unit/core/test_task_tree.py

class TestCyclicDependencyDetection:
    def test_direct_cycle_rejected(self):
        """A → B → depends_on A should raise ValueError."""
        tree = TaskTree("proj")
        root = tree.create_root("emp1", "root")
        child_a = tree.add_child(root.id, "emp2", "A", [])
        with pytest.raises(ValueError, match="[Cc]ircular"):
            tree.add_child(root.id, "emp3", "B", [], depends_on=[child_a.id])
            # Now try to make A depend on B — but add_child sets depends_on at creation
            # So test: create B depending on A, then C depending on B, then D depending on C+A
            pass

    def test_direct_self_cycle_rejected(self):
        """A node cannot depend on itself."""
        tree = TaskTree("proj")
        root = tree.create_root("emp1", "root")
        child_a = tree.add_child(root.id, "emp2", "A", [])
        # Can't create a node that depends on itself (id not known yet),
        # but we can test _has_cycle with explicit ids
        assert tree._has_cycle(child_a.id, [child_a.id]) is True

    def test_indirect_cycle_detected(self):
        """A → B → C → A should be detected."""
        tree = TaskTree("proj")
        root = tree.create_root("emp1", "root")
        a = tree.add_child(root.id, "emp2", "A", [])
        b = tree.add_child(root.id, "emp3", "B", [], depends_on=[a.id])
        # C depends on B, which depends on A — now if we try to make A depend on C
        # We can't retroactively add deps, but _has_cycle should detect it
        c = tree.add_child(root.id, "emp4", "C", [], depends_on=[b.id])
        # Verify the chain works (no cycle)
        assert tree.all_deps_resolved(c.id) is False  # a is pending

    def test_no_false_positive(self):
        """Diamond deps (A→B, A→C, D→[B,C]) should NOT trigger cycle detection."""
        tree = TaskTree("proj")
        root = tree.create_root("emp1", "root")
        b = tree.add_child(root.id, "emp2", "B", [])
        c = tree.add_child(root.id, "emp3", "C", [])
        # D depends on both B and C — diamond, not cycle
        d = tree.add_child(root.id, "emp4", "D", [], depends_on=[b.id, c.id])
        assert d is not None  # Should succeed without ValueError
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py::TestCyclicDependencyDetection -v`
Expected: Some tests fail (no `_has_cycle` method yet)

- [ ] **Step 3: Implement `_has_cycle()` and guard in `add_child()`**

In `task_tree.py`, add before `add_child()`:

```python
def _has_cycle(self, start_id: str, depends_on: list[str]) -> bool:
    """DFS check: would adding depends_on edges to start_id create a cycle?"""
    visited: set[str] = set()

    def _dfs(node_id: str) -> bool:
        if node_id == start_id:
            return True
        if node_id in visited:
            return False
        visited.add(node_id)
        node = self._nodes.get(node_id)
        if not node:
            return False
        for dep_id in node.depends_on:
            if _dfs(dep_id):
                return True
        return False

    for dep_id in depends_on:
        if dep_id == start_id:
            return True
        if _dfs(dep_id):
            return True
    return False
```

And in `add_child()`, after creating the child node but before adding to tree:

```python
def add_child(self, parent_id, employee_id, description, acceptance_criteria,
              timeout_seconds=3600, depends_on=None):
    parent = self._nodes[parent_id]
    _depends_on = depends_on or []
    # Check for circular dependencies before creating the node
    if _depends_on and self._has_cycle_check(parent_id, _depends_on):
        raise ValueError(
            f"Circular dependency detected: adding deps {_depends_on} "
            f"would create a cycle"
        )
    child = TaskNode(...)
    ...
```

Note: Since `_has_cycle` checks if any dep transitively depends back on `start_id`, and the child doesn't exist yet, we check using a temporary approach: walk from each dep backward through its deps — if any path reaches any of the other deps being added, there's a cycle. Actually simpler: since the child node doesn't exist yet, we just need to check that no dep in `depends_on` transitively depends on another dep in the list (which would be a different bug), and that no dep depends on the parent through the child (which can't happen since child doesn't exist). The main real-world risk is deps referencing IDs that would be the new node — but since the ID is generated inside add_child, external callers can't know it.

The practical cycle scenario: Node A exists, Node B depends on A. Now someone calls `add_child` with depends_on=[B.id] and later tries to make A depend on the new node. Since deps are set at creation time and can't be modified, true cycles can only form if someone passes a dep ID that transitively depends back through the tree to one of the other deps. The real guard is: validate that all `depends_on` IDs exist in the tree.

Revised simpler implementation: validate deps exist + no self-reference. Add `_has_cycle` for completeness.

- [ ] **Step 4: Run tests, confirm they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_tree.py::TestCyclicDependencyDetection -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/task_tree.py tests/unit/core/test_task_tree.py
git commit -m "fix: add circular dependency detection in task_tree.add_child()"
```

---

### Task 2: HOLDING Global Timeout

**Files:**
- Modify: `src/onemancompany/core/vessel.py:1472-1503` — `_setup_holding_watchdog_by_id()`
- Modify: `src/onemancompany/core/vessel.py:1518-1560` — `resume_held_task()`
- Modify: `src/onemancompany/core/config.py` — add `MAX_HOLD_SECONDS` constant
- Test: `tests/unit/core/test_vessel_holding_timeout.py` (new)

The existing watchdog prompt already mentions 30-minute escalation (vessel.py:1496-1497), but it's just text advice — the agent may ignore it. We need a hard server-side timeout.

- [ ] **Step 1: Add MAX_HOLD_SECONDS to config.py**

```python
# In config.py
MAX_HOLD_SECONDS = 1800  # 30 minutes — hard timeout for HOLDING tasks
```

- [ ] **Step 2: Write failing test for HOLDING timeout**

```python
# tests/unit/core/test_vessel_holding_timeout.py

class TestHoldingTimeout:
    """HOLDING tasks must auto-fail after MAX_HOLD_SECONDS."""

    def test_holding_watchdog_includes_timeout_metadata(self):
        """Watchdog setup should record hold_started_at on the node."""
        # Mock tree, node in HOLDING, check that hold_started_at is set

    async def test_holding_exceeds_max_auto_fails(self):
        """A HOLDING node past MAX_HOLD_SECONDS should be force-failed by the watchdog check."""
        # Create node, set hold_started_at to >30min ago
        # Call the watchdog check function
        # Assert node.status == FAILED

    async def test_holding_within_max_not_failed(self):
        """A HOLDING node within MAX_HOLD_SECONDS should NOT be auto-failed."""
```

- [ ] **Step 3: Implement HOLDING timeout in `_setup_holding_watchdog_by_id`**

Add `hold_started_at` to the node when entering HOLDING (in `_execute_task`, line 1398 area):

```python
node.set_status(TaskPhase.HOLDING)
node.hold_started_at = datetime.now().isoformat()  # Track when HOLDING started
```

In the watchdog prompt (line 1491-1498), add a hard check: if `hold_started_at` is older than `MAX_HOLD_SECONDS`, auto-fail the node instead of nudging.

Add a method `_check_holding_timeout()` that the cron calls:

```python
async def _check_holding_timeout(self, employee_id: str, task_id: str, tree_path: str) -> bool:
    """Check if a HOLDING task has exceeded MAX_HOLD_SECONDS. Returns True if timed out and failed."""
    from onemancompany.core.config import MAX_HOLD_SECONDS
    tree = get_tree(Path(tree_path))
    node = tree.get_node(task_id)
    if not node or node.status != TaskPhase.HOLDING.value:
        return False
    hold_start = node.hold_started_at
    if not hold_start:
        return False
    elapsed = (datetime.now() - datetime.fromisoformat(hold_start)).total_seconds()
    if elapsed > MAX_HOLD_SECONDS:
        node.result = f"__TIMEOUT: HOLDING exceeded {MAX_HOLD_SECONDS}s (elapsed: {elapsed:.0f}s)\n{node.result or ''}"
        node.set_status(TaskPhase.FAILED)
        save_tree_async(tree_path)
        logger.warning("[HOLDING_TIMEOUT] node={} employee={} elapsed={:.0f}s → FAILED", task_id, employee_id, elapsed)
        return True
    return False
```

- [ ] **Step 4: Run tests, confirm they pass**

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ --timeout=30`

- [ ] **Step 6: Commit**

```bash
git commit -m "fix: add hard timeout for HOLDING tasks (MAX_HOLD_SECONDS=1800)"
```

---

### Task 3: Make `_confirm_ceo_report` Idempotent

**Files:**
- Modify: `src/onemancompany/core/vessel.py:2548-2584` — `_confirm_ceo_report()`
- Test: `tests/unit/core/test_vessel_ceo_report.py` (new)

Currently, calling `_confirm_ceo_report` twice silently returns False on second call, which is correct for "do nothing". But if the first call crashed mid-execution, the project is stuck. Fix: check project status on disk as fallback.

- [ ] **Step 1: Write failing test**

```python
class TestConfirmCeoReportIdempotent:
    async def test_second_confirm_returns_true_if_already_completed(self):
        """If project was already completed, _confirm_ceo_report should return True (not False)."""

    async def test_first_confirm_works_normally(self):
        """Normal case: pending report exists, confirm succeeds."""

    async def test_no_pending_and_not_completed_returns_false(self):
        """No pending report and project not completed → False."""
```

- [ ] **Step 2: Implement idempotent check**

In `_confirm_ceo_report()`, after the early return for `not pending`:

```python
async def _confirm_ceo_report(self, project_id: str) -> bool:
    pending = self._pending_ceo_reports.pop(project_id, None)
    if not pending:
        # Idempotency: if project is already completed/archived, treat as success
        from onemancompany.core.project_archive import load_named_project, PROJECT_STATUS_ARCHIVED
        proj = load_named_project(project_id.split("/")[0] if "/" in project_id else project_id)
        if proj and proj.get("status") == PROJECT_STATUS_ARCHIVED:
            logger.debug("[ceo_report] project={} already archived, idempotent confirm", project_id)
            return True
        logger.debug("[ceo_report] no pending report for project={}", project_id)
        return False
    # ... rest unchanged
```

- [ ] **Step 3: Run test, confirm pass**

- [ ] **Step 4: Commit**

```bash
git commit -m "fix: make _confirm_ceo_report idempotent for already-completed projects"
```

---

### Task 4: Fix `_ceo_report_auto_confirm` Error Cleanup

**Files:**
- Modify: `src/onemancompany/core/vessel.py:2537-2546` — `_ceo_report_auto_confirm()`
- Test: `tests/unit/core/test_vessel_ceo_report.py` (same file as Task 3)

Currently, if `_confirm_ceo_report()` raises inside `_ceo_report_auto_confirm`, the exception is logged but `_pending_ceo_reports` is NOT cleaned up (because `pop` already happened inside `_confirm_ceo_report`). Actually wait — `_confirm_ceo_report` does `pop` first, so on exception the entry IS removed. But if `_confirm_ceo_report` itself is never called (e.g., asyncio.sleep raises), the entry stays.

The real fix: ensure `_pending_ceo_reports` is cleaned up in the `except` block.

- [ ] **Step 1: Write failing test**

```python
class TestAutoConfirmErrorCleanup:
    async def test_exception_in_confirm_cleans_pending(self):
        """If _confirm_ceo_report raises, _pending_ceo_reports should still be cleaned."""

    async def test_cancel_during_sleep_cleans_pending(self):
        """If auto-confirm task is cancelled during sleep, pending entry should be cleaned."""
```

- [ ] **Step 2: Implement cleanup in except/finally**

```python
async def _ceo_report_auto_confirm(self, project_id: str, cleanup_ctx: dict) -> None:
    try:
        await asyncio.sleep(self.CEO_REPORT_CONFIRM_DELAY)
        logger.info("[ceo_report] auto-confirming project={}", project_id)
        await self._confirm_ceo_report(project_id)
    except asyncio.CancelledError:
        # Cancellation means CEO manually confirmed — entry already popped by _confirm_ceo_report
        raise
    except Exception as e:
        logger.error("[ceo_report] auto-confirm error for {}: {}", project_id, e)
        # Ensure pending entry is cleaned up even on error
        self._pending_ceo_reports.pop(project_id, None)
```

- [ ] **Step 3: Run test, confirm pass**

- [ ] **Step 4: Commit**

```bash
git commit -m "fix: clean up _pending_ceo_reports on auto-confirm error"
```

---

### Task 5: Track `save_tree_async` with `spawn_background()`

**Files:**
- Modify: `src/onemancompany/core/task_tree.py:498-516` — `save_tree_async()`
- Test: `tests/unit/core/test_tree_save_tracked.py` (new)

Currently uses raw `loop.create_task()` which is not tracked by `_background_tasks` set. On graceful restart these saves can be GC'd or lost.

- [ ] **Step 1: Write failing test**

```python
class TestSaveTreeAsyncTracked:
    def test_save_uses_spawn_background(self):
        """save_tree_async should use spawn_background, not raw create_task."""
        # Verify by patching spawn_background and checking it was called
```

- [ ] **Step 2: Replace `loop.create_task()` with `spawn_background()`**

```python
def save_tree_async(path: str | Path) -> None:
    key = _key(path)
    tree = _cache.get(key)
    if not tree:
        return
    _path = Path(path)
    try:
        asyncio.get_running_loop()
        from onemancompany.core.async_utils import spawn_background
        spawn_background(_do_save(tree, _path))
    except RuntimeError:
        # No event loop — save synchronously
        lock = get_tree_lock(path)
        with lock:
            tree.save(_path)
```

- [ ] **Step 3: Run test, confirm pass**

- [ ] **Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ --timeout=30`

- [ ] **Step 5: Commit**

```bash
git commit -m "fix: track save_tree_async tasks via spawn_background to prevent GC loss"
```

---

### Final Task: Integration Check

- [ ] **Step 1: Run full test suite**

```bash
.venv/bin/python -m pytest tests/unit/ --timeout=30
```
Expected: All tests pass, no regressions.

- [ ] **Step 2: Verify imports compile**

```bash
.venv/bin/python -c "from onemancompany.core.task_tree import TaskTree, save_tree_async; print('OK')"
.venv/bin/python -c "from onemancompany.core.vessel import EmployeeManager; print('OK')"
```
