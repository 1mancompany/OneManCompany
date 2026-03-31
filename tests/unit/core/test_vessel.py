"""Unit tests for core/vessel.py — Vessel architecture and backward compat."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.vessel import (
    ClaudeSessionExecutor,
    EmployeeHandle,
    EmployeeManager,
    LangChainExecutor,
    Launcher,
    LaunchResult,
    ScheduleEntry,
    ScriptExecutor,
    TaskContext,
    Vessel,
    _AgentRef,
    _VesselRef,
    _current_loop,
    _current_vessel,
    agent_loops,
    employee_manager,
    get_agent_loop,
    register_agent,
    register_self_hosted,
)
from onemancompany.core.task_lifecycle import TaskPhase
from onemancompany.core.task_tree import TaskNode, TaskTree
from onemancompany.core.vessel_config import VesselConfig, LimitsConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tree_entry(tmp_path, employee_id="emp01", description="Build widget",
                     project_id="proj1", status="pending"):
    tree = TaskTree(project_id=project_id)
    root = tree.create_root(employee_id=employee_id, description=description)
    if status != "pending":
        root.status = status
    tree_path = tmp_path / "task_tree.yaml"
    tree.save(tree_path)
    entry = ScheduleEntry(node_id=root.id, tree_path=str(tree_path))
    return entry, tree_path, root


# ---------------------------------------------------------------------------
# Backward compat aliases
# ---------------------------------------------------------------------------

class TestBackwardCompatAliases:
    """Verify renamed symbols have working backward compat aliases."""

    def test_employee_handle_is_vessel(self):
        assert EmployeeHandle is Vessel

    def test_agent_ref_is_vessel_ref(self):
        assert _AgentRef is _VesselRef

    def test_current_loop_is_current_vessel(self):
        assert _current_loop is _current_vessel

    def test_langchain_launcher_alias(self):
        from onemancompany.core.vessel import LangChainLauncher
        assert LangChainLauncher is LangChainExecutor

    def test_claude_session_launcher_alias(self):
        from onemancompany.core.vessel import ClaudeSessionLauncher
        assert ClaudeSessionLauncher is ClaudeSessionExecutor

    def test_script_launcher_alias(self):
        from onemancompany.core.vessel import ScriptLauncher
        assert ScriptLauncher is ScriptExecutor


class TestBackwardCompatShim:
    """Verify imports through agent_loop.py shim work correctly."""

    def test_import_from_agent_loop(self):
        from onemancompany.core.agent_loop import (
            EmployeeHandle,
            EmployeeManager,
            Launcher,
            LangChainLauncher,
            employee_manager,
            agent_loops,
            register_agent,
            get_agent_loop,
            _current_loop,
            _current_task_id,
            _AgentRef,
            PROGRESS_LOG_MAX_LINES,
            MAX_RETRIES,
            RETRY_DELAYS,
        )
        # All should be importable
        assert EmployeeHandle is Vessel
        assert _current_loop is _current_vessel

    def test_singleton_identity(self):
        from onemancompany.core.agent_loop import employee_manager as em_old
        from onemancompany.core.vessel import employee_manager as em_new
        assert em_old is em_new


# ---------------------------------------------------------------------------
# Vessel (was EmployeeHandle)
# ---------------------------------------------------------------------------

class TestVessel:
    def test_vessel_creation(self):
        mgr = EmployeeManager()
        vessel = Vessel(mgr, "00010")
        assert vessel.employee_id == "00010"
        assert vessel.agent.employee_id == "00010"

    def test_vessel_task_history_default(self):
        mgr = EmployeeManager()
        vessel = Vessel(mgr, "00010")
        assert vessel.task_history == []


# ---------------------------------------------------------------------------
# EmployeeManager with VesselConfig
# ---------------------------------------------------------------------------

class TestEmployeeManagerVesselConfig:
    def test_register_with_config(self):
        mgr = EmployeeManager()
        mock_launcher = MagicMock(spec=Launcher)
        config = VesselConfig(limits=LimitsConfig(max_retries=7))

        vessel = mgr.register("00010", mock_launcher, config=config)
        assert "00010" in mgr.configs
        assert mgr.configs["00010"].limits.max_retries == 7
        assert isinstance(vessel, Vessel)

    def test_register_without_config(self):
        mgr = EmployeeManager()
        mock_launcher = MagicMock(spec=Launcher)

        vessel = mgr.register("00010", mock_launcher)
        assert "00010" not in mgr.configs
        assert isinstance(vessel, Vessel)

    def test_unregister_cleans_config(self):
        mgr = EmployeeManager()
        mock_launcher = MagicMock(spec=Launcher)
        config = VesselConfig()

        mgr.register("00010", mock_launcher, config=config)
        assert "00010" in mgr.configs
        mgr.unregister("00010")
        assert "00010" not in mgr.configs

    def test_backward_compat_properties(self):
        mgr = EmployeeManager()
        mock_launcher = MagicMock(spec=Launcher)
        mgr.register("00010", mock_launcher)

        # launchers ↔ executors
        assert mgr.launchers is mgr.executors
        assert "00010" in mgr.launchers

        # _handles ↔ vessels
        assert mgr._handles is mgr.vessels
        assert "00010" in mgr._handles

    def test_get_handle_returns_vessel(self):
        mgr = EmployeeManager()
        mock_launcher = MagicMock(spec=Launcher)
        mgr.register("00010", mock_launcher)

        vessel = mgr.get_handle("00010")
        assert isinstance(vessel, Vessel)
        assert vessel.employee_id == "00010"

    def test_get_handle_unknown_returns_none(self):
        mgr = EmployeeManager()
        assert mgr.get_handle("99999") is None


# ---------------------------------------------------------------------------
# Executor aliases (was Launcher)
# ---------------------------------------------------------------------------

class TestExecutorAliases:
    def test_langchain_executor_creation(self):
        mock_runner = MagicMock()
        executor = LangChainExecutor(mock_runner)
        assert executor.agent is mock_runner
        assert executor.is_ready() is True

    def test_claude_session_executor_creation(self):
        executor = ClaudeSessionExecutor("00010")
        assert executor.employee_id == "00010"
        assert executor.is_ready() is True

    def test_script_executor_creation(self):
        executor = ScriptExecutor("00010", "/path/to/launch.sh")
        assert executor.employee_id == "00010"
        assert executor.script_path == "/path/to/launch.sh"
        assert executor.is_ready() is True


# ---------------------------------------------------------------------------
# VesselRef (was _AgentRef)
# ---------------------------------------------------------------------------

class TestVesselRef:
    def test_vessel_ref_employee_id(self):
        ref = _VesselRef("00010")
        assert ref.employee_id == "00010"

    def test_vessel_ref_role(self, monkeypatch):
        from onemancompany.core import store as store_mod
        monkeypatch.setattr(store_mod, "load_employee",
                            lambda eid: {"id": eid, "role": "Engineer"})
        ref = _VesselRef("test_emp")
        assert ref.role == "Engineer"

    def test_vessel_ref_role_default(self, monkeypatch):
        from onemancompany.core import store as store_mod
        monkeypatch.setattr(store_mod, "load_employee", lambda eid: None)
        ref = _VesselRef("99999")
        assert ref.role == "Employee"


# ---------------------------------------------------------------------------
# Review prompt in _on_child_complete
# ---------------------------------------------------------------------------

class TestOnChildCompleteReviewPrompt:
    """Verify _on_child_complete builds correct review prompts with rejection context."""

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_review_prompt_skips_accepted_children(self, mock_bus, mock_state, tmp_path):
        """Already-accepted children listed separately, not in review list."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00100", description="parent task")

        # Child 1: already accepted
        child1 = tree.add_child(
            parent_id=root.id, employee_id="00101",
            description="accepted subtask", acceptance_criteria=["c1"],
        )
        child1.status = "accepted"
        child1.result = "done"
        child1.acceptance_result = {"passed": True, "notes": "good"}

        # Child 2: completed, needs review
        child2 = tree.add_child(
            parent_id=root.id, employee_id="00102",
            description="needs review subtask", acceptance_criteria=["c2"],
        )
        child2.status = "completed"
        child2.result = "also done"

        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)
        entry = ScheduleEntry(node_id=child2.id, tree_path=str(tree_path))

        em = EmployeeManager()
        em.register("00100", MagicMock(spec=Launcher))
        em.register("00102", MagicMock(spec=Launcher))

        await em._on_child_complete("00102", entry, project_id="proj1")

        # Parent (00100) should have a scheduled review node
        parent_entries = em._schedule.get("00100", [])
        assert len(parent_entries) > 0

        # Load tree and check review node content
        reloaded = TaskTree.load(tree_path, skeleton_only=False)
        review_entry = parent_entries[0]
        review_node = reloaded.get_node(review_entry.node_id)
        prompt = review_node.description

        # Already-accepted child listed with checkmark
        assert "\u2713" in prompt
        assert "accepted subtask" in prompt
        # Needs-review child listed with criteria
        assert "needs review subtask" in prompt
        assert "c2" in prompt

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_review_prompt_shows_rejection_history(self, mock_bus, mock_state, tmp_path):
        """Previously rejected children show rejection reason in review prompt."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00100", description="parent task")

        # Child that was previously rejected and re-completed
        child = tree.add_child(
            parent_id=root.id, employee_id="00103",
            description="retried subtask", acceptance_criteria=["works"],
        )
        child.status = "completed"
        child.result = "second attempt result"
        child.acceptance_result = {"passed": False, "notes": "tests were failing"}

        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)
        entry = ScheduleEntry(node_id=child.id, tree_path=str(tree_path))

        em = EmployeeManager()
        em.register("00100", MagicMock(spec=Launcher))
        em.register("00103", MagicMock(spec=Launcher))

        await em._on_child_complete("00103", entry, project_id="proj1")

        parent_entries = em._schedule.get("00100", [])
        assert len(parent_entries) > 0

        reloaded = TaskTree.load(tree_path, skeleton_only=False)
        review_node = reloaded.get_node(parent_entries[0].node_id)
        prompt = review_node.description

        # Should show rejection warning
        assert "\u26a0" in prompt
        assert "tests were failing" in prompt

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_review_prompt_no_previously_accepted(self, mock_bus, mock_state, tmp_path):
        """When no children are accepted yet, prompt lists all for review without accepted section."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00100", description="parent task")

        child = tree.add_child(
            parent_id=root.id, employee_id="00104",
            description="only subtask", acceptance_criteria=["done"],
        )
        child.status = "completed"
        child.result = "all done"

        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)
        entry = ScheduleEntry(node_id=child.id, tree_path=str(tree_path))

        em = EmployeeManager()
        em.register("00100", MagicMock(spec=Launcher))
        em.register("00104", MagicMock(spec=Launcher))

        await em._on_child_complete("00104", entry, project_id="proj1")

        parent_entries = em._schedule.get("00100", [])
        assert len(parent_entries) > 0

        reloaded = TaskTree.load(tree_path, skeleton_only=False)
        review_node = reloaded.get_node(parent_entries[0].node_id)
        prompt = review_node.description

        # Should NOT have checkmark section (no previously accepted)
        assert "\u2713" not in prompt
        # Should list the subtask for review
        assert "only subtask" in prompt
        assert "The following subtasks need review" in prompt


