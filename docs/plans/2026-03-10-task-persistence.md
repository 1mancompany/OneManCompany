# Task Persistence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Persist employee tasks to per-employee `tasks/` folders with write-through on every state change, and auto-resume unfinished tasks on any restart.

**Architecture:** Add a `_persist_task()` / `_archive_task()` / `_delete_task_file()` module in `core/task_persistence.py` that handles YAML serialization. Hook into every task status change point in `EmployeeManager`. Replace the old single-file `save_task_queue`/`restore_task_queue` with per-employee scanning at startup.

**Tech Stack:** Python, PyYAML, pathlib, pytest

**Key files reference:**
- `src/onemancompany/core/vessel.py` — `AgentTask`, `AgentTaskBoard`, `EmployeeManager`
- `src/onemancompany/core/task_lifecycle.py` — `TaskPhase`, `TERMINAL_STATES`
- `src/onemancompany/main.py` — lifespan startup/shutdown
- `src/onemancompany/core/config.py` — `EMPLOYEES_DIR`

---

### Task 1: Create `task_persistence.py` — serialize/deserialize + persist/archive/load

**Files:**
- Create: `src/onemancompany/core/task_persistence.py`
- Create: `tests/unit/core/test_task_persistence.py`

**Step 1: Write the failing tests**

