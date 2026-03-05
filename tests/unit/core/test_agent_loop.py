"""Unit tests for core/agent_loop.py — EmployeeManager task dispatch system."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.agent_loop import (
    AgentTask,
    AgentTaskBoard,
    ClaudeSessionLauncher,
    EmployeeHandle,
    EmployeeManager,
    LaunchResult,
    LangChainLauncher,
    Launcher,
    ScriptLauncher,
    TaskContext,
    _AgentRef,
    _append_progress,
    _load_progress,
    agent_loops,
    get_agent_loop,
    register_agent,
    register_self_hosted,
    start_all_loops,
    stop_all_loops,
    register_and_start_agent,
    PROGRESS_LOG_MAX_LINES,
    MAX_RETRIES,
    RETRY_DELAYS,
)


# ---------------------------------------------------------------------------
# AgentTask
# ---------------------------------------------------------------------------

class TestAgentTask:
    def test_creation_defaults(self):
        task = AgentTask(id="abc123", description="Do something")
        assert task.id == "abc123"
        assert task.description == "Do something"
        assert task.status == "pending"
        assert task.parent_id == ""
        assert task.project_id == ""
        assert task.project_dir == ""
        assert task.sub_task_ids == []
        assert task.logs == []
        assert task.result == ""
        assert task.created_at != ""  # auto-set by __post_init__
        assert task.completed_at == ""
        assert task.input_tokens == 0
        assert task.output_tokens == 0
        assert task.total_tokens == 0
        assert task.estimated_cost_usd == 0.0

    def test_creation_with_values(self):
        task = AgentTask(
            id="xyz",
            description="Build feature",
            status="in_progress",
            parent_id="parent1",
            project_id="proj1",
            project_dir="/tmp/proj",
            created_at="2024-01-01T00:00:00",
        )
        assert task.status == "in_progress"
        assert task.parent_id == "parent1"
        assert task.project_id == "proj1"
        assert task.project_dir == "/tmp/proj"
        assert task.created_at == "2024-01-01T00:00:00"

    def test_post_init_sets_created_at(self):
        before = datetime.now().isoformat()
        task = AgentTask(id="t1", description="test")
        after = datetime.now().isoformat()
        assert before <= task.created_at <= after

    def test_post_init_preserves_explicit_created_at(self):
        task = AgentTask(id="t1", description="test", created_at="2020-01-01")
        assert task.created_at == "2020-01-01"

    def test_to_dict(self):
        task = AgentTask(
            id="t1",
            description="Build widget",
            status="completed",
            parent_id="p1",
            project_id="proj1",
            original_project_id="orig1",
            sub_task_ids=["s1", "s2"],
            result="Done!",
            created_at="2024-01-01T00:00:00",
            completed_at="2024-01-01T01:00:00",
            model_used="gpt-4",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            estimated_cost_usd=0.01,
        )
        d = task.to_dict()
        assert d["id"] == "t1"
        assert d["description"] == "Build widget"
        assert d["status"] == "completed"
        assert d["parent_id"] == "p1"
        assert d["project_id"] == "proj1"
        assert d["original_project_id"] == "orig1"
        assert d["sub_task_ids"] == ["s1", "s2"]
        assert d["result"] == "Done!"
        assert d["created_at"] == "2024-01-01T00:00:00"
        assert d["completed_at"] == "2024-01-01T01:00:00"
        assert d["model_used"] == "gpt-4"
        assert d["input_tokens"] == 100
        assert d["output_tokens"] == 50
        assert d["total_tokens"] == 150
        assert d["estimated_cost_usd"] == 0.01

    def test_to_dict_truncates_logs_to_50(self):
        task = AgentTask(id="t1", description="test")
        task.logs = [{"timestamp": "t", "type": "log", "content": f"entry {i}"} for i in range(80)]
        d = task.to_dict()
        assert len(d["logs"]) == 50

    def test_to_dict_truncates_result(self):
        task = AgentTask(id="t1", description="test", result="x" * 1000)
        d = task.to_dict()
        assert len(d["result"]) <= 300  # MAX_SUMMARY_LEN

    def test_status_transitions(self):
        task = AgentTask(id="t1", description="test")
        assert task.status == "pending"
        task.status = "in_progress"
        assert task.status == "in_progress"
        task.status = "completed"
        assert task.status == "completed"

    def test_status_failed(self):
        task = AgentTask(id="t1", description="test")
        task.status = "failed"
        assert task.status == "failed"

    def test_status_cancelled(self):
        task = AgentTask(id="t1", description="test")
        task.status = "cancelled"
        assert task.status == "cancelled"


# ---------------------------------------------------------------------------
# AgentTaskBoard
# ---------------------------------------------------------------------------

class TestAgentTaskBoard:
    def test_push_creates_task(self):
        board = AgentTaskBoard()
        task = board.push("Do something")
        assert task.description == "Do something"
        assert task.status == "pending"
        assert len(task.id) == 12
        assert len(board.tasks) == 1

    def test_push_with_project_info(self):
        board = AgentTaskBoard()
        task = board.push("Build it", project_id="proj1", project_dir="/tmp/proj")
        assert task.project_id == "proj1"
        assert task.project_dir == "/tmp/proj"

    def test_push_subtask_links_parent(self):
        board = AgentTaskBoard()
        parent = board.push("Parent task")
        child = board.push("Child task", parent_id=parent.id)
        assert child.parent_id == parent.id
        assert child.id in parent.sub_task_ids

    def test_push_subtask_with_missing_parent(self):
        board = AgentTaskBoard()
        child = board.push("Orphan child", parent_id="nonexistent")
        assert child.parent_id == "nonexistent"
        assert len(board.tasks) == 1

    def test_get_next_pending_returns_first_top_level(self):
        board = AgentTaskBoard()
        t1 = board.push("First")
        t2 = board.push("Second")
        result = board.get_next_pending()
        assert result is t1

    def test_get_next_pending_skips_non_pending(self):
        board = AgentTaskBoard()
        t1 = board.push("First")
        t1.status = "completed"
        t2 = board.push("Second")
        result = board.get_next_pending()
        assert result is t2

    def test_get_next_pending_skips_subtasks(self):
        board = AgentTaskBoard()
        parent = board.push("Parent")
        parent.status = "in_progress"
        child = board.push("Child", parent_id=parent.id)
        result = board.get_next_pending()
        assert result is None  # child is a subtask, not top-level

    def test_get_next_pending_empty_board(self):
        board = AgentTaskBoard()
        assert board.get_next_pending() is None

    def test_get_pending_subtasks(self):
        board = AgentTaskBoard()
        parent = board.push("Parent")
        c1 = board.push("Child 1", parent_id=parent.id)
        c2 = board.push("Child 2", parent_id=parent.id)
        c3 = board.push("Child 3", parent_id=parent.id)
        c2.status = "completed"
        pending = board.get_pending_subtasks(parent.id)
        assert len(pending) == 2
        assert c1 in pending
        assert c3 in pending

    def test_get_pending_subtasks_none(self):
        board = AgentTaskBoard()
        parent = board.push("Parent")
        assert board.get_pending_subtasks(parent.id) == []

    def test_cancel_by_project(self):
        board = AgentTaskBoard()
        t1 = board.push("Task 1", project_id="proj1")
        t2 = board.push("Task 2", project_id="proj1")
        t3 = board.push("Task 3", project_id="proj2")
        t2.status = "completed"  # already completed, should not be cancelled
        cancelled = board.cancel_by_project("proj1")
        assert len(cancelled) == 1
        assert t1 in cancelled
        assert t1.status == "cancelled"
        assert t1.result == "Cancelled by CEO"
        assert t2.status == "completed"  # not changed
        assert t3.status == "pending"  # different project

    def test_cancel_by_project_cancels_subtasks(self):
        board = AgentTaskBoard()
        parent = board.push("Parent", project_id="proj1")
        child = board.push("Child", project_id="proj1", parent_id=parent.id)
        cancelled = board.cancel_by_project("proj1")
        assert len(cancelled) == 2
        assert parent.status == "cancelled"
        assert child.status == "cancelled"
        assert child.result == "Parent task cancelled"

    def test_cancel_by_project_no_match(self):
        board = AgentTaskBoard()
        board.push("Task", project_id="proj1")
        cancelled = board.cancel_by_project("nonexistent")
        assert cancelled == []

    def test_get_task_found(self):
        board = AgentTaskBoard()
        t = board.push("Test")
        assert board.get_task(t.id) is t

    def test_get_task_not_found(self):
        board = AgentTaskBoard()
        assert board.get_task("nonexistent") is None

    def test_to_dict(self):
        board = AgentTaskBoard()
        board.push("Task 1")
        board.push("Task 2")
        result = board.to_dict()
        assert len(result) == 2
        assert result[0]["description"] == "Task 1"
        assert result[1]["description"] == "Task 2"


# ---------------------------------------------------------------------------
# LaunchResult / TaskContext
# ---------------------------------------------------------------------------

class TestLaunchResult:
    def test_defaults(self):
        r = LaunchResult()
        assert r.output == ""
        assert r.model_used == ""
        assert r.input_tokens == 0
        assert r.output_tokens == 0
        assert r.total_tokens == 0

    def test_with_values(self):
        r = LaunchResult(output="ok", model_used="gpt-4", input_tokens=10, output_tokens=5, total_tokens=15)
        assert r.output == "ok"
        assert r.total_tokens == 15


class TestTaskContext:
    def test_defaults(self):
        ctx = TaskContext()
        assert ctx.project_id == ""
        assert ctx.work_dir == ""
        assert ctx.employee_id == ""

    def test_with_values(self):
        ctx = TaskContext(project_id="p1", work_dir="/tmp", employee_id="e1")
        assert ctx.project_id == "p1"


# ---------------------------------------------------------------------------
# _AgentRef
# ---------------------------------------------------------------------------

class TestAgentRef:
    def test_employee_id(self):
        ref = _AgentRef("00010")
        assert ref.employee_id == "00010"

    @patch("onemancompany.core.agent_loop.company_state")
    def test_role_from_state(self, mock_state):
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"00010": emp}
        ref = _AgentRef("00010")
        assert ref.role == "Engineer"

    @patch("onemancompany.core.agent_loop.company_state")
    def test_role_missing_employee(self, mock_state):
        mock_state.employees = {}
        ref = _AgentRef("00099")
        assert ref.role == "Employee"


# ---------------------------------------------------------------------------
# LangChainLauncher
# ---------------------------------------------------------------------------

class TestLangChainLauncher:
    @pytest.mark.asyncio
    async def test_execute_calls_agent(self):
        runner = MagicMock()
        runner.run_streamed = AsyncMock(return_value="Task done")
        runner._last_usage = {
            "model": "claude-3",
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
        }
        launcher = LangChainLauncher(runner)
        ctx = TaskContext(project_id="p1", employee_id="e1")
        result = await launcher.execute("Do something", ctx)
        runner.run_streamed.assert_called_once()
        assert result.output == "Task done"
        assert result.model_used == "claude-3"
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert result.total_tokens == 150

    @pytest.mark.asyncio
    async def test_execute_no_usage(self):
        runner = MagicMock()
        runner.run_streamed = AsyncMock(return_value="Done")
        # No _last_usage attribute
        del runner._last_usage
        launcher = LangChainLauncher(runner)
        ctx = TaskContext()
        result = await launcher.execute("Do it", ctx)
        assert result.output == "Done"
        assert result.model_used == ""
        assert result.total_tokens == 0

    @pytest.mark.asyncio
    async def test_execute_none_result(self):
        runner = MagicMock()
        runner.run_streamed = AsyncMock(return_value=None)
        runner._last_usage = {}
        launcher = LangChainLauncher(runner)
        ctx = TaskContext()
        result = await launcher.execute("Do it", ctx)
        assert result.output == ""

    def test_is_ready(self):
        runner = MagicMock()
        launcher = LangChainLauncher(runner)
        assert launcher.is_ready() is True


# ---------------------------------------------------------------------------
# ClaudeSessionLauncher
# ---------------------------------------------------------------------------

class TestClaudeSessionLauncher:
    @pytest.mark.asyncio
    async def test_execute(self):
        launcher = ClaudeSessionLauncher("emp01")
        ctx = TaskContext(project_id="proj1", work_dir="/tmp/work")
        with patch("onemancompany.core.claude_session.run_claude_session", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "Claude output"
            on_log = MagicMock()
            result = await launcher.execute("Do task", ctx, on_log=on_log)
            mock_run.assert_called_once_with("emp01", "proj1", prompt="Do task", work_dir="/tmp/work")
            assert result.output == "Claude output"
            on_log.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_default_project(self):
        launcher = ClaudeSessionLauncher("emp01")
        ctx = TaskContext()  # empty project_id
        with patch("onemancompany.core.claude_session.run_claude_session", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "output"
            result = await launcher.execute("Do task", ctx)
            mock_run.assert_called_once_with("emp01", "default", prompt="Do task", work_dir="")
            assert result.output == "output"

    @pytest.mark.asyncio
    async def test_execute_none_output(self):
        launcher = ClaudeSessionLauncher("emp01")
        ctx = TaskContext(project_id="p1")
        with patch("onemancompany.core.claude_session.run_claude_session", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = None
            result = await launcher.execute("Do task", ctx)
            assert result.output == ""

    @pytest.mark.asyncio
    async def test_execute_no_log_callback(self):
        launcher = ClaudeSessionLauncher("emp01")
        ctx = TaskContext(project_id="p1")
        with patch("onemancompany.core.claude_session.run_claude_session", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "output"
            result = await launcher.execute("Do task", ctx, on_log=None)
            assert result.output == "output"

    def test_is_ready(self):
        launcher = ClaudeSessionLauncher("emp01")
        assert launcher.is_ready() is True


# ---------------------------------------------------------------------------
# ScriptLauncher
# ---------------------------------------------------------------------------

class TestScriptLauncher:
    def test_default_script_path(self):
        launcher = ScriptLauncher("emp01")
        assert "emp01" in launcher.script_path
        assert launcher.script_path.endswith("launch.sh")

    def test_custom_script_path(self):
        launcher = ScriptLauncher("emp01", script_path="/custom/run.sh")
        assert launcher.script_path == "/custom/run.sh"

    @pytest.mark.asyncio
    async def test_execute_success(self):
        launcher = ScriptLauncher("emp01", script_path="/tmp/test.sh")
        ctx = TaskContext(project_id="proj1", work_dir="/tmp")

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"hello output", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            with patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(b"hello output", b"")):
                on_log = MagicMock()
                result = await launcher.execute("task desc", ctx, on_log=on_log)
                assert result.output == "hello output"
                on_log.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        launcher = ScriptLauncher("emp01", script_path="/tmp/test.sh")
        ctx = TaskContext(project_id="proj1", work_dir="/tmp")

        mock_proc = AsyncMock()

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            with patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=asyncio.TimeoutError):
                result = await launcher.execute("task desc", ctx)
                assert "[script timeout]" in result.output

    @pytest.mark.asyncio
    async def test_execute_exception(self):
        launcher = ScriptLauncher("emp01", script_path="/tmp/test.sh")
        ctx = TaskContext(project_id="proj1", work_dir="/tmp")

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, side_effect=OSError("No such file")):
            result = await launcher.execute("task desc", ctx)
            assert "[script error]" in result.output
            assert "No such file" in result.output

    def test_is_ready(self):
        launcher = ScriptLauncher("emp01")
        assert launcher.is_ready() is True


# ---------------------------------------------------------------------------
# EmployeeHandle
# ---------------------------------------------------------------------------

class TestEmployeeHandle:
    def test_creation(self):
        mgr = EmployeeManager()
        handle = EmployeeHandle(mgr, "emp01")
        assert handle.employee_id == "emp01"
        assert handle.agent.employee_id == "emp01"

    def test_board_returns_existing(self):
        mgr = EmployeeManager()
        board = AgentTaskBoard()
        mgr.boards["emp01"] = board
        handle = EmployeeHandle(mgr, "emp01")
        assert handle.board is board

    def test_board_returns_new_if_missing(self):
        mgr = EmployeeManager()
        handle = EmployeeHandle(mgr, "emp01")
        board = handle.board
        assert isinstance(board, AgentTaskBoard)
        assert len(board.tasks) == 0

    def test_task_history_returns_existing(self):
        mgr = EmployeeManager()
        mgr.task_histories["emp01"] = [{"task": "t1"}]
        handle = EmployeeHandle(mgr, "emp01")
        assert handle.task_history == [{"task": "t1"}]

    def test_task_history_returns_empty_if_missing(self):
        mgr = EmployeeManager()
        handle = EmployeeHandle(mgr, "emp01")
        assert handle.task_history == []

    @patch.object(EmployeeManager, "push_task")
    def test_push_task_delegates_to_manager(self, mock_push):
        mgr = EmployeeManager()
        mock_push.return_value = AgentTask(id="t1", description="test")
        handle = EmployeeHandle(mgr, "emp01")
        result = handle.push_task("Do something", project_id="proj1", project_dir="/tmp")
        mock_push.assert_called_once_with(
            "emp01", "Do something",
            project_id="proj1", project_dir="/tmp",
        )
        assert result.id == "t1"

    @patch.object(EmployeeManager, "get_history_context")
    def test_get_history_context_delegates(self, mock_ctx):
        mgr = EmployeeManager()
        mock_ctx.return_value = "some context"
        handle = EmployeeHandle(mgr, "emp01")
        assert handle.get_history_context() == "some context"
        mock_ctx.assert_called_once_with("emp01")


# ---------------------------------------------------------------------------
# Progress log helpers
# ---------------------------------------------------------------------------

class TestProgressLog:
    def test_append_progress(self, tmp_path):
        with patch("onemancompany.core.agent_loop.EMPLOYEES_DIR", tmp_path):
            _append_progress("emp01", "Did something")
            log_path = tmp_path / "emp01" / "progress.log"
            assert log_path.exists()
            content = log_path.read_text()
            assert "Did something" in content

    def test_append_progress_creates_dir(self, tmp_path):
        with patch("onemancompany.core.agent_loop.EMPLOYEES_DIR", tmp_path):
            _append_progress("newguy", "First task")
            assert (tmp_path / "newguy" / "progress.log").exists()

    def test_load_progress_empty(self, tmp_path):
        with patch("onemancompany.core.agent_loop.EMPLOYEES_DIR", tmp_path):
            result = _load_progress("emp01")
            assert result == ""

    def test_load_progress_reads_lines(self, tmp_path):
        with patch("onemancompany.core.agent_loop.EMPLOYEES_DIR", tmp_path):
            log_dir = tmp_path / "emp01"
            log_dir.mkdir()
            log_path = log_dir / "progress.log"
            lines = [f"[2024-01-01T00:00:{i:02d}] Entry {i}\n" for i in range(10)]
            log_path.write_text("".join(lines))
            result = _load_progress("emp01")
            assert "Entry 0" in result
            assert "Entry 9" in result

    def test_load_progress_truncates_to_max_lines(self, tmp_path):
        with patch("onemancompany.core.agent_loop.EMPLOYEES_DIR", tmp_path):
            log_dir = tmp_path / "emp01"
            log_dir.mkdir()
            log_path = log_dir / "progress.log"
            lines = [f"[2024-01-01T00:00:00] Entry {i}\n" for i in range(100)]
            log_path.write_text("".join(lines))
            result = _load_progress("emp01", max_lines=5)
            result_lines = result.strip().split("\n")
            assert len(result_lines) == 5
            assert "Entry 95" in result
            assert "Entry 99" in result


# ---------------------------------------------------------------------------
# EmployeeManager — Registration
# ---------------------------------------------------------------------------

class TestEmployeeManagerRegistration:
    def test_register(self):
        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        handle = mgr.register("emp01", launcher)
        assert isinstance(handle, EmployeeHandle)
        assert handle.employee_id == "emp01"
        assert mgr.launchers["emp01"] is launcher
        assert "emp01" in mgr.boards
        assert "emp01" in mgr.task_histories
        assert mgr._handles["emp01"] is handle

    def test_register_preserves_existing_board(self):
        mgr = EmployeeManager()
        board = AgentTaskBoard()
        board.push("Existing task")
        mgr.boards["emp01"] = board
        launcher = MagicMock(spec=Launcher)
        mgr.register("emp01", launcher)
        assert mgr.boards["emp01"] is board
        assert len(mgr.boards["emp01"].tasks) == 1

    def test_register_hooks(self):
        mgr = EmployeeManager()
        hooks = {"pre_task": lambda t, c: t, "post_task": lambda t, r: None}
        mgr.register_hooks("emp01", hooks)
        assert mgr._hooks["emp01"] is hooks

    def test_unregister(self):
        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("emp01", launcher)
        mgr.register_hooks("emp01", {"pre_task": lambda t, c: t})
        mgr.unregister("emp01")
        assert "emp01" not in mgr.launchers
        assert "emp01" not in mgr._handles
        assert "emp01" not in mgr._hooks

    def test_unregister_nonexistent(self):
        mgr = EmployeeManager()
        mgr.unregister("nonexistent")  # should not raise

    def test_get_handle(self):
        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        handle = mgr.register("emp01", launcher)
        assert mgr.get_handle("emp01") is handle

    def test_get_handle_missing(self):
        mgr = EmployeeManager()
        assert mgr.get_handle("nonexistent") is None


# ---------------------------------------------------------------------------
# EmployeeManager — push_task
# ---------------------------------------------------------------------------

class TestEmployeeManagerPushTask:
    def test_push_task_creates_task(self):
        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("emp01", launcher)
        with patch.object(mgr, "_publish_task_update"):
            with patch.object(mgr, "_schedule_next"):
                task = mgr.push_task("emp01", "Do something", project_id="proj1")
                assert task.description == "Do something"
                assert task.project_id == "proj1"
                assert len(mgr.boards["emp01"].tasks) == 1

    def test_push_task_auto_creates_board(self):
        mgr = EmployeeManager()
        with patch.object(mgr, "_publish_task_update"):
            with patch.object(mgr, "_schedule_next"):
                task = mgr.push_task("newguy", "Do something")
                assert "newguy" in mgr.boards
                assert task.description == "Do something"

    def test_push_task_calls_schedule(self):
        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("emp01", launcher)
        with patch.object(mgr, "_publish_task_update"):
            with patch.object(mgr, "_schedule_next") as mock_sched:
                mgr.push_task("emp01", "Do something")
                mock_sched.assert_called_once_with("emp01")

    def test_push_task_publishes_update(self):
        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("emp01", launcher)
        with patch.object(mgr, "_publish_task_update") as mock_pub:
            with patch.object(mgr, "_schedule_next"):
                task = mgr.push_task("emp01", "Do something")
                mock_pub.assert_called_once_with("emp01", task)


# ---------------------------------------------------------------------------
# EmployeeManager — _schedule_next
# ---------------------------------------------------------------------------

class TestEmployeeManagerScheduleNext:
    def test_schedule_next_does_nothing_if_running(self):
        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("emp01", launcher)
        mgr._running_tasks["emp01"] = MagicMock()
        mgr.boards["emp01"].push("Task")
        # Should not create new task because one is already running
        mgr._schedule_next("emp01")
        # The running_tasks should still have only the mock
        assert isinstance(mgr._running_tasks["emp01"], MagicMock)

    def test_schedule_next_no_board(self):
        mgr = EmployeeManager()
        mgr._schedule_next("nobody")  # should not raise

    @patch("onemancompany.core.agent_loop.company_state")
    def test_schedule_next_no_pending_sets_idle(self, mock_state):
        mgr = EmployeeManager()
        emp = MagicMock()
        mock_state.employees = {"emp01": emp}
        mgr.boards["emp01"] = AgentTaskBoard()  # empty board
        mgr._schedule_next("emp01")
        assert emp.status == "idle"


# ---------------------------------------------------------------------------
# EmployeeManager — _execute_task (mocked end-to-end)
# ---------------------------------------------------------------------------

class TestEmployeeManagerExecuteTask:
    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_execute_task_happy_path(self, mock_append, mock_load, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Task done!"))
        handle = mgr.register("emp01", launcher)

        task = AgentTask(id="t1", description="Build widget")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", task)

        assert task.status == "completed"
        assert task.result == "Task done!"
        assert task.completed_at != ""
        launcher.execute.assert_called_once()

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_execute_task_failure_retries(self, mock_append, mock_load, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(side_effect=RuntimeError("API down"))
        handle = mgr.register("emp01", launcher)

        task = AgentTask(id="t1", description="Build widget")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.agent_loop.asyncio.sleep", new_callable=AsyncMock):
            with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
                await mgr._execute_task("emp01", task)

        assert task.status == "failed"
        assert "Error" in task.result
        assert launcher.execute.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_execute_task_no_launcher_raises(self, mock_append, mock_load, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        # Register but then remove the launcher to simulate missing launcher
        mgr.boards["emp01"] = AgentTaskBoard()
        mgr._handles["emp01"] = EmployeeHandle(mgr, "emp01")
        mgr.task_histories["emp01"] = []

        task = AgentTask(id="t1", description="Build widget")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", task)

        assert task.status == "failed"
        assert "No launcher" in task.result

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="Previous work here")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_execute_task_injects_progress(self, mock_append, mock_load, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Done"))
        mgr.register("emp01", launcher)

        task = AgentTask(id="t1", description="Build widget")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", task)

        # Verify progress was injected into the task description
        call_args = launcher.execute.call_args
        task_with_ctx = call_args[0][0]
        assert "Previous Work Learnings" in task_with_ctx
        assert "Previous work here" in task_with_ctx

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_execute_task_pre_hook_modifies_description(self, mock_append, mock_load, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Done"))
        mgr.register("emp01", launcher)
        mgr.register_hooks("emp01", {"pre_task": lambda desc, ctx: "Modified: " + desc})

        task = AgentTask(id="t1", description="Original task")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", task)

        call_args = launcher.execute.call_args
        task_desc = call_args[0][0]
        assert task_desc.startswith("Modified: ")

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_execute_task_post_hook_called(self, mock_append, mock_load, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        post_hook = MagicMock()
        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Done"))
        mgr.register("emp01", launcher)
        mgr.register_hooks("emp01", {"post_task": post_hook})

        task = AgentTask(id="t1", description="Do task")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", task)

        post_hook.assert_called_once_with(task, "Done")

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_execute_task_with_project_dir(self, mock_append, mock_load, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Done"))
        mgr.register("emp01", launcher)

        task = AgentTask(id="t1", description="Build", project_dir="/tmp/workspace")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", task)

        call_args = launcher.execute.call_args
        task_desc = call_args[0][0]
        assert "Project workspace: /tmp/workspace" in task_desc

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_execute_task_records_token_usage(self, mock_append, mock_load, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(
            output="Done",
            model_used="gpt-4",
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
        ))
        mgr.register("emp01", launcher)

        task = AgentTask(id="t1", description="Build widget")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            with patch("onemancompany.core.model_costs.get_model_cost", return_value={"input": 10.0, "output": 30.0}):
                await mgr._execute_task("emp01", task)

        assert task.model_used == "gpt-4"
        assert task.input_tokens == 1000
        assert task.output_tokens == 500
        assert task.total_tokens == 1500
        assert task.estimated_cost_usd > 0

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_execute_task_cancelled_task_stays_cancelled(self, mock_append, mock_load, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)

        async def fake_execute(desc, ctx, on_log=None):
            # Simulate cancellation during execution
            task.status = "cancelled"
            return LaunchResult(output="partial")

        launcher.execute = AsyncMock(side_effect=fake_execute)
        mgr.register("emp01", launcher)

        task = AgentTask(id="t1", description="Build widget")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", task)

        assert task.status == "cancelled"


# ---------------------------------------------------------------------------
# EmployeeManager — _run_task (scheduling chain)
# ---------------------------------------------------------------------------

class TestEmployeeManagerRunTask:
    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_run_task_cleans_up_and_schedules_next(self, mock_append, mock_load, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Done"))
        mgr.register("emp01", launcher)

        task = AgentTask(id="t1", description="Task 1")
        mgr.boards["emp01"].tasks.append(task)
        mgr._running_tasks["emp01"] = MagicMock()

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._run_task("emp01", task)

        # After _run_task, the running task should be removed
        assert "emp01" not in mgr._running_tasks


# ---------------------------------------------------------------------------
# EmployeeManager — Task history
# ---------------------------------------------------------------------------

class TestEmployeeManagerTaskHistory:
    def test_append_history(self):
        mgr = EmployeeManager()
        task = AgentTask(
            id="t1", description="Built feature X",
            result="Feature X is done", completed_at="2024-01-01T12:00:00",
        )
        mgr._append_history("emp01", task)
        history = mgr.task_histories["emp01"]
        assert len(history) == 1
        assert history[0]["task"] == "Built feature X"
        assert history[0]["result"] == "Feature X is done"
        assert history[0]["completed_at"] == "2024-01-01T12:00:00"

    def test_get_history_context_empty(self):
        mgr = EmployeeManager()
        assert mgr.get_history_context("emp01") == ""

    def test_get_history_context_with_entries(self):
        mgr = EmployeeManager()
        mgr.task_histories["emp01"] = [
            {"task": "Task A", "result": "Result A", "completed_at": "2024-01-01T12:00:00"},
            {"task": "Task B", "result": "Result B", "completed_at": "2024-01-02T12:00:00"},
        ]
        ctx = mgr.get_history_context("emp01")
        assert "Recent Work History" in ctx
        assert "Task A" in ctx
        assert "Result B" in ctx

    def test_get_history_context_with_summary(self):
        mgr = EmployeeManager()
        mgr._history_summaries["emp01"] = "Earlier: built many features"
        mgr.task_histories["emp01"] = [
            {"task": "Task C", "result": "Result C", "completed_at": "2024-01-03T12:00:00"},
        ]
        ctx = mgr.get_history_context("emp01")
        assert "Earlier work summary" in ctx
        assert "built many features" in ctx
        assert "Task C" in ctx


# ---------------------------------------------------------------------------
# EmployeeManager — Helpers
# ---------------------------------------------------------------------------

class TestEmployeeManagerHelpers:
    @patch("onemancompany.core.agent_loop.company_state")
    def test_get_role_found(self, mock_state):
        emp = MagicMock()
        emp.role = "COO"
        mock_state.employees = {"emp01": emp}
        mgr = EmployeeManager()
        assert mgr._get_role("emp01") == "COO"

    @patch("onemancompany.core.agent_loop.company_state")
    def test_get_role_missing(self, mock_state):
        mock_state.employees = {}
        mgr = EmployeeManager()
        assert mgr._get_role("nobody") == "Employee"

    @patch("onemancompany.core.agent_loop.company_state")
    def test_set_employee_status(self, mock_state):
        emp = MagicMock()
        mock_state.employees = {"emp01": emp}
        mgr = EmployeeManager()
        mgr._set_employee_status("emp01", "working")
        assert emp.status == "working"

    @patch("onemancompany.core.agent_loop.company_state")
    def test_set_employee_status_missing(self, mock_state):
        mock_state.employees = {}
        mgr = EmployeeManager()
        mgr._set_employee_status("nobody", "working")  # should not raise

    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    def test_log_appends_to_task(self, mock_bus, mock_state):
        mock_state.employees = {}
        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test")
        mgr._log("emp01", task, "info", "Something happened")
        assert len(task.logs) == 1
        assert task.logs[0]["type"] == "info"
        assert task.logs[0]["content"] == "Something happened"

    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    def test_publish_task_update_no_event_loop(self, mock_bus, mock_state):
        mock_state.employees = {}
        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test")
        # Should not raise even without event loop
        mgr._publish_task_update("emp01", task)


# ---------------------------------------------------------------------------
# EmployeeManager — _execute_subtask
# ---------------------------------------------------------------------------

class TestEmployeeManagerExecuteSubtask:
    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_execute_subtask_success(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Sub done"))
        mgr.register("emp01", launcher)

        parent = AgentTask(id="p1", description="Parent")
        sub = AgentTask(id="s1", description="Sub task", parent_id="p1")
        mgr.boards["emp01"].tasks.extend([parent, sub])
        parent.sub_task_ids.append("s1")

        await mgr._execute_subtask("emp01", sub, depth=1)

        assert sub.status == "completed"
        assert sub.result == "Sub done"
        assert sub.completed_at != ""

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_execute_subtask_max_depth(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("emp01", launcher)

        sub = AgentTask(id="s1", description="Deep sub task")
        mgr.boards["emp01"].tasks.append(sub)

        await mgr._execute_subtask("emp01", sub, depth=3)  # > MAX_SUBTASK_DEPTH

        assert sub.status == "failed"
        assert "Max sub-task depth" in sub.result

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_execute_subtask_cancelled(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("emp01", launcher)

        sub = AgentTask(id="s1", description="Cancelled sub", status="cancelled")
        mgr.boards["emp01"].tasks.append(sub)

        await mgr._execute_subtask("emp01", sub, depth=1)

        assert sub.status == "cancelled"
        launcher.execute.assert_not_called()

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_execute_subtask_failure(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(side_effect=RuntimeError("boom"))
        mgr.register("emp01", launcher)

        sub = AgentTask(id="s1", description="Bad sub")
        mgr.boards["emp01"].tasks.append(sub)

        with patch("onemancompany.core.agent_loop.asyncio.sleep", new_callable=AsyncMock):
            await mgr._execute_subtask("emp01", sub, depth=1)

        assert sub.status == "failed"
        assert "Error" in sub.result


# ---------------------------------------------------------------------------
# EmployeeManager — _completion_check
# ---------------------------------------------------------------------------

class TestEmployeeManagerCompletionCheck:
    @pytest.mark.asyncio
    async def test_completion_check_no_subtasks(self):
        mgr = EmployeeManager()
        mgr.boards["emp01"] = AgentTaskBoard()
        task = AgentTask(id="t1", description="Main task")
        mgr.boards["emp01"].tasks.append(task)
        result = await mgr._completion_check("emp01", task)
        assert result is True

    @pytest.mark.asyncio
    async def test_completion_check_complete(self):
        mgr = EmployeeManager()
        mgr.boards["emp01"] = AgentTaskBoard()

        task = AgentTask(id="t1", description="Main task", sub_task_ids=["s1"])
        sub = AgentTask(id="s1", description="Sub", status="completed", result="Done", parent_id="t1")
        mgr.boards["emp01"].tasks.extend([task, sub])

        mock_result = MagicMock()
        mock_result.content = "COMPLETE"

        with patch("onemancompany.agents.base.make_llm") as mock_make:
            with patch("onemancompany.agents.base.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result):
                result = await mgr._completion_check("emp01", task)
                assert result is True

    @pytest.mark.asyncio
    async def test_completion_check_incomplete_adds_subtasks(self):
        mgr = EmployeeManager()
        mgr.boards["emp01"] = AgentTaskBoard()

        task = AgentTask(id="t1", description="Main task", sub_task_ids=["s1"])
        sub = AgentTask(id="s1", description="Sub", status="completed", result="Partial", parent_id="t1")
        mgr.boards["emp01"].tasks.extend([task, sub])

        mock_result = MagicMock()
        mock_result.content = 'INCOMPLETE\n[{"description": "Additional work needed"}]'

        with patch("onemancompany.agents.base.make_llm") as mock_make:
            with patch("onemancompany.agents.base.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result):
                result = await mgr._completion_check("emp01", task)
                assert result is False
                # New subtask should be added
                assert len(mgr.boards["emp01"].tasks) == 3

    @pytest.mark.asyncio
    async def test_completion_check_error_returns_true(self):
        mgr = EmployeeManager()
        mgr.boards["emp01"] = AgentTaskBoard()

        task = AgentTask(id="t1", description="Main task", sub_task_ids=["s1"])
        sub = AgentTask(id="s1", description="Sub", status="completed", result="Done", parent_id="t1")
        mgr.boards["emp01"].tasks.extend([task, sub])

        with patch("onemancompany.agents.base.make_llm", side_effect=RuntimeError("LLM error")):
            result = await mgr._completion_check("emp01", task)
            assert result is True  # error -> treat as complete


# ---------------------------------------------------------------------------
# EmployeeManager — Acceptance/review/rectification task pushing
# ---------------------------------------------------------------------------

class TestEmployeeManagerAcceptanceTasks:
    def test_push_acceptance_task(self):
        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("officer01", launcher)

        with patch.object(mgr, "_publish_task_update"):
            with patch.object(mgr, "_schedule_next"):
                mgr._push_acceptance_task(
                    "officer01", "proj1", "/tmp/proj",
                    ["Criteria 1", "Criteria 2"],
                    {"task": "Build app", "timeline": []},
                )

        board = mgr.boards["officer01"]
        assert len(board.tasks) == 1
        assert "验收" in board.tasks[0].description
        assert "Criteria 1" in board.tasks[0].description

    def test_push_acceptance_task_no_handle(self, capsys):
        mgr = EmployeeManager()
        mgr._push_acceptance_task(
            "nonexistent", "proj1", "/tmp",
            ["Criteria"], {"task": "task", "timeline": []},
        )
        captured = capsys.readouterr()
        assert "WARNING" in captured.out

    def test_push_ea_review_task(self):
        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("ea01", launcher)

        with patch.object(mgr, "_publish_task_update"):
            with patch.object(mgr, "_schedule_next"):
                mgr._push_ea_review_task(
                    "ea01", "proj1", "/tmp/proj",
                    ["Criteria 1"],
                    {"notes": "Looks good", "officer_id": "officer01"},
                    {"task": "Build app", "timeline": []},
                )

        board = mgr.boards["ea01"]
        assert len(board.tasks) == 1
        assert "CEO" in board.tasks[0].description or "EA" in board.tasks[0].description

    def test_push_rectification_task(self):
        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("officer01", launcher)

        with patch.object(mgr, "_publish_task_update"):
            with patch.object(mgr, "_schedule_next"):
                mgr._push_rectification_task(
                    "officer01", "proj1", "/tmp/proj",
                    ["Criteria 1"],
                    "Quality too low",
                    {"task": "Build app"},
                )

        board = mgr.boards["officer01"]
        assert len(board.tasks) == 1
        assert "整改" in board.tasks[0].description
        assert "Quality too low" in board.tasks[0].description


# ---------------------------------------------------------------------------
# Backward-compatible API functions
# ---------------------------------------------------------------------------

class TestBackwardCompatAPI:
    def test_register_agent(self):
        runner = MagicMock()
        with patch("onemancompany.core.agent_loop.employee_manager") as mock_mgr:
            mock_mgr.register.return_value = MagicMock(spec=EmployeeHandle)
            handle = register_agent("emp01", runner)
            mock_mgr.register.assert_called_once()
            call_args = mock_mgr.register.call_args
            assert call_args[0][0] == "emp01"
            assert isinstance(call_args[0][1], LangChainLauncher)

    def test_register_self_hosted(self):
        with patch("onemancompany.core.agent_loop.employee_manager") as mock_mgr:
            mock_mgr.register.return_value = MagicMock(spec=EmployeeHandle)
            handle = register_self_hosted("emp01")
            mock_mgr.register.assert_called_once()
            call_args = mock_mgr.register.call_args
            assert call_args[0][0] == "emp01"
            assert isinstance(call_args[0][1], ClaudeSessionLauncher)

    def test_get_agent_loop(self):
        with patch("onemancompany.core.agent_loop.employee_manager") as mock_mgr:
            mock_handle = MagicMock(spec=EmployeeHandle)
            mock_mgr.get_handle.return_value = mock_handle
            result = get_agent_loop("emp01")
            mock_mgr.get_handle.assert_called_once_with("emp01")
            assert result is mock_handle

    def test_get_agent_loop_missing(self):
        with patch("onemancompany.core.agent_loop.employee_manager") as mock_mgr:
            mock_mgr.get_handle.return_value = None
            result = get_agent_loop("nobody")
            assert result is None

    @pytest.mark.asyncio
    async def test_start_all_loops_is_noop(self):
        await start_all_loops()  # should not raise

    @pytest.mark.asyncio
    async def test_stop_all_loops_cancels_tasks(self):
        from onemancompany.core.agent_loop import employee_manager as real_mgr

        async def dummy_coro():
            await asyncio.sleep(100)

        loop = asyncio.get_running_loop()
        dummy = loop.create_task(dummy_coro())
        real_mgr._running_tasks["test_emp"] = dummy
        try:
            await stop_all_loops()
            assert dummy.cancelled() or dummy.done()
        finally:
            real_mgr._running_tasks.pop("test_emp", None)
            if not dummy.done():
                dummy.cancel()
                try:
                    await dummy
                except asyncio.CancelledError:
                    pass

    @pytest.mark.asyncio
    async def test_register_and_start_agent(self):
        runner = MagicMock()
        with patch("onemancompany.core.agent_loop.register_agent") as mock_reg:
            mock_reg.return_value = MagicMock(spec=EmployeeHandle)
            handle = await register_and_start_agent("emp01", runner)
            mock_reg.assert_called_once_with("emp01", runner)

    def test_agent_loops_alias(self):
        # agent_loops should be the same dict as employee_manager._handles
        from onemancompany.core.agent_loop import employee_manager
        assert agent_loops is employee_manager._handles


# ---------------------------------------------------------------------------
# EmployeeManager — GraphRecursionError handling
# ---------------------------------------------------------------------------

class TestEmployeeManagerGraphRecursionError:
    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_graph_recursion_error_no_retry(self, mock_append, mock_load, mock_bus, mock_state):
        from langgraph.errors import GraphRecursionError

        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(side_effect=GraphRecursionError("Recursion limit"))
        mgr.register("emp01", launcher)

        task = AgentTask(id="t1", description="Recursive task")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", task)

        assert task.status == "failed"
        # GraphRecursionError should NOT be retried — only 1 call
        assert launcher.execute.call_count == 1


# ---------------------------------------------------------------------------
# EmployeeManager — Pre-task hook failure handling
# ---------------------------------------------------------------------------

class TestEmployeeManagerHookFailures:
    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_pre_hook_failure_continues(self, mock_append, mock_load, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Done"))
        mgr.register("emp01", launcher)

        def bad_pre_hook(desc, ctx):
            raise RuntimeError("Hook failed!")

        mgr.register_hooks("emp01", {"pre_task": bad_pre_hook})

        task = AgentTask(id="t1", description="Test task")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", task)

        # Task should still complete despite pre-hook failure
        assert task.status == "completed"

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_post_hook_failure_does_not_crash(self, mock_append, mock_load, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Done"))
        mgr.register("emp01", launcher)

        def bad_post_hook(task, result):
            raise RuntimeError("Post hook boom!")

        mgr.register_hooks("emp01", {"post_task": bad_post_hook})

        task = AgentTask(id="t1", description="Test task")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", task)

        # Task should still be completed despite post-hook failure
        assert task.status == "completed"


# ---------------------------------------------------------------------------
# AgentTask — additional edge cases for to_dict
# ---------------------------------------------------------------------------

class TestAgentTaskToDict:
    def test_to_dict_empty_result(self):
        task = AgentTask(id="t1", description="test", result="")
        d = task.to_dict()
        assert d["result"] == ""

    def test_to_dict_logs_under_50(self):
        task = AgentTask(id="t1", description="test")
        task.logs = [{"timestamp": "t", "type": "log", "content": f"entry {i}"} for i in range(10)]
        d = task.to_dict()
        assert len(d["logs"]) == 10


# ---------------------------------------------------------------------------
# ScriptLauncher — error code with no stdout
# ---------------------------------------------------------------------------

class TestScriptLauncherErrorCode:
    @pytest.mark.asyncio
    async def test_execute_nonzero_exit_no_stdout(self):
        """When returncode != 0 and stdout is empty, stderr is used."""
        launcher = ScriptLauncher("emp01", script_path="/tmp/test.sh")
        ctx = TaskContext(project_id="proj1", work_dir="/tmp")

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"some error"))
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            with patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(b"", b"some error")):
                result = await launcher.execute("task desc", ctx)
                assert "[script error]" in result.output
                assert "some error" in result.output

    @pytest.mark.asyncio
    async def test_execute_nonzero_exit_with_stdout(self):
        """When returncode != 0 but stdout has content, stdout is preferred."""
        launcher = ScriptLauncher("emp01", script_path="/tmp/test.sh")
        ctx = TaskContext(project_id="proj1", work_dir="/tmp")

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"output content", b"some error"))
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            with patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(b"output content", b"some error")):
                result = await launcher.execute("task desc", ctx)
                assert result.output == "output content"

    @pytest.mark.asyncio
    async def test_execute_no_on_log(self):
        """When on_log is None it shouldn't crash."""
        launcher = ScriptLauncher("emp01", script_path="/tmp/test.sh")
        ctx = TaskContext(project_id="proj1", work_dir="/tmp")

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"hello", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            with patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(b"hello", b"")):
                result = await launcher.execute("task desc", ctx, on_log=None)
                assert result.output == "hello"

    @pytest.mark.asyncio
    async def test_execute_default_work_dir(self):
        """When context.work_dir is empty, uses employee dir as cwd."""
        launcher = ScriptLauncher("emp01", script_path="/tmp/test.sh")
        ctx = TaskContext(project_id="proj1", work_dir="")

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc) as mock_exec:
            with patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(b"ok", b"")):
                result = await launcher.execute("task desc", ctx)
                assert result.output == "ok"
                # cwd should be the employee dir, not empty
                call_kwargs = mock_exec.call_args
                assert "emp01" in str(call_kwargs)


