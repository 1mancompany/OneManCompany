"""Push 9 modules to 100% coverage by covering remaining missing lines.

Each test class targets one module's uncovered lines.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml


# ── 1. task_verification (lines 155-156) ──────────────────────────────────

class TestTaskVerificationFallback:
    """Lines 155-156: ast.literal_eval fallback fails (ValueError/SyntaxError)."""

    def test_invalid_json_and_invalid_python_literal(self):
        """Tool call args that start with { but are neither valid JSON nor Python."""
        from onemancompany.core.task_verification import collect_evidence

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "nodes" / "n1"
            log_dir.mkdir(parents=True)
            with open(log_dir / "execution.log", "w") as f:
                # Args starting with { but totally broken — not JSON, not Python literal
                entry = {"type": "tool_call", "content": "write({not valid at all !!!})"}
                f.write(json.dumps(entry) + "\n")

            ev = collect_evidence(tmpdir, "n1")
            assert "write" in ev.tools_called
            # Should not crash, args fallback to {}
            assert ev.files_written == []


# ── 2. tool_registry (lines 161-162) ──────────────────────────────────────

class TestToolRegistryDefaultDir:
    """Lines 161-162: load_asset_tools with tools_dir=None uses TOOLS_DIR from config."""

    def test_load_asset_tools_default_dir(self, tmp_path):
        from onemancompany.core.tool_registry import ToolRegistry

        reg = ToolRegistry()
        # Mock TOOLS_DIR to a non-existent path so it hits lines 161-162 then returns at 164
        fake_dir = tmp_path / "nonexistent_tools"
        with patch("onemancompany.core.config.TOOLS_DIR", fake_dir):
            reg.load_asset_tools()  # tools_dir=None → imports TOOLS_DIR


# ── 3. task_persistence (lines 140-142) ──────────────────────────────────

class TestTaskPersistenceCorruptSystemTree:
    """Lines 140-142: corrupt system_tasks.yaml gets skipped with warning."""

    def test_corrupt_system_tree_skipped(self, tmp_path):
        from onemancompany.core.task_persistence import recover_schedule_from_trees

        # Create employees dir with a corrupt system_tasks.yaml
        emp_dir = tmp_path / "employees" / "00099"
        emp_dir.mkdir(parents=True)
        sys_tasks_file = emp_dir / "system_tasks.yaml"
        sys_tasks_file.write_text("totally broken: [yaml: {{{", encoding="utf-8")

        # Create projects dir (empty)
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()

        mock_em = MagicMock()
        mock_em._schedule = {}

        # Should not raise even with corrupt system_tasks.yaml
        recover_schedule_from_trees(mock_em, projects_dir, tmp_path / "employees")


# ── 4. state (lines 83-84, 107-108) ──────────────────────────────────────

class TestStateGetActiveTasks:
    """Lines 83-84: import of vessel fails. Lines 107-108: exception loading tree."""

    def test_import_vessel_fails_returns_empty(self):
        from onemancompany.core.state import get_active_tasks

        # Make the import of employee_manager fail inside get_active_tasks
        with patch.dict("sys.modules", {"onemancompany.core.vessel": None}):
            result = get_active_tasks()
            assert result == []

    def test_tree_load_exception(self, tmp_path):
        """Lines 107-108: TaskTree.load raises exception."""
        from onemancompany.core.state import get_active_tasks

        mock_entry = MagicMock()
        mock_entry.tree_path = str(tmp_path / "tree.yaml")
        mock_entry.node_id = "n1"

        # Create the file so tp.exists() is True
        tree_file = tmp_path / "tree.yaml"
        tree_file.write_text("invalid: {broken", encoding="utf-8")

        mock_em = MagicMock()
        mock_em._schedule = {"emp1": [mock_entry]}

        # The function does `from onemancompany.core.vessel import employee_manager`
        # We need to patch at vessel module level
        mock_vessel = MagicMock()
        mock_vessel.employee_manager = mock_em
        with patch.dict("sys.modules", {"onemancompany.core.vessel": mock_vessel}):
            result = get_active_tasks()
            assert result == []


# ── 5. routine — timeline_ctx, meeting lines ──────────────────────────────

# Lines 370, 434, 492, 617 are all `if timeline_text: timeline_ctx = ...`
# inside deeply nested prompt-building functions. We test them by calling
# the actual handler functions with project_record containing timeline data.

def _make_routine_step(title="Test Step"):
    from onemancompany.core.workflow_engine import WorkflowStep
    return WorkflowStep(index=0, title=title, owner="HR",
                        instructions=[], output_description="output",
                        raw_text="raw", depends_on=[])


def _make_routine_ctx_with_timeline(participants=None):
    from onemancompany.core import routine as mod
    from onemancompany.core.workflow_engine import WorkflowDefinition
    wf = WorkflowDefinition(name="test", flow_id="test", owner="HR",
                            collaborators="", trigger="", steps=[])
    project_record = {
        "timeline": [
            {"employee_id": "00010", "action": "code_review", "detail": "Reviewed PR"},
        ]
    }
    return mod.StepContext(
        "task summary", participants or ["00010", "00011"], "room1", wf, {},
        project_record=project_record,
    )


def _routine_mock_store():
    return MagicMock(
        load_culture=MagicMock(return_value=[]),
        load_rooms=MagicMock(return_value={}),
        save_guidance=AsyncMock(),
        save_room=AsyncMock(),
        save_employee_runtime=AsyncMock(),
        load_employee_guidance=MagicMock(return_value=[]),
    )


class TestRoutineTimelineContextBranch:
    """Lines 370, 434, 492, 617: timeline_ctx branches with actual handler calls."""

    @pytest.mark.asyncio
    async def test_senior_review_with_timeline(self):
        """Line 370: timeline_ctx populated in _handle_senior_review."""
        from onemancompany.core import routine as mod

        step = _make_routine_step("Senior Peer Review")
        ctx = _make_routine_ctx_with_timeline()
        ctx.self_evaluations = [
            {"employee_id": "00011", "name": "Junior", "nickname": "J", "level": 1, "evaluation": "I tried"},
        ]
        senior = {"name": "Senior", "nickname": "S", "level": 3, "role": "Lead",
                  "department": "Tech", "work_principles": "", "performance_history": []}
        junior = {"name": "Junior", "nickname": "J", "level": 1, "role": "Dev",
                  "department": "Tech", "work_principles": "", "performance_history": []}

        def _load(eid):
            return senior if eid == "00010" else junior

        with (
            patch.object(mod, "load_employee", side_effect=_load),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock,
                         return_value=MagicMock(content='[{"name":"Junior","review":"Good"}]')),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_store", _routine_mock_store()),
        ):
            result = await mod._handle_senior_review(step, ctx)
            assert "senior_reviews" in result

    @pytest.mark.asyncio
    async def test_hr_summary_with_timeline(self):
        """Line 434: timeline_ctx in _handle_hr_summary."""
        from onemancompany.core import routine as mod

        step = _make_routine_step("HR Summary")
        ctx = _make_routine_ctx_with_timeline()
        ctx.self_evaluations = [
            {"employee_id": "00010", "name": "Dev", "nickname": "D", "level": 1, "evaluation": "OK"},
        ]
        ctx.senior_reviews = []

        with (
            patch.object(mod, "load_employee", return_value={"name": "D", "nickname": "D", "level": 1}),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock,
                         return_value=MagicMock(content='{"summary":"Good"}')),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_store", _routine_mock_store()),
        ):
            result = await mod._handle_hr_summary(step, ctx)
            assert "hr_summary" in result

    @pytest.mark.asyncio
    async def test_coo_report_with_timeline(self):
        """Line 492: timeline_ctx in _handle_coo_report."""
        from onemancompany.core import routine as mod

        step = _make_routine_step("COO Report")
        ctx = _make_routine_ctx_with_timeline()

        with (
            patch.object(mod, "load_employee", return_value={"name": "COO", "nickname": "C", "level": 5}),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock,
                         return_value=MagicMock(content='{"report":"OK"}')),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_store", _routine_mock_store()),
            patch.object(mod, "get_employee_skills_prompt", return_value=""),
            patch.object(mod, "get_employee_tools_prompt", return_value=""),
        ):
            result = await mod._handle_coo_report(step, ctx)
            assert "coo_report" in result

    @pytest.mark.asyncio
    async def test_employee_open_floor_with_timeline(self):
        """Line 617: timeline_ctx in _handle_employee_open_floor."""
        from onemancompany.core import routine as mod

        step = _make_routine_step("Employee Open Floor")
        ctx = _make_routine_ctx_with_timeline()
        ctx.coo_report = "All good"

        with (
            patch.object(mod, "load_employee", return_value={
                "name": "Dev", "nickname": "D", "level": 1, "role": "Dev",
                "department": "Tech", "work_principles": ""}),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock,
                         return_value=MagicMock(content='{"feedback":"More resources","suggestions":[]}')),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_store", _routine_mock_store()),
            patch.object(mod, "get_employee_skills_prompt", return_value=""),
            patch.object(mod, "get_employee_tools_prompt", return_value=""),
        ):
            result = await mod._handle_employee_open_floor(step, ctx)
            assert "employee_feedback" in result


class TestRoutinePhase1WithPrinciples:
    """Lines 1628, 1795: principles_ctx in phase1/phase2 with work_principles."""

    @pytest.mark.asyncio
    async def test_phase1_with_principles(self):
        """Line 1628: employee has work_principles in _run_review_phase1."""
        from onemancompany.core import routine as mod

        emp_with_principles = {
            "name": "Dev1", "nickname": "D1", "level": 1,
            "role": "Dev", "department": "Tech",
            "work_principles": "Always write tests before code",
            "performance_history": [],
        }

        responses = [
            MagicMock(content="I did great"),  # self-eval
            MagicMock(content='[{"summary":"OK"}]'),  # HR summary
        ]

        with (
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, side_effect=responses),
            patch.object(mod, "load_employee", return_value=emp_with_principles),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "get_employee_skills_prompt", return_value=""),
            patch.object(mod, "get_employee_tools_prompt", return_value=""),
        ):
            result = await mod._run_review_phase1(
                "task summary", ["00010"], workflow_doc="", room_id="room1"
            )
            assert len(result["self_evaluations"]) == 1

    @pytest.mark.asyncio
    async def test_phase2_with_principles_and_missing_emp(self):
        """Lines 1795, 1799: employee with principles + missing employee in phase2."""
        from onemancompany.core import routine as mod

        emp_with_principles = {
            "name": "Dev1", "nickname": "D1", "level": 1,
            "role": "Dev", "department": "Tech",
            "work_principles": "Ship fast, measure impact",
            "performance_history": [],
        }
        phase1_result = {
            "self_evaluations": [{"employee_id": "00010", "name": "Dev1", "evaluation": "Good"}],
            "senior_reviews": [],
            "hr_summary": [{"summary": "OK"}],
        }

        responses = [
            MagicMock(content='{"report":"All good"}'),  # COO report
            MagicMock(content='{"feedback":"More resources","suggestions":[]}'),  # employee feedback for 00010
            MagicMock(content='[{"description":"Improve testing","source":"00010","priority":"high"}]'),  # action plan
        ]

        def _load(eid):
            if eid == "ghost":
                return None  # Line 1795: continue
            return emp_with_principles

        with (
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, side_effect=responses),
            patch.object(mod, "load_employee", side_effect=_load),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "get_employee_skills_prompt", return_value=""),
            patch.object(mod, "get_employee_tools_prompt", return_value=""),
            patch.object(mod, "_store", _routine_mock_store()),
        ):
            result = await mod._run_review_phase2(
                "task summary", ["00010", "ghost"], phase1_result, room_id="room1"
            )
            assert "coo_report" in result


class TestRoutineFallbackWithActionItems:
    """Line 1591: action items recorded in _run_post_task_routine_fallback."""

    @pytest.mark.asyncio
    async def test_fallback_with_project_id_and_action_items(self):
        """Line 1591: append_action called for each action item."""
        from onemancompany.core import routine as mod

        phase1_result = {
            "self_evaluations": [{"employee_id": "00010", "name": "Dev", "evaluation": "OK"}],
            "senior_reviews": [],
            "hr_summary": [],
        }
        phase2_result = {
            "coo_report": "Report",
            "employee_feedback": [],
            "action_items": [
                {"description": "Test more", "source": "00010", "priority": "high"},
            ],
        }

        mock_room = MagicMock()
        mock_room.id = "room1"
        mock_room.is_booked = False
        mock_room.participants = []

        with (
            patch.object(mod, "load_workflows", return_value={}),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={"room1": mock_room})),
            patch.object(mod, "_run_review_phase1", new_callable=AsyncMock, return_value=phase1_result),
            patch.object(mod, "_run_review_phase2", new_callable=AsyncMock, return_value=phase2_result),
            patch.object(mod, "_ea_auto_approve_actions", new_callable=AsyncMock, return_value={}),
            patch.object(mod, "_set_participants_status", new_callable=AsyncMock),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_save_report"),
            patch.object(mod, "_build_summary", return_value="summary"),
            patch.object(mod, "_store", _routine_mock_store()),
            patch("onemancompany.core.project_archive.append_action") as mock_append,
        ):
            await mod._run_post_task_routine_fallback(
                "task summary", ["00010", "00011"], project_id="proj1"
            )
            # Line 1591: append_action called for action items
            assert mock_append.call_count >= 1


class TestRoutineCeoMeetingEdgeCases:
    """Lines 2319, 2347, 2355-2356, 2367, 2373, 2426: CEO meeting code."""

    @pytest.mark.asyncio
    async def test_end_meeting_emp_not_found(self):
        """Line 2426: emp_data is None during end_ceo_meeting."""
        from onemancompany.core import routine as mod

        # Set up meeting state
        mod._active_ceo_meeting = {
            "type": "all_hands",
            "room_id": "room1",
            "room_name": "Meeting Room 1",
            "participants": ["00010", "ghost_emp"],
            "chat_history": [{"speaker": "CEO", "message": "hi"}],
        }
        mod._ceo_meeting_cancel = asyncio.Event()

        # load_all_employees returns dict: emp_id -> data
        # ghost_emp has no data → line 2426
        all_emps = {"00010": {"name": "Dev", "nickname": "D"}}

        mock_store = _routine_mock_store()

        # Mock the EA summary response
        summary_json = '{"action_points":[],"summary":"Good meeting"}'

        with (
            patch.object(mod, "load_all_employees", return_value=all_emps),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock,
                         return_value=MagicMock(content=summary_json)),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_store", mock_store),
            patch.object(mod, "_set_participants_status", new_callable=AsyncMock),
        ):
            result = await mod.end_ceo_meeting()

        # Clean up
        mod._active_ceo_meeting = None
        mod._ceo_meeting_cancel = None


# ── 6. task_tree ──────────────────────────────────────────────────────────

class TestTaskNodeSetattr:
    """Lines 109-110, 114-115: __setattr__ AttributeError during init."""

    def test_setattr_description_after_init(self):
        """Lines 107-108: setting description after init marks _content_dirty."""
        from onemancompany.core.task_tree import TaskNode

        node = TaskNode(parent_id="root", employee_id="emp1", title="test")
        node._content_dirty = False
        node.description = "updated description"
        assert node._content_dirty is True
        assert node._description_preview == "updated description"

    def test_setattr_result_after_init(self):
        """Lines 112-113: setting result after init marks _content_dirty."""
        from onemancompany.core.task_tree import TaskNode

        node = TaskNode(parent_id="root", employee_id="emp1", title="test")
        node._content_dirty = False
        node.result = "new result"
        assert node._content_dirty is True

    def test_setattr_directives_after_init(self):
        """Lines 112-115: setting directives marks _content_dirty."""
        from onemancompany.core.task_tree import TaskNode

        node = TaskNode(parent_id="root", employee_id="emp1", title="test")
        node._content_dirty = False
        node.directives = [{"from": "coo", "directive": "do better"}]
        assert node._content_dirty is True


class TestTaskNodeSaveContentError:
    """Lines 143-144: OSError during temp file cleanup in save_content."""

    def test_save_content_write_fails_cleanup_fails(self, tmp_path):
        from onemancompany.core.task_tree import TaskNode

        node = TaskNode(parent_id="root", employee_id="emp1", title="test",
                        description="desc")
        node._content_dirty = True

        with patch("os.replace", side_effect=RuntimeError("disk fail")):
            with patch("os.unlink", side_effect=OSError("unlink fail")):
                with pytest.raises(RuntimeError, match="disk fail"):
                    node.save_content(tmp_path)


class TestTaskTreeCycleDeps:
    """Lines 295, 335, 339, 347: cycle detection."""

    def test_circular_dependency_detected(self):
        """Line 295: cycle raises ValueError."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="emp1", description="root")
        a = tree.add_child(root.id, employee_id="emp1", description="A", acceptance_criteria=[])
        b = tree.add_child(root.id, employee_id="emp1", description="B", acceptance_criteria=[],
                           depends_on=[a.id])
        c = tree.add_child(root.id, employee_id="emp1", description="C", acceptance_criteria=[],
                           depends_on=[b.id])
        # Create cycle: A depends on C (A→...→C→B→A)
        a.depends_on = [c.id]
        with pytest.raises(ValueError, match="Circular dependency"):
            tree.add_child(root.id, employee_id="emp1", description="D", acceptance_criteria=[],
                           depends_on=[a.id])

    def test_dep_not_found_in_tree(self):
        """Line 339 (via line 288-291): nonexistent dep ID."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="emp1", description="root")
        with pytest.raises(ValueError, match="not found in tree"):
            tree.add_child(root.id, employee_id="emp1", description="A", acceptance_criteria=[],
                           depends_on=["nonexistent"])

    def test_diamond_deps_not_cycle(self):
        """Line 347: diamond pattern is not a cycle."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="emp1", description="root")
        b = tree.add_child(root.id, employee_id="emp1", description="B", acceptance_criteria=[])
        c = tree.add_child(root.id, employee_id="emp1", description="C", acceptance_criteria=[])
        d = tree.add_child(root.id, employee_id="emp1", description="D", acceptance_criteria=[],
                           depends_on=[b.id, c.id])
        assert d is not None


class TestTaskTreeCycleEdgeCases:
    """Lines 329, 333, 341: DFS edge cases in _has_cycle."""

    def test_dfs_revisits_node(self):
        """Line 329: visited node encountered again in DFS (diamond in deps)."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="emp1", description="root")
        # Create diamond: A depends on D, B depends on D
        d = tree.add_child(root.id, employee_id="emp1", description="D", acceptance_criteria=[])
        b = tree.add_child(root.id, employee_id="emp1", description="B", acceptance_criteria=[],
                           depends_on=[d.id])
        c = tree.add_child(root.id, employee_id="emp1", description="C", acceptance_criteria=[],
                           depends_on=[d.id])
        # A depends on both B and C → DFS from A will reach D twice
        a = tree.add_child(root.id, employee_id="emp1", description="A", acceptance_criteria=[],
                           depends_on=[b.id, c.id])
        # Now add a node depending on A — DFS traverses A→B→D and A→C→D, hitting D twice
        e = tree.add_child(root.id, employee_id="emp1", description="E", acceptance_criteria=[],
                           depends_on=[a.id])
        assert e is not None  # Should succeed, no cycle

    def test_dfs_missing_node_in_transitive_deps(self):
        """Line 333: transitive dep references nonexistent node → skip."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="emp1", description="root")
        a = tree.add_child(root.id, employee_id="emp1", description="A", acceptance_criteria=[])
        # Inject a fake depends_on that references a non-existent node
        a.depends_on = ["ghost_node_xyz"]
        # Now add node depending on A → DFS follows A→ghost_node_xyz → not found → continue
        b = tree.add_child(root.id, employee_id="emp1", description="B", acceptance_criteria=[],
                           depends_on=[a.id])
        assert b is not None

    def test_diamond_pattern_hits_pass(self):
        """Line 341: diamond pattern where upstream is in dep_set but != start."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="emp1", description="root")
        a = tree.add_child(root.id, employee_id="emp1", description="A", acceptance_criteria=[])
        b = tree.add_child(root.id, employee_id="emp1", description="B", acceptance_criteria=[],
                           depends_on=[a.id])
        # Add node with depends_on=[A, B]. When DFS starts from B, it follows
        # B.depends_on=[A], and A is in dep_set and A != start(B) → hits line 341
        c = tree.add_child(root.id, employee_id="emp1", description="C", acceptance_criteria=[],
                           depends_on=[a.id, b.id])
        assert c is not None


class TestTaskTreeAllChildrenDone:
    """Line 428: all_children_done with finished children."""

    def test_all_children_done_no_children(self):
        """Line 422: no non-system children → return True."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="emp1", description="root")
        # No children at all
        assert tree.all_children_done(root.id) is True


class TestTaskTreeSaveError:
    """Lines 530-535: save() catches BaseException during atomic write."""

    def test_save_write_fails_cleanup_fails(self, tmp_path):
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="emp1", description="root")
        # Clear content_dirty so save_content returns early (no-op)
        root._content_dirty = False

        save_path = tmp_path / "tree.yaml"
        with patch("os.replace", side_effect=RuntimeError("disk fail")):
            with patch("os.unlink", side_effect=OSError("cleanup fail")):
                with pytest.raises(RuntimeError, match="disk fail"):
                    tree.save(save_path)