```python
"""Unit tests for core/task_persistence.py — task file persistence."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from onemancompany.core.vessel import AgentTask
from onemancompany.core.task_lifecycle import TaskPhase


class TestPersistTask:
    def test_creates_yaml_file(self, tmp_path, monkeypatch):
        from onemancompany.core import task_persistence as tp

        monkeypatch.setattr(tp, "EMPLOYEES_DIR", tmp_path)

        task = AgentTask(id="abc123", description="Do something")
        tp.persist_task("00010", task)

        path = tmp_path / "00010" / "tasks" / "abc123.yaml"
        assert path.exists()

        data = yaml.safe_load(path.read_text())
        assert data["id"] == "abc123"
        assert data["description"] == "Do something"
        assert data["status"] == "pending"

    def test_overwrites_existing_file(self, tmp_path, monkeypatch):
        from onemancompany.core import task_persistence as tp

        monkeypatch.setattr(tp, "EMPLOYEES_DIR", tmp_path)

        task = AgentTask(id="abc123", description="Do something")
        tp.persist_task("00010", task)

        task.status = TaskPhase.PROCESSING
        tp.persist_task("00010", task)

        path = tmp_path / "00010" / "tasks" / "abc123.yaml"
        data = yaml.safe_load(path.read_text())
        assert data["status"] == "processing"

    def test_creates_directories(self, tmp_path, monkeypatch):
        from onemancompany.core import task_persistence as tp

        monkeypatch.setattr(tp, "EMPLOYEES_DIR", tmp_path)

        task = AgentTask(id="abc123", description="Test")
        tp.persist_task("00099", task)

        assert (tmp_path / "00099" / "tasks").is_dir()


class TestArchiveTask:
    def test_moves_to_archive(self, tmp_path, monkeypatch):
        from onemancompany.core import task_persistence as tp

        monkeypatch.setattr(tp, "EMPLOYEES_DIR", tmp_path)

        task = AgentTask(id="abc123", description="Done")
        task.status = TaskPhase.FINISHED
        tp.persist_task("00010", task)

        tp.archive_task("00010", task)

        assert not (tmp_path / "00010" / "tasks" / "abc123.yaml").exists()
        archive_path = tmp_path / "00010" / "tasks" / "archive" / "abc123.yaml"
        assert archive_path.exists()
        data = yaml.safe_load(archive_path.read_text())
        assert data["status"] == "finished"

    def test_archive_noop_if_no_file(self, tmp_path, monkeypatch):
        from onemancompany.core import task_persistence as tp

        monkeypatch.setattr(tp, "EMPLOYEES_DIR", tmp_path)

        task = AgentTask(id="nonexist", description="Ghost")
        task.status = TaskPhase.FAILED
        # Should not raise
        tp.archive_task("00010", task)


class TestLoadActiveTasks:
    def test_loads_all_yaml_files(self, tmp_path, monkeypatch):
        from onemancompany.core import task_persistence as tp

        monkeypatch.setattr(tp, "EMPLOYEES_DIR", tmp_path)

        tasks_dir = tmp_path / "00010" / "tasks"
        tasks_dir.mkdir(parents=True)

        for tid, desc, status in [
            ("aaa111", "Task A", "pending"),
            ("bbb222", "Task B", "processing"),
        ]:
            (tasks_dir / f"{tid}.yaml").write_text(yaml.dump({
                "id": tid, "description": desc, "status": status,
                "task_type": "simple", "parent_id": "", "project_id": "",
                "project_dir": "", "result": "", "created_at": "2026-01-01",
                "completed_at": "", "model_used": "", "input_tokens": 0,
                "output_tokens": 0, "total_tokens": 0, "estimated_cost_usd": 0.0,
                "logs": [],
            }))

        tasks = tp.load_active_tasks("00010")
        assert len(tasks) == 2
        ids = {t.id for t in tasks}
        assert ids == {"aaa111", "bbb222"}

    def test_resets_processing_to_pending(self, tmp_path, monkeypatch):
        from onemancompany.core import task_persistence as tp

        monkeypatch.setattr(tp, "EMPLOYEES_DIR", tmp_path)

        tasks_dir = tmp_path / "00010" / "tasks"
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "ccc333.yaml").write_text(yaml.dump({
            "id": "ccc333", "description": "Was running", "status": "processing",
            "task_type": "simple", "parent_id": "", "project_id": "",
            "project_dir": "", "result": "", "created_at": "2026-01-01",
            "completed_at": "", "model_used": "", "input_tokens": 0,
            "output_tokens": 0, "total_tokens": 0, "estimated_cost_usd": 0.0,
            "logs": [],
        }))

        tasks = tp.load_active_tasks("00010")
        assert len(tasks) == 1
        assert tasks[0].status == TaskPhase.PENDING

    def test_returns_empty_if_no_tasks_dir(self, tmp_path, monkeypatch):
        from onemancompany.core import task_persistence as tp

        monkeypatch.setattr(tp, "EMPLOYEES_DIR", tmp_path)

        tasks = tp.load_active_tasks("00010")
        assert tasks == []

    def test_skips_archive_dir(self, tmp_path, monkeypatch):
        from onemancompany.core import task_persistence as tp

        monkeypatch.setattr(tp, "EMPLOYEES_DIR", tmp_path)

        tasks_dir = tmp_path / "00010" / "tasks"
        archive_dir = tasks_dir / "archive"
        archive_dir.mkdir(parents=True)
        (archive_dir / "old111.yaml").write_text(yaml.dump({
            "id": "old111", "description": "Old", "status": "finished",
        }))
        (tasks_dir / "new222.yaml").write_text(yaml.dump({
            "id": "new222", "description": "New", "status": "pending",
            "task_type": "simple", "parent_id": "", "project_id": "",
            "project_dir": "", "result": "", "created_at": "2026-01-01",
            "completed_at": "", "model_used": "", "input_tokens": 0,
            "output_tokens": 0, "total_tokens": 0, "estimated_cost_usd": 0.0,
            "logs": [],
        }))

        tasks = tp.load_active_tasks("00010")
        assert len(tasks) == 1
        assert tasks[0].id == "new222"

    def test_handles_corrupt_yaml(self, tmp_path, monkeypatch):
        from onemancompany.core import task_persistence as tp

        monkeypatch.setattr(tp, "EMPLOYEES_DIR", tmp_path)

        tasks_dir = tmp_path / "00010" / "tasks"
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "bad.yaml").write_text(": bad: yaml: {{}")
        (tasks_dir / "good.yaml").write_text(yaml.dump({
            "id": "good", "description": "OK", "status": "pending",
            "task_type": "simple", "parent_id": "", "project_id": "",
            "project_dir": "", "result": "", "created_at": "2026-01-01",
            "completed_at": "", "model_used": "", "input_tokens": 0,
            "output_tokens": 0, "total_tokens": 0, "estimated_cost_usd": 0.0,
            "logs": [],
        }))

        tasks = tp.load_active_tasks("00010")
        assert len(tasks) == 1
        assert tasks[0].id == "good"


class TestLoadAllActiveTasks:
    def test_scans_all_employee_dirs(self, tmp_path, monkeypatch):
        from onemancompany.core import task_persistence as tp

        monkeypatch.setattr(tp, "EMPLOYEES_DIR", tmp_path)

        for eid, tid in [("00010", "aaa"), ("00020", "bbb")]:
            d = tmp_path / eid / "tasks"
            d.mkdir(parents=True)
            (d / f"{tid}.yaml").write_text(yaml.dump({
                "id": tid, "description": f"Task for {eid}", "status": "pending",
                "task_type": "simple", "parent_id": "", "project_id": "",
                "project_dir": "", "result": "", "created_at": "2026-01-01",
                "completed_at": "", "model_used": "", "input_tokens": 0,
                "output_tokens": 0, "total_tokens": 0, "estimated_cost_usd": 0.0,
                "logs": [],
            }))

        result = tp.load_all_active_tasks()
        assert "00010" in result
        assert "00020" in result
        assert len(result["00010"]) == 1
        assert len(result["00020"]) == 1

    def test_skips_dirs_without_tasks(self, tmp_path, monkeypatch):
        from onemancompany.core import task_persistence as tp

        monkeypatch.setattr(tp, "EMPLOYEES_DIR", tmp_path)

        (tmp_path / "00010").mkdir()  # no tasks/ subdir
        result = tp.load_all_active_tasks()
        assert result == {}
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_persistence.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'onemancompany.core.task_persistence'`