# ---------------------------------------------------------------------------
# Progress log — error handling
# ---------------------------------------------------------------------------

class TestProgressLogErrors:
    def test_load_progress_file_error(self, tmp_path):
        """When the progress file can't be read, return empty string."""
        with patch("onemancompany.core.agent_loop.EMPLOYEES_DIR", tmp_path):
            log_dir = tmp_path / "emp01"
            log_dir.mkdir()
            log_path = log_dir / "progress.log"
            log_path.write_text("some content")
            # Make the file unreadable by patching read_text
            with patch.object(type(log_path), "read_text", side_effect=PermissionError("denied")):
                result = _load_progress("emp01")
                assert result == ""


# ---------------------------------------------------------------------------
# EmployeeManager — _schedule_next creates asyncio task
# ---------------------------------------------------------------------------

class TestEmployeeManagerScheduleNextWithLoop:
    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_schedule_next_creates_task(self, mock_bus, mock_state):
        """When event loop is running and there's a pending task, _schedule_next creates a task."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Done"))
        mgr.register("emp01", launcher)

        task = AgentTask(id="t1", description="Test")
        mgr.boards["emp01"].tasks.append(task)

        # Call _schedule_next which should create an asyncio.Task
        mgr._schedule_next("emp01")

        assert "emp01" in mgr._running_tasks
        # Clean up
        mgr._running_tasks["emp01"].cancel()
        try:
            await mgr._running_tasks["emp01"]
        except (asyncio.CancelledError, Exception):
            pass
        mgr._running_tasks.pop("emp01", None)

    def test_schedule_next_no_event_loop(self):
        """When no event loop is running, _schedule_next should not raise."""
        mgr = EmployeeManager()
        mgr.boards["emp01"] = AgentTaskBoard()
        mgr.boards["emp01"].push("Test task")
        # No event loop running — should gracefully handle RuntimeError
        mgr._schedule_next("emp01")


# ---------------------------------------------------------------------------
# EmployeeManager — _execute_task with project_id tracking
# ---------------------------------------------------------------------------

class TestEmployeeManagerExecuteTaskWithProject:
    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_execute_task_creates_task_entry(self, mock_append, mock_load, mock_bus, mock_state):
        """When a task has project_id, a TaskEntry is appended to active_tasks."""
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Done"))
        mgr.register("emp01", launcher)

        task = AgentTask(id="t1", description="Build widget", project_id="proj1")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            with patch.object(mgr, "_post_task_cleanup", new_callable=AsyncMock):
                await mgr._execute_task("emp01", task)

        assert task.status == "completed"
        # TaskEntry should have been appended
        assert len(mock_state.active_tasks) == 1

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_execute_task_with_project_context(self, mock_append, mock_load, mock_bus, mock_state):
        """When task has project_id, project history context is injected."""
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Done"))
        mgr.register("emp01", launcher)

        task = AgentTask(id="t1", description="Build widget", project_id="proj1")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            with patch.object(mgr, "_get_project_history_context", return_value="[Project Context]") as mock_ctx:
                with patch.object(mgr, "_get_project_workflow_context", return_value="[Workflow]") as mock_wf:
                    with patch.object(mgr, "_post_task_cleanup", new_callable=AsyncMock):
                        await mgr._execute_task("emp01", task)

        call_args = launcher.execute.call_args
        task_desc = call_args[0][0]
        assert "[Project Context]" in task_desc
        assert "[Workflow]" in task_desc


# ---------------------------------------------------------------------------
# EmployeeManager — _execute_subtask with token tracking
# ---------------------------------------------------------------------------

class TestEmployeeManagerSubtaskTokenTracking:
    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_subtask_token_accumulation(self, mock_bus, mock_state):
        """Sub-task tokens should accumulate on the parent task."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(
            output="Sub done",
            model_used="gpt-4",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
        ))
        mgr.register("emp01", launcher)

        parent = AgentTask(id="p1", description="Parent", model_used="gpt-4")
        sub = AgentTask(id="s1", description="Sub task", parent_id="p1")
        mgr.boards["emp01"].tasks.extend([parent, sub])
        parent.sub_task_ids.append("s1")

        with patch("onemancompany.core.model_costs.get_model_cost", return_value={"input": 10.0, "output": 30.0}):
            await mgr._execute_subtask("emp01", sub, depth=1)

        assert sub.status == "completed"
        assert sub.input_tokens == 100
        assert sub.output_tokens == 50
        assert sub.total_tokens == 150
        # Parent should accumulate child tokens
        assert parent.input_tokens == 100
        assert parent.output_tokens == 50
        assert parent.total_tokens == 150
        assert parent.estimated_cost_usd > 0

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_subtask_no_launcher(self, mock_bus, mock_state):
        """Sub-task with missing launcher should fail."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}

        mgr = EmployeeManager()
        mgr.boards["emp01"] = AgentTaskBoard()
        mgr._handles["emp01"] = EmployeeHandle(mgr, "emp01")

        sub = AgentTask(id="s1", description="Sub task")
        mgr.boards["emp01"].tasks.append(sub)

        await mgr._execute_subtask("emp01", sub, depth=1)

        assert sub.status == "failed"
        assert "No launcher" in sub.result


# ---------------------------------------------------------------------------
# EmployeeManager — _maybe_compress_history
# ---------------------------------------------------------------------------

class TestEmployeeManagerCompressHistory:
    @pytest.mark.asyncio
    async def test_compress_not_triggered_when_small(self):
        """History under limits should not trigger compression."""
        mgr = EmployeeManager()
        mgr.task_histories["emp01"] = [
            {"task": "Task A", "result": "Done", "completed_at": "2024-01-01"},
        ]
        await mgr._maybe_compress_history("emp01")
        # Should still have the same entries
        assert len(mgr.task_histories["emp01"]) == 1
        assert "emp01" not in mgr._history_summaries

    @pytest.mark.asyncio
    async def test_compress_triggered_when_large(self):
        """When history is large enough, compression should run."""
        mgr = EmployeeManager()
        # Create enough history to trigger compression
        mgr.task_histories["emp01"] = [
            {"task": f"Task {i}" * 50, "result": f"Result {i}" * 50, "completed_at": f"2024-01-{i:02d}"}
            for i in range(1, 20)
        ]
        mock_result = MagicMock()
        mock_result.content = "Summary of work done"

        with patch("onemancompany.agents.base.make_llm"):
            with patch("onemancompany.agents.base.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result):
                await mgr._maybe_compress_history("emp01")

        # History should be trimmed
        assert len(mgr.task_histories["emp01"]) < 19
        assert mgr._history_summaries["emp01"] == "Summary of work done"

    @pytest.mark.asyncio
    async def test_compress_handles_llm_error(self):
        """When LLM fails during compression, fallback to concatenation."""
        mgr = EmployeeManager()
        mgr.task_histories["emp01"] = [
            {"task": f"Task {i}" * 50, "result": f"Result {i}" * 50, "completed_at": f"2024-01-{i:02d}"}
            for i in range(1, 20)
        ]

        with patch("onemancompany.agents.base.make_llm", side_effect=RuntimeError("LLM down")):
            await mgr._maybe_compress_history("emp01")

        # Fallback: summary should be set from raw text
        assert "emp01" in mgr._history_summaries
        assert len(mgr._history_summaries["emp01"]) <= 800

    @pytest.mark.asyncio
    async def test_compress_with_existing_summary(self):
        """When there's an existing summary, it's included in the compression prompt."""
        mgr = EmployeeManager()
        mgr._history_summaries["emp01"] = "Previous work summary"
        mgr.task_histories["emp01"] = [
            {"task": f"Task {i}" * 50, "result": f"Result {i}" * 50, "completed_at": f"2024-01-{i:02d}"}
            for i in range(1, 20)
        ]
        mock_result = MagicMock()
        mock_result.content = "Updated summary"

        with patch("onemancompany.agents.base.make_llm"):
            with patch("onemancompany.agents.base.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_result) as mock_invoke:
                await mgr._maybe_compress_history("emp01")

        assert mgr._history_summaries["emp01"] == "Updated summary"
        # The prompt should include "Previous summary"
        call_args = mock_invoke.call_args
        prompt = call_args[0][1]
        assert "Previous summary" in prompt


# ---------------------------------------------------------------------------
# EmployeeManager — _get_project_history_context
# ---------------------------------------------------------------------------

class TestEmployeeManagerProjectHistoryContext:
    def test_returns_empty_for_v1_project(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_v1", return_value=True):
            with patch("onemancompany.core.project_archive._is_iteration", return_value=False):
                result = mgr._get_project_history_context("20240101_120000_abc")
                assert result == ""

    def test_returns_empty_for_auto_project(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_v1", return_value=False):
            with patch("onemancompany.core.project_archive._is_iteration", return_value=False):
                result = mgr._get_project_history_context("_auto_12345")
                assert result == ""

    def test_returns_empty_for_iteration_no_project(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_iteration", return_value=True):
            with patch("onemancompany.core.project_archive._find_project_for_iteration", return_value=None):
                result = mgr._get_project_history_context("iter_001")
                assert result == ""

    def test_returns_empty_for_missing_project(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_iteration", return_value=False):
            with patch("onemancompany.core.project_archive._is_v1", return_value=False):
                with patch("onemancompany.core.project_archive.load_named_project", return_value=None):
                    result = mgr._get_project_history_context("my-project")
                    assert result == ""

    def test_returns_empty_no_iterations_no_files(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_iteration", return_value=False):
            with patch("onemancompany.core.project_archive._is_v1", return_value=False):
                with patch("onemancompany.core.project_archive.load_named_project", return_value={
                    "iterations": [], "name": "Test", "status": "active"
                }):
                    with patch("onemancompany.core.project_archive.list_project_files", return_value=[]):
                        result = mgr._get_project_history_context("my-project")
                        assert result == ""

    def test_returns_context_with_iterations(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_iteration", return_value=False):
            with patch("onemancompany.core.project_archive._is_v1", return_value=False):
                with patch("onemancompany.core.project_archive.load_named_project", return_value={
                    "iterations": ["iter_001"], "name": "Test Project", "status": "active"
                }):
                    with patch("onemancompany.core.project_archive.list_project_files", return_value=[]):
                        with patch("onemancompany.core.project_archive.load_iteration", return_value={
                            "iteration_id": "iter_001", "status": "completed",
                            "task": "Build widget", "output": "Widget built",
                            "timeline": [{"time": "2024-01-01T12:00:00", "employee_id": "emp01", "action": "started", "detail": "Begin"}],
                            "cost": {"actual_cost_usd": 0.05, "budget_estimate_usd": 1.0, "token_usage": {"input": 1000, "output": 500}},
                            "acceptance_criteria": ["Works correctly"],
                        }):
                            result = mgr._get_project_history_context("my-project")
                            assert "Project Context" in result
                            assert "Test Project" in result
                            assert "iter_001" in result
                            assert "Build widget" in result

    def test_returns_context_with_files(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_iteration", return_value=False):
            with patch("onemancompany.core.project_archive._is_v1", return_value=False):
                with patch("onemancompany.core.project_archive.load_named_project", return_value={
                    "iterations": [], "name": "Test Project", "status": "active"
                }):
                    with patch("onemancompany.core.project_archive.list_project_files", return_value=["file1.py", "file2.txt"]):
                        with patch("onemancompany.core.project_archive.get_project_workspace", return_value="/tmp/workspace"):
                            result = mgr._get_project_history_context("my-project")
                            assert "Workspace files" in result
                            assert "file1.py" in result

    def test_handles_iteration_project_id(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_iteration", return_value=True):
            with patch("onemancompany.core.project_archive._find_project_for_iteration", return_value="my-project"):
                with patch("onemancompany.core.project_archive.load_named_project", return_value={
                    "iterations": ["iter_001", "iter_002"], "name": "Test", "status": "active"
                }):
                    with patch("onemancompany.core.project_archive.list_project_files", return_value=[]):
                        with patch("onemancompany.core.project_archive.load_iteration", return_value={
                            "iteration_id": "iter_001", "status": "completed",
                            "task": "Build it", "output": "Done",
                            "timeline": [], "cost": {"actual_cost_usd": 0.0, "budget_estimate_usd": 0.0, "token_usage": {}},
                        }):
                            # current iter is iter_002, so only iter_001 should appear
                            result = mgr._get_project_history_context("iter_002")
                            assert "Project Context" in result


# ---------------------------------------------------------------------------
# EmployeeManager — _get_project_workflow_context
# ---------------------------------------------------------------------------

class TestEmployeeManagerWorkflowContext:
    @patch("onemancompany.core.agent_loop.company_state")
    def test_manager_coo_gets_manager_guide(self, mock_state):
        emp = MagicMock()
        emp.role = "COO"
        mock_state.employees = {"emp01": emp}

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="proj1")
        result = mgr._get_project_workflow_context("emp01", task)
        assert "Manager Execution Guide" in result

    @patch("onemancompany.core.agent_loop.company_state")
    def test_manager_cso_gets_manager_guide(self, mock_state):
        emp = MagicMock()
        emp.role = "CSO"
        mock_state.employees = {"emp01": emp}

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="proj1")
        result = mgr._get_project_workflow_context("emp01", task)
        assert "Manager Execution Guide" in result

    @patch("onemancompany.core.agent_loop.company_state")
    def test_engineer_gets_verification_instructions(self, mock_state):
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="proj1")

        with patch("onemancompany.core.config.load_workflows", return_value={}):
            result = mgr._get_project_workflow_context("emp01", task)
            assert "Self-Verification" in result
            assert "sandbox_execute_code" in result

    @patch("onemancompany.core.agent_loop.company_state")
    def test_engineer_with_workflow_verification(self, mock_state):
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="proj1")

        mock_wf_doc = "# Workflow\n## 1. Execution\n- Build and run the code\n- Verify output"
        mock_wf = MagicMock()
        mock_step = MagicMock()
        mock_step.title = "Execution Phase"
        mock_step.instructions = ["Build and run the code", "Check output"]
        mock_wf.steps = [mock_step]

        with patch("onemancompany.core.config.load_workflows", return_value={"project_intake_workflow": mock_wf_doc}):
            with patch("onemancompany.core.workflow_engine.parse_workflow", return_value=mock_wf):
                result = mgr._get_project_workflow_context("emp01", task)
                assert "Self-Verification" in result
                assert "Build and run the code" in result

    @patch("onemancompany.core.agent_loop.company_state")
    def test_missing_employee_uses_default(self, mock_state):
        mock_state.employees = {}

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="proj1")

        with patch("onemancompany.core.config.load_workflows", return_value={}):
            result = mgr._get_project_workflow_context("nobody", task)
            assert "Self-Verification" in result

    @patch("onemancompany.core.agent_loop.company_state")
    def test_hr_is_manager_but_not_coo_cso(self, mock_state):
        """HR is a manager role but not COO/CSO, so should get verification guide."""
        emp = MagicMock()
        emp.role = "HR"
        mock_state.employees = {"emp01": emp}

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="proj1")

        with patch("onemancompany.core.config.load_workflows", return_value={}):
            result = mgr._get_project_workflow_context("emp01", task)
            assert "Self-Verification" in result


# ---------------------------------------------------------------------------
# EmployeeManager — _post_task_cleanup
# ---------------------------------------------------------------------------

class TestEmployeeManagerPostTaskCleanup:
    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_cleanup_no_project_id_returns_early(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="")
        # Should return early without error
        await mgr._post_task_cleanup("emp01", task, agent_error=False, project_id="")

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_cleanup_no_criteria_all_complete(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="proj1", result="Done", total_tokens=0)

        with patch("onemancompany.core.project_archive.append_action"):
            with patch("onemancompany.core.project_archive.load_project", return_value={"acceptance_criteria": []}):
                with patch("onemancompany.core.project_archive.record_dispatch_completion"):
                    with patch("onemancompany.core.project_archive.all_dispatches_complete", return_value=True):
                        with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                            with patch.object(mgr, "_full_cleanup", new_callable=AsyncMock) as mock_full:
                                await mgr._post_task_cleanup("emp01", task, agent_error=False, project_id="proj1")
                                mock_full.assert_called_once()

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_cleanup_no_criteria_not_all_complete(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="proj1", result="Done", total_tokens=0)

        with patch("onemancompany.core.project_archive.append_action"):
            with patch("onemancompany.core.project_archive.load_project", return_value={"acceptance_criteria": []}):
                with patch("onemancompany.core.project_archive.record_dispatch_completion"):
                    with patch("onemancompany.core.project_archive.all_dispatches_complete", return_value=False):
                        with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                            with patch.object(mgr, "_minimal_cleanup", new_callable=AsyncMock) as mock_min:
                                await mgr._post_task_cleanup("emp01", task, agent_error=False, project_id="proj1")
                                mock_min.assert_called_once()

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_cleanup_with_criteria_not_accepted(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("officer01", launcher)

        task = AgentTask(id="t1", description="test", project_id="proj1", project_dir="/tmp/proj", result="Done", total_tokens=0)

        with patch("onemancompany.core.project_archive.append_action"):
            with patch("onemancompany.core.project_archive.load_project", return_value={
                "acceptance_criteria": ["Must work"], "acceptance_result": None, "responsible_officer": "officer01"
            }):
                with patch("onemancompany.core.project_archive.record_dispatch_completion"):
                    with patch("onemancompany.core.project_archive.all_dispatches_complete", return_value=True):
                        with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                            with patch.object(mgr, "_push_acceptance_task") as mock_accept:
                                with patch.object(mgr, "_minimal_cleanup", new_callable=AsyncMock):
                                    await mgr._post_task_cleanup("emp01", task, agent_error=False, project_id="proj1")
                                    mock_accept.assert_called_once()

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_cleanup_accepted_no_ea_review(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("ea01", launcher)

        task = AgentTask(id="t1", description="test", project_id="proj1", project_dir="/tmp/proj", result="Done", total_tokens=0)

        with patch("onemancompany.core.project_archive.append_action"):
            with patch("onemancompany.core.project_archive.load_project", return_value={
                "acceptance_criteria": ["Must work"],
                "acceptance_result": {"accepted": True, "notes": "OK"},
                "ea_review_result": None,
                "responsible_officer": "officer01",
            }):
                with patch("onemancompany.core.project_archive.record_dispatch_completion"):
                    with patch("onemancompany.core.project_archive.all_dispatches_complete", return_value=True):
                        with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                            with patch.object(mgr, "_push_ea_review_task") as mock_ea:
                                with patch.object(mgr, "_minimal_cleanup", new_callable=AsyncMock):
                                    with patch("onemancompany.core.config.EA_ID", "ea01"):
                                        await mgr._post_task_cleanup("emp01", task, agent_error=False, project_id="proj1")
                                        mock_ea.assert_called_once()

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_cleanup_ea_approved(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="proj1", result="Done", total_tokens=0)

        with patch("onemancompany.core.project_archive.append_action"):
            with patch("onemancompany.core.project_archive.load_project", return_value={
                "acceptance_criteria": ["Must work"],
                "acceptance_result": {"accepted": True},
                "ea_review_result": {"approved": True},
            }):
                with patch("onemancompany.core.project_archive.record_dispatch_completion"):
                    with patch("onemancompany.core.project_archive.all_dispatches_complete", return_value=True):
                        with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                            with patch.object(mgr, "_full_cleanup", new_callable=AsyncMock) as mock_full:
                                await mgr._post_task_cleanup("emp01", task, agent_error=False, project_id="proj1")
                                mock_full.assert_called_once()

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_cleanup_ea_rejected(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("officer01", launcher)

        task = AgentTask(id="t1", description="test", project_id="proj1", project_dir="/tmp/proj", result="Done", total_tokens=0)

        with patch("onemancompany.core.project_archive.append_action"):
            with patch("onemancompany.core.project_archive.load_project", return_value={
                "acceptance_criteria": ["Must work"],
                "acceptance_result": {"accepted": True},
                "ea_review_result": {"approved": False, "notes": "Quality too low"},
                "responsible_officer": "officer01",
            }):
                with patch("onemancompany.core.project_archive.record_dispatch_completion"):
                    with patch("onemancompany.core.project_archive.all_dispatches_complete", return_value=True):
                        with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                            with patch.object(mgr, "_push_rectification_task") as mock_rect:
                                with patch("onemancompany.core.project_archive._save_project"):
                                    with patch.object(mgr, "_minimal_cleanup", new_callable=AsyncMock):
                                        with patch("onemancompany.core.config.COO_ID", "officer01"):
                                            await mgr._post_task_cleanup("emp01", task, agent_error=False, project_id="proj1")
                                            mock_rect.assert_called_once()

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_cleanup_records_cost(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        task = AgentTask(
            id="t1", description="test", project_id="proj1",
            result="Done", total_tokens=100, model_used="gpt-4",
            input_tokens=60, output_tokens=40, estimated_cost_usd=0.01,
        )

        with patch("onemancompany.core.project_archive.append_action"):
            with patch("onemancompany.core.project_archive.load_project", return_value={"acceptance_criteria": []}):
                with patch("onemancompany.core.project_archive.record_dispatch_completion"):
                    with patch("onemancompany.core.project_archive.all_dispatches_complete", return_value=True):
                        with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                            with patch("onemancompany.core.project_archive.record_project_cost") as mock_cost:
                                with patch.object(mgr, "_full_cleanup", new_callable=AsyncMock):
                                    await mgr._post_task_cleanup("emp01", task, agent_error=False, project_id="proj1")
                                    mock_cost.assert_called_once_with("proj1", "emp01", "gpt-4", 60, 40, 0.01)


# ---------------------------------------------------------------------------
# EmployeeManager — _full_cleanup
# ---------------------------------------------------------------------------

class TestEmployeeManagerFullCleanup:
    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_full_cleanup_runs_routine(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="proj1")

        with patch("onemancompany.core.routine.run_post_task_routine", new_callable=AsyncMock) as mock_routine:
            with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                with patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
                    with patch("onemancompany.core.project_archive.complete_project"):
                        with patch("onemancompany.core.state.flush_pending_reload", return_value=None):
                            with patch("onemancompany.core.config.FOUNDING_LEVEL", 4):
                                await mgr._full_cleanup("emp01", task, False, "proj1")
                                mock_routine.assert_called_once()

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_full_cleanup_routine_error(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="proj1")

        with patch("onemancompany.core.routine.run_post_task_routine", new_callable=AsyncMock, side_effect=RuntimeError("Routine failed")):
            with patch("onemancompany.core.project_archive.append_action"):
                with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                    with patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
                        with patch("onemancompany.core.project_archive.complete_project"):
                            with patch("onemancompany.core.state.flush_pending_reload", return_value=None):
                                with patch("onemancompany.core.config.FOUNDING_LEVEL", 4):
                                    await mgr._full_cleanup("emp01", task, False, "proj1")
                                    # Should not raise, should publish error event
                                    assert mock_bus.publish.call_count >= 1

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_full_cleanup_with_flush_result(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.level = 1
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="proj1")

        with patch("onemancompany.core.routine.run_post_task_routine", new_callable=AsyncMock):
            with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                with patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
                    with patch("onemancompany.core.project_archive.complete_project"):
                        with patch("onemancompany.core.state.flush_pending_reload", return_value={
                            "employees_updated": ["emp01"], "employees_added": []
                        }):
                            with patch("onemancompany.core.config.FOUNDING_LEVEL", 4):
                                await mgr._full_cleanup("emp01", task, False, "proj1")

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_full_cleanup_agent_error_label(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="proj1")

        with patch("onemancompany.core.routine.run_post_task_routine", new_callable=AsyncMock):
            with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                with patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
                    with patch("onemancompany.core.project_archive.complete_project") as mock_complete:
                        with patch("onemancompany.core.state.flush_pending_reload", return_value=None):
                            with patch("onemancompany.core.config.FOUNDING_LEVEL", 4):
                                await mgr._full_cleanup("emp01", task, True, "proj1")
                                call_args = mock_complete.call_args
                                assert "with errors" in call_args[0][1]

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_full_cleanup_auto_project_skips_complete(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="_auto_12345")

        with patch("onemancompany.core.routine.run_post_task_routine", new_callable=AsyncMock):
            with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                with patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
                    with patch("onemancompany.core.project_archive.complete_project") as mock_complete:
                        with patch("onemancompany.core.state.flush_pending_reload", return_value=None):
                            with patch("onemancompany.core.config.FOUNDING_LEVEL", 4):
                                await mgr._full_cleanup("emp01", task, False, "_auto_12345")
                                mock_complete.assert_not_called()


# ---------------------------------------------------------------------------
# EmployeeManager — _minimal_cleanup
# ---------------------------------------------------------------------------

class TestEmployeeManagerMinimalCleanup:
    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_minimal_cleanup(self, mock_bus):
        mock_bus.publish = AsyncMock()

        mgr = EmployeeManager()

        with patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock) as mock_sandbox:
            await mgr._minimal_cleanup("proj1")
            mock_sandbox.assert_called_once()
            mock_bus.publish.assert_called_once()


# ---------------------------------------------------------------------------
# EmployeeManager — push acceptance/ea/rectification with no handle
# ---------------------------------------------------------------------------

class TestEmployeeManagerPushTasksNoHandle:
    def test_push_ea_review_task_no_handle(self, capsys):
        mgr = EmployeeManager()
        mgr._push_ea_review_task(
            "nonexistent", "proj1", "/tmp",
            ["Criteria"], {"notes": "ok", "officer_id": "off1"},
            {"task": "task", "timeline": []},
        )
        captured = capsys.readouterr()
        assert "WARNING" in captured.out

    def test_push_rectification_task_no_handle(self, capsys):
        mgr = EmployeeManager()
        mgr._push_rectification_task(
            "nonexistent", "proj1", "/tmp",
            ["Criteria"], "Bad quality", {"task": "task"},
        )
        captured = capsys.readouterr()
        assert "WARNING" in captured.out


# ---------------------------------------------------------------------------
# EmployeeManager — push_acceptance_task with timeline entries
# ---------------------------------------------------------------------------

class TestEmployeeManagerAcceptanceWithTimeline:
    def test_push_acceptance_task_with_timeline(self):
        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("officer01", launcher)

        with patch.object(mgr, "_publish_task_update"):
            with patch.object(mgr, "_schedule_next"):
                mgr._push_acceptance_task(
                    "officer01", "proj1", "/tmp/proj",
                    ["Criteria 1"],
                    {
                        "task": "Build app",
                        "timeline": [
                            {"employee_id": "emp01", "action": "started", "detail": "Begin work"},
                            {"employee_id": "emp02", "action": "completed", "detail": "Done"},
                        ]
                    },
                )

        board = mgr.boards["officer01"]
        assert len(board.tasks) == 1
        assert "emp01" in board.tasks[0].description
        assert "started" in board.tasks[0].description

    def test_push_ea_review_with_timeline(self):
        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("ea01", launcher)

        with patch.object(mgr, "_publish_task_update"):
            with patch.object(mgr, "_schedule_next"):
                mgr._push_ea_review_task(
                    "ea01", "proj1", "/tmp/proj",
                    ["Criteria 1"],
                    {"notes": "LGTM", "officer_id": "off01"},
                    {
                        "task": "Build app",
                        "timeline": [
                            {"employee_id": "emp01", "action": "completed", "detail": "All done"},
                        ]
                    },
                )

        board = mgr.boards["ea01"]
        assert len(board.tasks) == 1
        assert "emp01" in board.tasks[0].description


# ---------------------------------------------------------------------------
# EmployeeManager — _execute_task with subtask loop
# ---------------------------------------------------------------------------

class TestEmployeeManagerSubtaskLoop:
    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_execute_task_processes_subtasks(self, mock_append, mock_load, mock_bus, mock_state):
        """When launcher adds subtasks, the subtask loop should process them."""
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()

        call_count = 0
        async def fake_execute(desc, ctx, on_log=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Main task: push a subtask
                board = mgr.boards["emp01"]
                main_task = board.tasks[0]
                board.push("Sub work", parent_id=main_task.id)
                return LaunchResult(output="Main done, need subtask")
            else:
                return LaunchResult(output="Sub done")

        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(side_effect=fake_execute)
        mgr.register("emp01", launcher)

        task = AgentTask(id="t1", description="Build widget")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            with patch.object(mgr, "_completion_check", new_callable=AsyncMock, return_value=True):
                await mgr._execute_task("emp01", task)

        assert task.status == "completed"
        assert call_count == 2  # main + subtask

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_execute_task_cancelled_during_subtask_loop(self, mock_append, mock_load, mock_bus, mock_state):
        """If task is cancelled during subtask loop, it stops processing."""
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()

        async def fake_execute(desc, ctx, on_log=None):
            # Main task: push a subtask then cancel the main task
            board = mgr.boards["emp01"]
            main_task = board.tasks[0]
            board.push("Sub work", parent_id=main_task.id)
            main_task.status = "cancelled"
            return LaunchResult(output="Partial")

        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(side_effect=fake_execute)
        mgr.register("emp01", launcher)

        task = AgentTask(id="t1", description="Build widget")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", task)

        assert task.status == "cancelled"


# ---------------------------------------------------------------------------
# EmployeeManager — _log with running event loop
# ---------------------------------------------------------------------------

class TestEmployeeManagerLogWithLoop:
    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_log_publishes_event(self, mock_bus, mock_state):
        """When event loop is running, _log should fire-and-forget an event."""
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test")
        mgr._log("emp01", task, "info", "Test message")

        # Give the fire-and-forget task a chance to run
        await asyncio.sleep(0.01)

        assert len(task.logs) == 1
        assert task.logs[0]["content"] == "Test message"


# ---------------------------------------------------------------------------
# EmployeeManager — _publish_task_update with running event loop
# ---------------------------------------------------------------------------

class TestEmployeeManagerPublishWithLoop:
    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_publish_with_event_loop(self, mock_bus, mock_state):
        """When event loop is running, publish should create a fire-and-forget task."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test")
        mgr._publish_task_update("emp01", task)

        await asyncio.sleep(0.01)

        # Should have published
        assert mock_bus.publish.called


