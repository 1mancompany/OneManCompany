"""Supplementary coverage tests for core/vessel.py.

Targets uncovered lines to push toward 100% coverage.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from onemancompany.core.vessel import (
    EmployeeManager,
    LangChainExecutor,
    ClaudeSessionExecutor,
    ScriptExecutor,
    Launcher,
    LaunchResult,
    ScheduleEntry,
    TaskContext,
    Vessel,
    _VesselRef,
    _current_vessel,
    _current_task_id,
    employee_manager,
    _load_project_tree,
    _save_project_tree,
    _build_dependency_context,
    _build_tree_context,
    _trigger_dep_resolution,
    _load_task_history,
    _save_task_history,
    _history_path,
    _parse_holding_metadata,
    _append_progress,
    _append_node_execution_log,
    _trunc,
    _result_preview,
    _collect_work_results,
    _list_deliverables,
    _summarize_project_for_ceo,
    _load_progress,
    _ensure_work_principles,
    _create_executor_for_hosting,
    build_role_identity,
    _load_archetype_templates,
    MANAGER_ROLES,
    LEVEL_LABELS,
    detect_unfulfilled_promises,
    scan_overdue_reviews,
    stop_cron,
    ExecutionError,
)
from onemancompany.core.task_lifecycle import TaskPhase, NodeType
from onemancompany.core.task_tree import TaskNode, TaskTree


# =====================================================================
# Module-level helpers
# =====================================================================


def _make_tree(tmp_path, employee_id="emp01", description="Do work",
               project_id="proj1", status="pending"):
    tree = TaskTree(project_id=project_id)
    root = tree.create_root(employee_id=employee_id, description=description)
    if status != "pending":
        root.status = status
    tree_path = tmp_path / "task_tree.yaml"
    tree.save(tree_path)
    return tree, tree_path, root


# =====================================================================
# _load_project_tree / _save_project_tree
# =====================================================================


class TestLoadSaveProjectTree:
    def test_load_returns_none_when_no_file(self, tmp_path):
        result = _load_project_tree(str(tmp_path))
        assert result is None

    def test_load_returns_tree_when_file_exists(self, tmp_path):
        tree = TaskTree(project_id="p1")
        tree.create_root(employee_id="e1", description="root")
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        with patch("onemancompany.core.task_tree.get_tree", return_value=tree):
            result = _load_project_tree(str(tmp_path))
            assert result is tree

    def test_save_creates_file_first_time(self, tmp_path):
        tree = TaskTree(project_id="p1")
        tree.create_root(employee_id="e1", description="root")

        with patch("onemancompany.core.task_tree.register_tree") as mock_reg, \
             patch("onemancompany.core.task_tree.save_tree_async") as mock_async:
            _save_project_tree(str(tmp_path), tree)
            mock_reg.assert_called_once()
            # File didn't exist, so tree.save() was called (sync), not save_tree_async
            mock_async.assert_not_called()

    def test_save_uses_async_when_file_exists(self, tmp_path):
        tree = TaskTree(project_id="p1")
        tree.create_root(employee_id="e1", description="root")
        tree_path = tmp_path / "task_tree.yaml"
        tree_path.write_text("existing", encoding="utf-8")

        with patch("onemancompany.core.task_tree.register_tree") as mock_reg, \
             patch("onemancompany.core.task_tree.save_tree_async") as mock_async:
            _save_project_tree(str(tmp_path), tree)
            mock_reg.assert_called_once()
            mock_async.assert_called_once()


# =====================================================================
# _build_dependency_context
# =====================================================================


class TestBuildDependencyContext:
    def test_empty_when_no_depends_on(self):
        node = MagicMock()
        node.depends_on = []
        result = _build_dependency_context(MagicMock(), node)
        assert result == ""

    def test_skips_unresolved_deps(self):
        tree = MagicMock()
        dep = MagicMock()
        dep.is_resolved = False
        tree.get_node.return_value = dep

        node = MagicMock()
        node.depends_on = ["dep1"]

        result = _build_dependency_context(tree, node)
        assert result == ""

    def test_includes_resolved_dep(self):
        tree = MagicMock()
        dep = MagicMock()
        dep.is_resolved = True
        dep.result = "dep result text"
        dep.project_dir = ""
        dep.status = TaskPhase.ACCEPTED
        dep.employee_id = "e1"
        dep.description = "dep task"
        tree.get_node.return_value = dep

        node = MagicMock()
        node.depends_on = ["dep1"]

        result = _build_dependency_context(tree, node, project_dir="/proj")
        assert "Dependency Results" in result
        assert "dep result text" in result

    def test_truncates_long_result(self):
        tree = MagicMock()
        dep = MagicMock()
        dep.is_resolved = True
        dep.result = "x" * 5000
        dep.project_dir = "/p"
        dep.status = TaskPhase.ACCEPTED
        dep.employee_id = "e1"
        dep.description = "dep"
        tree.get_node.return_value = dep

        node = MagicMock()
        node.depends_on = ["dep1"]

        result = _build_dependency_context(tree, node)
        # Should truncate to max_per_dep (2000 for <=3 deps)
        assert len(result) < 5500

    def test_dep_with_no_result(self):
        tree = MagicMock()
        dep = MagicMock()
        dep.is_resolved = True
        dep.result = None
        dep.project_dir = ""
        dep.status = "completed"
        dep.employee_id = "e1"
        dep.description = "dep"
        tree.get_node.return_value = dep

        node = MagicMock()
        node.depends_on = ["dep1"]

        result = _build_dependency_context(tree, node)
        assert "(no result)" in result

    def test_dep_not_found_returns_empty(self):
        tree = MagicMock()
        tree.get_node.return_value = None

        node = MagicMock()
        node.depends_on = ["dep1"]

        result = _build_dependency_context(tree, node)
        assert result == ""

    def test_many_deps_use_shorter_truncation(self):
        tree = MagicMock()
        deps = []
        for i in range(5):
            dep = MagicMock()
            dep.is_resolved = True
            dep.result = "r" * 2000
            dep.project_dir = ""
            dep.status = TaskPhase.ACCEPTED
            dep.employee_id = f"e{i}"
            dep.description = f"dep{i}"
            deps.append(dep)
        tree.get_node.side_effect = deps

        node = MagicMock()
        node.depends_on = [f"dep{i}" for i in range(5)]

        result = _build_dependency_context(tree, node)
        assert "Dependency Results" in result


# =====================================================================
# _build_tree_context
# =====================================================================


class TestBuildTreeContext:
    def test_basic_context_no_ancestors(self):
        tree = MagicMock()
        node = MagicMock()
        node.parent_id = None
        node.id = "n1"
        node.description = "do stuff"
        node.directives = []
        node.result = ""
        tree.get_active_children.return_value = []

        result = _build_tree_context(tree, node, "/proj")
        assert "Current Task" in result

    def test_context_with_ancestors(self):
        tree = MagicMock()
        parent = MagicMock()
        parent.parent_id = None
        parent.id = "parent1"
        parent.employee_id = "e1"
        parent.status = "processing"
        parent.description = "parent desc"
        parent.result = "parent result"

        node = MagicMock()
        node.parent_id = "parent1"
        node.id = "n1"
        node.description = "child desc"
        node.directives = []
        node.result = ""
        tree.get_node.return_value = parent
        tree.get_active_children.return_value = []

        result = _build_tree_context(tree, node, "/proj")
        assert "Task Chain (ancestors)" in result
        assert "parent desc" in result

    def test_context_with_grandparent(self):
        tree = MagicMock()
        grandparent = MagicMock()
        grandparent.parent_id = None
        grandparent.id = "gp1"
        grandparent.employee_id = "e0"
        grandparent.status = "processing"
        grandparent.description = "gp desc"
        grandparent.description_preview = "gp prev"
        grandparent.result = ""

        parent = MagicMock()
        parent.parent_id = "gp1"
        parent.id = "parent1"
        parent.employee_id = "e1"
        parent.status = "processing"
        parent.description = "parent desc"
        parent.result = "parent result"

        node = MagicMock()
        node.parent_id = "parent1"
        node.id = "n1"
        node.description = "child desc"
        node.directives = []
        node.result = ""

        def get_node_fn(nid):
            if nid == "parent1":
                return parent
            if nid == "gp1":
                return grandparent
            return None

        tree.get_node.side_effect = get_node_fn
        tree.get_active_children.return_value = []

        result = _build_tree_context(tree, node, "/proj")
        # Grandparent at distance 2 shows only preview
        assert "Preview:" in result

    def test_context_with_directives(self):
        tree = MagicMock()
        node = MagicMock()
        node.parent_id = None
        node.id = "n1"
        node.description = "do stuff"
        node.directives = [{"from": "e0", "directive": "be careful"}]
        node.result = ""
        tree.get_active_children.return_value = []

        result = _build_tree_context(tree, node, "/proj")
        assert "Directives from upstream" in result
        assert "be careful" in result

    def test_context_with_children(self):
        tree = MagicMock()
        node = MagicMock()
        node.parent_id = None
        node.id = "n1"
        node.description = "do stuff"
        node.directives = []
        node.result = ""

        # CEO node that's done with result
        ceo_child = MagicMock()
        ceo_child.is_ceo_node = True
        ceo_child.is_done_executing = True
        ceo_child.id = "ceo1"
        ceo_child.result = "CEO replied"
        ceo_child.status = TaskPhase.FINISHED.value

        # CEO node not done
        ceo_pending = MagicMock()
        ceo_pending.is_ceo_node = True
        ceo_pending.is_done_executing = False

        # Accepted child
        accepted = MagicMock()
        accepted.is_ceo_node = False
        accepted.status = TaskPhase.ACCEPTED
        accepted.id = "acc1"
        accepted.employee_id = "e2"
        accepted.description_preview = "accepted task"

        # Completed child needing review
        completed = MagicMock()
        completed.is_ceo_node = False
        completed.status = TaskPhase.COMPLETED
        completed.is_done_executing = True
        completed.id = "comp1"
        completed.employee_id = "e3"
        completed.description = "completed task"
        completed.result = "the result"

        # Pending child
        pending = MagicMock()
        pending.is_ceo_node = False
        pending.status = TaskPhase.PENDING
        pending.is_done_executing = False
        pending.id = "pend1"
        pending.employee_id = "e4"
        pending.description_preview = "pending task"

        tree.get_active_children.return_value = [
            ceo_child, ceo_pending, accepted, completed, pending,
        ]

        result = _build_tree_context(tree, node, "/proj")
        assert "CEO REPLY" in result
        assert "ACCEPTED" in result
        assert "completed task" in result
        assert "PENDING" in result


# =====================================================================
# _trigger_dep_resolution
# =====================================================================


class TestTriggerDepResolution:
    def test_with_running_loop(self):
        loop = asyncio.new_event_loop()
        mock_node = MagicMock()
        mock_tree = MagicMock()

        async def _run():
            with patch.object(employee_manager, '_resolve_dependencies', new_callable=AsyncMock):
                _trigger_dep_resolution("/proj", mock_tree, mock_node)

        loop.run_until_complete(_run())
        loop.close()

    def test_without_running_loop_with_event_loop(self):
        mock_node = MagicMock()
        mock_tree = MagicMock()

        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False

        with patch.object(employee_manager, '_event_loop', mock_loop), \
             patch.object(employee_manager, '_resolve_dependencies', new_callable=AsyncMock):
            _trigger_dep_resolution("/proj", mock_tree, mock_node)
            mock_loop.call_soon_threadsafe.assert_called_once()

    def test_without_any_loop(self):
        mock_node = MagicMock()
        mock_tree = MagicMock()

        with patch.object(employee_manager, '_event_loop', None), \
             patch.object(employee_manager, '_resolve_dependencies', new_callable=AsyncMock):
            # Should not raise
            _trigger_dep_resolution("/proj", mock_tree, mock_node)


# =====================================================================
# _load_task_history / _save_task_history
# =====================================================================


class TestTaskHistory:
    def test_load_nonexistent(self, tmp_path):
        with patch("onemancompany.core.vessel.EMPLOYEES_DIR", tmp_path):
            entries, summary = _load_task_history("emp01")
            assert entries == []
            assert summary == ""

    def test_save_and_load(self, tmp_path):
        emp_dir = tmp_path / "emp01"
        emp_dir.mkdir()
        with patch("onemancompany.core.vessel.EMPLOYEES_DIR", tmp_path):
            entries = [{"task": "do thing", "result": "done", "completed_at": "2024-01-01"}]
            _save_task_history("emp01", entries, "summary text")

            loaded_entries, summary = _load_task_history("emp01")
            assert len(loaded_entries) == 1
            assert summary == "summary text"

    def test_load_corrupted_file(self, tmp_path):
        emp_dir = tmp_path / "emp01"
        emp_dir.mkdir()
        hist_path = emp_dir / "task_history.json"
        hist_path.write_text("not json", encoding="utf-8")

        with patch("onemancompany.core.vessel.EMPLOYEES_DIR", tmp_path):
            entries, summary = _load_task_history("emp01")
            assert entries == []


# =====================================================================
# _parse_holding_metadata
# =====================================================================


class TestParseHoldingMetadata:
    def test_none_input(self):
        assert _parse_holding_metadata(None) is None

    def test_non_holding_prefix(self):
        assert _parse_holding_metadata("just a result") is None

    def test_empty_payload(self):
        assert _parse_holding_metadata("__HOLDING:") == {}

    def test_with_metadata(self):
        result = _parse_holding_metadata("__HOLDING:thread_id=abc,interval=2m")
        assert result == {"thread_id": "abc", "interval": "2m"}

    def test_multiline_only_first_line(self):
        result = _parse_holding_metadata("__HOLDING:key=val\nmore stuff\nhere")
        assert result == {"key": "val"}

    def test_whitespace_payload(self):
        result = _parse_holding_metadata("__HOLDING:   ")
        assert result == {}


# =====================================================================
# _append_node_execution_log
# =====================================================================


class TestAppendNodeExecutionLog:
    def test_skips_empty_project_dir(self):
        # Should not raise
        _append_node_execution_log("", "n1", "start", "content")

    def test_writes_jsonl(self, tmp_path):
        _append_node_execution_log(str(tmp_path), "node1", "start", "hello")
        log_path = tmp_path / "nodes" / "node1" / "execution.log"
        assert log_path.exists()
        data = json.loads(log_path.read_text().strip())
        assert data["type"] == "start"
        assert data["content"] == "hello"

    def test_handles_dict_content(self, tmp_path):
        _append_node_execution_log(str(tmp_path), "node1", "tool_call",
                                   {"content": "the string part", "extra": "data"})
        log_path = tmp_path / "nodes" / "node1" / "execution.log"
        data = json.loads(log_path.read_text().strip())
        assert data["content"] == "the string part"

    def test_handles_write_error(self, tmp_path):
        with patch("builtins.open", side_effect=OSError("fail")):
            # Should not raise
            _append_node_execution_log(str(tmp_path), "node1", "start", "content")


# =====================================================================
# _trunc / _result_preview / _list_deliverables
# =====================================================================


class TestHelpers:
    def test_trunc_short(self):
        assert _trunc("hello") == "hello"

    def test_trunc_long(self):
        result = _trunc("x" * 5000, 100)
        assert result.endswith("...")
        assert len(result) == 103

    def test_trunc_none(self):
        assert _trunc(None) == ""

    def test_result_preview_empty(self):
        assert _result_preview("") == ""
        assert _result_preview("  ") == ""

    def test_result_preview_multiline(self):
        text = "line1\nline2\nline3\nline4\nline5"
        result = _result_preview(text, max_lines=3)
        assert "line1" in result
        assert "line3" in result
        assert "line4" not in result

    def test_list_deliverables_empty_dir(self, tmp_path):
        result = _list_deliverables(str(tmp_path / "nonexistent"))
        assert result == []

    def test_list_deliverables_skips_system_files(self, tmp_path):
        (tmp_path / "task_tree.yaml").write_text("x")
        (tmp_path / ".DS_Store").write_text("x")
        (tmp_path / "output.txt").write_text("x")
        (tmp_path / "nodes").mkdir()

        result = _list_deliverables(str(tmp_path))
        assert result == ["output.txt"]


# =====================================================================
# _collect_work_results
# =====================================================================


class TestCollectWorkResults:
    def test_collects_completed_nodes(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e2",
                               description="work", acceptance_criteria=[])
        child.status = TaskPhase.COMPLETED.value
        child.result = "done!"

        # Create node content dir
        node_dir = tmp_path / "nodes" / child.id
        node_dir.mkdir(parents=True)
        (node_dir / "description.md").write_text("work")
        (node_dir / "result.md").write_text("done!")

        results = _collect_work_results(tree, str(tmp_path))
        assert len(results) >= 1


# =====================================================================
# _summarize_project_for_ceo
# =====================================================================


class TestSummarizeProjectForCeo:
    @pytest.mark.asyncio
    async def test_empty_work_nodes(self):
        result = await _summarize_project_for_ceo("proj", [], [])
        assert result == ""

    @pytest.mark.asyncio
    async def test_llm_success(self):
        node = MagicMock()
        node.status = TaskPhase.COMPLETED.value
        node.title = "Build widget"
        node.description_preview = "Build widget"
        node.result = "Widget built"
        node.employee_id = "e1"

        mock_resp = MagicMock()
        mock_resp.content = "Summary: Widget was built successfully."

        with patch("onemancompany.core.vessel.make_llm") as mock_llm, \
             patch("onemancompany.agents.base.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_resp):
            result = await _summarize_project_for_ceo("proj", [node], ["output.txt"])
            assert "Summary" in result

    @pytest.mark.asyncio
    async def test_llm_failure_fallback(self):
        node = MagicMock()
        node.status = TaskPhase.COMPLETED.value
        node.title = "Build widget"
        node.description_preview = "Build widget"
        node.result = "Widget built"
        node.employee_id = "e1"

        with patch("onemancompany.core.vessel.make_llm", side_effect=Exception("fail")), \
             patch("onemancompany.agents.base.tracked_ainvoke", new_callable=AsyncMock, side_effect=Exception("fail")):
            result = await _summarize_project_for_ceo("proj", [node], ["output.txt"])
            assert "Work summary" in result
            assert "Build widget" in result

    @pytest.mark.asyncio
    async def test_fallback_with_failed_node(self):
        node = MagicMock()
        node.status = TaskPhase.FAILED.value
        node.title = ""
        node.description_preview = "failed task"
        node.result = "Error happened"
        node.employee_id = "e1"

        with patch("onemancompany.core.vessel.make_llm", side_effect=Exception("fail")), \
             patch("onemancompany.agents.base.tracked_ainvoke", new_callable=AsyncMock, side_effect=Exception("fail")):
            result = await _summarize_project_for_ceo("proj", [node], [])
            assert "failed task" in result

    @pytest.mark.asyncio
    async def test_fallback_no_preview(self):
        """Fallback branch where node has no result preview."""
        node = MagicMock()
        node.status = TaskPhase.COMPLETED.value
        node.title = "Task"
        node.description_preview = "Task"
        node.result = ""
        node.employee_id = "e1"

        with patch("onemancompany.core.vessel.make_llm", side_effect=Exception("fail")), \
             patch("onemancompany.agents.base.tracked_ainvoke", new_callable=AsyncMock, side_effect=Exception("fail")):
            result = await _summarize_project_for_ceo("proj", [node], [])
            assert "Task" in result


# =====================================================================
# _load_progress
# =====================================================================


class TestLoadProgress:
    def test_no_file(self, tmp_path):
        with patch("onemancompany.core.vessel.EMPLOYEES_DIR", tmp_path):
            result = _load_progress("emp01")
            assert result == ""

    def test_loads_recent_lines(self, tmp_path):
        emp_dir = tmp_path / "emp01"
        emp_dir.mkdir()
        prog_path = emp_dir / "progress.log"
        lines = [f"[2024-01-01] line{i}" for i in range(50)]
        prog_path.write_text("\n".join(lines), encoding="utf-8")

        with patch("onemancompany.core.vessel.EMPLOYEES_DIR", tmp_path):
            result = _load_progress("emp01", max_lines=5)
            assert "line49" in result
            assert "line0" not in result


# =====================================================================
# _load_archetype_templates / build_role_identity
# =====================================================================


class TestRoleIdentity:
    def test_load_archetype_templates_no_content(self):
        with patch("onemancompany.core.config.load_workflows", return_value={}):
            mgr, exe = _load_archetype_templates()
            assert "coordinator" in mgr
            assert "executor" in exe

    def test_load_archetype_templates_with_content(self):
        content = (
            "# Role Archetypes\n"
            "## Manager Archetype\n"
            "Plan and delegate.\n"
            "## Executor Archetype\n"
            "Build things.\n"
        )
        with patch("onemancompany.core.config.load_workflows",
                    return_value={"role_archetype_templates": content}):
            mgr, exe = _load_archetype_templates()
            assert "Plan and delegate." in mgr
            assert "Build things." in exe

    def test_build_role_identity_founding(self):
        with patch("onemancompany.core.config.FOUNDING_IDS", {"00001"}):
            result = build_role_identity("00001")
            assert result == ""

    def test_build_role_identity_with_role_guide(self, tmp_path):
        emp_dir = tmp_path / "emp01"
        emp_dir.mkdir()
        guide = emp_dir / "role_guide.md"
        guide.write_text("Custom role guide content")

        with patch("onemancompany.core.config.FOUNDING_IDS", set()), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", tmp_path), \
             patch("onemancompany.core.config.load_employee_profile_yaml",
                    return_value={"name": "Test", "role": "Engineer"}):
            result = build_role_identity("emp01")
            assert "Custom role guide content" in result

    def test_build_role_identity_manager(self, tmp_path):
        emp_dir = tmp_path / "emp01"
        emp_dir.mkdir()

        with patch("onemancompany.core.config.FOUNDING_IDS", set()), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", tmp_path), \
             patch("onemancompany.core.config.load_employee_profile_yaml",
                    return_value={"name": "Test", "nickname": "TT", "role": "PM",
                                  "department": "Eng", "level": 3}), \
             patch("onemancompany.core.vessel._load_archetype_templates",
                    return_value=("manager tmpl", "executor tmpl")):
            result = build_role_identity("emp01")
            assert "Test" in result
            assert "TT" in result
            assert "Senior" in result
            assert "PM" in result
            assert "manager tmpl" in result

    def test_build_role_identity_executor(self, tmp_path):
        emp_dir = tmp_path / "emp01"
        emp_dir.mkdir()

        with patch("onemancompany.core.config.FOUNDING_IDS", set()), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", tmp_path), \
             patch("onemancompany.core.config.load_employee_profile_yaml",
                    return_value={"name": "Dev", "role": "Engineer", "level": 2}), \
             patch("onemancompany.core.vessel._load_archetype_templates",
                    return_value=("manager tmpl", "executor tmpl")):
            result = build_role_identity("emp01")
            assert "executor tmpl" in result
            assert "Mid-level" in result

    def test_build_role_identity_unknown_level(self, tmp_path):
        emp_dir = tmp_path / "emp01"
        emp_dir.mkdir()

        with patch("onemancompany.core.config.FOUNDING_IDS", set()), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", tmp_path), \
             patch("onemancompany.core.config.load_employee_profile_yaml",
                    return_value={"name": "Dev", "role": "Engineer", "level": 99}), \
             patch("onemancompany.core.vessel._load_archetype_templates",
                    return_value=("m", "e")):
            result = build_role_identity("emp01")
            assert "Lv.99" in result


# =====================================================================
# stop_cron wrapper
# =====================================================================


class TestStopCron:
    def test_delegates_to_automation(self):
        with patch("onemancompany.core.automation.stop_cron", return_value={"status": "ok"}) as mock:
            result = stop_cron("emp01", "cron1")
            mock.assert_called_once_with("emp01", "cron1")


# =====================================================================
# EmployeeManager — scheduling methods
# =====================================================================


class TestCleanupOrphanedSchedule:
    def test_removes_missing_tree(self, tmp_path):
        mgr = EmployeeManager()
        mgr._schedule["emp01"] = [
            ScheduleEntry(node_id="n1", tree_path=str(tmp_path / "nonexistent.yaml"))
        ]
        removed = mgr.cleanup_orphaned_schedule()
        assert removed == 1
        assert mgr._schedule["emp01"] == []

    def test_removes_missing_node(self, tmp_path):
        tree = TaskTree(project_id="p1")
        tree.create_root(employee_id="e1", description="root")
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        mgr._schedule["emp01"] = [
            ScheduleEntry(node_id="nonexistent_node", tree_path=str(tree_path))
        ]
        removed = mgr.cleanup_orphaned_schedule()
        assert removed == 1

    def test_removes_terminal_node(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        root.status = TaskPhase.FINISHED.value
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        mgr._schedule["e1"] = [
            ScheduleEntry(node_id=root.id, tree_path=str(tree_path))
        ]
        removed = mgr.cleanup_orphaned_schedule()
        assert removed == 1

    def test_keeps_pending_node(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="task", acceptance_criteria=[])
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        mgr._schedule["e1"] = [
            ScheduleEntry(node_id=child.id, tree_path=str(tree_path))
        ]
        removed = mgr.cleanup_orphaned_schedule()
        assert removed == 0
        assert len(mgr._schedule["e1"]) == 1

    def test_corrupt_tree(self, tmp_path):
        tree_path = tmp_path / "task_tree.yaml"
        tree_path.write_text("invalid yaml: [[[", encoding="utf-8")

        mgr = EmployeeManager()
        mgr._schedule["e1"] = [
            ScheduleEntry(node_id="n1", tree_path=str(tree_path))
        ]
        removed = mgr.cleanup_orphaned_schedule()
        assert removed == 1


class TestGetTask:
    def test_get_task_found(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        mgr._schedule["e1"] = [
            ScheduleEntry(node_id=root.id, tree_path=str(tree_path))
        ]
        result = mgr.get_task(root.id)
        assert result is not None

    def test_get_task_not_found(self):
        mgr = EmployeeManager()
        mgr._schedule["e1"] = []
        result = mgr.get_task("nonexistent")
        assert result is None

    def test_get_task_missing_tree(self, tmp_path):
        mgr = EmployeeManager()
        mgr._schedule["e1"] = [
            ScheduleEntry(node_id="n1", tree_path=str(tmp_path / "gone.yaml"))
        ]
        result = mgr.get_task("n1")
        assert result is None


class TestGetNextScheduled:
    def test_skips_missing_tree(self, tmp_path):
        mgr = EmployeeManager()
        mgr._schedule["e1"] = [
            ScheduleEntry(node_id="n1", tree_path=str(tmp_path / "gone.yaml"))
        ]
        result = mgr.get_next_scheduled("e1")
        assert result is None


# =====================================================================
# EmployeeManager — register with history summary
# =====================================================================


class TestRegisterWithHistorySummary:
    def test_register_loads_history_with_summary(self, tmp_path):
        emp_dir = tmp_path / "emp01"
        emp_dir.mkdir()
        hist_path = emp_dir / "task_history.json"
        hist_path.write_text(json.dumps({
            "entries": [{"task": "t", "result": "r", "completed_at": "2024-01-01"}],
            "summary": "previous summary",
        }), encoding="utf-8")

        mgr = EmployeeManager()
        mock_launcher = MagicMock(spec=Launcher)

        with patch("onemancompany.core.vessel.EMPLOYEES_DIR", tmp_path), \
             patch.object(mgr, "_recover_orphaned_tasks"):
            vessel = mgr.register("emp01", mock_launcher)

        assert "emp01" in mgr.task_histories
        assert mgr._history_summaries.get("emp01") == "previous summary"


# =====================================================================
# EmployeeManager — is_idle
# =====================================================================


class TestIsIdle:
    def test_idle_when_empty(self):
        mgr = EmployeeManager()
        assert mgr.is_idle() is True

    def test_not_idle_with_running_task(self):
        mgr = EmployeeManager()
        mgr._running_tasks["e1"] = MagicMock()
        assert mgr.is_idle() is False

    def test_not_idle_with_system_task(self):
        mgr = EmployeeManager()
        mgr._system_tasks["sys1"] = MagicMock()
        assert mgr.is_idle() is False

    def test_idle_with_excluded(self):
        mgr = EmployeeManager()
        mgr._running_tasks["e1"] = MagicMock()
        assert mgr.is_idle(exclude="e1") is True

    def test_not_idle_with_excluded_different(self):
        mgr = EmployeeManager()
        mgr._running_tasks["e1"] = MagicMock()
        mgr._running_tasks["e2"] = MagicMock()
        assert mgr.is_idle(exclude="e1") is False


# =====================================================================
# EmployeeManager — _schedule_next edge cases
# =====================================================================


class TestScheduleNextEdgeCases:
    def test_schedule_next_already_running(self):
        mgr = EmployeeManager()
        mgr._running_tasks["e1"] = MagicMock()
        mgr._schedule_next("e1")  # should return early, no crash

    def test_schedule_next_no_pending_clears_deferred(self):
        mgr = EmployeeManager()
        mgr._deferred_schedule.add("e1")
        mgr.executors["e1"] = MagicMock()
        with patch.object(mgr, "_set_employee_status"), \
             patch.object(mgr, "_publish_dispatch_status"):
            mgr._schedule_next("e1")
        assert "e1" not in mgr._deferred_schedule

    def test_schedule_next_runtime_error_with_event_loop(self, tmp_path):
        tree, tree_path, root = _make_tree(tmp_path)
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="task", acceptance_criteria=[])
        tree.save(tree_path)

        mgr = EmployeeManager()
        mgr.executors["e1"] = MagicMock()
        mgr._schedule["e1"] = [ScheduleEntry(node_id=child.id, tree_path=str(tree_path))]

        mock_loop = MagicMock()
        mock_loop.is_closed.return_value = False
        mgr._event_loop = mock_loop

        # Force RuntimeError on asyncio.get_running_loop
        with patch("onemancompany.core.vessel.asyncio.get_running_loop",
                    side_effect=RuntimeError("no loop")), \
             patch.object(mgr, "_publish_dispatch_status"):
            mgr._schedule_next("e1")
        mock_loop.call_soon_threadsafe.assert_called_once()

    def test_schedule_next_runtime_error_no_event_loop(self, tmp_path):
        tree, tree_path, root = _make_tree(tmp_path)
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="task", acceptance_criteria=[])
        tree.save(tree_path)

        mgr = EmployeeManager()
        mgr.executors["e1"] = MagicMock()
        mgr._schedule["e1"] = [ScheduleEntry(node_id=child.id, tree_path=str(tree_path))]
        mgr._event_loop = None

        with patch("onemancompany.core.vessel.asyncio.get_running_loop",
                    side_effect=RuntimeError("no loop")), \
             patch.object(mgr, "_publish_dispatch_status"):
            mgr._schedule_next("e1")
        assert "e1" in mgr._deferred_schedule


class TestCreateRunTask:
    def test_skips_if_already_running(self):
        mgr = EmployeeManager()
        mgr._running_tasks["e1"] = MagicMock()
        entry = ScheduleEntry(node_id="n1", tree_path="/tmp/t.yaml")
        # Should return early
        loop = asyncio.new_event_loop()

        async def _run():
            mgr._create_run_task("e1", entry)

        loop.run_until_complete(_run())
        loop.close()


# =====================================================================
# EmployeeManager — drain_pending
# =====================================================================


class TestDrainPending:
    def test_drain_pending_with_deferred(self):
        mgr = EmployeeManager()
        mgr._deferred_schedule.add("e1")
        mgr.executors["e1"] = MagicMock()

        loop = asyncio.new_event_loop()

        async def _run():
            with patch.object(mgr, "_schedule_next") as mock_sched, \
                 patch.object(mgr, "get_next_scheduled", return_value=None):
                mgr.drain_pending()
                mock_sched.assert_called()

        loop.run_until_complete(_run())
        loop.close()

    def test_drain_pending_reschedules_orphaned(self):
        mgr = EmployeeManager()
        mgr.executors["e1"] = MagicMock()
        entry = ScheduleEntry(node_id="n1", tree_path="/tmp/t.yaml")
        mgr._schedule["e1"] = [entry]

        loop = asyncio.new_event_loop()

        async def _run():
            with patch.object(mgr, "_schedule_next") as mock_sched, \
                 patch.object(mgr, "get_next_scheduled", return_value=entry):
                mgr.drain_pending()

        loop.run_until_complete(_run())
        loop.close()

    def test_drain_pending_no_loop(self):
        mgr = EmployeeManager()
        # Called without event loop — should not crash
        mgr.drain_pending()


# =====================================================================
# EmployeeManager — restore_persisted_tasks
# =====================================================================


class TestRestorePersistedTasks:
    def test_restore_calls_recover(self):
        mgr = EmployeeManager()

        with patch("onemancompany.core.task_persistence.recover_schedule_from_trees") as mock_recover, \
             patch.object(mgr, "_restart_holding_pollers", return_value=0):
            result = mgr.restore_persisted_tasks()
            mock_recover.assert_called_once()
            assert result == 0

    def test_restore_returns_count(self):
        mgr = EmployeeManager()
        mgr._schedule["e1"] = [ScheduleEntry(node_id="n1", tree_path="/tmp/t.yaml")]

        with patch("onemancompany.core.task_persistence.recover_schedule_from_trees"), \
             patch.object(mgr, "_restart_holding_pollers", return_value=0):
            result = mgr.restore_persisted_tasks()
            assert result == 1


# =====================================================================
# EmployeeManager — _restart_holding_pollers
# =====================================================================


class TestRestartHoldingPollers:
    def test_restarts_holding_nodes(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="task", acceptance_criteria=[])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.HOLDING)
        child.result = "__HOLDING:thread_id=abc"
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        # Create content files
        node_dir = tmp_path / "nodes" / child.id
        node_dir.mkdir(parents=True)
        (node_dir / "result.md").write_text("__HOLDING:thread_id=abc")

        mgr = EmployeeManager()
        mgr._schedule["e1"] = [ScheduleEntry(node_id=child.id, tree_path=str(tree_path))]

        with patch.object(mgr, "_setup_holding_watchdog_by_id"):
            count = mgr._restart_holding_pollers()
            assert count == 1

    def test_skips_non_holding(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="task", acceptance_criteria=[])
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        mgr._schedule["e1"] = [ScheduleEntry(node_id=child.id, tree_path=str(tree_path))]

        count = mgr._restart_holding_pollers()
        assert count == 0


# =====================================================================
# EmployeeManager — abort_employee
# =====================================================================


class TestAbortEmployee:
    def test_abort_cancels_all(self, tmp_path):
        tree, tree_path, root = _make_tree(tmp_path, employee_id="emp01")

        mgr = EmployeeManager()
        entry = ScheduleEntry(node_id=root.id, tree_path=str(tree_path))
        mgr._schedule["emp01"] = [entry]

        mock_task = MagicMock()
        mock_task.done.return_value = False
        mgr._running_tasks["emp01"] = mock_task
        mgr._deferred_schedule.add("emp01")

        with patch("onemancompany.core.task_tree.save_tree_async"), \
             patch("onemancompany.core.automation.stop_all_crons_for_employee"), \
             patch("onemancompany.core.vessel.company_state") as mock_state, \
             patch.object(mgr, "_publish_node_update"):
            mock_state.employees = {}
            count = mgr.abort_employee("emp01")

        mock_task.cancel.assert_called_once()
        assert mgr._schedule["emp01"] == []
        assert "emp01" not in mgr._deferred_schedule

    def test_abort_resets_status(self, tmp_path):
        mgr = EmployeeManager()
        mgr._schedule["emp01"] = []

        with patch("onemancompany.core.automation.stop_all_crons_for_employee"), \
             patch("onemancompany.core.vessel.company_state") as mock_state:
            emp_mock = MagicMock()
            mock_state.employees = {"emp01": emp_mock}
            mgr.abort_employee("emp01")
            assert emp_mock.status == "idle"
            assert emp_mock.current_task is None


# =====================================================================
# EmployeeManager — abort_all
# =====================================================================


class TestAbortAll:
    @pytest.mark.asyncio
    async def test_abort_all_stops_everything(self):
        mgr = EmployeeManager()
        mgr._schedule["e1"] = []
        mgr._schedule["e2"] = []

        with patch.object(mgr, "abort_employee", return_value=0) as mock_abort, \
             patch("onemancompany.core.automation.stop_all_automations", new_callable=AsyncMock), \
             patch("onemancompany.core.claude_session.stop_all_daemons", new_callable=AsyncMock):
            total = await mgr.abort_all()
            assert mock_abort.call_count >= 2

    @pytest.mark.asyncio
    async def test_abort_all_includes_running_tasks(self):
        mgr = EmployeeManager()
        mgr._running_tasks["e3"] = MagicMock()

        with patch.object(mgr, "abort_employee", return_value=1) as mock_abort, \
             patch("onemancompany.core.automation.stop_all_automations", new_callable=AsyncMock), \
             patch("onemancompany.core.claude_session.stop_all_daemons", new_callable=AsyncMock):
            total = await mgr.abort_all()
            # e3 should be aborted
            mock_abort.assert_any_call("e3")


# =====================================================================
# EmployeeManager — register_hooks
# =====================================================================


class TestRegisterHooks:
    def test_registers_pre_and_post_hooks(self):
        mgr = EmployeeManager()
        hooks = {
            "pre_task": MagicMock(return_value="extra context"),
            "post_task": MagicMock(),
        }
        with patch("onemancompany.core.skill_hooks.register_callback_hook") as mock_reg, \
             patch("onemancompany.core.skill_hooks.clear_hooks"), \
             patch("onemancompany.core.skill_hooks.load_hooks_from_skills"):
            mgr.register_hooks("emp01", hooks)

        assert "emp01" in mgr._hooks
        assert mock_reg.call_count == 2

    def test_pre_hook_wrapper_handles_exception(self):
        mgr = EmployeeManager()
        hooks = {"pre_task": MagicMock(side_effect=Exception("boom"))}
        with patch("onemancompany.core.skill_hooks.register_callback_hook") as mock_reg, \
             patch("onemancompany.core.skill_hooks.clear_hooks"), \
             patch("onemancompany.core.skill_hooks.load_hooks_from_skills"):
            mgr.register_hooks("emp01", hooks)

        # Get the registered wrapper and call it
        call_args = mock_reg.call_args_list[0]
        wrapper = call_args[0][2]  # 3rd positional arg is the callback

        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(wrapper({"task_description": "test"}))
        loop.close()
        assert result == {}


# =====================================================================
# EmployeeManager — push_task
# =====================================================================


class TestPushTask:
    def test_push_task_with_node_id(self):
        mgr = EmployeeManager()
        mgr.executors["e1"] = MagicMock()
        with patch.object(mgr, "schedule_node"), \
             patch.object(mgr, "_schedule_next"):
            result = mgr.push_task("e1", "desc", node_id="n1", tree_path="/tmp/t.yaml")
            assert result == "n1"

    def test_push_task_without_node_id(self):
        mgr = EmployeeManager()
        with patch.object(mgr, "_schedule_next"):
            result = mgr.push_task("e1", "desc")
            assert result == ""


# =====================================================================
# EmployeeManager — Vessel delegation
# =====================================================================


class TestVesselDelegation:
    def test_vessel_push_task(self):
        mgr = EmployeeManager()
        vessel = Vessel(mgr, "e1")
        with patch.object(mgr, "push_task", return_value="n1") as mock_push:
            result = vessel.push_task("desc", project_id="p1", node_id="n1", tree_path="/t.yaml")
            assert result == "n1"
            mock_push.assert_called_once()

    def test_vessel_get_history_context(self):
        mgr = EmployeeManager()
        vessel = Vessel(mgr, "e1")
        with patch.object(mgr, "get_history_context", return_value="ctx"):
            result = vessel.get_history_context()
            assert result == "ctx"

    def test_vessel_get_task(self):
        mgr = EmployeeManager()
        vessel = Vessel(mgr, "e1")
        mock_node = MagicMock()
        with patch.object(mgr, "get_task", return_value=mock_node):
            result = vessel.get_task("t1")
            assert result is mock_node


# =====================================================================
# EmployeeManager — _recover_orphaned_tasks
# =====================================================================


class TestRecoverOrphanedTasks:
    def test_recovers_pending_tasks(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="task", acceptance_criteria=[])
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        mgr.executors["e1"] = MagicMock()

        with patch("onemancompany.core.store.load_task_index",
                    return_value=[{"node_id": child.id, "tree_path": str(tree_path)}]), \
             patch.object(mgr, "_schedule_next"):
            mgr._recover_orphaned_tasks("e1")

        assert len(mgr._schedule.get("e1", [])) == 1

    def test_skips_non_pending(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="task", acceptance_criteria=[])
        child.status = TaskPhase.COMPLETED.value
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        mgr.executors["e1"] = MagicMock()

        with patch("onemancompany.core.store.load_task_index",
                    return_value=[{"node_id": child.id, "tree_path": str(tree_path)}]):
            mgr._recover_orphaned_tasks("e1")

        assert len(mgr._schedule.get("e1", [])) == 0

    def test_skips_already_scheduled(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="task", acceptance_criteria=[])
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        mgr.executors["e1"] = MagicMock()
        mgr._schedule["e1"] = [ScheduleEntry(node_id=child.id, tree_path=str(tree_path))]

        with patch("onemancompany.core.store.load_task_index",
                    return_value=[{"node_id": child.id, "tree_path": str(tree_path)}]):
            mgr._recover_orphaned_tasks("e1")

        # Should still be just 1
        assert len(mgr._schedule["e1"]) == 1


# =====================================================================
# EmployeeManager — get_history_context
# =====================================================================


class TestGetHistoryContext:
    def test_empty_history(self):
        mgr = EmployeeManager()
        result = mgr.get_history_context("e1")
        assert result == ""

    def test_with_history_and_summary(self):
        mgr = EmployeeManager()
        mgr.task_histories["e1"] = [
            {"task": "built widget", "result": "success", "completed_at": "2024-01-01T12:00:00"}
        ]
        mgr._history_summaries["e1"] = "did some work"

        result = mgr.get_history_context("e1")
        assert "Recent Work History" in result
        assert "Earlier work summary" in result
        assert "built widget" in result


# =====================================================================
# EmployeeManager — _maybe_compress_history
# =====================================================================


class TestMaybeCompressHistory:
    @pytest.mark.asyncio
    async def test_no_compression_when_small(self):
        mgr = EmployeeManager()
        mgr.task_histories["e1"] = [
            {"task": "t", "result": "r", "completed_at": "2024-01-01"}
        ]
        await mgr._maybe_compress_history("e1")
        # Should remain unchanged
        assert len(mgr.task_histories["e1"]) == 1

    @pytest.mark.asyncio
    async def test_compresses_when_large(self):
        mgr = EmployeeManager()
        # Create many large entries
        entries = [
            {"task": f"task {'x' * 200}", "result": f"result {'y' * 200}",
             "completed_at": f"2024-01-{i+1:02d}"}
            for i in range(20)
        ]
        mgr.task_histories["e1"] = entries.copy()

        mock_resp = MagicMock()
        mock_resp.content = "compressed summary"

        with patch("onemancompany.core.vessel.make_llm"), \
             patch("onemancompany.agents.base.tracked_ainvoke",
                    new_callable=AsyncMock, return_value=mock_resp), \
             patch("onemancompany.core.vessel._save_task_history"):
            await mgr._maybe_compress_history("e1")

        assert len(mgr.task_histories["e1"]) < 20
        assert mgr._history_summaries["e1"] == "compressed summary"

    @pytest.mark.asyncio
    async def test_compresses_fallback_on_llm_failure(self):
        mgr = EmployeeManager()
        entries = [
            {"task": f"task {'x' * 200}", "result": f"result {'y' * 200}",
             "completed_at": f"2024-01-{i+1:02d}"}
            for i in range(20)
        ]
        mgr.task_histories["e1"] = entries.copy()

        with patch("onemancompany.core.vessel.make_llm", side_effect=Exception("fail")), \
             patch("onemancompany.agents.base.tracked_ainvoke",
                    new_callable=AsyncMock, side_effect=Exception("fail")), \
             patch("onemancompany.core.vessel._save_task_history"):
            await mgr._maybe_compress_history("e1")

        assert len(mgr.task_histories["e1"]) < 20
        # Fallback sets summary
        assert mgr._history_summaries["e1"] != ""


# =====================================================================
# EmployeeManager — abort_project edge cases
# =====================================================================


class TestAbortProjectEdgeCases:
    def test_abort_stops_crons(self, tmp_path):
        tree, tree_path, root = _make_tree(tmp_path, employee_id="emp01", project_id="proj-A")
        mgr = EmployeeManager()
        mgr._schedule["emp01"] = [ScheduleEntry(node_id=root.id, tree_path=str(tree_path))]

        with patch("onemancompany.core.automation.stop_cron") as mock_stop:
            count = mgr.abort_project("proj-A")
            assert count >= 1
            # Should attempt to stop crons
            assert mock_stop.call_count >= 1


# =====================================================================
# EmployeeManager — schedule_node edge cases
# =====================================================================


class TestScheduleNodeEdgeCases:
    def test_schedule_node_no_executor(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.store.append_task_index_entry"):
            mgr.schedule_node("unregistered", "n1", "/tmp/t.yaml")
        # Should log warning but not add to _schedule (since no executor)
        assert "unregistered" not in mgr._schedule

    def test_schedule_node_dedup(self):
        mgr = EmployeeManager()
        mgr.executors["e1"] = MagicMock()
        with patch("onemancompany.core.store.append_task_index_entry"):
            mgr.schedule_node("e1", "n1", "/tmp/t.yaml")
            mgr.schedule_node("e1", "n1", "/tmp/t.yaml")
        assert len(mgr._schedule["e1"]) == 1


# =====================================================================
# EmployeeManager — find_holding_task
# =====================================================================


class TestFindHoldingTask:
    def test_finds_matching_holding_task(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="held task", acceptance_criteria=[])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.HOLDING)
        child.result = "__HOLDING:thread_id=abc"
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        mgr._schedule["e1"] = [ScheduleEntry(node_id=child.id, tree_path=str(tree_path))]

        with patch("onemancompany.core.task_tree.get_tree", return_value=tree):
            result = mgr.find_holding_task("e1", "thread_id=abc")
            assert result == child.id

    def test_returns_none_when_no_match(self):
        mgr = EmployeeManager()
        mgr._schedule["e1"] = []
        result = mgr.find_holding_task("e1", "xyz")
        assert result is None


# =====================================================================
# EmployeeManager — _check_holding_timeout
# =====================================================================


class TestCheckHoldingTimeout:
    def test_not_holding(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        assert mgr._check_holding_timeout(str(tree_path), root.id) is False

    def test_no_hold_started_at(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        root.set_status(TaskPhase.PROCESSING)
        root.set_status(TaskPhase.HOLDING)
        root.hold_started_at = None
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        assert mgr._check_holding_timeout(str(tree_path), root.id) is False

    def test_node_not_found(self, tmp_path):
        tree = TaskTree(project_id="p1")
        tree.create_root(employee_id="e1", description="root")
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        assert mgr._check_holding_timeout(str(tree_path), "nonexistent") is False

    def test_skips_no_watchdog_hold(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        root.set_status(TaskPhase.PROCESSING)
        root.set_status(TaskPhase.HOLDING)
        root.hold_started_at = (datetime.now() - timedelta(hours=2)).isoformat()
        root.hold_reason = "no_watchdog"
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        assert mgr._check_holding_timeout(str(tree_path), root.id) is False

    def test_not_yet_timed_out(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        root.set_status(TaskPhase.PROCESSING)
        root.set_status(TaskPhase.HOLDING)
        root.hold_started_at = datetime.now().isoformat()
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        assert mgr._check_holding_timeout(str(tree_path), root.id) is False


# =====================================================================
# EmployeeManager — _build_company_context_block
# =====================================================================


class TestBuildCompanyContextBlock:
    def test_builds_context_for_non_langchain(self):
        mgr = EmployeeManager()
        mgr.executors["e1"] = MagicMock(spec=ClaudeSessionExecutor)

        with patch("onemancompany.core.vessel.build_role_identity", return_value="Role info"), \
             patch("onemancompany.core.vessel._store") as mock_store, \
             patch("onemancompany.core.config.load_workflows", return_value={}), \
             patch("onemancompany.core.vessel.EMPLOYEES_DIR", Path("/fake")):
            mock_store.load_culture.return_value = [{"content": "Be excellent"}]
            mock_store.load_employee_guidance.return_value = ["Do X"]
            mock_store.load_employee_work_principles.return_value = "Principle 1"

            result = mgr._build_company_context_block("e1")
            assert "Company Context" in result
            assert "Role info" in result
            assert "Be excellent" in result
            assert "Do X" in result

    def test_skips_identity_for_langchain(self):
        mgr = EmployeeManager()
        mock_runner = MagicMock()
        mgr.executors["e1"] = LangChainExecutor(mock_runner)

        with patch("onemancompany.core.vessel._store") as mock_store, \
             patch("onemancompany.core.config.load_workflows", return_value={}), \
             patch("onemancompany.core.vessel.EMPLOYEES_DIR", Path("/fake")):
            mock_store.load_culture.return_value = []
            mock_store.load_employee_guidance.return_value = []
            mock_store.load_employee_work_principles.return_value = ""

            result = mgr._build_company_context_block("e1")
            # Should still have work principles section
            assert "Work Principles" in result


# =====================================================================
# EmployeeManager — _get_project_workflow_context
# =====================================================================


class TestGetProjectWorkflowContext:
    def test_manager_role_returns_guide(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.vessel._store") as mock_store:
            mock_store.load_employee.return_value = {"role": "COO"}
            result = mgr._get_project_workflow_context("e1")
            assert "Manager Execution Guide" in result

    def test_executor_with_workflow(self):
        mgr = EmployeeManager()
        mock_wf = MagicMock()
        mock_step = MagicMock()
        mock_step.title = "Execution Phase"
        mock_step.instructions = ["Build and run the tests", "Validate output"]
        mock_wf.steps = [mock_step]

        with patch("onemancompany.core.vessel._store") as mock_store, \
             patch("onemancompany.core.config.load_workflows",
                    return_value={"project_intake_workflow": "doc"}), \
             patch("onemancompany.core.workflow_engine.parse_workflow", return_value=mock_wf):
            mock_store.load_employee.return_value = {"role": "Engineer"}
            result = mgr._get_project_workflow_context("e1")
            assert "Verification" in result or "Self-Verification" in result

    def test_executor_fallback_sop(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.vessel._store") as mock_store, \
             patch("onemancompany.core.config.load_workflows",
                    return_value={"self_verification_sop": "Check your work!"}):
            mock_store.load_employee.return_value = {"role": "Engineer"}
            result = mgr._get_project_workflow_context("e1")
            assert "Check your work!" in result

    def test_executor_minimal_fallback(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.vessel._store") as mock_store, \
             patch("onemancompany.core.config.load_workflows", return_value={}):
            mock_store.load_employee.return_value = {"role": "Engineer"}
            result = mgr._get_project_workflow_context("e1")
            assert "Self-Verification" in result


# =====================================================================
# _ensure_work_principles
# =====================================================================


class TestEnsureWorkPrinciples:
    def test_creates_file_when_missing(self, tmp_path):
        emp_dir = tmp_path / "emp01"
        emp_dir.mkdir()

        cfg = MagicMock()
        cfg.name = "Test Employee"
        cfg.nickname = "TE"
        cfg.role = "Engineer"
        cfg.department = "Engineering"

        _ensure_work_principles("emp01", emp_dir, cfg)
        wp_path = emp_dir / "work_principles.md"
        assert wp_path.exists()
        content = wp_path.read_text()
        assert "Test Employee" in content
        assert "TE" in content

    def test_skips_when_exists(self, tmp_path):
        emp_dir = tmp_path / "emp01"
        emp_dir.mkdir()
        wp_path = emp_dir / "work_principles.md"
        wp_path.write_text("existing content")

        _ensure_work_principles("emp01", emp_dir)
        assert wp_path.read_text() == "existing content"

    def test_skips_when_no_dir(self, tmp_path):
        emp_dir = tmp_path / "nonexistent"
        _ensure_work_principles("emp01", emp_dir)  # Should not raise

    def test_no_cfg(self, tmp_path):
        emp_dir = tmp_path / "emp01"
        emp_dir.mkdir()

        _ensure_work_principles("emp01", emp_dir)
        wp_path = emp_dir / "work_principles.md"
        assert wp_path.exists()
        content = wp_path.read_text()
        assert "emp01" in content


# =====================================================================
# _create_executor_for_hosting
# =====================================================================


class TestCreateExecutorForHosting:
    def test_self_hosting(self, tmp_path):
        executor = _create_executor_for_hosting("self", "e1", None, tmp_path)
        assert isinstance(executor, ClaudeSessionExecutor)

    def test_openclaw_hosting(self, tmp_path):
        from onemancompany.core.subprocess_executor import SubprocessExecutor
        executor = _create_executor_for_hosting("openclaw", "e1", None, tmp_path)
        assert isinstance(executor, SubprocessExecutor)

    def test_company_hosting_with_cls(self, tmp_path):
        mock_cls = MagicMock()
        mock_runner = MagicMock()
        mock_cls.return_value = mock_runner

        executor = _create_executor_for_hosting("company", "e1", mock_cls, tmp_path)
        assert isinstance(executor, LangChainExecutor)

    def test_company_hosting_no_cls(self, tmp_path):
        with patch("onemancompany.agents.base.EmployeeAgent") as mock_agent_cls:
            mock_agent_cls.return_value = MagicMock()
            executor = _create_executor_for_hosting("company", "e1", None, tmp_path)
            assert isinstance(executor, LangChainExecutor)


# =====================================================================
# switch_hosting
# =====================================================================


class TestSwitchHosting:
    @pytest.mark.asyncio
    async def test_switch_hosting(self):
        from onemancompany.core.vessel import switch_hosting

        mgr = EmployeeManager()

        with patch("onemancompany.core.vessel.employee_manager", mgr), \
             patch("onemancompany.core.config.EMPLOYEES_DIR", Path("/fake")), \
             patch("onemancompany.core.config.employee_configs", {}), \
             patch("onemancompany.core.vessel._create_executor_for_hosting") as mock_create, \
             patch.object(mgr, "unregister"), \
             patch.object(mgr, "register"):
            mock_executor = ClaudeSessionExecutor("e1")
            mock_create.return_value = mock_executor

            result = await switch_hosting("e1", "self")
            assert result == "ClaudeSessionExecutor"

    @pytest.mark.asyncio
    async def test_switch_hosting_running_raises(self):
        from onemancompany.core.vessel import switch_hosting

        mgr = EmployeeManager()
        mgr._running_tasks["e1"] = MagicMock()

        with patch("onemancompany.core.vessel.employee_manager", mgr):
            with pytest.raises(RuntimeError, match="currently running"):
                await switch_hosting("e1", "self")

    @pytest.mark.asyncio
    async def test_switch_hosting_invalid_raises(self):
        from onemancompany.core.vessel import switch_hosting

        mgr = EmployeeManager()

        with patch("onemancompany.core.vessel.employee_manager", mgr):
            with pytest.raises(ValueError, match="Invalid hosting"):
                await switch_hosting("e1", "invalid_mode")


# =====================================================================
# scan_overdue_reviews
# =====================================================================


class TestScanOverdueReviews:
    def test_no_projects_dir(self, tmp_path):
        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path / "nonexistent"):
            result = scan_overdue_reviews()
            assert result == []

    def test_finds_overdue(self, tmp_path):
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="e0", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="overdue task", acceptance_criteria=[])
        child.status = TaskPhase.COMPLETED.value
        child.completed_at = (datetime.now() - timedelta(seconds=600)).isoformat()

        tree_path = proj_dir / "task_tree.yaml"
        tree.save(tree_path)

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path):
            result = scan_overdue_reviews(threshold_seconds=300)
            assert len(result) == 1
            assert result[0]["employee_id"] == "e1"
            assert result[0]["reviewer_id"] == "e0"

    def test_skips_recent(self, tmp_path):
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="e0", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="recent task", acceptance_criteria=[])
        child.status = TaskPhase.COMPLETED.value
        child.completed_at = datetime.now().isoformat()

        tree_path = proj_dir / "task_tree.yaml"
        tree.save(tree_path)

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path):
            result = scan_overdue_reviews(threshold_seconds=300)
            assert len(result) == 0


# =====================================================================
# start_all_loops / stop_all_loops
# =====================================================================


class TestStartStopLoops:
    @pytest.mark.asyncio
    async def test_start_all_loops(self):
        from onemancompany.core.vessel import start_all_loops
        with patch.object(employee_manager, "drain_pending") as mock_drain:
            await start_all_loops()
            mock_drain.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_all_loops(self):
        from onemancompany.core.vessel import stop_all_loops

        mgr_backup = employee_manager._running_tasks.copy()
        consumer_backup = employee_manager._completion_consumer

        try:
            employee_manager._running_tasks.clear()
            employee_manager._completion_consumer = None
            employee_manager._completion_queue = None

            with patch("onemancompany.core.conversation.get_conversation_service") as mock_conv:
                mock_conv.return_value = MagicMock()
                await stop_all_loops()
        finally:
            employee_manager._running_tasks = mgr_backup
            employee_manager._completion_consumer = consumer_backup


# =====================================================================
# register_and_start_agent
# =====================================================================


class TestRegisterAndStartAgent:
    @pytest.mark.asyncio
    async def test_registers(self):
        from onemancompany.core.vessel import register_and_start_agent
        mock_runner = MagicMock()
        with patch("onemancompany.core.vessel.register_agent", return_value=MagicMock()) as mock_reg:
            result = await register_and_start_agent("e1", mock_runner)
            mock_reg.assert_called_once_with("e1", mock_runner)


# =====================================================================
# _build_project_identity
# =====================================================================


class TestBuildProjectIdentity:
    def test_iteration_project(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_iteration", return_value=True), \
             patch("onemancompany.core.project_archive._find_project_for_iteration", return_value="my-proj"), \
             patch("onemancompany.core.project_archive.load_named_project", return_value={"name": "My Project"}), \
             patch("onemancompany.core.project_archive._split_qualified_iter", return_value=("my-proj", "iter_001")):
            result = mgr._build_project_identity("my-proj/iter_001")
            assert "My Project" in result
            assert "iter_001" in result

    def test_regular_project(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_iteration", return_value=False), \
             patch("onemancompany.core.project_archive.load_named_project", return_value={"name": "Direct Proj"}):
            result = mgr._build_project_identity("proj-1")
            assert "Direct Proj" in result

    def test_project_not_found(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_iteration", return_value=False), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            result = mgr._build_project_identity("nonexistent")
            assert result == ""


# =====================================================================
# EmployeeManager — _append_history_from_node
# =====================================================================


class TestAppendHistoryFromNode:
    def test_appends_and_saves(self):
        mgr = EmployeeManager()
        mgr.task_histories["e1"] = []

        node = MagicMock()
        node.description = "task desc"
        node.result = "task result"
        node.completed_at = "2024-01-01T12:00:00"

        with patch("onemancompany.core.vessel._save_task_history"), \
             patch("onemancompany.core.vessel.spawn_background"):
            mgr._append_history_from_node("e1", node)

        assert len(mgr.task_histories["e1"]) == 1

    def test_handles_no_event_loop(self):
        mgr = EmployeeManager()
        mgr.task_histories["e1"] = []

        node = MagicMock()
        node.description = "task"
        node.result = "done"
        node.completed_at = "2024-01-01"

        with patch("onemancompany.core.vessel._save_task_history"), \
             patch("onemancompany.core.vessel.spawn_background",
                    side_effect=RuntimeError("no loop")):
            mgr._append_history_from_node("e1", node)  # Should not raise


# =====================================================================
# LangChainExecutor
# =====================================================================


class TestLangChainExecutor:
    @pytest.mark.asyncio
    async def test_execute(self):
        mock_runner = MagicMock()
        mock_runner.run_streamed = AsyncMock(return_value="output text")
        mock_runner._last_usage = {
            "model": "gpt-4", "input_tokens": 100,
            "output_tokens": 50, "total_tokens": 150, "cost_usd": 0.01,
        }
        executor = LangChainExecutor(mock_runner)
        ctx = TaskContext(project_id="p1", employee_id="e1", task_id="t1")
        result = await executor.execute("do stuff", ctx)
        assert result.output == "output text"
        assert result.model_used == "gpt-4"
        assert result.total_tokens == 150


# =====================================================================
# ClaudeSessionExecutor
# =====================================================================


class TestClaudeSessionExecutor:
    @pytest.mark.asyncio
    async def test_execute_success(self):
        executor = ClaudeSessionExecutor("e1")
        ctx = TaskContext(project_id="p1", employee_id="e1", task_id="t1")

        with patch("onemancompany.core.claude_session.run_claude_session",
                    new_callable=AsyncMock,
                    return_value={"output": "result text", "model": "claude",
                                  "input_tokens": 10, "output_tokens": 20}):
            result = await executor.execute("do stuff", ctx)
            assert result.output == "result text"
            assert result.error is None

    @pytest.mark.asyncio
    async def test_execute_error(self):
        executor = ClaudeSessionExecutor("e1")
        ctx = TaskContext(project_id="p1", employee_id="e1", task_id="t1")

        on_log = MagicMock()
        with patch("onemancompany.core.claude_session.run_claude_session",
                    new_callable=AsyncMock,
                    return_value={"output": "[claude-daemon error] fail", "model": "claude",
                                  "input_tokens": 0, "output_tokens": 0}):
            result = await executor.execute("do stuff", ctx, on_log=on_log)
            assert result.error is not None
            assert result.output == ""
            on_log.assert_called()


# =====================================================================
# _push_to_conversation
# =====================================================================


class TestPushToConversation:
    def test_skips_ceo_nodes(self):
        mgr = EmployeeManager()
        node = MagicMock()
        node.employee_id = "00001"
        node.is_ceo_node = False

        with patch("onemancompany.core.config.CEO_ID", "00001"):
            mgr._push_to_conversation(node, "msg")  # Should return early

    def test_pushes_for_regular_employee(self):
        mgr = EmployeeManager()
        node = MagicMock()
        node.employee_id = "e1"
        node.is_ceo_node = False
        node.project_id = "p1"
        node.id = "n1"

        with patch("onemancompany.core.config.CEO_ID", "00001"), \
             patch("onemancompany.core.conversation.get_conversation_service") as mock_svc, \
             patch("onemancompany.core.async_utils.spawn_background") as mock_spawn:
            mgr._push_to_conversation(node, "test msg")
            mock_spawn.assert_called_once()


# =====================================================================
# _publish_node_update
# =====================================================================


class TestPublishNodeUpdate:
    def test_publishes_event(self):
        mgr = EmployeeManager()
        node = MagicMock()
        node.to_dict.return_value = {"id": "n1"}

        loop = asyncio.new_event_loop()

        async def _run():
            with patch("onemancompany.core.vessel._store") as mock_store, \
                 patch("onemancompany.core.vessel.event_bus") as mock_bus:
                mock_store.load_employee.return_value = {"role": "Engineer"}
                mock_bus.publish = AsyncMock()
                mgr._publish_node_update("e1", node)

        loop.run_until_complete(_run())
        loop.close()

    def test_handles_no_event_loop(self):
        mgr = EmployeeManager()
        node = MagicMock()
        node.to_dict.return_value = {"id": "n1"}

        with patch("onemancompany.core.vessel._store") as mock_store:
            mock_store.load_employee.return_value = {"role": "Engineer"}
            # No event loop — should log warning, not raise
            mgr._publish_node_update("e1", node)


# =====================================================================
# _setup_holding_watchdog_by_id
# =====================================================================


class TestSetupHoldingWatchdog:
    def test_gmail_reply_poller(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.automation.start_cron", return_value={"status": "ok"}) as mock_start:
            mgr._setup_holding_watchdog_by_id("e1", "t1", "2024-01-01", {"thread_id": "abc"})
            mock_start.assert_called_once()
            assert "reply_t1" in mock_start.call_args[0][1]

    def test_generic_watchdog(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.automation.start_cron", return_value={"status": "ok"}) as mock_start:
            mgr._setup_holding_watchdog_by_id("e1", "t1", "2024-01-01", {"key": "val"})
            mock_start.assert_called_once()
            assert "holding_t1" in mock_start.call_args[0][1]

    def test_logs_error_on_failure(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.automation.start_cron", return_value={"status": "error"}):
            mgr._setup_holding_watchdog_by_id("e1", "t1", "2024-01-01", {})


# =====================================================================
# resume_held_task
# =====================================================================


class TestResumeHeldTask:
    @pytest.mark.asyncio
    async def test_resumes_holding_task(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e0", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="held", acceptance_criteria=[])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.HOLDING)
        child.project_dir = str(tmp_path)
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        mgr._schedule["e1"] = [ScheduleEntry(node_id=child.id, tree_path=str(tree_path))]

        with patch("onemancompany.core.vessel.stop_cron"), \
             patch("onemancompany.core.task_tree.save_tree_async"), \
             patch.object(mgr, "_log_node"), \
             patch.object(mgr, "_publish_node_update"), \
             patch.object(mgr, "_append_history_from_node"), \
             patch("onemancompany.core.vessel._append_progress"), \
             patch.object(mgr, "_on_child_complete", new_callable=AsyncMock), \
             patch("onemancompany.core.vessel._trigger_dep_resolution"), \
             patch.object(mgr, "unschedule"), \
             patch.object(mgr, "_schedule_next"):
            result = await mgr.resume_held_task("e1", child.id, "condition met")
            assert result is True

    @pytest.mark.asyncio
    async def test_resume_not_found(self):
        mgr = EmployeeManager()
        mgr._schedule["e1"] = []
        result = await mgr.resume_held_task("e1", "nonexistent", "result")
        assert result is False

    @pytest.mark.asyncio
    async def test_resume_not_holding(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        mgr._schedule["e1"] = [ScheduleEntry(node_id=root.id, tree_path=str(tree_path))]

        result = await mgr.resume_held_task("e1", root.id, "result")
        assert result is False

    @pytest.mark.asyncio
    async def test_resume_system_node_auto_finish(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e0", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="system", acceptance_criteria=[])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.HOLDING)
        child.node_type = NodeType.REVIEW
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)
        child_id = child.id

        mgr = EmployeeManager()
        mgr._schedule["e1"] = [ScheduleEntry(node_id=child_id, tree_path=str(tree_path))]

        with patch("onemancompany.core.vessel.stop_cron"), \
             patch("onemancompany.core.task_tree.save_tree_async"), \
             patch.object(mgr, "_log_node"), \
             patch.object(mgr, "_publish_node_update"), \
             patch.object(mgr, "_append_history_from_node"), \
             patch("onemancompany.core.vessel._append_progress"), \
             patch.object(mgr, "unschedule"), \
             patch.object(mgr, "_schedule_next"):
            result = await mgr.resume_held_task("e1", child_id, "done")
            assert result is True
            # Verify via tree cache that node is finished
            from onemancompany.core.task_tree import get_tree
            loaded_tree = get_tree(str(tree_path))
            node = loaded_tree.get_node(child_id)
            assert node.status == TaskPhase.FINISHED.value


# =====================================================================
# _completion_consumer_loop / _on_child_complete
# =====================================================================


class TestCompletionQueue:
    @pytest.mark.asyncio
    async def test_ensure_completion_queue(self):
        mgr = EmployeeManager()
        mgr._completion_queue = None
        mgr._completion_consumer = None
        mgr._ensure_completion_queue()
        assert mgr._completion_queue is not None
        assert mgr._completion_consumer is not None

        # Cleanup
        mgr._completion_consumer.cancel()
        try:
            await mgr._completion_consumer
        except asyncio.CancelledError:
            pass
        mgr._completion_queue = None
        mgr._completion_consumer = None

    @pytest.mark.asyncio
    async def test_ensure_completion_queue_idempotent(self):
        mgr = EmployeeManager()
        mgr._completion_queue = asyncio.Queue()
        mgr._completion_consumer = MagicMock()
        original_queue = mgr._completion_queue

        mgr._ensure_completion_queue()
        assert mgr._completion_queue is original_queue  # Should not recreate


# =====================================================================
# _get_role
# =====================================================================


class TestGetRole:
    def test_found(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.vessel._store") as mock_store:
            mock_store.load_employee.return_value = {"role": "PM"}
            assert mgr._get_role("e1") == "PM"

    def test_not_found_defaults(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.vessel._store") as mock_store:
            mock_store.load_employee.return_value = None
            assert mgr._get_role("e1") == "Employee"


# =====================================================================
# _set_employee_status
# =====================================================================


class TestSetEmployeeStatus:
    def test_spawns_background(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.vessel.spawn_background") as mock_spawn:
            mgr._set_employee_status("e1", "working")
            mock_spawn.assert_called_once()

    def test_handles_no_loop(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.vessel.spawn_background",
                    side_effect=RuntimeError("no loop")):
            mgr._set_employee_status("e1", "working")  # Should not raise


# =====================================================================
# _publish_dispatch_status
# =====================================================================


class TestPublishDispatchStatus:
    def test_idle_status(self):
        mgr = EmployeeManager()
        loop = asyncio.new_event_loop()

        async def _run():
            with patch("onemancompany.core.vessel.event_bus") as mock_bus:
                mock_bus.publish = AsyncMock()
                mgr._publish_dispatch_status("e1", status="idle")

        loop.run_until_complete(_run())
        loop.close()

    def test_dispatched_with_entry(self, tmp_path):
        tree, tree_path, root = _make_tree(tmp_path)
        entry = ScheduleEntry(node_id=root.id, tree_path=str(tree_path))

        mgr = EmployeeManager()
        loop = asyncio.new_event_loop()

        async def _run():
            with patch("onemancompany.core.vessel.event_bus") as mock_bus:
                mock_bus.publish = AsyncMock()
                mgr._publish_dispatch_status("e1", status="dispatched", entry=entry)

        loop.run_until_complete(_run())
        loop.close()

    def test_no_event_loop(self):
        mgr = EmployeeManager()
        mgr._publish_dispatch_status("e1", status="idle")  # Should not raise


# =====================================================================
# ExecutionError
# =====================================================================


class TestExecutionError:
    def test_creation(self):
        err = ExecutionError("something failed")
        assert str(err) == "something failed"


# =====================================================================
# register_founding_employee
# =====================================================================


class TestRegisterFoundingEmployee:
    def test_company_hosting(self, tmp_path):
        from onemancompany.core.vessel import register_founding_employee

        emp_dir = tmp_path / "emp01"
        emp_dir.mkdir()
        (emp_dir / "work_principles.md").write_text("existing")

        cfg = MagicMock()
        cfg.hosting = "company"
        cfg.name = "Test"
        cfg.nickname = "TT"
        cfg.role = "Engineer"
        cfg.department = "Dev"
        emp_cfgs = {"emp01": cfg}

        mock_cls = MagicMock()
        mock_cls.return_value = MagicMock()

        with patch("onemancompany.core.vessel_config.load_vessel_config", return_value=None), \
             patch.object(employee_manager, "register") as mock_reg:
            mock_reg.return_value = MagicMock(spec=Vessel)
            vessel = register_founding_employee("emp01", mock_cls, emp_cfgs, tmp_path)
            mock_reg.assert_called_once()

    def test_self_hosting(self, tmp_path):
        from onemancompany.core.vessel import register_founding_employee

        emp_dir = tmp_path / "emp02"
        emp_dir.mkdir()

        cfg = MagicMock()
        cfg.hosting = "self"
        cfg.name = "Self"
        cfg.nickname = ""
        cfg.role = "Designer"
        cfg.department = ""
        emp_cfgs = {"emp02": cfg}

        with patch("onemancompany.core.vessel_config.load_vessel_config", return_value=None), \
             patch.object(employee_manager, "register") as mock_reg:
            mock_reg.return_value = MagicMock(spec=Vessel)
            register_founding_employee("emp02", None, emp_cfgs, tmp_path)
            # Check the executor is ClaudeSessionExecutor
            call_args = mock_reg.call_args
            assert isinstance(call_args[0][1], ClaudeSessionExecutor)

    def test_openclaw_hosting(self, tmp_path):
        from onemancompany.core.vessel import register_founding_employee

        emp_dir = tmp_path / "emp03"
        emp_dir.mkdir()

        cfg = MagicMock()
        cfg.hosting = "openclaw"
        cfg.name = "Open"
        cfg.nickname = ""
        cfg.role = "Worker"
        cfg.department = ""
        emp_cfgs = {"emp03": cfg}

        with patch("onemancompany.core.vessel_config.load_vessel_config", return_value=None), \
             patch.object(employee_manager, "register") as mock_reg:
            mock_reg.return_value = MagicMock(spec=Vessel)
            register_founding_employee("emp03", None, emp_cfgs, tmp_path)
            from onemancompany.core.subprocess_executor import SubprocessExecutor
            call_args = mock_reg.call_args
            assert isinstance(call_args[0][1], SubprocessExecutor)


# =====================================================================
# schedule_system_task
# =====================================================================


class TestScheduleSystemTask:
    def test_schedules_and_returns_id(self):
        mgr = EmployeeManager()
        loop = asyncio.new_event_loop()

        async def _run():
            mgr._event_loop = asyncio.get_running_loop()
            coro = asyncio.sleep(0)
            task_id = mgr.schedule_system_task(coro, "test_task", project_id="sys_proj")
            assert task_id == "sys_proj"
            assert "sys_proj" in mgr._system_tasks
            # Wait for it to finish
            await mgr._system_tasks["sys_proj"]

        with patch("onemancompany.core.vessel.event_bus") as mock_bus, \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
            mock_bus.publish = AsyncMock()
            loop.run_until_complete(_run())
        loop.close()

    def test_auto_generates_id(self):
        mgr = EmployeeManager()
        loop = asyncio.new_event_loop()

        async def _run():
            mgr._event_loop = asyncio.get_running_loop()
            coro = asyncio.sleep(0)
            task_id = mgr.schedule_system_task(coro, "test_task")
            assert task_id.startswith("_sys_")
            await mgr._system_tasks[task_id]

        with patch("onemancompany.core.vessel.event_bus") as mock_bus, \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
            mock_bus.publish = AsyncMock()
            loop.run_until_complete(_run())
        loop.close()

    def test_handles_error_in_coro(self):
        mgr = EmployeeManager()
        loop = asyncio.new_event_loop()

        async def _failing():
            raise ValueError("boom")

        async def _run():
            mgr._event_loop = asyncio.get_running_loop()
            task_id = mgr.schedule_system_task(_failing(), "fail_task", project_id="sys_fail")
            await mgr._system_tasks[task_id]

        with patch("onemancompany.core.vessel.event_bus") as mock_bus, \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
            mock_bus.publish = AsyncMock()
            loop.run_until_complete(_run())
        loop.close()


# =====================================================================
# _resolve_dependencies
# =====================================================================


class TestResolveDependencies:
    @pytest.mark.asyncio
    async def test_resolves_ready_dependents(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e0", description="root")
        dep = tree.add_child(parent_id=root.id, employee_id="e1",
                             description="dep task", acceptance_criteria=[])
        dep.set_status(TaskPhase.PROCESSING)
        dep.set_status(TaskPhase.COMPLETED)
        dep.set_status(TaskPhase.ACCEPTED)

        dependent = tree.add_child(parent_id=root.id, employee_id="e2",
                                   description="depends on dep", acceptance_criteria=[],
                                   depends_on=[dep.id])
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()

        with patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.task_tree.get_tree_lock") as mock_lock, \
             patch.object(mgr, "schedule_node") as mock_sched, \
             patch.object(mgr, "_schedule_next"), \
             patch("onemancompany.core.vessel._save_project_tree"), \
             patch("onemancompany.core.vessel._store") as mock_store:
            mock_lock.return_value = MagicMock(__enter__=MagicMock(), __exit__=MagicMock())
            mock_store.save_project_status = AsyncMock()
            await mgr._resolve_dependencies(tree, dep, str(tmp_path))
            mock_sched.assert_called()

    @pytest.mark.asyncio
    async def test_blocks_on_failed_dep(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e0", description="root")
        dep = tree.add_child(parent_id=root.id, employee_id="e1",
                             description="dep task", acceptance_criteria=[])
        dep.set_status(TaskPhase.PROCESSING)
        dep.set_status(TaskPhase.FAILED)

        dependent = tree.add_child(parent_id=root.id, employee_id="e2",
                                   description="depends on dep", acceptance_criteria=[],
                                   depends_on=[dep.id])
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()

        with patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.task_tree.get_tree_lock") as mock_lock, \
             patch.object(mgr, "schedule_node"), \
             patch.object(mgr, "_schedule_next"), \
             patch("onemancompany.core.vessel._save_project_tree"), \
             patch("onemancompany.core.vessel._store") as mock_store:
            mock_lock.return_value = MagicMock(__enter__=MagicMock(), __exit__=MagicMock())
            mock_store.save_project_status = AsyncMock()
            await mgr._resolve_dependencies(tree, dep, str(tmp_path))
            # Dependent should be blocked
            assert dependent.status == TaskPhase.BLOCKED.value

    @pytest.mark.asyncio
    async def test_cascade_cancels_on_cancelled_dep(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e0", description="root")
        dep = tree.add_child(parent_id=root.id, employee_id="e1",
                             description="dep task", acceptance_criteria=[])
        dep.set_status(TaskPhase.PROCESSING)
        from onemancompany.core.task_lifecycle import safe_cancel
        safe_cancel(dep)

        dependent = tree.add_child(parent_id=root.id, employee_id="e2",
                                   description="depends on dep", acceptance_criteria=[],
                                   depends_on=[dep.id])
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()

        with patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.task_tree.get_tree_lock") as mock_lock, \
             patch.object(mgr, "schedule_node"), \
             patch.object(mgr, "_schedule_next"), \
             patch("onemancompany.core.vessel._save_project_tree"), \
             patch("onemancompany.core.vessel._store") as mock_store:
            mock_lock.return_value = MagicMock(__enter__=MagicMock(), __exit__=MagicMock())
            mock_store.save_project_status = AsyncMock()
            await mgr._resolve_dependencies(tree, dep, str(tmp_path))
            assert dependent.status == TaskPhase.CANCELLED.value

    @pytest.mark.asyncio
    async def test_no_dependents(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e0", description="root")
        dep = tree.add_child(parent_id=root.id, employee_id="e1",
                             description="dep task", acceptance_criteria=[])
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()

        with patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.task_tree.get_tree_lock") as mock_lock:
            mock_lock.return_value = MagicMock(__enter__=MagicMock(), __exit__=MagicMock())
            await mgr._resolve_dependencies(tree, dep, str(tmp_path))
            # Should just return without error


# =====================================================================
# _full_cleanup
# =====================================================================


class TestFullCleanup:
    @pytest.mark.asyncio
    async def test_basic_cleanup(self, tmp_path):
        mgr = EmployeeManager()
        node = MagicMock()
        node.description = "test task"
        node.result = "done"
        node.project_dir = str(tmp_path)

        with patch.object(mgr, "_update_soul", new_callable=AsyncMock), \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock), \
             patch("onemancompany.core.vessel._store") as mock_store, \
             patch("onemancompany.core.vessel.event_bus") as mock_bus, \
             patch("onemancompany.core.state.flush_pending_reload", return_value=None), \
             patch.object(mgr, "_release_project_resources"), \
             patch("onemancompany.core.project_archive.complete_project"), \
             patch("onemancompany.core.project_archive.append_action"):
            mock_store.load_all_employees.return_value = []
            mock_store.load_employee.return_value = {"role": "Engineer"}
            mock_store.save_employee_runtime = AsyncMock()
            mock_store.save_project_status = AsyncMock()
            mock_bus.publish = AsyncMock()

            await mgr._full_cleanup("e1", node, agent_error=False,
                                    project_id="proj1", run_retrospective=False)

    @pytest.mark.asyncio
    async def test_cleanup_with_error(self, tmp_path):
        mgr = EmployeeManager()
        node = MagicMock()
        node.description = "test task"
        node.result = "Error: something"
        node.project_dir = str(tmp_path)

        with patch.object(mgr, "_update_soul", new_callable=AsyncMock), \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock), \
             patch("onemancompany.core.vessel._store") as mock_store, \
             patch("onemancompany.core.vessel.event_bus") as mock_bus, \
             patch("onemancompany.core.state.flush_pending_reload", return_value=None), \
             patch.object(mgr, "_release_project_resources"), \
             patch("onemancompany.core.project_archive.complete_project"):
            mock_store.load_all_employees.return_value = []
            mock_store.load_employee.return_value = {"role": "Engineer"}
            mock_store.save_employee_runtime = AsyncMock()
            mock_store.save_project_status = AsyncMock()
            mock_bus.publish = AsyncMock()

            await mgr._full_cleanup("e1", node, agent_error=True,
                                    project_id="proj1", run_retrospective=False)
            # Should call save_project_status with FAILED
            mock_store.save_project_status.assert_called_once()


# =====================================================================
# _update_soul
# =====================================================================


class TestUpdateSoul:
    @pytest.mark.asyncio
    async def test_skips_founding(self):
        mgr = EmployeeManager()
        node = MagicMock()
        with patch("onemancompany.core.config.FOUNDING_IDS", {"e1"}):
            await mgr._update_soul("e1", node)
            # Should return early

    @pytest.mark.asyncio
    async def test_skips_empty_result(self):
        mgr = EmployeeManager()
        node = MagicMock()
        node.result = ""
        with patch("onemancompany.core.config.FOUNDING_IDS", set()):
            await mgr._update_soul("e1", node)
            # Should return early

    @pytest.mark.asyncio
    async def test_updates_soul_md(self, tmp_path):
        mgr = EmployeeManager()
        node = MagicMock()
        node.result = "learned important lesson"
        node.description = "task desc"

        mock_resp = MagicMock()
        mock_resp.content = "# Updated SOUL\nLearned something new"

        with patch("onemancompany.core.config.FOUNDING_IDS", set()), \
             patch("onemancompany.core.config.get_workspace_dir", return_value=tmp_path), \
             patch("onemancompany.core.vessel._store") as mock_store, \
             patch("onemancompany.core.vessel.make_llm"), \
             patch("onemancompany.agents.base.tracked_ainvoke",
                    new_callable=AsyncMock, return_value=mock_resp):
            mock_store.load_employee.return_value = {"name": "Test", "nickname": "TT", "role": "Eng"}
            await mgr._update_soul("e1", node)
            soul_path = tmp_path / "SOUL.md"
            assert soul_path.exists()

    @pytest.mark.asyncio
    async def test_soul_update_failure(self, tmp_path):
        mgr = EmployeeManager()
        node = MagicMock()
        node.result = "something"
        node.description = "desc"

        with patch("onemancompany.core.config.FOUNDING_IDS", set()), \
             patch("onemancompany.core.config.get_workspace_dir", return_value=tmp_path), \
             patch("onemancompany.core.vessel._store") as mock_store, \
             patch("onemancompany.core.vessel.make_llm", side_effect=Exception("llm fail")):
            mock_store.load_employee.return_value = {"name": "T", "nickname": "", "role": "E"}
            # Should not raise
            await mgr._update_soul("e1", node)

    @pytest.mark.asyncio
    async def test_soul_skips_no_employee_data(self, tmp_path):
        mgr = EmployeeManager()
        node = MagicMock()
        node.result = "something"
        node.description = "desc"

        with patch("onemancompany.core.config.FOUNDING_IDS", set()), \
             patch("onemancompany.core.config.get_workspace_dir", return_value=tmp_path), \
             patch("onemancompany.core.vessel._store") as mock_store:
            mock_store.load_employee.return_value = None
            await mgr._update_soul("e1", node)
            # Should return early


# =====================================================================
# _release_project_resources
# =====================================================================


class TestReleaseProjectResources:
    def test_evicts_tree_cache(self, tmp_path):
        mgr = EmployeeManager()
        node = MagicMock()
        node.project_dir = str(tmp_path)
        mgr.executors["e1"] = MagicMock()

        with patch("onemancompany.core.task_tree.evict_tree") as mock_evict:
            mgr._release_project_resources("e1", node, "proj1")
            mock_evict.assert_called_once()

    def test_releases_claude_session_lock(self, tmp_path):
        mgr = EmployeeManager()
        node = MagicMock()
        node.project_dir = str(tmp_path)
        mgr.executors["e1"] = ClaudeSessionExecutor("e1")

        with patch("onemancompany.core.task_tree.evict_tree"), \
             patch("onemancompany.core.claude_session._remove_session_lock") as mock_remove:
            mgr._release_project_resources("e1", node, "proj1")
            mock_remove.assert_called_once()


# =====================================================================
# stop_all_loops with completion consumer
# =====================================================================


class TestStopAllLoopsCompletion:
    @pytest.mark.asyncio
    async def test_cancels_completion_consumer(self):
        from onemancompany.core.vessel import stop_all_loops

        mgr_backup_running = employee_manager._running_tasks.copy()
        mgr_backup_consumer = employee_manager._completion_consumer
        mgr_backup_queue = employee_manager._completion_queue

        try:
            employee_manager._running_tasks.clear()

            # Create a fake consumer task
            async def _fake_consumer():
                await asyncio.sleep(100)

            employee_manager._completion_consumer = asyncio.ensure_future(_fake_consumer())
            employee_manager._completion_queue = asyncio.Queue()

            with patch("onemancompany.core.conversation.get_conversation_service") as mock_conv:
                mock_conv.return_value = MagicMock()
                await stop_all_loops()

            assert employee_manager._completion_consumer is None
            assert employee_manager._completion_queue is None
        finally:
            employee_manager._running_tasks = mgr_backup_running
            employee_manager._completion_consumer = mgr_backup_consumer
            employee_manager._completion_queue = mgr_backup_queue


# =====================================================================
# scan_overdue_reviews edge cases
# =====================================================================


class TestScanOverdueReviewsEdgeCases:
    def test_skips_non_dir(self, tmp_path):
        (tmp_path / "file.txt").write_text("x")
        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path):
            result = scan_overdue_reviews()
            assert result == []

    def test_skips_system_nodes(self, tmp_path):
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="e0", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="review", acceptance_criteria=[])
        child.status = TaskPhase.COMPLETED.value
        child.completed_at = (datetime.now() - timedelta(seconds=600)).isoformat()
        child.node_type = NodeType.REVIEW

        tree_path = proj_dir / "task_tree.yaml"
        tree.save(tree_path)

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path):
            result = scan_overdue_reviews(threshold_seconds=300)
            assert len(result) == 0  # System nodes skipped

    def test_invalid_completed_at(self, tmp_path):
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="e0", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="task", acceptance_criteria=[])
        child.status = TaskPhase.COMPLETED.value
        child.completed_at = "not-a-date"

        tree_path = proj_dir / "task_tree.yaml"
        tree.save(tree_path)

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path):
            result = scan_overdue_reviews()
            assert len(result) == 0  # Invalid date skipped

    def test_no_completed_at(self, tmp_path):
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="e0", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="task", acceptance_criteria=[])
        child.status = TaskPhase.COMPLETED.value
        child.completed_at = ""

        tree_path = proj_dir / "task_tree.yaml"
        tree.save(tree_path)

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path):
            result = scan_overdue_reviews()
            assert len(result) == 0

    def test_no_tree_file(self, tmp_path):
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()
        # No task_tree.yaml

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path):
            result = scan_overdue_reviews()
            assert len(result) == 0

    def test_corrupt_tree(self, tmp_path):
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()
        (proj_dir / "task_tree.yaml").write_text("invalid: [[[")

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path):
            result = scan_overdue_reviews()
            assert len(result) == 0

    def test_no_parent_node(self, tmp_path):
        """Node with no parent_id still reports but with empty reviewer."""
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="e1", description="root task")
        root.status = TaskPhase.COMPLETED.value
        root.completed_at = (datetime.now() - timedelta(seconds=600)).isoformat()

        tree_path = proj_dir / "task_tree.yaml"
        tree.save(tree_path)

        with patch("onemancompany.core.config.PROJECTS_DIR", tmp_path):
            result = scan_overdue_reviews(threshold_seconds=300)
            assert len(result) == 1
            assert result[0]["reviewer_id"] == ""


# =====================================================================
# _check_holding_timeout — actual timeout
# =====================================================================


class TestCheckHoldingTimeoutActual:
    def test_times_out_and_fails(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="task", acceptance_criteria=[])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.HOLDING)
        child.hold_started_at = (datetime.now() - timedelta(hours=2)).isoformat()
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()

        with patch("onemancompany.core.task_tree.save_tree_async"), \
             patch("onemancompany.core.vessel.stop_cron"):
            result = mgr._check_holding_timeout(str(tree_path), child.id)
            assert result is True

    def test_invalid_hold_started_at(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="task", acceptance_criteria=[])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.HOLDING)
        child.hold_started_at = "not-a-valid-date"
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        result = mgr._check_holding_timeout(str(tree_path), child.id)
        assert result is False

    def test_batch_id_hold_skips_timeout(self, tmp_path):
        tree = TaskTree(project_id="p1")
        root = tree.create_root(employee_id="e1", description="root")
        child = tree.add_child(parent_id=root.id, employee_id="e1",
                               description="task", acceptance_criteria=[])
        child.set_status(TaskPhase.PROCESSING)
        child.set_status(TaskPhase.HOLDING)
        child.hold_started_at = (datetime.now() - timedelta(hours=2)).isoformat()
        child.hold_reason = "batch_id=xyz"
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = EmployeeManager()
        result = mgr._check_holding_timeout(str(tree_path), child.id)
        assert result is False


# =====================================================================
# _build_project_identity edge case: iteration not found
# =====================================================================


class TestBuildProjectIdentityEdgeCases:
    def test_iteration_not_found(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_iteration", return_value=True), \
             patch("onemancompany.core.project_archive._find_project_for_iteration", return_value=None):
            result = mgr._build_project_identity("proj/iter_001")
            assert result == ""


# =====================================================================
# _get_project_history_context
# =====================================================================


class TestGetProjectHistoryContext:
    def test_iteration_no_matching_project(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_iteration", return_value=True), \
             patch("onemancompany.core.project_archive._find_project_for_iteration", return_value=None):
            result = mgr._get_project_history_context("proj/iter_001")
            assert result == ""

    def test_project_not_found(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_iteration", return_value=False), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=None):
            result = mgr._get_project_history_context("nonexistent")
            assert result == ""

    def test_no_prev_iters_no_files(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_iteration", return_value=False), \
             patch("onemancompany.core.project_archive.load_named_project",
                    return_value={"name": "proj", "status": "active", "iterations": []}), \
             patch("onemancompany.core.project_archive.list_project_files", return_value=[]):
            result = mgr._get_project_history_context("proj")
            assert result == ""

    def test_with_prev_iterations_and_files(self):
        mgr = EmployeeManager()
        proj = {
            "name": "My Project",
            "status": "active",
            "iterations": ["iter_001", "iter_002"],
        }
        iter_data = {
            "task": "Build feature",
            "status": "completed",
            "acceptance_criteria": ["works", "tested"],
            "timeline": [
                {"time": "2024-01-01T12:00:00", "employee_id": "e1",
                 "action": "started", "detail": "began work"},
            ],
            "output": "Feature built successfully",
            "cost": {
                "budget_estimate_usd": 10.0,
                "actual_cost_usd": 5.0,
                "token_usage": {"input": 1000, "output": 500},
            },
        }

        with patch("onemancompany.core.project_archive._is_iteration", return_value=True), \
             patch("onemancompany.core.project_archive._find_project_for_iteration", return_value="my-proj"), \
             patch("onemancompany.core.project_archive._split_qualified_iter",
                    return_value=("my-proj", "iter_002")), \
             patch("onemancompany.core.project_archive.load_named_project", return_value=proj), \
             patch("onemancompany.core.project_archive.load_iteration", return_value=iter_data), \
             patch("onemancompany.core.project_archive.list_project_files",
                    return_value=["output.txt", "report.md"]), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value="/fake/dir"):
            result = mgr._get_project_history_context("my-proj/iter_002")
            assert "Project Context" in result
            assert "My Project" in result
            assert "output.txt" in result


# =====================================================================
# _full_cleanup with retrospective
# =====================================================================


class TestFullCleanupRetrospective:
    @pytest.mark.asyncio
    async def test_cleanup_with_retrospective(self, tmp_path):
        mgr = EmployeeManager()
        node = MagicMock()
        node.description = "test task"
        node.result = "done"
        node.project_dir = str(tmp_path)

        with patch.object(mgr, "_update_soul", new_callable=AsyncMock), \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock), \
             patch("onemancompany.core.vessel._store") as mock_store, \
             patch("onemancompany.core.vessel.event_bus") as mock_bus, \
             patch("onemancompany.core.state.flush_pending_reload", return_value=None), \
             patch.object(mgr, "_release_project_resources"), \
             patch("onemancompany.core.project_archive.complete_project"), \
             patch("onemancompany.core.project_archive.append_action"), \
             patch("onemancompany.core.routine.run_post_task_routine", new_callable=AsyncMock):
            mock_store.load_all_employees.return_value = []
            mock_store.load_employee.return_value = {"role": "Engineer"}
            mock_store.save_employee_runtime = AsyncMock()
            mock_store.save_project_status = AsyncMock()
            mock_bus.publish = AsyncMock()

            await mgr._full_cleanup("e1", node, agent_error=False,
                                    project_id="proj1", run_retrospective=True)

    @pytest.mark.asyncio
    async def test_cleanup_retrospective_fails(self, tmp_path):
        mgr = EmployeeManager()
        node = MagicMock()
        node.description = "test task"
        node.result = "done"
        node.project_dir = str(tmp_path)

        with patch.object(mgr, "_update_soul", new_callable=AsyncMock), \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock), \
             patch("onemancompany.core.vessel._store") as mock_store, \
             patch("onemancompany.core.vessel.event_bus") as mock_bus, \
             patch("onemancompany.core.state.flush_pending_reload", return_value=None), \
             patch.object(mgr, "_release_project_resources"), \
             patch("onemancompany.core.project_archive.complete_project"), \
             patch("onemancompany.core.project_archive.append_action"), \
             patch("onemancompany.core.routine.run_post_task_routine",
                    new_callable=AsyncMock, side_effect=Exception("retro failed")):
            mock_store.load_all_employees.return_value = []
            mock_store.load_employee.return_value = {"role": "Engineer"}
            mock_store.save_employee_runtime = AsyncMock()
            mock_store.save_project_status = AsyncMock()
            mock_bus.publish = AsyncMock()

            await mgr._full_cleanup("e1", node, agent_error=False,
                                    project_id="proj1", run_retrospective=True)

    @pytest.mark.asyncio
    async def test_cleanup_with_flush_result(self, tmp_path):
        mgr = EmployeeManager()
        node = MagicMock()
        node.description = "test task"
        node.result = "done"
        node.project_dir = str(tmp_path)

        with patch.object(mgr, "_update_soul", new_callable=AsyncMock), \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock), \
             patch("onemancompany.core.vessel._store") as mock_store, \
             patch("onemancompany.core.vessel.event_bus") as mock_bus, \
             patch("onemancompany.core.state.flush_pending_reload",
                    return_value={"employees_updated": ["e1"], "employees_added": []}), \
             patch.object(mgr, "_release_project_resources"), \
             patch("onemancompany.core.project_archive.complete_project"), \
             patch("onemancompany.core.project_archive.append_action"):
            mock_store.load_all_employees.return_value = ["e1"]
            mock_store.load_employee.return_value = {"role": "Engineer"}
            mock_store.save_employee_runtime = AsyncMock()
            mock_store.save_project_status = AsyncMock()
            mock_bus.publish = AsyncMock()

            await mgr._full_cleanup("e1", node, agent_error=False,
                                    project_id="proj1", run_retrospective=False)

    @pytest.mark.asyncio
    async def test_cleanup_system_project(self, tmp_path):
        mgr = EmployeeManager()
        node = MagicMock()
        node.description = "system task"
        node.result = "done"
        node.project_dir = str(tmp_path)

        with patch.object(mgr, "_update_soul", new_callable=AsyncMock), \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock), \
             patch("onemancompany.core.vessel._store") as mock_store, \
             patch("onemancompany.core.vessel.event_bus") as mock_bus, \
             patch("onemancompany.core.state.flush_pending_reload", return_value=None), \
             patch.object(mgr, "_release_project_resources"):
            mock_store.load_all_employees.return_value = []
            mock_store.load_employee.return_value = {"role": "Engineer"}
            mock_store.save_employee_runtime = AsyncMock()
            mock_bus.publish = AsyncMock()

            await mgr._full_cleanup("e1", node, agent_error=False,
                                    project_id="_sys_test", run_retrospective=False)

    @pytest.mark.asyncio
    async def test_cleanup_with_product_slug(self, tmp_path):
        mgr = EmployeeManager()
        node = MagicMock()
        node.description = "task"
        node.result = "done"
        node.project_dir = str(tmp_path)

        with patch.object(mgr, "_update_soul", new_callable=AsyncMock), \
             patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock), \
             patch("onemancompany.core.vessel._store") as mock_store, \
             patch("onemancompany.core.vessel.event_bus") as mock_bus, \
             patch("onemancompany.core.state.flush_pending_reload", return_value=None), \
             patch.object(mgr, "_release_project_resources"), \
             patch("onemancompany.core.project_archive.complete_project"), \
             patch("onemancompany.core.project_archive.append_action"), \
             patch("onemancompany.core.project_archive.load_project",
                    return_value={"product_id": "prod-1"}), \
             patch("onemancompany.core.product.find_slug_by_product_id", return_value="my-product"), \
             patch("onemancompany.core.product.list_issues",
                    return_value=[{"id": "issue-1", "linked_task_ids": ["proj1"]}]):
            mock_store.load_all_employees.return_value = []
            mock_store.load_employee.return_value = {"role": "Engineer"}
            mock_store.save_employee_runtime = AsyncMock()
            mock_bus.publish = AsyncMock()

            await mgr._full_cleanup("e1", node, agent_error=False,
                                    project_id="proj1", run_retrospective=False)