**Step 3: Write the implementation**

```python
"""Task persistence — per-employee YAML-based write-through task storage.

Each employee's active tasks live in employees/{id}/tasks/{task_id}.yaml.
Terminal tasks are moved to employees/{id}/tasks/archive/{task_id}.yaml.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from loguru import logger

from onemancompany.core.config import EMPLOYEES_DIR
from onemancompany.core.task_lifecycle import TaskPhase
from onemancompany.core.vessel import AgentTask


def _task_to_dict(task: AgentTask) -> dict:
    """Serialize AgentTask to a plain dict for YAML storage."""
    return {
        "id": task.id,
        "description": task.description,
        "status": task.status.value if isinstance(task.status, TaskPhase) else str(task.status),
        "task_type": task.task_type,
        "parent_id": task.parent_id,
        "project_id": task.project_id,
        "project_dir": task.project_dir,
        "result": task.result,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
        "model_used": task.model_used,
        "input_tokens": task.input_tokens,
        "output_tokens": task.output_tokens,
        "total_tokens": task.total_tokens,
        "estimated_cost_usd": task.estimated_cost_usd,
        "logs": task.logs[-50:],
    }


def _dict_to_task(data: dict) -> AgentTask:
    """Deserialize a dict into an AgentTask."""
    status_raw = data.get("status", "pending")
    try:
        status = TaskPhase(status_raw)
    except ValueError:
        status = TaskPhase.PENDING

    task = AgentTask(
        id=data["id"],
        description=data.get("description", ""),
        status=status,
        task_type=data.get("task_type", "simple"),
        parent_id=data.get("parent_id", ""),
        project_id=data.get("project_id", ""),
        project_dir=data.get("project_dir", ""),
        result=data.get("result", ""),
        created_at=data.get("created_at", ""),
        completed_at=data.get("completed_at", ""),
        model_used=data.get("model_used", ""),
        input_tokens=data.get("input_tokens", 0),
        output_tokens=data.get("output_tokens", 0),
        total_tokens=data.get("total_tokens", 0),
        estimated_cost_usd=data.get("estimated_cost_usd", 0.0),
    )
    task.logs = data.get("logs", [])
    return task


def _tasks_dir(employee_id: str) -> Path:
    return EMPLOYEES_DIR / employee_id / "tasks"


def persist_task(employee_id: str, task: AgentTask) -> None:
    """Write task state to disk (write-through)."""
    d = _tasks_dir(employee_id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{task.id}.yaml"
    try:
        path.write_text(yaml.dump(_task_to_dict(task), allow_unicode=True), encoding="utf-8")
    except Exception as e:
        logger.error("Failed to persist task {} for {}: {}", task.id, employee_id, e)


def archive_task(employee_id: str, task: AgentTask) -> None:
    """Move task file from active to archive directory."""
    d = _tasks_dir(employee_id)
    src = d / f"{task.id}.yaml"
    archive_dir = d / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    dst = archive_dir / f"{task.id}.yaml"
    try:
        if src.exists():
            # Write final state to archive
            dst.write_text(yaml.dump(_task_to_dict(task), allow_unicode=True), encoding="utf-8")
            src.unlink()
        else:
            # File doesn't exist in active — just write to archive
            dst.write_text(yaml.dump(_task_to_dict(task), allow_unicode=True), encoding="utf-8")
    except Exception as e:
        logger.error("Failed to archive task {} for {}: {}", task.id, employee_id, e)


def load_active_tasks(employee_id: str) -> list[AgentTask]:
    """Load all active (non-archived) tasks for an employee.

    PROCESSING tasks are reset to PENDING (can't resume mid-execution).
    """
    d = _tasks_dir(employee_id)
    if not d.exists():
        return []

    tasks = []
    for f in d.glob("*.yaml"):
        if f.parent.name == "archive":
            continue
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "id" not in data:
                logger.warning("Skipping malformed task file: {}", f)
                continue
            task = _dict_to_task(data)
            # Reset interrupted tasks
            if task.status == TaskPhase.PROCESSING:
                task.status = TaskPhase.PENDING
            tasks.append(task)
        except Exception as e:
            logger.warning("Failed to load task file {}: {}", f, e)

    return tasks


def load_all_active_tasks() -> dict[str, list[AgentTask]]:
    """Scan all employee directories and load their active tasks.

    Returns: {employee_id: [AgentTask, ...]}
    """
    result: dict[str, list[AgentTask]] = {}
    if not EMPLOYEES_DIR.exists():
        return result

    for emp_dir in EMPLOYEES_DIR.iterdir():
        if not emp_dir.is_dir():
            continue
        tasks = load_active_tasks(emp_dir.name)
        if tasks:
            result[emp_dir.name] = tasks

    return result
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_persistence.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/onemancompany/core/task_persistence.py tests/unit/core/test_task_persistence.py
git commit -m "feat: add task_persistence module for per-employee YAML task storage"
```