# ── 7. background_tasks ──────────────────────────────────────────────────

class TestBackgroundTasksSaveCleanup:
    """Lines 139-142: save raises → temp file cleanup."""

    def test_save_error_cleans_temp(self, tmp_path):
        from onemancompany.core.background_tasks import BackgroundTaskManager

        mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
        mgr._tasks = {}
        mgr._processes = {}
        mgr._monitors = {}
        mgr._employee_id = "test"

        yaml_path = tmp_path / "bg_tasks.yaml"
        with patch.object(mgr, '_yaml_path', return_value=yaml_path):
            with patch("onemancompany.core.background_tasks.yaml.dump", side_effect=RuntimeError("dump fail")):
                with pytest.raises(RuntimeError, match="dump fail"):
                    mgr._save()


class TestBackgroundTasksReadOutputError:
    """Lines 192-194: read_output fails."""

    def test_read_output_exception(self, tmp_path):
        from onemancompany.core.background_tasks import BackgroundTaskManager

        mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
        mgr._tasks = {}
        mgr._processes = {}
        mgr._monitors = {}
        mgr._employee_id = "test"

        log_file = tmp_path / "output.log"
        log_file.write_text("some output")

        with patch.object(mgr, 'output_log_path', return_value=log_file):
            with patch("onemancompany.core.background_tasks.open_utf", side_effect=PermissionError("no")):
                result = mgr.read_output_tail("task1", lines=10)
                assert result == ""