# ---------------------------------------------------------------------------
# CEO confirmation gate before retrospective
# ---------------------------------------------------------------------------

class TestCeoConfirmation:
    """Test CEO confirmation gate before project retrospective."""

    def _make_tree_with_root(self, tmp_path):
        """Create a TaskTree with root and one completed child, saved to disk."""
        tree = TaskTree(project_id="proj_ceo")
        tree.create_root(employee_id="00100", description="root task")
        child = tree.add_child(
            parent_id=tree.root_id, employee_id="00101",
            description="child subtask", acceptance_criteria=["done"],
        )
        child.status = "accepted"
        child.result = "child done"

        iter_dir = tmp_path / "iterations" / "iter_001"
        iter_dir.mkdir(parents=True)
        tree_path = iter_dir / "task_tree.yaml"
        tree.save(tree_path)
        return tree, tree_path

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_root_complete_creates_confirm_node(self, mock_bus, mock_state, tmp_path):
        """Root node completion should create a CEO_REQUEST confirm node, not call old path."""
        from onemancompany.core.task_lifecycle import NodeType

        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        tree, tree_path = self._make_tree_with_root(tmp_path)
        root = tree.get_node(tree.root_id)
        root.status = "completed"
        root.result = "project done"
        tree.save(tree_path)

        entry = ScheduleEntry(node_id=root.id, tree_path=str(tree_path))

        em = EmployeeManager()
        em.register("00100", MagicMock(spec=Launcher))
        em.register("00001", MagicMock(spec=Launcher))

        with (
            patch.object(em, "_full_cleanup", new_callable=AsyncMock) as mock_cleanup,
            patch.object(em, "schedule_node") as mock_schedule,
            patch.object(em, "_schedule_next"),
        ):
            await em._on_child_complete("00100", entry, project_id="proj_ceo")

        # New path creates confirm node + schedules via CeoExecutor
        mock_cleanup.assert_not_called()
        mock_schedule.assert_called_once()
        assert mock_schedule.call_args[0][0] == "00001"  # CEO_ID


