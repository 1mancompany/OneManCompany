"""Unit tests for core/task_persistence.py — per-employee YAML task storage."""

from __future__ import annotations

import yaml
import pytest
from pathlib import Path

from onemancompany.core.task_lifecycle import TaskPhase
from onemancompany.core.vessel import AgentTask
from onemancompany.core import task_persistence as tp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(
    task_id: str = "abc123",
    description: str = "do something",
    status: TaskPhase = TaskPhase.PENDING,
    **kwargs,
) -> AgentTask:
    return AgentTask(id=task_id, description=description, status=status, **kwargs)


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def _read_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


@pytest.fixture(autouse=True)
def _isolate_employees_dir(tmp_path, monkeypatch):
    """Redirect EMPLOYEES_DIR to a temp directory for every test."""
    monkeypatch.setattr(tp, "EMPLOYEES_DIR", tmp_path)
    yield


# ---------------------------------------------------------------------------
# _task_to_dict / _dict_to_task
# ---------------------------------------------------------------------------

class TestTaskToDict:
    def test_roundtrip_basic(self):
        task = _make_task(task_type="project", result="done")
        d = tp._task_to_dict(task)
        assert d["id"] == "abc123"
        assert d["description"] == "do something"
        assert d["status"] == "pending"
        assert d["task_type"] == "project"
        assert d["result"] == "done"

    def test_roundtrip_preserves_all_fields(self):
        task = _make_task(
            parent_id="parent1",
            project_id="proj1",
            project_dir="/tmp/proj",
            logs=[{"ts": "now", "msg": "hi"}],
            model_used="gpt-4",
            input_tokens=100,
            output_tokens=200,
            total_tokens=300,
            estimated_cost_usd=0.05,
        )
        d = tp._task_to_dict(task)
        restored = tp._dict_to_task(d)
        assert restored.id == task.id
        assert restored.description == task.description
        assert restored.parent_id == "parent1"
        assert restored.project_id == "proj1"
        assert restored.project_dir == "/tmp/proj"
        assert restored.logs == [{"ts": "now", "msg": "hi"}]
        assert restored.model_used == "gpt-4"
        assert restored.input_tokens == 100
        assert restored.output_tokens == 200
        assert restored.total_tokens == 300
        assert restored.estimated_cost_usd == 0.05

    def test_status_serialized_as_string(self):
        task = _make_task(status=TaskPhase.PROCESSING)
        d = tp._task_to_dict(task)
        assert d["status"] == "processing"
        assert isinstance(d["status"], str)


class TestDictToTask:
    def test_minimal_dict(self):
        d = {"id": "t1", "description": "hello"}
        task = tp._dict_to_task(d)
        assert task.id == "t1"
        assert task.description == "hello"
        assert task.status == TaskPhase.PENDING

    def test_status_restored_as_enum(self):
        d = {"id": "t1", "description": "x", "status": "completed"}
        task = tp._dict_to_task(d)
        assert task.status == TaskPhase.COMPLETED

    def test_unknown_status_defaults_to_pending(self):
        d = {"id": "t1", "description": "x", "status": "bogus"}
        task = tp._dict_to_task(d)
        assert task.status == TaskPhase.PENDING


# ---------------------------------------------------------------------------
# _tasks_dir
# ---------------------------------------------------------------------------

class TestTasksDir:
    def test_returns_correct_path(self, tmp_path):
        expected = tmp_path / "00010" / "tasks"
        assert tp._tasks_dir("00010") == expected


# ---------------------------------------------------------------------------
# persist_task
# ---------------------------------------------------------------------------

class TestPersistTask:
    def test_creates_yaml_file(self, tmp_path):
        task = _make_task()
        tp.persist_task("00010", task)
        path = tmp_path / "00010" / "tasks" / "abc123.yaml"
        assert path.exists()
        data = _read_yaml(path)
        assert data["id"] == "abc123"
        assert data["description"] == "do something"

    def test_overwrites_existing(self, tmp_path):
        task = _make_task(result="v1")
        tp.persist_task("00010", task)
        task.result = "v2"
        tp.persist_task("00010", task)
        path = tmp_path / "00010" / "tasks" / "abc123.yaml"
        data = _read_yaml(path)
        assert data["result"] == "v2"

    def test_creates_directories(self, tmp_path):
        task = _make_task()
        tp.persist_task("00099", task)
        assert (tmp_path / "00099" / "tasks").is_dir()


# ---------------------------------------------------------------------------
# archive_task
# ---------------------------------------------------------------------------

class TestArchiveTask:
    def test_moves_to_archive(self, tmp_path):
        task = _make_task()
        tp.persist_task("00010", task)
        tp.archive_task("00010", task)
        active_path = tmp_path / "00010" / "tasks" / "abc123.yaml"
        archive_path = tmp_path / "00010" / "tasks" / "archive" / "abc123.yaml"
        assert not active_path.exists()
        assert archive_path.exists()
        data = _read_yaml(archive_path)
        assert data["id"] == "abc123"

    def test_noop_if_no_file(self, tmp_path):
        task = _make_task(task_id="nonexistent")
        # Should not raise
        tp.archive_task("00010", task)