---

### Task 2: Hook write-through into `EmployeeManager` status change points

**Files:**
- Modify: `src/onemancompany/core/vessel.py`
- Modify: `tests/unit/core/test_vessel.py`

**Context:** Every point where `task.status` changes in `EmployeeManager` must call `persist_task()`. Terminal states must also call `archive_task()`. The key mutation points are:

| Location (vessel.py) | Status Change | Action |
|---|---|---|
| `push_task()` (line 526) | new task created (PENDING) | `persist_task()` |
| `_execute_task()` (line 755) | → PROCESSING | `persist_task()` |
| `cancel_by_project()` (via `AgentTaskBoard.cancel_by_project`, line 168) | → CANCELLED | `persist_task()` + `archive_task()` |
| `_execute_task()` (line 896) | → CANCELLED (asyncio) | `persist_task()` + `archive_task()` |
| `_execute_task()` (line 903) | → FAILED (timeout) | `persist_task()` + `archive_task()` |
| `_execute_task()` (line 917) | → FAILED (exception) | `persist_task()` + `archive_task()` |
| `_execute_task()` (line 936) | → COMPLETE | `persist_task()` |
| `_post_task_cleanup` / task tree (after COMPLETE→FINISHED) | → FINISHED | `archive_task()` |

**Step 1: Write the failing test**