# ---------------------------------------------------------------------------
# Project completion via CeoExecutor (CEO_REQUEST confirm node)
# ---------------------------------------------------------------------------

class TestProjectConfirmViaExecutor:
    """Test project completion creates a CEO_REQUEST confirm node under EA."""

    def _make_project_tree(self, tmp_path, *, ea_status="finished", child_status="accepted"):
        """Create a project tree: CEO_PROMPT → EA → child (all resolved by default)."""
        from onemancompany.core.task_lifecycle import NodeType

        tree = TaskTree(project_id="proj_confirm")
        # CEO_PROMPT root
        root = tree.create_root(employee_id="00001", description="CEO prompt")
        root.node_type = NodeType.CEO_PROMPT
        root.status = TaskPhase.PROCESSING.value

        # EA node
        ea = tree.add_child(
            parent_id=root.id, employee_id="00100",
            description="Build feature X",
            acceptance_criteria=["done"],
        )
        ea.node_type = NodeType.TASK
        ea.status = ea_status
        ea.result = "Feature built"

        # Child subtask under EA
        child = tree.add_child(
            parent_id=ea.id, employee_id="00101",
            description="Implement module",
            acceptance_criteria=["tests pass"],
        )
        child.status = child_status
        child.result = "Module done"

        iter_dir = tmp_path / "iterations" / "iter_001"
        iter_dir.mkdir(parents=True)
        tree_path = iter_dir / "task_tree.yaml"
        tree.save(tree_path)
        return tree, tree_path, root, ea, child

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_project_completion_creates_confirm_node(self, mock_bus, mock_state, tmp_path):
        """When project completes, a CEO_REQUEST confirm node is created under EA."""
        from onemancompany.core.task_lifecycle import NodeType

        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        tree, tree_path, root, ea, child = self._make_project_tree(tmp_path)
        entry = ScheduleEntry(node_id=child.id, tree_path=str(tree_path))

        em = EmployeeManager()
        em.register("00100", MagicMock(spec=Launcher))
        em.register("00101", MagicMock(spec=Launcher))
        em.register("00001", MagicMock(spec=Launcher))

        with (
            patch.object(em, "schedule_node") as mock_schedule,
            patch.object(em, "_schedule_next") as mock_next,
            patch.object(em, "_full_cleanup", new_callable=AsyncMock) as mock_cleanup,
        ):
            await em._on_child_complete("00101", entry, project_id="proj_confirm")

        # Cleanup not called directly — goes through CeoExecutor
        mock_cleanup.assert_not_called()

        # schedule_node should have been called with CEO_ID for the confirm node
        mock_schedule.assert_called_once()
        call_args = mock_schedule.call_args
        assert call_args[0][0] == "00001"  # CEO_ID

        # Verify a CEO_REQUEST node was added under EA in the tree
        reloaded = TaskTree.load(tree_path)
        ea_children = reloaded.get_children(ea.id)
        confirm_nodes = [c for c in ea_children if c.node_type == NodeType.CEO_REQUEST.value or c.node_type == NodeType.CEO_REQUEST]
        assert len(confirm_nodes) == 1
        assert "complete" in confirm_nodes[0].description_preview.lower()

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_duplicate_confirm_node_guard(self, mock_bus, mock_state, tmp_path):
        """If a confirm node already exists under EA, don't create another."""
        from onemancompany.core.task_lifecycle import NodeType

        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        tree, tree_path, root, ea, child = self._make_project_tree(tmp_path)

        # Pre-add an existing CEO_REQUEST confirm node under EA
        existing = tree.add_child(
            parent_id=ea.id, employee_id="00001",
            description="Existing confirm", acceptance_criteria=[],
        )
        existing.node_type = NodeType.CEO_REQUEST
        tree.save(tree_path)

        entry = ScheduleEntry(node_id=child.id, tree_path=str(tree_path))

        em = EmployeeManager()
        em.register("00100", MagicMock(spec=Launcher))
        em.register("00101", MagicMock(spec=Launcher))
        em.register("00001", MagicMock(spec=Launcher))

        with (
            patch.object(em, "schedule_node") as mock_schedule,
            patch.object(em, "_schedule_next") as mock_next,
            patch.object(em, "_full_cleanup", new_callable=AsyncMock),
        ):
            await em._on_child_complete("00101", entry, project_id="proj_confirm")

        # Should NOT schedule a new confirm node
        mock_schedule.assert_not_called()

        # Still only one CEO_REQUEST under EA
        reloaded = TaskTree.load(tree_path)
        ea_children = reloaded.get_children(ea.id)
        confirm_nodes = [c for c in ea_children if c.node_type == NodeType.CEO_REQUEST.value or c.node_type == NodeType.CEO_REQUEST]
        assert len(confirm_nodes) == 1

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_confirm_node_completion_triggers_cleanup(self, mock_bus, mock_state, tmp_path):
        """When a CEO_REQUEST confirm node finishes, _full_cleanup is triggered."""
        from onemancompany.core.task_lifecycle import NodeType

        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        tree, tree_path, root, ea, child = self._make_project_tree(tmp_path)

        # Add a finished confirm node under EA
        confirm = tree.add_child(
            parent_id=ea.id, employee_id="00001",
            description="Confirm project", acceptance_criteria=[],
        )
        confirm.node_type = NodeType.CEO_REQUEST
        confirm.project_id = "proj_confirm"
        confirm.project_dir = str(tmp_path / "iterations" / "iter_001")
        confirm.status = TaskPhase.FINISHED.value
        confirm.result = "Approved"
        tree.save(tree_path)

        entry = ScheduleEntry(node_id=confirm.id, tree_path=str(tree_path))

        em = EmployeeManager()
        em.register("00001", MagicMock(spec=Launcher))
        em.register("00100", MagicMock(spec=Launcher))

        with (
            patch.object(em, "_full_cleanup", new_callable=AsyncMock) as mock_cleanup,
            patch.object(em, "schedule_node") as mock_schedule,
            patch.object(em, "_schedule_next"),
        ):
            await em._on_child_complete("00001", entry, project_id="proj_confirm")

        mock_cleanup.assert_called_once()
        # Should run retrospective for project mode
        assert mock_cleanup.call_args.kwargs.get("run_retrospective") is True
        # Should NOT create another confirm node (guard prevents it)
        mock_schedule.assert_not_called()


