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
        assert "以下子任务需要审核" in prompt


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

        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)
        return tree, tree_path

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_root_complete_sends_ceo_report(self, mock_bus, mock_state, tmp_path):
        """Root node completion should call _request_ceo_confirmation, not _full_cleanup."""
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

        with (
            patch.object(em, "_request_ceo_confirmation", new_callable=AsyncMock) as mock_confirm,
            patch.object(em, "_full_cleanup", new_callable=AsyncMock) as mock_cleanup,
        ):
            await em._on_child_complete("00100", entry, project_id="proj_ceo")

        # Should call _request_ceo_confirmation, NOT _full_cleanup
        mock_confirm.assert_called_once()
        mock_cleanup.assert_not_called()

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_ceo_confirmation_auto_approves(self, mock_bus, mock_state, tmp_path):
        """_request_ceo_confirmation should auto-approve and call _full_cleanup with retrospective."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}

        tree, tree_path = self._make_tree_with_root(tmp_path)
        root_node = tree.get_node(tree.root_id)
        tree.save(tree_path)

        entry = ScheduleEntry(node_id=root_node.id, tree_path=str(tree_path))

        em = EmployeeManager()
        em.register("00100", MagicMock(spec=Launcher))

        with (
            patch.object(em, "_full_cleanup", new_callable=AsyncMock) as mock_cleanup,
        ):
            await em._request_ceo_confirmation("00100", root_node, tree, entry, "proj_ceo")

        # Should call _full_cleanup with run_retrospective=True (auto-approve, no blocking)
        mock_cleanup.assert_called_once()
        call_kwargs = mock_cleanup.call_args
        assert call_kwargs.kwargs.get("run_retrospective") is True


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

    def test_task_logs_buffer(self):
        mgr = EmployeeManager()
        mgr._log_node("00100", "node1", "start", "Starting task")
        assert len(mgr._task_logs["node1"]) == 1
        assert mgr._task_logs["node1"][0]["type"] == "start"

    def test_schedule_entry_from_agent_loop(self):
        """ScheduleEntry should be importable from agent_loop.py."""
        from onemancompany.core.agent_loop import ScheduleEntry as SE
        assert SE is ScheduleEntry
