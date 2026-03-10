"""Unit tests for core/vessel.py — Vessel architecture and backward compat."""

from __future__ import annotations

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

    @patch("onemancompany.core.vessel.company_state")
    def test_vessel_ref_role(self, mock_state):
        mock_emp = MagicMock()
        mock_emp.role = "Engineer"
        mock_state.employees = {"00010": mock_emp}

        ref = _VesselRef("00010")
        assert ref.role == "Engineer"

    @patch("onemancompany.core.vessel.company_state")
    def test_vessel_ref_role_default(self, mock_state):
        mock_state.employees = {}
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