# ---------------------------------------------------------------------------
# ScheduleEntry tests
# ---------------------------------------------------------------------------

class TestScheduleEntry:
    """Test ScheduleEntry dataclass and scheduling methods."""

    def test_schedule_entry_creation(self):
        entry = ScheduleEntry(node_id="abc123", tree_path="/tmp/tree.yaml")
        assert entry.node_id == "abc123"
        assert entry.tree_path == "/tmp/tree.yaml"

    def _mgr_with_executor(self):
        """Create an EmployeeManager with a dummy executor for 00100."""
        mgr = EmployeeManager()
        mgr.executors["00100"] = MagicMock()
        return mgr

    def test_schedule_node(self):
        mgr = self._mgr_with_executor()
        mgr.schedule_node("00100", "node1", "/tmp/tree.yaml")
        assert len(mgr._schedule["00100"]) == 1
        assert mgr._schedule["00100"][0].node_id == "node1"

    def test_schedule_multiple_nodes(self):
        mgr = self._mgr_with_executor()
        mgr.schedule_node("00100", "node1", "/tmp/tree.yaml")
        mgr.schedule_node("00100", "node2", "/tmp/tree.yaml")
        assert len(mgr._schedule["00100"]) == 2

    def test_unschedule(self):
        mgr = self._mgr_with_executor()
        mgr.schedule_node("00100", "node1", "/tmp/tree.yaml")
        mgr.schedule_node("00100", "node2", "/tmp/tree.yaml")
        mgr.unschedule("00100", "node1")
        assert len(mgr._schedule["00100"]) == 1
        assert mgr._schedule["00100"][0].node_id == "node2"

    def test_unschedule_nonexistent(self):
        mgr = self._mgr_with_executor()
        mgr.schedule_node("00100", "node1", "/tmp/tree.yaml")
        mgr.unschedule("00100", "nonexistent")
        assert len(mgr._schedule["00100"]) == 1

    def test_get_next_scheduled_with_pending_node(self, tmp_path):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00100", description="root")
        child = tree.add_child(
            parent_id=root.id, employee_id="00100",
            description="pending task", acceptance_criteria=["done"],
        )
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = self._mgr_with_executor()
        mgr.schedule_node("00100", child.id, str(tree_path))
        entry = mgr.get_next_scheduled("00100")
        assert entry is not None
        assert entry.node_id == child.id

    def test_get_next_scheduled_skips_non_pending(self, tmp_path):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00100", description="root")
        child = tree.add_child(
            parent_id=root.id, employee_id="00100",
            description="processing task", acceptance_criteria=["done"],
        )
        child.status = "processing"
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = self._mgr_with_executor()
        mgr.schedule_node("00100", child.id, str(tree_path))
        entry = mgr.get_next_scheduled("00100")
        assert entry is None

    def test_get_next_scheduled_skips_unresolved_deps(self, tmp_path):
        tree = TaskTree(project_id="proj1")
        root = tree.create_root(employee_id="00100", description="root")
        dep = tree.add_child(
            parent_id=root.id, employee_id="00100",
            description="dep task", acceptance_criteria=["done"],
        )
        dependent = tree.add_child(
            parent_id=root.id, employee_id="00100",
            description="depends on dep", acceptance_criteria=["done"],
            depends_on=[dep.id],
        )
        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        mgr = self._mgr_with_executor()
        mgr.schedule_node("00100", dependent.id, str(tree_path))
        entry = mgr.get_next_scheduled("00100")
        assert entry is None  # dep not resolved yet

    def test_get_next_scheduled_empty(self):
        mgr = EmployeeManager()
        entry = mgr.get_next_scheduled("00100")
        assert entry is None

    def test_log_node_publishes_event(self):
        """_log_node should publish WebSocket event (no in-memory buffer)."""
        mgr = EmployeeManager()
        # _log_node writes to disk + publishes event; no _task_logs buffer
        # Just verify it doesn't crash
        mgr._log_node("00100", "node1", "start", "Starting task")

    def test_schedule_entry_from_agent_loop(self):
        """ScheduleEntry should be importable from agent_loop.py."""
        from onemancompany.core.agent_loop import ScheduleEntry as SE
        assert SE is ScheduleEntry