class TestBackgroundTasksStartSubprocessError:
    """Lines 255-257: create_subprocess_shell raises → close log_fd."""

    @pytest.mark.asyncio
    async def test_start_subprocess_error(self, tmp_path):
        from onemancompany.core.background_tasks import BackgroundTaskManager

        mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
        mgr._tasks = {}
        mgr._processes = {}
        mgr._monitors = {}
        mgr._employee_id = "test"
        mgr._data_dir = tmp_path

        log_path = tmp_path / "logs" / "out.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with patch.object(mgr, '_yaml_path', return_value=tmp_path / "bg.yaml"):
            with patch.object(mgr, 'output_log_path', return_value=log_path):
                with patch.object(mgr, '_detect_port_from_command', return_value=None):
                    with patch.object(type(mgr), 'can_launch', new_callable=lambda: property(lambda s: True)):
                        with patch("asyncio.create_subprocess_shell", side_effect=OSError("no shell")):
                            with pytest.raises(OSError, match="no shell"):
                                await mgr.launch("bad_command", "test", str(tmp_path), "ceo")


class TestBackgroundTasksMonitorMissing:
    """Line 280: task not in _tasks → early return."""

    @pytest.mark.asyncio
    async def test_monitor_task_missing(self):
        from onemancompany.core.background_tasks import BackgroundTaskManager

        mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
        mgr._tasks = {}
        mgr._processes = {}
        mgr._monitors = {}
        mgr._employee_id = "test"

        await mgr._monitor("nonexistent", AsyncMock(), MagicMock())