Add to `tests/unit/core/test_vessel.py`:

```python
class TestTaskPersistenceIntegration:
    """Verify EmployeeManager calls persist_task/archive_task at status changes."""

    def _make_manager(self):
        em = EmployeeManager()
        mock_launcher = MagicMock(spec=Launcher)
        em.register("00010", mock_launcher)
        return em

    @patch("onemancompany.core.vessel.persist_task")
    def test_push_task_persists(self, mock_persist):
        em = self._make_manager()
        task = em.push_task("00010", "test task")
        mock_persist.assert_called_once_with("00010", task)

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.archive_task")
    @patch("onemancompany.core.vessel.persist_task")
    async def test_execute_task_persists_processing(self, mock_persist, mock_archive):
        em = self._make_manager()
        mock_launcher = em.executors["00010"]
        mock_launcher.execute = AsyncMock(return_value=LaunchResult(output="done"))

        task = AgentTask(id="t1", description="test")
        em.boards["00010"].tasks.append(task)

        # Patch company_state to avoid side effects
        with patch("onemancompany.core.vessel.company_state") as mock_cs:
            mock_cs.employees = {}
            mock_cs.active_tasks = []
            await em._execute_task("00010", task)

        # persist_task should be called for PROCESSING and COMPLETE
        calls = [c[0] for c in mock_persist.call_args_list]
        statuses = [c[1].status for c in calls]
        assert TaskPhase.PROCESSING in statuses
        assert TaskPhase.COMPLETE in statuses
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/core/test_vessel.py::TestTaskPersistenceIntegration -v`
Expected: FAIL — `ImportError` (persist_task not imported in vessel.py)

**Step 3: Modify `vessel.py` to add persistence calls**

At the top of `vessel.py`, add import:
```python
from onemancompany.core.task_persistence import persist_task, archive_task
```

Then add calls at each status change point:

In `push_task()` (after `task = board.push(...)`):
```python
persist_task(employee_id, task)
```

In `_execute_task()` after `task.status = TaskPhase.PROCESSING` (line 755):
```python
persist_task(employee_id, task)
```

In `_execute_task()` CANCELLED handler (after `task.status = TaskPhase.CANCELLED`, line 896):
```python
persist_task(employee_id, task)
```

In `_execute_task()` FAILED handlers (lines 903, 917):
```python
persist_task(employee_id, task)
```

In `_execute_task()` COMPLETE (after `task.status = TaskPhase.COMPLETE`, line 936):
```python
persist_task(employee_id, task)
```

After all post-task processing (end of `_execute_task`, after hooks + task tree updates), add terminal archival:
```python
# Archive terminal tasks to tasks/archive/
from onemancompany.core.task_lifecycle import TERMINAL_STATES
if task.status in TERMINAL_STATES:
    archive_task(employee_id, task)
```

In `abort_project()` (after `board.cancel_by_project` loop, line 706-712), add persistence for each cancelled task:
```python
for t in cancelled:
    persist_task(emp_id, t)
    archive_task(emp_id, t)
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_vessel.py::TestTaskPersistenceIntegration -v`
Expected: PASS