class TestAbortProject:
    """Tests for EmployeeManager.abort_project — cancel only the target project."""

    def test_abort_cancels_only_target_project_running_task(self, tmp_path):
        """Running asyncio.Task should only be cancelled if it belongs to the target project."""
        mgr = EmployeeManager()

        # Employee has a scheduled task for proj-A (the target)
        entry_a, _, _ = _make_tree_entry(tmp_path / "a", employee_id="emp01",
                                          project_id="proj-A", status="pending")
        mgr._schedule["emp01"] = [entry_a]

        # But the *running* task is for proj-B (different project)
        entry_b, _, _ = _make_tree_entry(tmp_path / "b", employee_id="emp01",
                                          project_id="proj-B", status="processing")
        mgr._current_entries["emp01"] = entry_b

        mock_task = MagicMock()
        mock_task.done.return_value = False
        mgr._running_tasks["emp01"] = mock_task

        mgr.abort_project("proj-A")

        # Running task for proj-B must NOT be cancelled
        mock_task.cancel.assert_not_called()

    def test_abort_cancels_running_task_for_matching_project(self, tmp_path):
        """Running asyncio.Task IS cancelled when it belongs to the target project."""
        mgr = EmployeeManager()

        # Employee has a scheduled task for proj-A
        entry_a, _, _ = _make_tree_entry(tmp_path / "a", employee_id="emp01",
                                          project_id="proj-A", status="pending")
        mgr._schedule["emp01"] = [entry_a]

        # Running task is also proj-A
        entry_run, _, _ = _make_tree_entry(tmp_path / "r", employee_id="emp01",
                                            project_id="proj-A", status="processing")
        mgr._current_entries["emp01"] = entry_run

        mock_task = MagicMock()
        mock_task.done.return_value = False
        mgr._running_tasks["emp01"] = mock_task

        mgr.abort_project("proj-A")

        # Running task for proj-A SHOULD be cancelled
        mock_task.cancel.assert_called_once()

    def test_abort_does_not_cancel_other_employees(self, tmp_path):
        """Employees with no tasks for the target project are untouched."""
        mgr = EmployeeManager()

        # emp01 has tasks for proj-A (target)
        entry_a, _, _ = _make_tree_entry(tmp_path / "a", employee_id="emp01",
                                          project_id="proj-A", status="pending")
        mgr._schedule["emp01"] = [entry_a]

        # emp02 has tasks only for proj-B
        entry_b, _, _ = _make_tree_entry(tmp_path / "b", employee_id="emp02",
                                          project_id="proj-B", status="processing")
        mgr._schedule["emp02"] = [entry_b]
        mgr._current_entries["emp02"] = entry_b

        mock_task_02 = MagicMock()
        mock_task_02.done.return_value = False
        mgr._running_tasks["emp02"] = mock_task_02

        mgr.abort_project("proj-A")

        # emp02's running task must NOT be cancelled
        mock_task_02.cancel.assert_not_called()