class TestBackgroundTasksPortDetection:
    """Lines 288-289, 318, 329-330: port detection edge cases."""

    @pytest.mark.asyncio
    async def test_port_detection_spawn_error(self):
        """Lines 288-289: spawn_background for port detection raises."""
        from onemancompany.core.background_tasks import BackgroundTaskManager, BackgroundTask

        mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
        task = BackgroundTask(
            id="t1", command="cmd", description="test", working_dir="/tmp",
            started_by="ceo", status="running",
        )
        task.port = None
        mgr._tasks = {"t1": task}
        mgr._processes = {}
        mgr._monitors = {}
        mgr._employee_id = "test"

        mock_proc = AsyncMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_fd = MagicMock()

        with patch("onemancompany.core.async_utils.spawn_background", side_effect=RuntimeError("no loop")):
            with patch.object(mgr, '_save'):
                with patch.object(mgr, '_broadcast_update'):
                    await mgr._monitor("t1", mock_proc, mock_fd)
                    assert task.status == "completed"

    @pytest.mark.asyncio
    async def test_detect_port_no_log_continue(self, tmp_path):
        """Line 318: log doesn't exist → continue (then task becomes non-running)."""
        from onemancompany.core.background_tasks import BackgroundTaskManager, BackgroundTask

        mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
        task = BackgroundTask(
            id="t1", command="cmd", description="test", working_dir="/tmp",
            started_by="ceo", status="running",
        )
        task.port = None
        mgr._tasks = {"t1": task}
        mgr._processes = {}
        mgr._monitors = {}
        mgr._employee_id = "test"

        call_count = 0
        original_sleep = asyncio.sleep

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                task.status = "completed"  # stop the loop
            await original_sleep(0)

        with patch.object(mgr, 'output_log_path', return_value=tmp_path / "nonexistent.log"):
            with patch("asyncio.sleep", side_effect=mock_sleep):
                await mgr._detect_port_from_output("t1")

    @pytest.mark.asyncio
    async def test_detect_port_read_error(self, tmp_path):
        """Lines 329-330: reading log raises exception."""
        from onemancompany.core.background_tasks import BackgroundTaskManager, BackgroundTask

        mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
        task = BackgroundTask(
            id="t1", command="cmd", description="test", working_dir="/tmp",
            started_by="ceo", status="running",
        )
        task.port = None
        mgr._tasks = {"t1": task}
        mgr._processes = {}
        mgr._monitors = {}
        mgr._employee_id = "test"

        log_file = tmp_path / "output.log"
        log_file.write_text("some text")

        call_count = 0
        original_sleep = asyncio.sleep

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                task.status = "completed"
            await original_sleep(0)

        with patch.object(mgr, 'output_log_path', return_value=log_file):
            with patch("onemancompany.core.background_tasks.read_text_utf", side_effect=PermissionError("no")):
                with patch("asyncio.sleep", side_effect=mock_sleep):
                    await mgr._detect_port_from_output("t1")