# ---------------------------------------------------------------------------
# EmployeeManager — project context with long timeline
# ---------------------------------------------------------------------------

class TestEmployeeManagerProjectContextTimeline:
    def test_long_timeline_omits_middle(self):
        """When timeline has > 15 entries, middle entries should be omitted."""
        mgr = EmployeeManager()
        timeline = [
            {"time": f"2024-01-01T{i:02d}:00:00", "employee_id": "emp01", "action": f"action_{i}", "detail": f"detail_{i}"}
            for i in range(25)
        ]
        with patch("onemancompany.core.project_archive._is_iteration", return_value=False):
            with patch("onemancompany.core.project_archive._is_v1", return_value=False):
                with patch("onemancompany.core.project_archive.load_named_project", return_value={
                    "iterations": ["iter_001"], "name": "Test", "status": "active"
                }):
                    with patch("onemancompany.core.project_archive.list_project_files", return_value=[]):
                        with patch("onemancompany.core.project_archive.load_iteration", return_value={
                            "iteration_id": "iter_001", "status": "completed",
                            "task": "Build it", "output": "",
                            "timeline": timeline,
                            "cost": {"actual_cost_usd": 0.0, "budget_estimate_usd": 0.0, "token_usage": {}},
                        }):
                            result = mgr._get_project_history_context("my-project")
                            assert "omitted" in result

    def test_context_with_many_files(self):
        """When there are many workspace files, only max files are shown."""
        mgr = EmployeeManager()
        many_files = [f"file_{i}.py" for i in range(40)]
        with patch("onemancompany.core.project_archive._is_iteration", return_value=False):
            with patch("onemancompany.core.project_archive._is_v1", return_value=False):
                with patch("onemancompany.core.project_archive.load_named_project", return_value={
                    "iterations": [], "name": "Test", "status": "active"
                }):
                    with patch("onemancompany.core.project_archive.list_project_files", return_value=many_files):
                        with patch("onemancompany.core.project_archive.get_project_workspace", return_value="/tmp/ws"):
                            result = mgr._get_project_history_context("my-project")
                            assert "and" in result and "more" in result

    def test_context_with_budget_spending(self):
        """When iterations have cost data, budget info should appear."""
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_iteration", return_value=False):
            with patch("onemancompany.core.project_archive._is_v1", return_value=False):
                with patch("onemancompany.core.project_archive.load_named_project", return_value={
                    "iterations": ["iter_001", "iter_002"], "name": "Test", "status": "active"
                }):
                    with patch("onemancompany.core.project_archive.list_project_files", return_value=[]):
                        def load_iter(slug, iter_id):
                            return {
                                "iteration_id": iter_id, "status": "completed",
                                "task": "Build", "output": "Done output text",
                                "timeline": [],
                                "cost": {"actual_cost_usd": 0.05, "budget_estimate_usd": 2.0,
                                         "token_usage": {"input": 1000, "output": 500}},
                            }
                        with patch("onemancompany.core.project_archive.load_iteration", side_effect=load_iter):
                            result = mgr._get_project_history_context("my-project")
                            assert "Budget" in result
                            assert "Spent" in result
                            assert "Cost" in result
                            assert "Tokens" in result