# ---------------------------------------------------------------------------
# _on_child_complete_inner — child FAILED resumes HOLDING parent
# ---------------------------------------------------------------------------

class TestChildFailedResumesHoldingParent:
    """When a child task FAILS and its parent is HOLDING, the parent should be
    resumed so it can react (retry, reassign, or escalate)."""

    @pytest.mark.asyncio
    async def test_child_failed_resumes_holding_parent(self, tmp_path):
        """Parent in HOLDING should be resumed when child FAILS."""
        mgr = EmployeeManager()

        # Build tree: root (CEO) → EA parent (HOLDING) → worker child (FAILED)
        tree = TaskTree(project_id="proj-stuck")
        root = tree.create_root(employee_id="ceo", description="CEO prompt")
        root.node_type = "ceo_prompt"
        root.status = TaskPhase.PENDING.value

        ea_parent = tree.add_child(
            parent_id=root.id, employee_id="00002",
            description="EA manages project", acceptance_criteria=[],
        )
        ea_parent.set_status(TaskPhase.PROCESSING)
        ea_parent.set_status(TaskPhase.HOLDING)
        ea_parent.project_id = "proj-stuck"
        ea_parent.project_dir = str(tmp_path)

        worker_child = tree.add_child(
            parent_id=ea_parent.id, employee_id="emp10",
            description="Do the work", acceptance_criteria=[],
        )
        worker_child.set_status(TaskPhase.PROCESSING)
        worker_child.set_status(TaskPhase.FAILED)
        worker_child.result = "Error: connection timeout"
        worker_child.project_id = "proj-stuck"
        worker_child.project_dir = str(tmp_path)

        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        entry = ScheduleEntry(node_id=worker_child.id, tree_path=str(tree_path))

        # Schedule EA parent so resume can find it
        mgr._schedule["00002"] = [
            ScheduleEntry(node_id=ea_parent.id, tree_path=str(tree_path)),
        ]

        with patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.task_tree.save_tree_async"), \
             patch("onemancompany.core.vessel._store") as mock_store, \
             patch.object(mgr, "_publish_node_update"), \
             patch.object(mgr, "schedule_node"), \
             patch.object(mgr, "_schedule_next"):
            mock_store.save_employee_runtime = AsyncMock()

            await mgr._on_child_complete_inner("emp10", entry, project_id="proj-stuck")

        # EA parent should no longer be HOLDING — it should be re-scheduled
        # to process again with the failure context
        assert ea_parent.status != TaskPhase.HOLDING.value, \
            "Parent should not remain HOLDING after child FAILS"

    @pytest.mark.asyncio
    async def test_child_completed_does_not_trigger_failure_resume(self, tmp_path):
        """COMPLETED child should NOT trigger the failure-resume path."""
        mgr = EmployeeManager()

        tree = TaskTree(project_id="proj-ok")
        root = tree.create_root(employee_id="ceo", description="CEO prompt")
        root.node_type = "ceo_prompt"
        root.status = TaskPhase.PENDING.value

        ea_parent = tree.add_child(
            parent_id=root.id, employee_id="00002",
            description="EA manages project", acceptance_criteria=[],
        )
        ea_parent.set_status(TaskPhase.PROCESSING)
        ea_parent.set_status(TaskPhase.HOLDING)
        ea_parent.project_id = "proj-ok"
        ea_parent.project_dir = str(tmp_path)

        worker_child = tree.add_child(
            parent_id=ea_parent.id, employee_id="emp10",
            description="Do the work", acceptance_criteria=[],
        )
        worker_child.set_status(TaskPhase.PROCESSING)
        worker_child.set_status(TaskPhase.COMPLETED)
        worker_child.result = "Done successfully"
        worker_child.project_id = "proj-ok"
        worker_child.project_dir = str(tmp_path)

        tree_path = tmp_path / "task_tree.yaml"
        tree.save(tree_path)

        entry = ScheduleEntry(node_id=worker_child.id, tree_path=str(tree_path))

        mgr._schedule["00002"] = [
            ScheduleEntry(node_id=ea_parent.id, tree_path=str(tree_path)),
        ]

        with patch("onemancompany.core.task_tree.get_tree", return_value=tree), \
             patch("onemancompany.core.task_tree.save_tree_async"), \
             patch("onemancompany.core.vessel._store") as mock_store, \
             patch.object(mgr, "_publish_node_update"), \
             patch.object(mgr, "schedule_node"), \
             patch.object(mgr, "_schedule_next"), \
             patch.object(mgr, "_spawn_review_or_escalate", new_callable=AsyncMock):
            mock_store.save_employee_runtime = AsyncMock()

            await mgr._on_child_complete_inner("emp10", entry, project_id="proj-ok")

        # Parent should still be HOLDING — COMPLETED child triggers review, not failure resume
        # (it goes through Gate 2 incremental review path instead)
        assert ea_parent.status == TaskPhase.HOLDING.value, \
            "COMPLETED child should not trigger failure-resume on HOLDING parent"
