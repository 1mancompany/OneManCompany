"""Unit tests for core/vessel.py — Vessel architecture and backward compat."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.vessel import (
    AgentTask,
    AgentTaskBoard,
    ClaudeSessionExecutor,
    EmployeeHandle,
    EmployeeManager,
    LangChainExecutor,
    Launcher,
    LaunchResult,
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
from onemancompany.core.vessel_config import VesselConfig, LimitsConfig


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

    def test_vessel_board_default(self):
        mgr = EmployeeManager()
        vessel = Vessel(mgr, "00010")
        board = vessel.board
        assert isinstance(board, AgentTaskBoard)
        assert len(board.tasks) == 0

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
# Task persistence integration
# ---------------------------------------------------------------------------

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
    async def test_execute_task_persists_processing_and_complete(self, mock_archive):
        em = self._make_manager()
        mock_launcher = em.executors["00010"]
        mock_launcher.execute = AsyncMock(return_value=LaunchResult(output="done"))

        task = AgentTask(id="t1", description="test")
        em.boards["00010"].tasks.append(task)

        # Capture status at each persist_task call since task is mutable
        captured_statuses = []
        def _capture_persist(emp_id, t):
            captured_statuses.append(t.status)

        with patch("onemancompany.core.vessel.persist_task", side_effect=_capture_persist):
            with patch("onemancompany.core.vessel.company_state") as mock_cs:
                mock_cs.employees = {}
                mock_cs.active_tasks = []
                await em._execute_task("00010", task)

        # persist_task should be called for PROCESSING and COMPLETE
        assert TaskPhase.PROCESSING in captured_statuses
        assert TaskPhase.COMPLETE in captured_statuses

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.archive_task")
    @patch("onemancompany.core.vessel.persist_task")
    async def test_execute_task_does_not_archive_on_complete(self, mock_persist, mock_archive):
        em = self._make_manager()
        mock_launcher = em.executors["00010"]
        mock_launcher.execute = AsyncMock(return_value=LaunchResult(output="done"))

        task = AgentTask(id="t2", description="test archive")
        em.boards["00010"].tasks.append(task)

        with patch("onemancompany.core.vessel.company_state") as mock_cs:
            mock_cs.employees = {}
            mock_cs.active_tasks = []
            await em._execute_task("00010", task)

        # COMPLETE is not in TERMINAL_STATES, so archive should NOT be called
        # (COMPLETE transitions to FINISHED via acceptance flow)
        assert task.status == TaskPhase.COMPLETE
        mock_archive.assert_not_called()

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.archive_task")
    async def test_execute_task_persists_and_archives_on_failure(self, mock_archive):
        em = self._make_manager()
        mock_launcher = em.executors["00010"]
        mock_launcher.execute = AsyncMock(side_effect=RuntimeError("boom"))

        task = AgentTask(id="t3", description="test fail")
        em.boards["00010"].tasks.append(task)

        # Capture status at each persist_task call since task is mutable
        captured_statuses = []
        def _capture_persist(emp_id, t):
            captured_statuses.append(t.status)

        with patch("onemancompany.core.vessel.persist_task", side_effect=_capture_persist):
            with patch("onemancompany.core.vessel.company_state") as mock_cs:
                mock_cs.employees = {}
                mock_cs.active_tasks = []
                await em._execute_task("00010", task)

        assert task.status == TaskPhase.FAILED
        # persist_task called for PROCESSING and FAILED
        assert TaskPhase.PROCESSING in captured_statuses
        assert TaskPhase.FAILED in captured_statuses
        # archive_task called for FAILED (terminal state)
        mock_archive.assert_called_once_with("00010", task)

    @patch("onemancompany.core.vessel.archive_task")
    @patch("onemancompany.core.vessel.persist_task")
    def test_abort_project_persists_and_archives(self, mock_persist, mock_archive):
        em = self._make_manager()
        board = em.boards["00010"]
        task = board.push("project task", project_id="proj_1")

        # Reset mocks after push (which also calls persist_task)
        mock_persist.reset_mock()
        mock_archive.reset_mock()

        em.abort_project("proj_1")

        assert task.status == TaskPhase.CANCELLED
        mock_persist.assert_called_once_with("00010", task)
        mock_archive.assert_called_once_with("00010", task)


# ---------------------------------------------------------------------------
# restore_persisted_tasks
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Review prompt in _on_child_complete
# ---------------------------------------------------------------------------

class TestOnChildCompleteReviewPrompt:
    """Verify _on_child_complete builds correct review prompts with rejection context."""

    @pytest.mark.asyncio
    async def test_review_prompt_skips_accepted_children(self):
        """Already-accepted children listed separately, not in review list."""
        from onemancompany.core.task_tree import TaskNode, TaskTree

        tree = TaskTree(project_id="proj1")
        tree.create_root(employee_id="00100", description="parent task")
        root_id = tree.root_id

        # Child 1: already accepted
        child1 = tree.add_child(
            parent_id=root_id, employee_id="00101",
            description="accepted subtask", acceptance_criteria=["c1"],
        )
        child1.status = "accepted"
        child1.result = "done"
        child1.acceptance_result = {"passed": True, "notes": "good"}

        # Child 2: completed, needs review
        child2 = tree.add_child(
            parent_id=root_id, employee_id="00102",
            description="needs review subtask", acceptance_criteria=["c2"],
        )
        child2.status = "completed"
        child2.result = "also done"

        # Map a task ID for child2
        tree.task_id_map["child2-task"] = child2.id

        em = EmployeeManager()
        em.register("00100", MagicMock(spec=Launcher))
        em.register("00102", MagicMock(spec=Launcher))

        task = AgentTask(id="child2-task", description="needs review subtask",
                         project_dir="/tmp/proj", project_id="proj1")
        task.result = "also done"

        with (
            patch("onemancompany.core.vessel._load_project_tree", return_value=tree),
            patch("onemancompany.core.vessel._save_project_tree"),
            patch("onemancompany.core.vessel.company_state") as mock_cs,
        ):
            mock_cs.employees = {}
            mock_cs.active_tasks = []
            await em._on_child_complete("00102", task, project_id="proj1")

        # Parent's board should have a review task
        board = em.boards.get("00100")
        assert board is not None
        assert len(board.tasks) == 1

        prompt = board.tasks[0].description
        # Already-accepted child listed with checkmark
        assert "\u2713" in prompt
        assert "accepted subtask" in prompt
        # Needs-review child listed with criteria
        assert "needs review subtask" in prompt
        assert "c2" in prompt

    @pytest.mark.asyncio
    async def test_review_prompt_shows_rejection_history(self):
        """Previously rejected children show rejection reason in review prompt."""
        from onemancompany.core.task_tree import TaskNode, TaskTree

        tree = TaskTree(project_id="proj1")
        tree.create_root(employee_id="00100", description="parent task")
        root_id = tree.root_id

        # Child that was previously rejected and re-completed
        child = tree.add_child(
            parent_id=root_id, employee_id="00103",
            description="retried subtask", acceptance_criteria=["works"],
        )
        child.status = "completed"
        child.result = "second attempt result"
        child.acceptance_result = {"passed": False, "notes": "tests were failing"}

        tree.task_id_map["retry-task"] = child.id

        em = EmployeeManager()
        em.register("00100", MagicMock(spec=Launcher))
        em.register("00103", MagicMock(spec=Launcher))

        task = AgentTask(id="retry-task", description="retried subtask",
                         project_dir="/tmp/proj", project_id="proj1")
        task.result = "second attempt result"

        with (
            patch("onemancompany.core.vessel._load_project_tree", return_value=tree),
            patch("onemancompany.core.vessel._save_project_tree"),
            patch("onemancompany.core.vessel.company_state") as mock_cs,
        ):
            mock_cs.employees = {}
            mock_cs.active_tasks = []
            await em._on_child_complete("00103", task, project_id="proj1")

        board = em.boards.get("00100")
        assert board is not None
        prompt = board.tasks[0].description
        # Should show rejection warning
        assert "\u26a0" in prompt
        assert "tests were failing" in prompt

    @pytest.mark.asyncio
    async def test_review_prompt_no_previously_accepted(self):
        """When no children are accepted yet, prompt lists all for review without accepted section."""
        from onemancompany.core.task_tree import TaskNode, TaskTree

        tree = TaskTree(project_id="proj1")
        tree.create_root(employee_id="00100", description="parent task")
        root_id = tree.root_id

        child = tree.add_child(
            parent_id=root_id, employee_id="00104",
            description="only subtask", acceptance_criteria=["done"],
        )
        child.status = "pending"  # will be overwritten to "completed" by _on_child_complete

        tree.task_id_map["single-task"] = child.id

        em = EmployeeManager()
        em.register("00100", MagicMock(spec=Launcher))
        em.register("00104", MagicMock(spec=Launcher))

        task = AgentTask(id="single-task", description="only subtask",
                         project_dir="/tmp/proj", project_id="proj1")
        task.result = "all done"

        with (
            patch("onemancompany.core.vessel._load_project_tree", return_value=tree),
            patch("onemancompany.core.vessel._save_project_tree"),
            patch("onemancompany.core.vessel.company_state") as mock_cs,
        ):
            mock_cs.employees = {}
            mock_cs.active_tasks = []
            await em._on_child_complete("00104", task, project_id="proj1")

        board = em.boards.get("00100")
        assert board is not None
        prompt = board.tasks[0].description
        # Should NOT have checkmark section (no previously accepted)
        assert "\u2713" not in prompt
        # Should list the subtask for review
        assert "only subtask" in prompt
        assert "以下子任务需要审核" in prompt


class TestRestorePersistedTasks:
    """Verify EmployeeManager.restore_persisted_tasks loads from disk."""

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

    @patch("onemancompany.core.vessel.load_all_active_tasks")
    def test_restores_multiple_employees(self, mock_load):
        em = EmployeeManager()
        em.register("00010", MagicMock(spec=Launcher))
        em.register("00020", MagicMock(spec=Launcher))

        mock_load.return_value = {
            "00010": [AgentTask(id="a1", description="Task A")],
            "00020": [AgentTask(id="b1", description="Task B"), AgentTask(id="b2", description="Task C")],
        }

        count = em.restore_persisted_tasks()
        assert count == 3
        assert len(em.boards["00010"].tasks) == 1
        assert len(em.boards["00020"].tasks) == 2


# ---------------------------------------------------------------------------
# CEO confirmation gate before retrospective
# ---------------------------------------------------------------------------

class TestCeoConfirmation:
    """Test CEO confirmation gate before project retrospective."""

    def _make_tree_with_root(self):
        """Create a TaskTree with root and one completed child."""
        from onemancompany.core.task_tree import TaskTree

        tree = TaskTree(project_id="proj_ceo")
        tree.create_root(employee_id="00100", description="root task")
        child = tree.add_child(
            parent_id=tree.root_id, employee_id="00101",
            description="child subtask", acceptance_criteria=["done"],
        )
        child.status = "accepted"
        child.result = "child done"
        return tree

    @pytest.mark.asyncio
    async def test_root_complete_sends_ceo_report(self):
        """Root node completion should call _request_ceo_confirmation, not _full_cleanup."""
        tree = self._make_tree_with_root()
        tree.task_id_map["root-task"] = tree.root_id

        em = EmployeeManager()
        em.register("00100", MagicMock(spec=Launcher))

        task = AgentTask(
            id="root-task", description="root task",
            project_dir="/tmp/proj", project_id="proj_ceo",
            task_type="project",
        )
        task.result = "project done"

        with (
            patch("onemancompany.core.vessel._load_project_tree", return_value=tree),
            patch("onemancompany.core.vessel._save_project_tree"),
            patch.object(em, "_request_ceo_confirmation", new_callable=AsyncMock) as mock_confirm,
            patch.object(em, "_full_cleanup", new_callable=AsyncMock) as mock_cleanup,
        ):
            await em._on_child_complete("00100", task, project_id="proj_ceo")

        # Should call _request_ceo_confirmation, NOT _full_cleanup
        mock_confirm.assert_called_once()
        mock_cleanup.assert_not_called()

    @pytest.mark.asyncio
    async def test_ceo_approve_triggers_retrospective(self):
        """CEO approve should call _full_cleanup with run_retrospective=True for project tasks."""
        tree = self._make_tree_with_root()
        root_node = tree.get_node(tree.root_id)

        em = EmployeeManager()
        em.register("00100", MagicMock(spec=Launcher))

        task = AgentTask(
            id="root-task", description="root task",
            project_dir="/tmp/proj", project_id="proj_ceo",
            task_type="project",
        )

        # Pre-set the _ceo_pending entry to auto-resolve with "approve"
        import onemancompany.agents.common_tools as ct
        key = ct._ceo_wait_key("00100", "proj_ceo")

        async def _auto_approve():
            """Simulate CEO approving after a short delay."""
            await asyncio.sleep(0.01)
            ct.resolve_ceo_pending("00100", "proj_ceo", {"action": "approve", "message": ""})

        with (
            patch("onemancompany.core.vessel.event_bus") as mock_bus,
            patch("onemancompany.core.vessel.company_state") as mock_cs,
            patch.object(em, "_full_cleanup", new_callable=AsyncMock) as mock_cleanup,
        ):
            mock_bus.publish = AsyncMock()
            mock_cs.employees = {}

            # Start auto-approve concurrently
            import asyncio
            approve_task = asyncio.create_task(_auto_approve())
            await em._request_ceo_confirmation("00100", task, tree, root_node, "proj_ceo")
            await approve_task

        # Should call _full_cleanup with run_retrospective=True
        mock_cleanup.assert_called_once()
        call_kwargs = mock_cleanup.call_args
        assert call_kwargs.kwargs.get("run_retrospective") is True

    @pytest.mark.asyncio
    async def test_ceo_revise_pushes_task(self):
        """CEO revise should push revision task to root employee, not cleanup."""
        tree = self._make_tree_with_root()
        root_node = tree.get_node(tree.root_id)

        em = EmployeeManager()
        em.register("00100", MagicMock(spec=Launcher))

        task = AgentTask(
            id="root-task", description="root task",
            project_dir="/tmp/proj", project_id="proj_ceo",
            task_type="project",
        )

        import onemancompany.agents.common_tools as ct

        async def _auto_revise():
            await asyncio.sleep(0.01)
            ct.resolve_ceo_pending("00100", "proj_ceo", {
                "action": "revise",
                "message": "请修改报告格式",
            })

        with (
            patch("onemancompany.core.vessel.event_bus") as mock_bus,
            patch("onemancompany.core.vessel.company_state") as mock_cs,
            patch.object(em, "_full_cleanup", new_callable=AsyncMock) as mock_cleanup,
            patch.object(em, "_schedule_next") as mock_schedule,
        ):
            mock_bus.publish = AsyncMock()
            mock_cs.employees = {}

            import asyncio
            revise_task = asyncio.create_task(_auto_revise())
            await em._request_ceo_confirmation("00100", task, tree, root_node, "proj_ceo")
            await revise_task

        # Should NOT call _full_cleanup
        mock_cleanup.assert_not_called()
        # Should push a revision task
        board = em.boards.get("00100")
        assert board is not None
        assert len(board.tasks) == 1
        assert "请修改报告格式" in board.tasks[0].description
        assert "CEO要求修改" in board.tasks[0].description
        # Should schedule next
        mock_schedule.assert_called_once_with("00100")

    @pytest.mark.asyncio
    async def test_ceo_timeout_auto_approves(self):
        """If CEO doesn't respond in time, auto-approve and run cleanup."""
        tree = self._make_tree_with_root()
        root_node = tree.get_node(tree.root_id)

        em = EmployeeManager()
        em.register("00100", MagicMock(spec=Launcher))

        task = AgentTask(
            id="root-task", description="root task",
            project_dir="/tmp/proj", project_id="proj_ceo",
            task_type="project",
        )

        with (
            patch("onemancompany.core.vessel.event_bus") as mock_bus,
            patch("onemancompany.core.vessel.company_state") as mock_cs,
            patch.object(em, "_full_cleanup", new_callable=AsyncMock) as mock_cleanup,
            # Mock asyncio.wait_for to immediately raise TimeoutError
            patch("onemancompany.core.vessel.asyncio.wait_for", side_effect=TimeoutError),
        ):
            mock_bus.publish = AsyncMock()
            mock_cs.employees = {}

            await em._request_ceo_confirmation("00100", task, tree, root_node, "proj_ceo")

        # Should auto-approve and call _full_cleanup with retrospective
        mock_cleanup.assert_called_once()
        call_kwargs = mock_cleanup.call_args
        assert call_kwargs.kwargs.get("run_retrospective") is True