# ---------------------------------------------------------------------------
# load_active_tasks
# ---------------------------------------------------------------------------

class TestLoadActiveTasks:
    def test_loads_all_yaml(self, tmp_path):
        for i in range(3):
            task = _make_task(task_id=f"task_{i}", description=f"task {i}")
            tp.persist_task("00010", task)
        tasks = tp.load_active_tasks("00010")
        assert len(tasks) == 3
        ids = {t.id for t in tasks}
        assert ids == {"task_0", "task_1", "task_2"}

    def test_resets_processing_to_pending(self, tmp_path):
        task = _make_task(status=TaskPhase.PROCESSING)
        tp.persist_task("00010", task)
        tasks = tp.load_active_tasks("00010")
        assert len(tasks) == 1
        assert tasks[0].status == TaskPhase.PENDING

    def test_empty_if_no_dir(self, tmp_path):
        tasks = tp.load_active_tasks("00099")
        assert tasks == []

    def test_skips_archive_dir(self, tmp_path):
        task = _make_task()
        tp.persist_task("00010", task)
        tp.archive_task("00010", task)
        tasks = tp.load_active_tasks("00010")
        assert len(tasks) == 0

    def test_handles_corrupt_yaml(self, tmp_path):
        # Write a valid task
        valid_task = _make_task(task_id="good")
        tp.persist_task("00010", valid_task)
        # Write corrupt YAML
        corrupt_path = tmp_path / "00010" / "tasks" / "bad.yaml"
        corrupt_path.write_text(": : : invalid yaml {{{\n", encoding="utf-8")
        tasks = tp.load_active_tasks("00010")
        # Should still load the valid one, skip the corrupt one
        assert len(tasks) == 1
        assert tasks[0].id == "good"

    def test_handles_yaml_missing_required_fields(self, tmp_path):
        """YAML that parses but lacks required fields should be skipped."""
        tasks_dir = tmp_path / "00010" / "tasks"
        tasks_dir.mkdir(parents=True)
        _write_yaml(tasks_dir / "incomplete.yaml", {"status": "pending"})
        valid_task = _make_task(task_id="good")
        tp.persist_task("00010", valid_task)
        tasks = tp.load_active_tasks("00010")
        assert len(tasks) == 1
        assert tasks[0].id == "good"


# ---------------------------------------------------------------------------
# load_all_active_tasks
# ---------------------------------------------------------------------------

class TestLoadAllActiveTasks:
    def test_scans_all_employee_dirs(self, tmp_path):
        for eid in ["00010", "00011"]:
            task = _make_task(task_id=f"task_{eid}")
            tp.persist_task(eid, task)
        result = tp.load_all_active_tasks()
        assert "00010" in result
        assert "00011" in result
        assert len(result["00010"]) == 1
        assert len(result["00011"]) == 1

    def test_skips_dirs_without_tasks(self, tmp_path):
        # Employee dir exists but no tasks/ subdirectory
        (tmp_path / "00010").mkdir()
        (tmp_path / "00010" / "profile.yaml").touch()
        result = tp.load_all_active_tasks()
        assert "00010" not in result

    def test_empty_when_no_employees(self, tmp_path):
        result = tp.load_all_active_tasks()
        assert result == {}


# ---------------------------------------------------------------------------
# Full lifecycle integration tests
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    """Integration: push task → persist → simulate restart → restore → verify."""

    def test_push_persist_restore(self, tmp_path):
        """Simulate: push task, persist, clear memory, load from disk."""
        # 1. Create and persist a task
        task = AgentTask(id="lifecycle1", description="Build feature X")
        task.status = TaskPhase.PENDING
        tp.persist_task("00010", task)

        # 2. Simulate status change: PROCESSING
        task.status = TaskPhase.PROCESSING
        tp.persist_task("00010", task)

        # Verify file has PROCESSING
        data = yaml.safe_load((tmp_path / "00010" / "tasks" / "lifecycle1.yaml").read_text())
        assert data["status"] == "processing"

        # 3. Simulate crash: load from disk
        loaded = tp.load_active_tasks("00010")
        assert len(loaded) == 1
        # PROCESSING should be reset to PENDING
        assert loaded[0].status == TaskPhase.PENDING
        assert loaded[0].description == "Build feature X"

    def test_complete_and_archive(self, tmp_path):
        """Simulate: task completes → archived → not in active load."""
        task = AgentTask(id="lifecycle2", description="Send report")
        task.status = TaskPhase.PENDING
        tp.persist_task("00010", task)

        # Complete the task
        task.status = TaskPhase.COMPLETED
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


# ---------------------------------------------------------------------------
# recover_schedule_from_trees
# ---------------------------------------------------------------------------

class _MockEM:
    """Minimal mock of EmployeeManager for schedule_node() tracking."""

    def __init__(self):
        self.scheduled: list[tuple[str, str, str]] = []

    def schedule_node(self, emp_id: str, node_id: str, tree_path: str) -> None:
        self.scheduled.append((emp_id, node_id, tree_path))