**Step 5: Run full vessel tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_vessel.py -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/onemancompany/core/vessel.py tests/unit/core/test_vessel.py
git commit -m "feat: hook write-through persistence into EmployeeManager status changes"
```

---

### Task 3: Replace `save_task_queue`/`restore_task_queue` with `load_all_active_tasks`

**Files:**
- Modify: `src/onemancompany/core/vessel.py` — remove `save_task_queue`, `restore_task_queue`, `TASK_QUEUE_PATH`
- Modify: `src/onemancompany/main.py` — replace restore call at startup
- Modify: `tests/unit/core/test_vessel.py` — remove old tests, add new restore tests

**Step 1: Write the failing test for new restore logic**

Add to `tests/unit/core/test_vessel.py`:

```python
class TestRestoreFromTaskFiles:
    """Verify EmployeeManager restores tasks from per-employee task files."""

    @patch("onemancompany.core.vessel.load_all_active_tasks")
    def test_restore_pushes_to_boards(self, mock_load):
        em = EmployeeManager()
        mock_launcher = MagicMock(spec=Launcher)
        em.register("00010", mock_launcher)

        task = AgentTask(id="restored1", description="Restored task")
        mock_load.return_value = {"00010": [task]}

        count = em.restore_persisted_tasks()
        assert count == 1
        assert len(em.boards["00010"].tasks) == 1
        assert em.boards["00010"].tasks[0].id == "restored1"

    @patch("onemancompany.core.vessel.load_all_active_tasks")
    def test_skips_unregistered_employees(self, mock_load):
        em = EmployeeManager()
        # Don't register "00099"

        task = AgentTask(id="orphan", description="Orphan")
        mock_load.return_value = {"00099": [task]}

        count = em.restore_persisted_tasks()
        assert count == 0
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/core/test_vessel.py::TestRestoreFromTaskFiles -v`
Expected: FAIL — `AttributeError: 'EmployeeManager' object has no attribute 'restore_persisted_tasks'`

**Step 3: Implement**

In `vessel.py`:

1. Add import at top:
```python
from onemancompany.core.task_persistence import persist_task, archive_task, load_all_active_tasks
```

2. Remove `TASK_QUEUE_PATH` class variable from `EmployeeManager`

3. Remove `save_task_queue()` and `restore_task_queue()` methods

4. Add new `restore_persisted_tasks()` method:
```python
def restore_persisted_tasks(self) -> int:
    """Restore tasks from per-employee task files on disk.

    Returns the number of tasks restored.
    """
    all_tasks = load_all_active_tasks()
    restored = 0
    for emp_id, tasks in all_tasks.items():
        if emp_id not in self.executors:
            logger.warning("Skipping restored tasks for unregistered employee {}", emp_id)
            continue
        board = self.boards.get(emp_id)
        if not board:
            board = AgentTaskBoard()
            self.boards[emp_id] = board
        for task in tasks:
            board.tasks.append(task)
            restored += 1
    if restored:
        logger.info("Restored {} task(s) from disk", restored)
    return restored
```

5. Update `_trigger_graceful_restart()` — remove `self.save_task_queue()` call (tasks are already persisted via write-through):
```python
async def _trigger_graceful_restart(self) -> None:
    """Execute a graceful restart: save state, then os.execv."""
    import os
    import sys
    from onemancompany.main import _save_ephemeral_state, _pending_code_changes

    _save_ephemeral_state()
    _pending_code_changes.clear()
    # ... rest unchanged
```

6. Update `main.py` lifespan — replace `restore_task_queue` with `restore_persisted_tasks`:

Find (around line 444-449):
```python
    # Restore task queue from a previous graceful restart
    from onemancompany.core.vessel import employee_manager as _em
    restored_count = _em.restore_task_queue()
    if restored_count:
        print(f"[startup] Restored {restored_count} pending task(s) from previous session")
        _em.drain_pending()
```

Replace with:
```python
    # Restore persisted tasks from per-employee task files
    from onemancompany.core.vessel import employee_manager as _em
    restored_count = _em.restore_persisted_tasks()
    if restored_count:
        print(f"[startup] Restored {restored_count} task(s) from disk — auto-resuming")
        _em.drain_pending()
```

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_vessel.py -v`
Expected: ALL PASS (old save/restore tests will need removal — they reference removed methods)

**Step 5: Remove old tests**