class TestBackgroundTasksTerminateKill:
    """Lines 344-346: terminate kills process after timeout."""

    @pytest.mark.asyncio
    async def test_terminate_timeout_then_kill(self, tmp_path):
        from onemancompany.core.background_tasks import BackgroundTaskManager, BackgroundTask

        mgr = BackgroundTaskManager.__new__(BackgroundTaskManager)
        task = BackgroundTask(
            id="t1", command="cmd", description="test", working_dir="/tmp",
            started_by="ceo", status="running",
        )
        mgr._tasks = {"t1": task}
        mgr._monitors = {}
        mgr._employee_id = "test"

        mock_proc = AsyncMock()
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mgr._processes = {"t1": mock_proc}

        async def timeout_wait(*a, **kw):
            raise asyncio.TimeoutError()

        with patch.object(mgr, '_yaml_path', return_value=tmp_path / "bg.yaml"):
            with patch.object(mgr, '_save'):
                with patch.object(mgr, '_broadcast_update'):
                    with patch("asyncio.wait_for", side_effect=timeout_wait):
                        result = await mgr.terminate("t1")
                        assert result is True
                        mock_proc.kill.assert_called_once()


# ── 8. project_archive ───────────────────────────────────────────────────

class TestProjectArchiveQualifiedIter:
    """Line 166: qualified iteration ID slug returned even when file missing."""

    def test_qualified_iter_file_exists(self, tmp_path):
        """Line 166: qualified iteration ID, file exists → return slug."""
        from onemancompany.core.project_archive import _find_project_for_iteration

        with patch("onemancompany.core.project_archive.PROJECTS_DIR", tmp_path):
            iter_dir = tmp_path / "my-project" / "iterations"
            iter_dir.mkdir(parents=True)
            (iter_dir / "iter_001.yaml").write_text("task: test", encoding="utf-8")
            result = _find_project_for_iteration("my-project/iter_001")
            assert result == "my-project"