# ---------------------------------------------------------------------------
# Coverage gap: line 553 — _on_log callback inside _execute_task
# ---------------------------------------------------------------------------

import onemancompany.core.agent_loop as agent_loop_mod


class TestExecuteTaskOnLogCallback:
    """Line 553: The _on_log closure inside _execute_task must be called by the launcher."""

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_on_log_callback_called_by_launcher(self, mock_append, mock_load, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()

        async def fake_execute(desc, ctx, on_log=None):
            # Launcher calls the on_log callback — triggers line 553
            if on_log:
                on_log("progress", "Working on it...")
            return LaunchResult(output="Done")

        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(side_effect=fake_execute)
        mgr.register("emp01", launcher)

        task = AgentTask(id="t1", description="Build widget")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", task)

        assert task.status == "completed"
        # Verify the on_log callback populated the task logs
        log_types = [lg["type"] for lg in task.logs]
        assert "progress" in log_types


# ---------------------------------------------------------------------------
# Coverage gap: lines 626, 630 — task cancelled during subtask inner loop
# ---------------------------------------------------------------------------

class TestSubtaskLoopCancelledBreaks:
    """Lines 626 and 630: task.status == 'cancelled' breaks during subtask processing."""

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_cancel_during_subtask_inner_loop(self, mock_append, mock_load, mock_bus, mock_state):
        """Line 626: task cancelled inside the 'for sub in pending_subs' loop."""
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()

        call_count = 0

        async def fake_execute(desc, ctx, on_log=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Main task: push two subtasks
                board = mgr.boards["emp01"]
                main_task = board.tasks[0]
                board.push("Sub 1", parent_id=main_task.id)
                board.push("Sub 2", parent_id=main_task.id)
                return LaunchResult(output="Main done")
            else:
                # First subtask execution: cancel the parent
                board = mgr.boards["emp01"]
                main_task = board.tasks[0]
                main_task.status = "cancelled"
                return LaunchResult(output="Sub 1 done")

        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(side_effect=fake_execute)
        mgr.register("emp01", launcher)

        task = AgentTask(id="t1", description="Build widget")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", task)

        assert task.status == "cancelled"
        # Only 2 calls: main + first subtask; second subtask skipped
        assert call_count == 2

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    @patch("onemancompany.core.agent_loop._load_progress", return_value="")
    @patch("onemancompany.core.agent_loop._append_progress")
    async def test_cancel_after_subtasks_before_completion_check(self, mock_append, mock_load, mock_bus, mock_state):
        """Line 630: task cancelled after all subtasks run, before _completion_check."""
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.role = "Engineer"
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []

        mgr = EmployeeManager()

        call_count = 0

        async def fake_execute(desc, ctx, on_log=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Main task: push one subtask
                board = mgr.boards["emp01"]
                main_task = board.tasks[0]
                board.push("Sub 1", parent_id=main_task.id)
                return LaunchResult(output="Main done")
            else:
                # Subtask execution: cancel parent after subtask completes
                board = mgr.boards["emp01"]
                main_task = board.tasks[0]
                main_task.status = "cancelled"
                return LaunchResult(output="Sub done")

        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(side_effect=fake_execute)
        mgr.register("emp01", launcher)

        task = AgentTask(id="t1", description="Build widget")
        mgr.boards["emp01"].tasks.append(task)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", task)

        assert task.status == "cancelled"
        # _completion_check should NOT have been called
        assert call_count == 2


# ---------------------------------------------------------------------------
# Coverage gap: line 708 — _on_log callback inside _execute_subtask
# ---------------------------------------------------------------------------

class TestExecuteSubtaskOnLogCallback:
    """Line 708: The _on_log closure inside _execute_subtask must be called by the launcher."""

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_subtask_on_log_callback(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}

        mgr = EmployeeManager()

        async def fake_execute(desc, ctx, on_log=None):
            # Launcher calls on_log — triggers line 708
            if on_log:
                on_log("subtask_progress", "Subtask working...")
            return LaunchResult(output="Sub done")

        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(side_effect=fake_execute)
        mgr.register("emp01", launcher)

        parent = AgentTask(id="p1", description="Parent")
        sub = AgentTask(id="s1", description="Sub task", parent_id="p1")
        mgr.boards["emp01"].tasks.extend([parent, sub])
        parent.sub_task_ids.append("s1")

        await mgr._execute_subtask("emp01", sub, depth=1)

        assert sub.status == "completed"
        log_types = [lg["type"] for lg in sub.logs]
        assert "subtask_progress" in log_types


# ---------------------------------------------------------------------------
# Coverage gap: lines 815-817 — _completion_check exception handling
# (Patch at the IMPORTING module level, not onemancompany.agents.base)
# ---------------------------------------------------------------------------

class TestCompletionCheckExceptionModuleLevel:
    """Lines 815-817: _completion_check exception caught and returns True."""

    @pytest.mark.asyncio
    async def test_completion_check_exception_module_level_patch(self):
        mgr = EmployeeManager()
        mgr.boards["emp01"] = AgentTaskBoard()

        task = AgentTask(id="t1", description="Main task", sub_task_ids=["s1"])
        sub = AgentTask(id="s1", description="Sub", status="completed", result="Done", parent_id="t1")
        mgr.boards["emp01"].tasks.extend([task, sub])

        # Patch make_llm at the importing module level (agent_loop), not agents.base
        with patch.object(agent_loop_mod, "make_llm", side_effect=RuntimeError("LLM down")):
            result = await mgr._completion_check("emp01", task)
            assert result is True


# ---------------------------------------------------------------------------
# Coverage gap: lines 862-863 — _compress_history LLM failure fallback
# (Patch at the importing module level)
# ---------------------------------------------------------------------------

class TestCompressHistoryFallbackModuleLevel:
    """Lines 862-863: When LLM call fails, falls back to raw concatenation."""

    @pytest.mark.asyncio
    async def test_compress_history_llm_failure_fallback(self):
        mgr = EmployeeManager()
        # Create enough history to trigger compression
        mgr.task_histories["emp01"] = [
            {"task": f"Task {i}" * 50, "result": f"Result {i}" * 50, "completed_at": f"2024-01-{i:02d}"}
            for i in range(1, 20)
        ]

        # Patch at agent_loop module level
        with patch.object(agent_loop_mod, "make_llm", side_effect=RuntimeError("LLM down")):
            await mgr._maybe_compress_history("emp01")

        # Fallback: summary should be set from raw text
        assert "emp01" in mgr._history_summaries
        assert len(mgr._history_summaries["emp01"]) <= 800


# ---------------------------------------------------------------------------
# Coverage gap: line 924 — continue when load_iteration returns None in budget loop
# ---------------------------------------------------------------------------

class TestProjectContextLoadIterationNone:
    """Line 924: continue when load_iteration returns None in budget calculation loop."""

    def test_load_iteration_returns_none_in_budget_loop(self):
        mgr = EmployeeManager()

        def load_iter(slug, iter_id):
            if iter_id == "iter_001":
                return None  # triggers line 924 continue
            return {
                "iteration_id": iter_id, "status": "completed",
                "task": "Build", "output": "Done",
                "timeline": [],
                "cost": {"actual_cost_usd": 0.05, "budget_estimate_usd": 2.0, "token_usage": {}},
            }

        with patch("onemancompany.core.project_archive._is_iteration", return_value=False):
            with patch("onemancompany.core.project_archive._is_v1", return_value=False):
                with patch("onemancompany.core.project_archive.load_named_project", return_value={
                    "iterations": ["iter_001", "iter_002"], "name": "Test", "status": "active"
                }):
                    with patch("onemancompany.core.project_archive.list_project_files", return_value=[]):
                        with patch("onemancompany.core.project_archive.load_iteration", side_effect=load_iter):
                            result = mgr._get_project_history_context("my-project")
                            # iter_001 was skipped (None), iter_002 should be present
                            assert "iter_002" in result


# ---------------------------------------------------------------------------
# Coverage gap: line 937 — budget spent line when total_spent > 0 but total_budget == 0
# ---------------------------------------------------------------------------

class TestProjectContextSpentNoBudget:
    """Line 937: 'Spent: $X' line when total_spent > 0 but total_budget == 0."""

    def test_spent_without_budget(self):
        mgr = EmployeeManager()

        def load_iter(slug, iter_id):
            return {
                "iteration_id": iter_id, "status": "completed",
                "task": "Build", "output": "Done",
                "timeline": [],
                "cost": {"actual_cost_usd": 0.05, "budget_estimate_usd": 0.0, "token_usage": {}},
            }

        with patch("onemancompany.core.project_archive._is_iteration", return_value=False):
            with patch("onemancompany.core.project_archive._is_v1", return_value=False):
                with patch("onemancompany.core.project_archive.load_named_project", return_value={
                    "iterations": ["iter_001"], "name": "Test", "status": "active"
                }):
                    with patch("onemancompany.core.project_archive.list_project_files", return_value=[]):
                        with patch("onemancompany.core.project_archive.load_iteration", side_effect=load_iter):
                            result = mgr._get_project_history_context("my-project")
                            # Budget is 0, but spent > 0, so we get "Spent: $X" without "Budget:"
                            assert "Spent:" in result
                            assert "Budget:" not in result


# ---------------------------------------------------------------------------
# Coverage gap: line 942 — continue when load_iteration returns None in detail loop
# ---------------------------------------------------------------------------

class TestProjectContextDetailLoopNone:
    """Line 942: continue when load_iteration returns None in iteration detail loop."""

    def test_load_iteration_returns_none_in_detail_loop(self):
        mgr = EmployeeManager()

        call_count = {"budget": 0, "detail": 0}

        def load_iter(slug, iter_id):
            # Budget loop gets all iterations; detail loop gets only prev_iters
            # We return valid data for budget, None for detail
            call_count[iter_id] = call_count.get(iter_id, 0) + 1
            # First call per iteration is from budget loop, second from detail loop
            if call_count[iter_id] == 1:
                return {
                    "iteration_id": iter_id, "status": "completed",
                    "task": "Build", "output": "Done",
                    "timeline": [],
                    "cost": {"actual_cost_usd": 0.0, "budget_estimate_usd": 0.0, "token_usage": {}},
                }
            else:
                return None  # triggers line 942 continue in detail loop

        with patch("onemancompany.core.project_archive._is_iteration", return_value=False):
            with patch("onemancompany.core.project_archive._is_v1", return_value=False):
                with patch("onemancompany.core.project_archive.load_named_project", return_value={
                    "iterations": ["iter_001", "iter_002"], "name": "Test", "status": "active"
                }):
                    with patch("onemancompany.core.project_archive.list_project_files", return_value=[]):
                        with patch("onemancompany.core.project_archive.load_iteration", side_effect=load_iter):
                            result = mgr._get_project_history_context("my-project")
                            # Should not crash, returns whatever context is available
                            assert "Project Context" in result


# ---------------------------------------------------------------------------
# Coverage gap: line 1101 — event_bus.publish for resolution_ready event
# ---------------------------------------------------------------------------

class TestPostTaskCleanupResolutionReady:
    """Line 1101: event_bus.publish for resolution_ready when create_resolution returns a value."""

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_resolution_ready_event_published(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="proj1", result="Done", total_tokens=0)

        mock_resolution = {"id": "res1", "summary": "Resolution summary"}

        with patch("onemancompany.core.project_archive.append_action"):
            with patch("onemancompany.core.project_archive.load_project", return_value={"acceptance_criteria": []}):
                with patch("onemancompany.core.project_archive.record_dispatch_completion"):
                    with patch("onemancompany.core.project_archive.all_dispatches_complete", return_value=True):
                        with patch("onemancompany.core.resolutions.create_resolution", return_value=mock_resolution):
                            with patch.object(mgr, "_full_cleanup", new_callable=AsyncMock):
                                await mgr._post_task_cleanup("emp01", task, agent_error=False, project_id="proj1")

        # Verify resolution_ready event was published
        resolution_calls = [
            c for c in mock_bus.publish.call_args_list
            if c[0][0].type == "resolution_ready"
        ]
        assert len(resolution_calls) == 1
        assert resolution_calls[0][0][0].payload == mock_resolution


# ---------------------------------------------------------------------------
# Coverage gap: lines 1164-1165 — fallthrough: has criteria + rejected acceptance
# ---------------------------------------------------------------------------

class TestPostTaskCleanupFallthrough:
    """Lines 1164-1165: record_dispatch_completion + _minimal_cleanup for the fallthrough case.

    This happens when:
    - acceptance_criteria is truthy
    - acceptance_result is truthy but accepted is False (not truthy)
    - So CASE A (not acceptance_result) is skipped
    - CASE B (acceptance_result.get('accepted')) is skipped
    - CASE C (not acceptance_criteria) is skipped
    - Falls through to lines 1164-1165
    """

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_fallthrough_criteria_with_rejected_acceptance(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="proj1", result="Done", total_tokens=0)

        with patch("onemancompany.core.project_archive.append_action"):
            with patch("onemancompany.core.project_archive.load_project", return_value={
                "acceptance_criteria": ["Must work"],
                "acceptance_result": {"accepted": False, "notes": "Not good enough"},
                "ea_review_result": None,
            }):
                with patch("onemancompany.core.project_archive.record_dispatch_completion") as mock_record:
                    with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                        with patch.object(mgr, "_minimal_cleanup", new_callable=AsyncMock) as mock_min:
                            await mgr._post_task_cleanup("emp01", task, agent_error=False, project_id="proj1")
                            mock_record.assert_called_once_with("proj1", "emp01")
                            mock_min.assert_called_once_with("proj1")


# ---------------------------------------------------------------------------
# Coverage gap: line 1191 — routine_resolution in _full_cleanup
# ---------------------------------------------------------------------------

class TestFullCleanupRoutineResolution:
    """Line 1191: event_bus.publish for routine resolution_ready in _full_cleanup."""

    @pytest.mark.asyncio
    @patch("onemancompany.core.agent_loop.company_state")
    @patch("onemancompany.core.agent_loop.event_bus")
    async def test_routine_resolution_published(self, mock_bus, mock_state):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        task = AgentTask(id="t1", description="test", project_id="proj1")

        mock_resolution = {"id": "res2", "summary": "Routine resolution"}

        with patch("onemancompany.core.routine.run_post_task_routine", new_callable=AsyncMock):
            with patch("onemancompany.core.resolutions.create_resolution", return_value=mock_resolution):
                with patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
                    with patch("onemancompany.core.project_archive.complete_project"):
                        with patch("onemancompany.core.state.flush_pending_reload", return_value=None):
                            with patch("onemancompany.core.config.FOUNDING_LEVEL", 4):
                                await mgr._full_cleanup("emp01", task, False, "proj1")

        # Verify resolution_ready event was published
        resolution_calls = [
            c for c in mock_bus.publish.call_args_list
            if c[0][0].type == "resolution_ready"
        ]
        assert len(resolution_calls) >= 1
        assert resolution_calls[0][0][0].payload == mock_resolution