Search `test_vessel.py` for any tests referencing `save_task_queue` or `restore_task_queue` and remove them. Also search for `TASK_QUEUE_PATH` references.

**Step 6: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -v`
Expected: ALL PASS

**Step 7: Commit**

```bash
git add src/onemancompany/core/vessel.py src/onemancompany/main.py tests/unit/core/test_vessel.py
git commit -m "feat: replace save/restore_task_queue with per-employee task persistence"
```

---

### Task 4: Clean up — remove `.task_queue.json` references and add to `.gitignore`

**Files:**
- Modify: `.gitignore` — add `tasks/archive/` pattern
- Check and clean: any remaining references to `.task_queue.json`

**Step 1: Search for remaining references**

Run: `grep -r "task_queue" src/ tests/ --include="*.py"`
Expected: No results (if any remain, remove them)

Run: `grep -r "TASK_QUEUE_PATH" src/ tests/ --include="*.py"`
Expected: No results

**Step 2: Add gitignore patterns**

Add to `.gitignore`:
```
# Task persistence archives (runtime data)
company/human_resource/employees/*/tasks/
```

**Step 3: Delete stale `.task_queue.json` if it exists**

Run: `rm -f company/.task_queue.json`

**Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add .gitignore
git commit -m "chore: clean up old task_queue references, gitignore task persistence"
```

---

### Task 5: Integration test — full lifecycle (push → persist → restart → restore → resume)

**Files:**
- Modify: `tests/unit/core/test_task_persistence.py` — add integration test

**Step 1: Write the integration test**

```python
class TestFullLifecycle:
    """Integration: push task → persist → simulate restart → restore → verify."""

    def test_push_persist_restore(self, tmp_path, monkeypatch):
        """Simulate: push task, persist, clear memory, load from disk."""
        from onemancompany.core import task_persistence as tp

        monkeypatch.setattr(tp, "EMPLOYEES_DIR", tmp_path)
        (tmp_path / "00010" / "tasks").mkdir(parents=True)

        # 1. Create and persist a task
        task = AgentTask(id="lifecycle1", description="Build feature X")
        task.status = TaskPhase.PENDING
        tp.persist_task("00010", task)

        # 2. Simulate status change: PROCESSING
        task.status = TaskPhase.PROCESSING
        tp.persist_task("00010", task)

        # Verify file has PROCESSING
        import yaml
        data = yaml.safe_load((tmp_path / "00010" / "tasks" / "lifecycle1.yaml").read_text())
        assert data["status"] == "processing"

        # 3. Simulate crash: load from disk
        loaded = tp.load_active_tasks("00010")
        assert len(loaded) == 1
        # PROCESSING should be reset to PENDING
        assert loaded[0].status == TaskPhase.PENDING
        assert loaded[0].description == "Build feature X"

    def test_complete_and_archive(self, tmp_path, monkeypatch):
        """Simulate: task completes → archived → not in active load."""
        from onemancompany.core import task_persistence as tp

        monkeypatch.setattr(tp, "EMPLOYEES_DIR", tmp_path)

        task = AgentTask(id="lifecycle2", description="Send report")
        task.status = TaskPhase.PENDING
        tp.persist_task("00010", task)

        # Complete the task
        task.status = TaskPhase.COMPLETE
        tp.persist_task("00010", task)

        # Finish and archive
        task.status = TaskPhase.FINISHED
        tp.persist_task("00010", task)
        tp.archive_task("00010", task)

        # Active tasks should be empty
        active = tp.load_active_tasks("00010")
        assert len(active) == 0

        # Archive should have the task
        archive = tmp_path / "00010" / "tasks" / "archive" / "lifecycle2.yaml"
        assert archive.exists()
```

**Step 2: Run test**

Run: `.venv/bin/python -m pytest tests/unit/core/test_task_persistence.py::TestFullLifecycle -v`
Expected: ALL PASS

**Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -v`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add tests/unit/core/test_task_persistence.py
git commit -m "test: add full lifecycle integration tests for task persistence"
```