class TestProjectArchiveRenameWhenReady:
    """Lines 283-295: _rename_when_ready background task for LLM naming."""

    @pytest.mark.asyncio
    async def test_rename_timeout(self):
        """Lines 286-288: LLM naming times out."""
        from onemancompany.core.project_archive import async_create_project_from_task

        captured_coro = None

        def capture_spawn(coro):
            nonlocal captured_coro
            captured_coro = coro

        with patch("onemancompany.core.project_archive.create_named_project", return_value="proj1"):
            with patch("onemancompany.core.project_archive.create_iteration", return_value="iter1"):
                with patch("onemancompany.core.async_utils.spawn_background", side_effect=capture_spawn):
                    with patch("onemancompany.core.project_archive._auto_project_name", return_value="auto"):
                        result = await async_create_project_from_task("build widget", "emp1")
                        assert result == ("proj1", "iter1")

        # Now run the captured coroutine with a timeout
        if captured_coro:
            with patch("onemancompany.core.project_archive._llm_project_name",
                        new_callable=AsyncMock, side_effect=asyncio.TimeoutError):
                await captured_coro  # Lines 284-288

    @pytest.mark.asyncio
    async def test_rename_success(self):
        """Lines 289-295: successful rename."""
        from onemancompany.core.project_archive import async_create_project_from_task

        captured_coro = None

        def capture_spawn(coro):
            nonlocal captured_coro
            captured_coro = coro

        with patch("onemancompany.core.project_archive.create_named_project", return_value="proj1"):
            with patch("onemancompany.core.project_archive.create_iteration", return_value="iter1"):
                with patch("onemancompany.core.async_utils.spawn_background", side_effect=capture_spawn):
                    with patch("onemancompany.core.project_archive._auto_project_name", return_value="auto"):
                        await async_create_project_from_task("build widget", "emp1")

        if captured_coro:
            with patch("onemancompany.core.project_archive._llm_project_name",
                        new_callable=AsyncMock, return_value="Cool Widget"):
                with patch("onemancompany.core.project_archive.update_project_name"):
                    with patch("onemancompany.core.store.mark_dirty"):
                        await captured_coro  # Lines 289-295


class TestProjectArchiveUpdateStatusNoDoc:
    """Lines 576-578: update_project_status when no doc found."""

    def test_update_status_no_doc(self):
        from onemancompany.core.project_archive import update_project_status

        with patch("onemancompany.core.project_archive._resolve_and_load", return_value=("v2", None, "")):
            update_project_status("nonexistent", "active")  # Should not raise

    def test_update_status_with_doc(self):
        """Lines 576-578: doc found → update and save."""
        from onemancompany.core.project_archive import update_project_status

        doc = {"status": "active", "name": "test"}
        with patch("onemancompany.core.project_archive._resolve_and_load", return_value=("v2", doc, "proj1")):
            with patch("onemancompany.core.project_archive._save_resolved") as mock_save:
                update_project_status("proj1", "completed", extra_field="value")
                assert doc["status"] == "completed"
                assert doc["extra_field"] == "value"
                mock_save.assert_called_once()


class TestProjectArchiveListFilesRipgrep:
    """Lines 708, 711-712, 714-716: ripgrep edge cases."""

    def test_ripgrep_error_returncode(self, tmp_path):
        from onemancompany.core.project_archive import _list_files_ripgrep

        mock_result = MagicMock()
        mock_result.returncode = 2
        with patch("subprocess.run", return_value=mock_result):
            assert _list_files_ripgrep(tmp_path, 100) is None

    def test_ripgrep_truncates(self, tmp_path):
        from onemancompany.core.project_archive import _list_files_ripgrep

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "\n".join([f"file{i}.py" for i in range(200)])
        with patch("subprocess.run", return_value=mock_result):
            result = _list_files_ripgrep(tmp_path, 50)
            assert len(result) == 50

    def test_ripgrep_not_found(self, tmp_path):
        from onemancompany.core.project_archive import _list_files_ripgrep
        import subprocess

        with patch("subprocess.run", side_effect=FileNotFoundError("no rg")):
            assert _list_files_ripgrep(tmp_path, 100) is None

    def test_ripgrep_timeout(self, tmp_path):
        from onemancompany.core.project_archive import _list_files_ripgrep
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("rg", 10)):
            assert _list_files_ripgrep(tmp_path, 100) is None


class TestProjectArchiveCostReport:
    """Lines 882, 902-903: ex-employee lookup in cost report."""

    def test_cost_report_no_iterations(self, tmp_path):
        """Line 882: project with no iterations key → skip."""
        from onemancompany.core.project_archive import get_cost_summary, PROJECT_YAML_FILENAME

        project_dir = tmp_path / "proj_no_iter"
        project_dir.mkdir()
        (project_dir / PROJECT_YAML_FILENAME).write_text(
            yaml.dump({"name": "NoIter", "status": "active"}), encoding="utf-8"
        )

        with patch("onemancompany.core.project_archive.PROJECTS_DIR", tmp_path):
            report = get_cost_summary()
            assert report["total"]["cost_usd"] == 0

    def test_cost_report_ex_employee(self, tmp_path):
        from onemancompany.core.project_archive import get_cost_summary

        project_dir = tmp_path / "proj1"
        project_dir.mkdir()
        project_data = {
            "name": "Test",
            "status": "completed",
            "iterations": ["iter1"],
        }
        from onemancompany.core.project_archive import PROJECT_YAML_FILENAME
        (project_dir / PROJECT_YAML_FILENAME).write_text(
            yaml.dump(project_data), encoding="utf-8"
        )

        iter_doc = {
            "cost": {
                "actual_cost_usd": 5.0,
                "token_usage": {"input": 100, "output": 50},
                "breakdown": [
                    {"employee_id": "ex1", "cost_usd": 5.0,
                     "input_tokens": 100, "output_tokens": 50}
                ],
            }
        }

        with patch("onemancompany.core.project_archive.PROJECTS_DIR", tmp_path):
            with patch("onemancompany.core.project_archive.load_iteration", return_value=iter_doc):
                with patch("onemancompany.core.store.load_employee", return_value=None):
                    with patch("onemancompany.core.store.load_ex_employees",
                               return_value={"ex1": {"department": "Engineering"}}):
                        report = get_cost_summary()
                        assert report["total"]["cost_usd"] == 5.0
                        assert "Engineering" in report["by_department"]