class TestRecoverScheduleFromTrees:
    def test_resets_processing_to_pending(self, tmp_path):
        """PROCESSING nodes should be reset to PENDING on recovery."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree("proj1")
        root = tree.create_root("emp1", "root task")
        root.status = "processing"
        proj_dir = tmp_path / "projects" / "proj1"
        tree_path = proj_dir / "task_tree.yaml"
        tree.save(tree_path)

        em = _MockEM()
        tp.recover_schedule_from_trees(em, tmp_path / "projects", tmp_path / "employees")

        # Verify node reset to pending
        loaded = TaskTree.load(tree_path)
        assert loaded.get_node(root.id).status == "pending"
        # And scheduled
        assert len(em.scheduled) == 1

    def test_schedules_pending_with_deps_met(self, tmp_path):
        """PENDING nodes with deps resolved should be scheduled."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree("proj1")
        root = tree.create_root("emp1", "root")
        child = tree.add_child(root.id, "emp2", "child", [])
        root.status = "accepted"  # parent is done
        # child is pending with no deps
        proj_dir = tmp_path / "projects" / "proj1"
        tree_path = proj_dir / "task_tree.yaml"
        tree.save(tree_path)

        em = _MockEM()
        tp.recover_schedule_from_trees(em, tmp_path / "projects", tmp_path / "employees")

        # child should be scheduled (it's pending with no deps)
        assert any(s[1] == child.id for s in em.scheduled)

    def test_skips_resolved_nodes(self, tmp_path):
        """Resolved nodes (accepted, finished, etc.) should not be scheduled."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree("proj1")
        root = tree.create_root("emp1", "root")
        root.status = "accepted"
        proj_dir = tmp_path / "projects" / "proj1"
        tree_path = proj_dir / "task_tree.yaml"
        tree.save(tree_path)

        em = _MockEM()
        tp.recover_schedule_from_trees(em, tmp_path / "projects", tmp_path / "employees")

        assert len(em.scheduled) == 0

    def test_holding_nodes_left_as_is(self, tmp_path):
        """HOLDING nodes should not be reset or scheduled."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree("proj1")
        root = tree.create_root("emp1", "root")
        root.status = "holding"
        proj_dir = tmp_path / "projects" / "proj1"
        tree_path = proj_dir / "task_tree.yaml"
        tree.save(tree_path)

        em = _MockEM()
        tp.recover_schedule_from_trees(em, tmp_path / "projects", tmp_path / "employees")

        loaded = TaskTree.load(tree_path)
        assert loaded.get_node(root.id).status == "holding"
        assert len(em.scheduled) == 0

    def test_pending_with_unresolved_deps_not_scheduled(self, tmp_path):
        """PENDING nodes whose deps are not yet resolved should NOT be scheduled."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree("proj1")
        root = tree.create_root("emp1", "root")
        child_a = tree.add_child(root.id, "emp2", "child A", [])
        child_b = tree.add_child(root.id, "emp3", "child B", [], depends_on=[child_a.id])
        # child_a is pending, so child_b's dep is unresolved
        proj_dir = tmp_path / "projects" / "proj1"
        tree_path = proj_dir / "task_tree.yaml"
        tree.save(tree_path)

        em = _MockEM()
        tp.recover_schedule_from_trees(em, tmp_path / "projects", tmp_path / "employees")

        scheduled_ids = {s[1] for s in em.scheduled}
        assert child_a.id in scheduled_ids  # pending, no deps → scheduled
        assert child_b.id not in scheduled_ids  # pending, dep unresolved → NOT scheduled

    def test_recovers_system_task_trees(self, tmp_path):
        """System task trees should also be recovered."""
        from onemancompany.core.system_tasks import SystemTaskTree

        sys_tree = SystemTaskTree("emp1")
        node = sys_tree.create_system_node("emp1", "check gmail")
        node.status = "processing"
        emp_dir = tmp_path / "employees" / "emp1"
        sys_path = emp_dir / "system_tasks.yaml"
        sys_tree.save(sys_path)

        em = _MockEM()
        tp.recover_schedule_from_trees(em, tmp_path / "projects", tmp_path / "employees")

        # Node should be reset and scheduled
        loaded = SystemTaskTree.load(sys_path, "emp1")
        recovered = loaded.get_all_nodes()
        assert len(recovered) == 1
        assert recovered[0].status == "pending"
        assert any(s[1] == node.id for s in em.scheduled)

    def test_skips_corrupt_tree_files(self, tmp_path):
        """Corrupt tree YAML files should be skipped without crashing."""
        proj_dir = tmp_path / "projects" / "badproj"
        tree_path = proj_dir / "task_tree.yaml"
        tree_path.parent.mkdir(parents=True, exist_ok=True)
        tree_path.write_text(": : : invalid yaml {{{\n", encoding="utf-8")

        em = _MockEM()
        # Should not raise
        tp.recover_schedule_from_trees(em, tmp_path / "projects", tmp_path / "employees")
        assert len(em.scheduled) == 0

    def test_empty_dirs(self, tmp_path):
        """No crash when projects/employees dirs don't exist."""
        em = _MockEM()
        tp.recover_schedule_from_trees(em, tmp_path / "projects", tmp_path / "employees")
        assert len(em.scheduled) == 0