# ── 9. conversation ──────────────────────────────────────────────────────

class TestConversationEnsureIndexed:
    """Line 191: ensure_indexed registers a conversation directory."""

    def test_ensure_indexed(self, tmp_path):
        from onemancompany.core.conversation import ConversationService

        svc = ConversationService()
        svc.ensure_indexed("conv1", tmp_path / "conv1")
        assert "conv1" in svc._index


class TestConversationListPhaseFilter:
    """Lines 243-245: list_by_phase filters conversations."""

    def test_list_filters_non_active_default(self, tmp_path):
        """Lines 242-243: phase=None → skip non-ACTIVE."""
        from onemancompany.core.conversation import (
            ConversationService, Conversation, ConversationPhase,
            CONVERSATION_META_FILENAME,
        )
        from onemancompany.core.config import open_utf

        svc = ConversationService()
        conv_dir = tmp_path / "conv_closed"
        conv_dir.mkdir(parents=True)
        conv = Conversation(
            id="conv_closed", type="oneonone", phase=ConversationPhase.CLOSED.value,
            employee_id="emp1", tools_enabled=False,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with open_utf(conv_dir / CONVERSATION_META_FILENAME, "w") as f:
            yaml.dump(conv.to_dict(), f, allow_unicode=True)
        svc.ensure_indexed("conv_closed", conv_dir)

        result = svc.list_by_phase()
        assert len(result) == 0

    def test_list_filters_by_specific_phase(self, tmp_path):
        """Lines 244-245: explicit phase filter doesn't match."""
        from onemancompany.core.conversation import (
            ConversationService, Conversation, ConversationPhase,
            CONVERSATION_META_FILENAME,
        )
        from onemancompany.core.config import open_utf

        svc = ConversationService()
        conv_dir = tmp_path / "conv_active"
        conv_dir.mkdir(parents=True)
        conv = Conversation(
            id="conv_active", type="oneonone", phase=ConversationPhase.ACTIVE.value,
            employee_id="emp1", tools_enabled=False,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with open_utf(conv_dir / CONVERSATION_META_FILENAME, "w") as f:
            yaml.dump(conv.to_dict(), f, allow_unicode=True)
        svc.ensure_indexed("conv_active", conv_dir)

        result = svc.list_by_phase(phase=ConversationPhase.CLOSED.value)
        assert len(result) == 0


class TestConversationCloseHookErrors:
    """Lines 269-270: close hook exception is caught and logged."""

    @pytest.mark.asyncio
    async def test_close_hook_exception(self, tmp_path):
        from onemancompany.core.conversation import (
            ConversationService, Conversation, ConversationPhase,
            CONVERSATION_META_FILENAME,
        )
        from onemancompany.core.config import open_utf

        svc = ConversationService()
        conv_dir = tmp_path / "conv1"
        conv_dir.mkdir(parents=True)
        conv = Conversation(
            id="conv1", type="oneonone", phase=ConversationPhase.ACTIVE.value,
            employee_id="emp1", tools_enabled=False,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with open_utf(conv_dir / CONVERSATION_META_FILENAME, "w") as f:
            yaml.dump(conv.to_dict(), f, allow_unicode=True)
        svc.ensure_indexed("conv1", conv_dir)

        with patch("onemancompany.core.conversation_hooks.run_close_hook",
                    side_effect=RuntimeError("hook boom")):
            with patch("onemancompany.core.conversation.save_conversation_meta"):
                with patch("onemancompany.core.conversation.event_bus.publish", new_callable=AsyncMock):
                    result_conv, _ = await svc.close("conv1")
                    assert result_conv.phase == ConversationPhase.CLOSED.value


class TestConversationAutoReplyTimer:
    """Lines 408-418, 427-428: auto-reply timer fires / errors."""

    @pytest.mark.asyncio
    async def test_auto_reply_timer_fires(self):
        """Lines 408-418: timer completes and auto-replies."""
        from onemancompany.core.conversation import ConversationService, Interaction

        svc = ConversationService()
        loop = asyncio.get_event_loop()
        interaction = Interaction(
            node_id="n1", tree_path="/tmp/tree.yaml", project_id="proj1",
            source_employee="emp1", interaction_type="ceo_request",
            message="Should I proceed?", future=loop.create_future(),
        )

        with patch.object(svc, '_ea_auto_reply', new_callable=AsyncMock,
                          return_value="[EA Auto-Reply] Decision: ACCEPT\nLooks good"):
            with patch("onemancompany.core.config.get_ceo_dnd", return_value=True):
                # Don't add interaction to pending so remove() raises ValueError (line 416)
                svc._pending["conv1"] = deque()
                svc._start_auto_reply_timer("conv1", interaction)
                result = await asyncio.wait_for(interaction.future, timeout=5)
                assert "ACCEPT" in result

    @pytest.mark.asyncio
    async def test_auto_reply_timer_error(self):
        """Lines 427-428: timer raises → error logged, key cleaned up."""
        from onemancompany.core.conversation import ConversationService, Interaction

        svc = ConversationService()
        loop = asyncio.get_event_loop()
        interaction = Interaction(
            node_id="n1", tree_path="/tmp/tree.yaml", project_id="proj1",
            source_employee="emp1", interaction_type="ceo_request",
            message="What?", future=loop.create_future(),
        )

        with patch.object(svc, '_ea_auto_reply', new_callable=AsyncMock,
                          side_effect=RuntimeError("LLM down")):
            with patch("onemancompany.core.config.get_ceo_dnd", return_value=True):
                svc._start_auto_reply_timer("conv1", interaction)
                await asyncio.sleep(0.5)
                assert "conv1:n1" not in svc._auto_reply_tasks


class TestConversationAutoReplyNoLoop:
    """Lines 438-441: no event loop → skip timer."""

    @pytest.mark.asyncio
    async def test_auto_reply_no_event_loop(self):
        """asyncio.create_task raises RuntimeError → skip timer gracefully."""
        from onemancompany.core.conversation import ConversationService, Interaction

        svc = ConversationService()
        loop = asyncio.get_event_loop()
        interaction = Interaction(
            node_id="n2", tree_path="/tmp/tree.yaml", project_id="proj1",
            source_employee="emp1", interaction_type="ceo_request",
            message="What?", future=loop.create_future(),
        )

        original_create_task = asyncio.create_task

        def fake_create_task(coro, **kwargs):
            # Close the coroutine to avoid warning, then raise
            coro.close()
            raise RuntimeError("no current event loop")

        with patch("onemancompany.core.config.get_ceo_dnd", return_value=True):
            with patch("asyncio.create_task", side_effect=fake_create_task):
                svc._start_auto_reply_timer("conv1", interaction)
                # Should not raise, just skip
                assert "conv1:n2" not in svc._auto_reply_tasks


class TestConversationEaAutoReplyParseError:
    """Lines 493-494: JSON parse failure in _ea_auto_reply."""

    @pytest.mark.asyncio
    async def test_ea_auto_reply_invalid_json(self):
        from onemancompany.core.conversation import ConversationService, Interaction

        svc = ConversationService()
        loop = asyncio.get_event_loop()
        interaction = Interaction(
            node_id="n1", tree_path="/tmp/tree.yaml", project_id="proj1",
            source_employee="emp1", interaction_type="ceo_request",
            message="What?", future=loop.create_future(),
        )

        mock_resp = MagicMock()
        mock_resp.content = "I think {invalid json here} accept"

        with patch("onemancompany.agents.base.make_llm", return_value=MagicMock()):
            with patch("onemancompany.agents.base.tracked_ainvoke",
                        new_callable=AsyncMock, return_value=mock_resp):
                with patch("onemancompany.core.conversation.load_messages", return_value=[]):
                    result = await svc._ea_auto_reply("conv1", interaction)
                    assert "ACCEPT" in result


class TestConversationRecover:
    """Lines 615-617, 623-626: recover stuck conversations."""

    @pytest.mark.asyncio
    async def test_recover_load_failure(self, tmp_path):
        """Lines 615-617: get() fails → skip."""
        from onemancompany.core.conversation import ConversationService

        svc = ConversationService()
        svc._index["bad_conv"] = tmp_path / "nonexistent"
        recovered = await svc.recover()
        assert recovered == 0

    @pytest.mark.asyncio
    async def test_recover_import_error(self, tmp_path):
        """Lines 623-624: ImportError during recovery hook."""
        from onemancompany.core.conversation import (
            ConversationService, Conversation, ConversationPhase,
            CONVERSATION_META_FILENAME,
        )
        from onemancompany.core.config import open_utf

        svc = ConversationService()
        conv_dir = tmp_path / "stuck"
        conv_dir.mkdir(parents=True)
        conv = Conversation(
            id="stuck", type="oneonone", phase=ConversationPhase.CLOSING.value,
            employee_id="emp1", tools_enabled=False,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with open_utf(conv_dir / CONVERSATION_META_FILENAME, "w") as f:
            yaml.dump(conv.to_dict(), f, allow_unicode=True)
        svc.ensure_indexed("stuck", conv_dir)

        with patch("onemancompany.core.conversation_hooks.run_close_hook",
                    side_effect=ImportError("not available")):
            with patch("onemancompany.core.conversation.save_conversation_meta"):
                recovered = await svc.recover()
                assert recovered == 1

    @pytest.mark.asyncio
    async def test_recover_hook_exception(self, tmp_path):
        """Lines 625-626: general exception during recovery hook."""
        from onemancompany.core.conversation import (
            ConversationService, Conversation, ConversationPhase,
            CONVERSATION_META_FILENAME,
        )
        from onemancompany.core.config import open_utf

        svc = ConversationService()
        conv_dir = tmp_path / "stuck2"
        conv_dir.mkdir(parents=True)
        conv = Conversation(
            id="stuck2", type="oneonone", phase=ConversationPhase.CLOSING.value,
            employee_id="emp1", tools_enabled=False,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with open_utf(conv_dir / CONVERSATION_META_FILENAME, "w") as f:
            yaml.dump(conv.to_dict(), f, allow_unicode=True)
        svc.ensure_indexed("stuck2", conv_dir)

        with patch("onemancompany.core.conversation_hooks.run_close_hook",
                    side_effect=RuntimeError("hook fail")):
            with patch("onemancompany.core.conversation.save_conversation_meta"):
                recovered = await svc.recover()
                assert recovered == 1


class TestConversationRemoveByProjectError:
    """Lines 679-681: load_conversation_meta fails → skip."""

    def test_remove_by_project_load_error(self, tmp_path):
        from onemancompany.core.conversation import ConversationService

        svc = ConversationService()
        svc._index["bad_conv"] = tmp_path / "nonexistent_dir"
        svc.remove_by_project("proj1")
        # Should not crash, conv stays in index since load failed
        assert "bad_conv" in svc._index
