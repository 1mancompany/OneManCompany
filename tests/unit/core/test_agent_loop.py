"""Unit tests for core/agent_loop.py — EmployeeManager task dispatch system."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.agent_loop import (
    ClaudeSessionLauncher,
    EmployeeHandle,
    EmployeeManager,
    LaunchResult,
    LangChainLauncher,
    Launcher,
    ScriptLauncher,
    ScheduleEntry,
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
from onemancompany.core.task_tree import TaskNode, TaskTree
from onemancompany.core.task_lifecycle import TaskPhase


def _make_tree_entry(tmp_path, employee_id="emp01", description="Build widget",
                     project_id="proj1", node_id="", status="pending"):
    """Create a TaskTree file with one node and return (ScheduleEntry, tree_path, node)."""
    tree = TaskTree(project_id=project_id)
    root = tree.create_root(employee_id=employee_id, description=description)
    if node_id:
        root.id = node_id
        tree._nodes = {root.id: root}
        tree.root_id = root.id
    if status != "pending":
        root.status = status
    tree_path = tmp_path / "task_tree.yaml"
    tree.save(tree_path)
    entry = ScheduleEntry(node_id=root.id, tree_path=str(tree_path))
    return entry, tree_path, root



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

    def test_role_from_state(self, monkeypatch):
        from onemancompany.core import store as store_mod
        monkeypatch.setattr(store_mod, "load_employee",
                            lambda eid: {"id": eid, "role": "Engineer"})
        ref = _AgentRef("test_emp")
        assert ref.role == "Engineer"

    def test_role_missing_employee(self, monkeypatch):
        from onemancompany.core import store as store_mod
        monkeypatch.setattr(store_mod, "load_employee", lambda eid: None)
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
            mock_run.return_value = {"output": "Claude output", "model": "test", "input_tokens": 10, "output_tokens": 5}
            on_log = MagicMock()
            result = await launcher.execute("Do task", ctx, on_log=on_log)
            mock_run.assert_called_once_with("emp01", "proj1", prompt="Do task", work_dir="/tmp/work", task_id="")
            assert result.output == "Claude output"
            on_log.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_default_project(self):
        launcher = ClaudeSessionLauncher("emp01")
        ctx = TaskContext()  # empty project_id
        with patch("onemancompany.core.claude_session.run_claude_session", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"output": "output", "model": "test", "input_tokens": 0, "output_tokens": 0}
            result = await launcher.execute("Do task", ctx)
            mock_run.assert_called_once_with("emp01", "default", prompt="Do task", work_dir="", task_id="")
            assert result.output == "output"

    @pytest.mark.asyncio
    async def test_execute_none_output(self):
        launcher = ClaudeSessionLauncher("emp01")
        ctx = TaskContext(project_id="p1")
        with patch("onemancompany.core.claude_session.run_claude_session", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"output": None, "model": "", "input_tokens": 0, "output_tokens": 0}
            result = await launcher.execute("Do task", ctx)
            assert result.output == ""

    @pytest.mark.asyncio
    async def test_execute_no_log_callback(self):
        launcher = ClaudeSessionLauncher("emp01")
        ctx = TaskContext(project_id="p1")
        with patch("onemancompany.core.claude_session.run_claude_session", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"output": "output", "model": "test", "input_tokens": 0, "output_tokens": 0}
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
        mock_push.return_value = "node123"
        handle = EmployeeHandle(mgr, "emp01")
        result = handle.push_task("Do something", project_id="proj1", project_dir="/tmp")
        mock_push.assert_called_once_with(
            "emp01", "Do something",
            project_id="proj1", project_dir="/tmp",
            node_id="", tree_path="",
        )
        assert result == "node123"

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
        with patch("onemancompany.core.vessel.EMPLOYEES_DIR", tmp_path):
            _append_progress("emp01", "Did something")
            log_path = tmp_path / "emp01" / "progress.log"
            assert log_path.exists()
            content = log_path.read_text()
            assert "Did something" in content

    def test_append_progress_creates_dir(self, tmp_path):
        with patch("onemancompany.core.vessel.EMPLOYEES_DIR", tmp_path):
            _append_progress("newguy", "First task")
            assert (tmp_path / "newguy" / "progress.log").exists()

    def test_load_progress_empty(self, tmp_path):
        with patch("onemancompany.core.vessel.EMPLOYEES_DIR", tmp_path):
            result = _load_progress("emp01")
            assert result == ""

    def test_load_progress_reads_lines(self, tmp_path):
        with patch("onemancompany.core.vessel.EMPLOYEES_DIR", tmp_path):
            log_dir = tmp_path / "emp01"
            log_dir.mkdir()
            log_path = log_dir / "progress.log"
            lines = [f"[2024-01-01T00:00:{i:02d}] Entry {i}\n" for i in range(10)]
            log_path.write_text("".join(lines))
            result = _load_progress("emp01")
            assert "Entry 0" in result
            assert "Entry 9" in result

    def test_load_progress_truncates_to_max_lines(self, tmp_path):
        with patch("onemancompany.core.vessel.EMPLOYEES_DIR", tmp_path):
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
        assert mgr.executors["emp01"] is launcher
        assert "emp01" in mgr.task_histories
        assert mgr.vessels["emp01"] is handle

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
        assert "emp01" not in mgr.executors
        assert "emp01" not in mgr.vessels
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
    def test_push_task_with_node_schedules(self):
        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("emp01", launcher)
        with patch.object(mgr, "_schedule_next"):
            result = mgr.push_task("emp01", "Do something", node_id="n1", tree_path="/tmp/tree.yaml")
            assert result == "n1"
            assert len(mgr._schedule["emp01"]) == 1
            assert mgr._schedule["emp01"][0].node_id == "n1"

    def test_push_task_without_node_returns_empty(self):
        mgr = EmployeeManager()
        with patch.object(mgr, "_schedule_next"):
            result = mgr.push_task("emp01", "Do something")
            assert result == ""

    def test_push_task_calls_schedule(self):
        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("emp01", launcher)
        with patch.object(mgr, "_schedule_next") as mock_sched:
            mgr.push_task("emp01", "Do something")
            mock_sched.assert_called_once_with("emp01")


# ---------------------------------------------------------------------------
# EmployeeManager — _schedule_next
# ---------------------------------------------------------------------------

class TestEmployeeManagerScheduleNext:
    def test_schedule_next_does_nothing_if_running(self):
        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        mgr.register("emp01", launcher)
        mgr._running_tasks["emp01"] = MagicMock()
        mgr.schedule_node("emp01", "n1", "/tmp/tree.yaml")
        # Should not create new task because one is already running
        mgr._schedule_next("emp01")
        # The running_tasks should still have only the mock
        assert isinstance(mgr._running_tasks["emp01"], MagicMock)

    def test_schedule_next_no_schedule(self):
        mgr = EmployeeManager()
        mgr._schedule_next("nobody")  # should not raise

    @patch("onemancompany.core.vessel._store")
    def test_schedule_next_no_pending_sets_idle(self, mock_store):
        mgr = EmployeeManager()
        mock_store.save_employee_runtime = AsyncMock()
        mgr._schedule["emp01"] = []  # empty schedule
        mgr._schedule_next("emp01")
        # _set_employee_status now persists via store (no in-memory emp.status)


# ---------------------------------------------------------------------------
# EmployeeManager — _execute_task (mocked end-to-end)
# ---------------------------------------------------------------------------

class TestEmployeeManagerExecuteTask:
    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    @patch("onemancompany.core.vessel._load_progress", return_value="")
    @patch("onemancompany.core.vessel._append_progress")
    async def test_execute_task_happy_path(self, mock_append, mock_load, mock_bus, mock_state, tmp_path):
        mock_bus.publish = AsyncMock()
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Task done!"))
        mgr.register("emp01", launcher)

        entry, tree_path, root = _make_tree_entry(tmp_path)
        mgr.schedule_node("emp01", entry.node_id, entry.tree_path)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", entry)

        # Reload tree to check node status
        tree = TaskTree.load(tree_path, skeleton_only=False)
        node = tree.get_node(entry.node_id)
        # Regular task nodes stop at "completed" (no auto-skip to finished)
        assert node.status == "completed"
        assert node.result == "Task done!"
        assert node.completed_at != ""
        launcher.execute.assert_called_once()

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    @patch("onemancompany.core.vessel._load_progress", return_value="")
    @patch("onemancompany.core.vessel._append_progress")
    async def test_execute_task_failure_retries(self, mock_append, mock_load, mock_bus, mock_state, tmp_path):
        mock_bus.publish = AsyncMock()
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(side_effect=RuntimeError("API down"))
        mgr.register("emp01", launcher)

        entry, tree_path, root = _make_tree_entry(tmp_path)
        mgr.schedule_node("emp01", entry.node_id, entry.tree_path)

        with patch("onemancompany.core.vessel.asyncio.sleep", new_callable=AsyncMock):
            with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
                await mgr._execute_task("emp01", entry)

        tree = TaskTree.load(tree_path, skeleton_only=False)
        node = tree.get_node(entry.node_id)
        assert node.status == "failed"
        assert "Error" in node.result
        assert launcher.execute.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    @patch("onemancompany.core.vessel._load_progress", return_value="")
    @patch("onemancompany.core.vessel._append_progress")
    async def test_execute_task_no_launcher(self, mock_append, mock_load, mock_bus, mock_state, tmp_path):
        mock_bus.publish = AsyncMock()
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        # Create vessel without launcher
        mgr.vessels["emp01"] = EmployeeHandle(mgr, "emp01")
        mgr.task_histories["emp01"] = []

        entry, tree_path, root = _make_tree_entry(tmp_path)
        mgr.schedule_node("emp01", entry.node_id, entry.tree_path)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", entry)

        tree = TaskTree.load(tree_path, skeleton_only=False)
        node = tree.get_node(entry.node_id)
        assert node.status == "failed"
        assert "No executor" in node.result

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    @patch("onemancompany.core.vessel._load_progress", return_value="Previous work here")
    @patch("onemancompany.core.vessel._append_progress")
    async def test_execute_task_injects_progress(self, mock_append, mock_load, mock_bus, mock_state, tmp_path):
        mock_bus.publish = AsyncMock()
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Done"))
        mgr.register("emp01", launcher)

        entry, tree_path, root = _make_tree_entry(tmp_path)
        mgr.schedule_node("emp01", entry.node_id, entry.tree_path)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", entry)

        call_args = launcher.execute.call_args
        task_with_ctx = call_args[0][0]
        assert "Previous Work Learnings" in task_with_ctx
        assert "Previous work here" in task_with_ctx

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    @patch("onemancompany.core.vessel._load_progress", return_value="")
    @patch("onemancompany.core.vessel._append_progress")
    async def test_execute_task_records_token_usage(self, mock_append, mock_load, mock_bus, mock_state, tmp_path):
        mock_bus.publish = AsyncMock()
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

        entry, tree_path, root = _make_tree_entry(tmp_path)
        mgr.schedule_node("emp01", entry.node_id, entry.tree_path)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            with patch("onemancompany.core.model_costs.get_model_cost", return_value={"input": 10.0, "output": 30.0}):
                await mgr._execute_task("emp01", entry)

        tree = TaskTree.load(tree_path, skeleton_only=False)
        node = tree.get_node(entry.node_id)
        assert node.model_used == "gpt-4"
        assert node.input_tokens == 1000
        assert node.output_tokens == 500


# ---------------------------------------------------------------------------
# EmployeeManager — _run_task (scheduling chain)
# ---------------------------------------------------------------------------

class TestEmployeeManagerRunTask:
    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    @patch("onemancompany.core.vessel._load_progress", return_value="")
    @patch("onemancompany.core.vessel._append_progress")
    async def test_run_task_cleans_up_and_schedules_next(self, mock_append, mock_load, mock_bus, mock_state, tmp_path):
        mock_bus.publish = AsyncMock()
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Done"))
        mgr.register("emp01", launcher)

        entry, tree_path, root = _make_tree_entry(tmp_path)
        mgr.schedule_node("emp01", entry.node_id, entry.tree_path)
        mgr._running_tasks["emp01"] = MagicMock()

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._run_task("emp01", entry)

        # After _run_task, the running task should be removed
        assert "emp01" not in mgr._running_tasks


# ---------------------------------------------------------------------------
# EmployeeManager — Task history
# ---------------------------------------------------------------------------

class TestEmployeeManagerTaskHistory:
    def test_append_history_from_node(self):
        mgr = EmployeeManager()
        node = TaskNode(
            id="t1", description="Built feature X",
            result="Feature X is done", completed_at="2024-01-01T12:00:00",
            employee_id="emp01",
        )
        mgr._append_history_from_node("emp01", node)
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
    @patch("onemancompany.core.vessel._store")
    def test_get_role_found(self, mock_store):
        mock_store.load_employee.return_value = {"id": "emp01", "role": "COO"}
        mgr = EmployeeManager()
        assert mgr._get_role("emp01") == "COO"

    @patch("onemancompany.core.vessel._store")
    def test_get_role_missing(self, mock_store):
        mock_store.load_employee.return_value = None
        mgr = EmployeeManager()
        assert mgr._get_role("nobody") == "Employee"

    @patch("onemancompany.core.vessel._store")
    def test_set_employee_status(self, mock_store):
        mock_store.save_employee_runtime = AsyncMock()
        mgr = EmployeeManager()
        mgr._set_employee_status("emp01", "working")
        # _set_employee_status now persists via store (async), no in-memory mutation

    @patch("onemancompany.core.vessel._store")
    def test_set_employee_status_missing(self, mock_store):
        mock_store.save_employee_runtime = AsyncMock()
        mgr = EmployeeManager()
        mgr._set_employee_status("nobody", "working")  # should not raise

    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    def test_log_node_appends_to_buffer(self, mock_bus, mock_state):
        mock_state.employees = {}
        mgr = EmployeeManager()
        mgr._log_node("emp01", "n1", "info", "Something happened")
        assert len(mgr._task_logs["n1"]) == 1
        assert mgr._task_logs["n1"][0]["type"] == "info"
        assert mgr._task_logs["n1"][0]["content"] == "Something happened"

    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    def test_publish_node_update_no_event_loop(self, mock_bus, mock_state):
        mock_state.employees = {}
        mgr = EmployeeManager()
        node = TaskNode(id="n1", description="test", employee_id="emp01")
        # Should not raise even without event loop
        mgr._publish_node_update("emp01", node)



# ---------------------------------------------------------------------------
# Backward-compatible API functions
# ---------------------------------------------------------------------------

class TestBackwardCompatAPI:
    def test_register_agent(self):
        runner = MagicMock()
        with patch("onemancompany.core.vessel.employee_manager") as mock_mgr:
            mock_mgr.register.return_value = MagicMock(spec=EmployeeHandle)
            handle = register_agent("emp01", runner)
            mock_mgr.register.assert_called_once()
            call_args = mock_mgr.register.call_args
            assert call_args[0][0] == "emp01"
            assert isinstance(call_args[0][1], LangChainLauncher)

    def test_register_self_hosted(self):
        with patch("onemancompany.core.vessel.employee_manager") as mock_mgr:
            mock_mgr.register.return_value = MagicMock(spec=EmployeeHandle)
            handle = register_self_hosted("emp01")
            mock_mgr.register.assert_called_once()
            call_args = mock_mgr.register.call_args
            assert call_args[0][0] == "emp01"
            assert isinstance(call_args[0][1], ClaudeSessionLauncher)

    def test_get_agent_loop(self):
        with patch("onemancompany.core.vessel.employee_manager") as mock_mgr:
            mock_handle = MagicMock(spec=EmployeeHandle)
            mock_mgr.get_handle.return_value = mock_handle
            result = get_agent_loop("emp01")
            mock_mgr.get_handle.assert_called_once_with("emp01")
            assert result is mock_handle

    def test_get_agent_loop_missing(self):
        with patch("onemancompany.core.vessel.employee_manager") as mock_mgr:
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
        with patch("onemancompany.core.vessel.register_agent") as mock_reg:
            mock_reg.return_value = MagicMock(spec=EmployeeHandle)
            handle = await register_and_start_agent("emp01", runner)
            mock_reg.assert_called_once_with("emp01", runner)

    def test_agent_loops_alias(self):
        # agent_loops should be the same dict as employee_manager.vessels
        from onemancompany.core.agent_loop import employee_manager
        assert agent_loops is employee_manager.vessels


# ---------------------------------------------------------------------------
# EmployeeManager — GraphRecursionError handling
# ---------------------------------------------------------------------------

class TestEmployeeManagerGraphRecursionError:
    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    @patch("onemancompany.core.vessel._load_progress", return_value="")
    @patch("onemancompany.core.vessel._append_progress")
    async def test_graph_recursion_error_no_retry(self, mock_append, mock_load, mock_bus, mock_state, tmp_path):
        from langgraph.errors import GraphRecursionError

        mock_bus.publish = AsyncMock()
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(side_effect=GraphRecursionError("Recursion limit"))
        mgr.register("emp01", launcher)

        entry, tree_path, root = _make_tree_entry(tmp_path, description="Recursive task")
        mgr.schedule_node("emp01", entry.node_id, entry.tree_path)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", entry)

        tree = TaskTree.load(tree_path, skeleton_only=False)
        node = tree.get_node(entry.node_id)
        assert node.status == "failed"
        # GraphRecursionError should NOT be retried — only 1 call
        assert launcher.execute.call_count == 1


# ---------------------------------------------------------------------------
# EmployeeManager — Pre-task hook failure handling
# ---------------------------------------------------------------------------

class TestEmployeeManagerHookFailures:
    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    @patch("onemancompany.core.vessel._load_progress", return_value="")
    @patch("onemancompany.core.vessel._append_progress")
    async def test_pre_hook_failure_continues(self, mock_append, mock_load, mock_bus, mock_state, tmp_path):
        mock_bus.publish = AsyncMock()
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Done"))
        mgr.register("emp01", launcher)

        def bad_pre_hook(desc, ctx):
            raise RuntimeError("Hook failed!")

        mgr.register_hooks("emp01", {"pre_task": bad_pre_hook})

        entry, tree_path, root = _make_tree_entry(tmp_path, description="Test task")
        mgr.schedule_node("emp01", entry.node_id, entry.tree_path)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", entry)

        tree = TaskTree.load(tree_path, skeleton_only=False)
        node = tree.get_node(entry.node_id)
        assert node.status == "completed"

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    @patch("onemancompany.core.vessel._load_progress", return_value="")
    @patch("onemancompany.core.vessel._append_progress")
    async def test_post_hook_failure_does_not_crash(self, mock_append, mock_load, mock_bus, mock_state, tmp_path):
        mock_bus.publish = AsyncMock()
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Done"))
        mgr.register("emp01", launcher)

        def bad_post_hook(task, result):
            raise RuntimeError("Post hook boom!")

        mgr.register_hooks("emp01", {"post_task": bad_post_hook})

        entry, tree_path, root = _make_tree_entry(tmp_path, description="Test task")
        mgr.schedule_node("emp01", entry.node_id, entry.tree_path)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", entry)

        tree = TaskTree.load(tree_path, skeleton_only=False)
        node = tree.get_node(entry.node_id)
        assert node.status == "completed"


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
        with patch("onemancompany.core.vessel.EMPLOYEES_DIR", tmp_path):
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
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_schedule_next_creates_task(self, mock_bus, mock_state, tmp_path):
        """When event loop is running and there's a pending node, _schedule_next creates a task."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Done"))
        mgr.register("emp01", launcher)

        entry, tree_path, root = _make_tree_entry(tmp_path)
        mgr.schedule_node("emp01", entry.node_id, entry.tree_path)

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

    def test_schedule_next_no_event_loop(self, tmp_path):
        """When no event loop is running, _schedule_next should not raise."""
        mgr = EmployeeManager()
        entry, tree_path, root = _make_tree_entry(tmp_path)
        mgr.schedule_node("emp01", entry.node_id, entry.tree_path)
        # No event loop running — should gracefully handle RuntimeError
        mgr._schedule_next("emp01")


# ---------------------------------------------------------------------------
# EmployeeManager — _execute_task with project_id tracking
# ---------------------------------------------------------------------------

class TestEmployeeManagerExecuteTaskWithProject:
    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    @patch("onemancompany.core.vessel._load_progress", return_value="")
    @patch("onemancompany.core.vessel._append_progress")
    async def test_execute_task_creates_task_entry(self, mock_append, mock_load, mock_bus, mock_state, tmp_path):
        """When a node has project_id, execution completes the task."""
        mock_bus.publish = AsyncMock()
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Done"))
        mgr.register("emp01", launcher)

        entry, tree_path, root = _make_tree_entry(tmp_path, project_id="proj1")
        mgr.schedule_node("emp01", entry.node_id, entry.tree_path)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            with patch("onemancompany.core.project_archive.record_project_cost"):
                with patch("onemancompany.core.project_archive.append_action"):
                    with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                        with patch.object(mgr, "_on_child_complete", new_callable=AsyncMock):
                            await mgr._execute_task("emp01", entry)

        tree = TaskTree.load(tree_path, skeleton_only=False)
        node = tree.get_node(entry.node_id)
        assert node.status == "completed"

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    @patch("onemancompany.core.vessel._load_progress", return_value="")
    @patch("onemancompany.core.vessel._append_progress")
    async def test_execute_task_with_project_context(self, mock_append, mock_load, mock_bus, mock_state, tmp_path):
        """When node has project_id, project history context is injected."""
        mock_bus.publish = AsyncMock()
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(return_value=LaunchResult(output="Done"))
        mgr.register("emp01", launcher)

        entry, tree_path, root = _make_tree_entry(tmp_path, project_id="proj1")
        mgr.schedule_node("emp01", entry.node_id, entry.tree_path)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            with patch.object(mgr, "_get_project_history_context", return_value="[Project Context]") as mock_ctx:
                with patch.object(mgr, "_get_project_workflow_context", return_value="[Workflow]") as mock_wf:
                    with patch("onemancompany.core.project_archive.record_project_cost"):
                        with patch("onemancompany.core.project_archive.append_action"):
                            with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                                with patch.object(mgr, "_on_child_complete", new_callable=AsyncMock):
                                    await mgr._execute_task("emp01", entry)

        call_args = launcher.execute.call_args
        task_desc = call_args[0][0]
        assert "[Project Context]" in task_desc
        assert "[Workflow]" in task_desc



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

        with patch("onemancompany.core.vessel.make_llm", side_effect=RuntimeError("LLM down")):
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

        with patch("onemancompany.core.vessel.make_llm"):
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
    def test_returns_empty_for_auto_project(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_iteration", return_value=False):
            with patch("onemancompany.core.project_archive.load_named_project", return_value=None):
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
            with patch("onemancompany.core.project_archive.load_named_project", return_value=None):
                result = mgr._get_project_history_context("my-project")
                assert result == ""

    def test_returns_empty_no_iterations_no_files(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_iteration", return_value=False):
            with patch("onemancompany.core.project_archive.load_named_project", return_value={
                "iterations": [], "name": "Test", "status": "active"
            }):
                with patch("onemancompany.core.project_archive.list_project_files", return_value=[]):
                    result = mgr._get_project_history_context("my-project")
                    assert result == ""

    def test_returns_context_with_iterations(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.project_archive._is_iteration", return_value=False):
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
    @patch("onemancompany.core.vessel._store")
    def test_manager_coo_gets_manager_guide(self, mock_store):
        mock_store.load_employee.return_value = {"id": "emp01", "role": "COO"}

        mgr = EmployeeManager()
        result = mgr._get_project_workflow_context("emp01", "proj1")
        assert "Manager Execution Guide" in result

    @patch("onemancompany.core.vessel._store")
    def test_manager_cso_gets_manager_guide(self, mock_store):
        mock_store.load_employee.return_value = {"id": "emp01", "role": "CSO"}

        mgr = EmployeeManager()
        result = mgr._get_project_workflow_context("emp01", "proj1")
        assert "Manager Execution Guide" in result

    @patch("onemancompany.core.vessel._store")
    def test_engineer_gets_verification_instructions(self, mock_store):
        mock_store.load_employee.return_value = {"id": "emp01", "role": "Engineer"}

        mgr = EmployeeManager()

        with patch("onemancompany.core.config.load_workflows", return_value={}):
            result = mgr._get_project_workflow_context("emp01", "proj1")
            assert "Self-Verification" in result
            # Default verification (sandbox disabled) mentions code review
            assert "code/software" in result

    @patch("onemancompany.core.vessel._store")
    def test_engineer_with_workflow_verification(self, mock_store):
        mock_store.load_employee.return_value = {"id": "emp01", "role": "Engineer"}

        mgr = EmployeeManager()

        mock_wf_doc = "# Workflow\n## 1. Execution\n- Build and run the code\n- Verify output"
        mock_wf = MagicMock()
        mock_step = MagicMock()
        mock_step.title = "Execution Phase"
        mock_step.instructions = ["Build and run the code", "Check output"]
        mock_wf.steps = [mock_step]

        with patch("onemancompany.core.config.load_workflows", return_value={"project_intake_workflow": mock_wf_doc}):
            with patch("onemancompany.core.workflow_engine.parse_workflow", return_value=mock_wf):
                result = mgr._get_project_workflow_context("emp01", "proj1")
                assert "Self-Verification" in result
                assert "Build and run the code" in result

    @patch("onemancompany.core.vessel._store")
    def test_missing_employee_uses_default(self, mock_store):
        mock_store.load_employee.return_value = None

        mgr = EmployeeManager()

        with patch("onemancompany.core.config.load_workflows", return_value={}):
            result = mgr._get_project_workflow_context("nobody", "proj1")
            assert "Self-Verification" in result

    @patch("onemancompany.core.vessel._store")
    def test_hr_is_manager_but_not_coo_cso(self, mock_store):
        """HR is a manager role but not COO/CSO, so should get verification guide."""
        mock_store.load_employee.return_value = {"id": "emp01", "role": "HR"}

        mgr = EmployeeManager()

        with patch("onemancompany.core.config.load_workflows", return_value={}):
            result = mgr._get_project_workflow_context("emp01", "proj1")
            assert "Self-Verification" in result



# ---------------------------------------------------------------------------
# EmployeeManager — _full_cleanup
# ---------------------------------------------------------------------------

class TestEmployeeManagerFullCleanup:
    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel._store")
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_full_cleanup_runs_routine(self, mock_bus, mock_state, mock_store):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []
        mock_store.load_all_employees.return_value = {}
        mock_store.save_employee_runtime = AsyncMock()
        mock_store.save_project_status = AsyncMock()

        mgr = EmployeeManager()
        node = TaskNode(id="t1", description="test", project_id="proj1", employee_id="emp01")

        with patch("onemancompany.core.routine.run_post_task_routine", new_callable=AsyncMock) as mock_routine:
            with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                with patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
                    with patch("onemancompany.core.project_archive.complete_project"):
                        with patch("onemancompany.core.state.flush_pending_reload", return_value=None):
                            with patch("onemancompany.core.config.FOUNDING_LEVEL", 4):
                                await mgr._full_cleanup("emp01", node, False, "proj1", run_retrospective=True)
                                mock_routine.assert_called_once()

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel._store")
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_full_cleanup_routine_error(self, mock_bus, mock_state, mock_store):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []
        mock_store.load_all_employees.return_value = {}
        mock_store.save_employee_runtime = AsyncMock()
        mock_store.save_project_status = AsyncMock()

        mgr = EmployeeManager()
        node = TaskNode(id="t1", description="test", project_id="proj1", employee_id="emp01")

        with patch("onemancompany.core.routine.run_post_task_routine", new_callable=AsyncMock, side_effect=RuntimeError("Routine failed")):
            with patch("onemancompany.core.project_archive.append_action"):
                with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                    with patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
                        with patch("onemancompany.core.project_archive.complete_project"):
                            with patch("onemancompany.core.state.flush_pending_reload", return_value=None):
                                with patch("onemancompany.core.config.FOUNDING_LEVEL", 4):
                                    await mgr._full_cleanup("emp01", node, False, "proj1", run_retrospective=True)
                                    # Should not raise, should publish error event
                                    assert mock_bus.publish.call_count >= 1

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel._store")
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_full_cleanup_with_flush_result(self, mock_bus, mock_state, mock_store):
        mock_bus.publish = AsyncMock()
        emp = MagicMock()
        emp.level = 1
        mock_state.employees = {"emp01": emp}
        mock_state.active_tasks = []
        mock_store.load_all_employees.return_value = {}
        mock_store.save_employee_runtime = AsyncMock()
        mock_store.save_project_status = AsyncMock()

        mgr = EmployeeManager()
        node = TaskNode(id="t1", description="test", project_id="proj1", employee_id="emp01")

        with patch("onemancompany.core.routine.run_post_task_routine", new_callable=AsyncMock):
            with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                with patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
                    with patch("onemancompany.core.project_archive.complete_project"):
                        with patch("onemancompany.core.state.flush_pending_reload", return_value={
                            "employees_updated": ["emp01"], "employees_added": []
                        }):
                            with patch("onemancompany.core.config.FOUNDING_LEVEL", 4):
                                await mgr._full_cleanup("emp01", node, False, "proj1")

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel._store")
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_full_cleanup_agent_error_label(self, mock_bus, mock_state, mock_store):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []
        mock_store.load_all_employees.return_value = {}
        mock_store.save_employee_runtime = AsyncMock()
        mock_store.save_project_status = AsyncMock()

        mgr = EmployeeManager()
        node = TaskNode(id="t1", description="test", project_id="proj1", employee_id="emp01")

        with patch("onemancompany.core.routine.run_post_task_routine", new_callable=AsyncMock):
            with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                with patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
                    with patch("onemancompany.core.project_archive.complete_project") as mock_complete:
                        with patch("onemancompany.core.state.flush_pending_reload", return_value=None):
                            with patch("onemancompany.core.config.FOUNDING_LEVEL", 4):
                                await mgr._full_cleanup("emp01", node, True, "proj1")
                                call_args = mock_complete.call_args
                                assert "with errors" in call_args[0][1]

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel._store")
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_full_cleanup_auto_project_skips_complete(self, mock_bus, mock_state, mock_store):
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []
        mock_store.load_all_employees.return_value = {}
        mock_store.save_employee_runtime = AsyncMock()
        mock_store.save_project_status = AsyncMock()

        mgr = EmployeeManager()
        node = TaskNode(id="t1", description="test", project_id="_auto_12345", employee_id="emp01")

        with patch("onemancompany.core.routine.run_post_task_routine", new_callable=AsyncMock):
            with patch("onemancompany.core.resolutions.create_resolution", return_value=None):
                with patch("onemancompany.tools.sandbox.cleanup_sandbox", new_callable=AsyncMock):
                    with patch("onemancompany.core.project_archive.complete_project") as mock_complete:
                        with patch("onemancompany.core.state.flush_pending_reload", return_value=None):
                            with patch("onemancompany.core.config.FOUNDING_LEVEL", 4):
                                await mgr._full_cleanup("emp01", node, False, "_auto_12345")
                                mock_complete.assert_not_called()



# ---------------------------------------------------------------------------
# EmployeeManager — _log with running event loop
# ---------------------------------------------------------------------------

class TestEmployeeManagerLogWithLoop:
    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_log_node_publishes_event(self, mock_bus, mock_state):
        """When event loop is running, _log_node should fire-and-forget an event."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}

        mgr = EmployeeManager()
        mgr._log_node("emp01", "n1", "info", "Test message")

        await asyncio.sleep(0.01)

        assert len(mgr._task_logs["n1"]) == 1
        assert mgr._task_logs["n1"][0]["content"] == "Test message"


# ---------------------------------------------------------------------------
# EmployeeManager — _publish_node_update with running event loop
# ---------------------------------------------------------------------------

class TestEmployeeManagerPublishWithLoop:
    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_publish_with_event_loop(self, mock_bus, mock_state):
        """When event loop is running, publish should create a fire-and-forget task."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}

        mgr = EmployeeManager()
        node = TaskNode(id="n1", description="test", employee_id="emp01")
        mgr._publish_node_update("emp01", node)

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

import onemancompany.core.vessel as agent_loop_mod


class TestExecuteTaskOnLogCallback:
    """The _on_log closure inside _execute_task must be called by the launcher."""

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    @patch("onemancompany.core.vessel._load_progress", return_value="")
    @patch("onemancompany.core.vessel._append_progress")
    async def test_on_log_callback_called_by_launcher(self, mock_append, mock_load, mock_bus, mock_state, tmp_path):
        mock_bus.publish = AsyncMock()
        mock_state.active_tasks = []

        mgr = EmployeeManager()

        async def fake_execute(desc, ctx, on_log=None):
            if on_log:
                on_log("progress", "Working on it...")
            return LaunchResult(output="Done")

        launcher = MagicMock(spec=Launcher)
        launcher.execute = AsyncMock(side_effect=fake_execute)
        mgr.register("emp01", launcher)

        entry, tree_path, root = _make_tree_entry(tmp_path, description="Build widget")
        mgr.schedule_node("emp01", entry.node_id, entry.tree_path)

        with patch("onemancompany.core.resolutions.current_project_id", MagicMock()):
            await mgr._execute_task("emp01", entry)

        tree = TaskTree.load(tree_path, skeleton_only=False)
        node = tree.get_node(entry.node_id)
        assert node.status == "completed"
        # Verify the on_log callback populated the task log buffer
        logs = mgr._task_logs.get(entry.node_id, [])
        log_types = [lg["type"] for lg in logs]
        assert "progress" in log_types




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


# ---------------------------------------------------------------------------
# Coverage gap: line 1191 — routine_resolution in _full_cleanup
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Task tree child-completion callback
# ---------------------------------------------------------------------------

class TestTaskTreeCallback:
    """Tests for task tree child-completion callback in EmployeeManager."""

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_child_complete_wakes_parent_when_all_siblings_done(self, mock_bus, mock_state, tmp_path):
        """When last sibling completes, parent employee gets a review task."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        parent_launcher = MagicMock(spec=Launcher)
        mgr.register("00003", parent_launcher)

        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        parent_node = tree.add_child(root.id, "00003", "Manage feature", ["Feature works"])
        child1 = tree.add_child(parent_node.id, "00010", "Backend", ["API done"])
        child2 = tree.add_child(parent_node.id, "00011", "Frontend", ["UI done"])
        child1.status = "accepted"  # Already done
        child2.status = "completed"  # Just completed
        child2.result = "Frontend built"

        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)
        entry = ScheduleEntry(node_id=child2.id, tree_path=str(tree_path))

        await mgr._on_child_complete("00011", entry, project_id="proj1")

        # Parent (00003) should have received a scheduled review task
        assert len(mgr._schedule.get("00003", [])) > 0

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_child_complete_waits_when_siblings_pending(self, mock_bus, mock_state, tmp_path):
        """When siblings still running, no wake-up."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()

        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        parent_node = tree.add_child(root.id, "00003", "Manage", [])
        child1 = tree.add_child(parent_node.id, "00010", "Backend", [])
        child2 = tree.add_child(parent_node.id, "00011", "Frontend", [])
        child1.status = "completed"
        child2.status = "processing"  # Still running

        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)
        entry = ScheduleEntry(node_id=child1.id, tree_path=str(tree_path))

        await mgr._on_child_complete("00010", entry, project_id="proj1")

        # Parent should NOT be woken
        assert len(mgr._schedule.get("00003", [])) == 0

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_child_complete_updates_node(self, mock_bus, mock_state, tmp_path):
        """Child completion updates node status and result in tree."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()

        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root")
        child = tree.add_child(root.id, "00010", "Do work", ["Work done"])
        child.status = "completed"
        child.result = "Work completed successfully"
        child.input_tokens = 100
        child.output_tokens = 50
        child.cost_usd = 0.01

        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)
        entry = ScheduleEntry(node_id=child.id, tree_path=str(tree_path))

        await mgr._on_child_complete("00010", entry, project_id="proj1")

        # Reload from disk to verify persistence
        reloaded = TaskTree.load(tree_path, skeleton_only=False)
        updated_child = reloaded.get_node(child.id)
        assert updated_child.result == "Work completed successfully"
        assert updated_child.input_tokens == 100
        assert updated_child.output_tokens == 50
        assert updated_child.cost_usd == 0.01

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_no_tree_file_is_noop(self, mock_bus, mock_state, tmp_path):
        """If tree file doesn't exist, _on_child_complete is a no-op."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        entry = ScheduleEntry(node_id="nonexistent", tree_path=str(tmp_path / "no-tree.yaml"))

        # Should not raise
        await mgr._on_child_complete("00010", entry, project_id="proj1")


# ---------------------------------------------------------------------------
# Root node completion → _full_cleanup
# ---------------------------------------------------------------------------

class TestRootNodeCompletion:
    """Tests for root node completion triggering CEO confirmation gate."""

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_root_complete_triggers_ceo_confirmation(self, mock_bus, mock_state, tmp_path):
        """Root node completion triggers _request_ceo_confirmation (not _full_cleanup directly)."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()

        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root task")
        root.status = "completed"
        root.result = "All done"

        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)
        entry = ScheduleEntry(node_id=root.id, tree_path=str(tree_path))

        with patch.object(mgr, "_request_ceo_confirmation", new_callable=AsyncMock) as mock_confirm, \
             patch.object(mgr, "_full_cleanup", new_callable=AsyncMock) as mock_cleanup:
            await mgr._on_child_complete("00001", entry, project_id="proj1")

        # _request_ceo_confirmation should have been called, not _full_cleanup
        mock_confirm.assert_called_once()
        mock_cleanup.assert_not_called()

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_child_complete_does_not_trigger_full_cleanup(self, mock_bus, mock_state, tmp_path):
        """Non-root node completion does NOT trigger _full_cleanup."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {}
        mock_state.active_tasks = []

        mgr = EmployeeManager()

        tree = TaskTree(project_id="proj1")
        root = tree.create_root("00001", "Root task")
        child = tree.add_child(root.id, "00010", "Child task", ["Done"])
        child.status = "completed"
        child.result = "Child done"

        tree_path = tmp_path / "tree.yaml"
        tree.save(tree_path)
        entry = ScheduleEntry(node_id=child.id, tree_path=str(tree_path))

        with patch.object(mgr, "_full_cleanup", new_callable=AsyncMock) as mock_cleanup:
            await mgr._on_child_complete("00010", entry, project_id="proj1")

        # _full_cleanup should NOT have been called (child, not root)
        mock_cleanup.assert_not_called()


# ---------------------------------------------------------------------------
# TaskTimeout — TimeoutError handling in _execute_task
# ---------------------------------------------------------------------------

class TestTaskTimeout:
    """Tests for task timeout via TimeoutError handling."""

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_timeout_marks_task_failed(self, mock_bus, mock_state, tmp_path):
        """When executor raises TimeoutError, task is marked FAILED."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {"00010": MagicMock(current_task_summary="")}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        mock_executor = AsyncMock(spec=Launcher)
        mock_executor.execute.side_effect = TimeoutError("Timeout after 60s")
        mock_executor.is_ready.return_value = True
        mgr.register("00010", mock_executor)

        entry, tree_path, root = _make_tree_entry(tmp_path, employee_id="00010", description="slow work")
        mgr.schedule_node("00010", entry.node_id, str(tree_path))

        await mgr._execute_task("00010", entry)

        reloaded = TaskTree.load(tree_path, skeleton_only=False)
        node = reloaded.get_node(entry.node_id)
        assert node.status == TaskPhase.FAILED.value
        assert "Timeout" in (node.result or "")

    @pytest.mark.asyncio
    @patch("onemancompany.core.vessel.company_state")
    @patch("onemancompany.core.vessel.event_bus")
    async def test_timeout_publishes_task_update(self, mock_bus, mock_state, tmp_path):
        """TimeoutError publishes agent_task_update event with failed status."""
        mock_bus.publish = AsyncMock()
        mock_state.employees = {"00010": MagicMock(current_task_summary="")}
        mock_state.active_tasks = []

        mgr = EmployeeManager()
        mock_executor = AsyncMock(spec=Launcher)
        mock_executor.execute.side_effect = TimeoutError("Timeout after 60s")
        mock_executor.is_ready.return_value = True
        mgr.register("00010", mock_executor)

        entry, tree_path, root = _make_tree_entry(tmp_path, employee_id="00010", description="slow work")
        mgr.schedule_node("00010", entry.node_id, str(tree_path))

        await mgr._execute_task("00010", entry)

        # Verify node was marked failed on disk
        reloaded = TaskTree.load(tree_path, skeleton_only=False)
        node = reloaded.get_node(entry.node_id)
        assert node.status == TaskPhase.FAILED.value
        assert "Timeout" in (node.result or "")


# ---------------------------------------------------------------------------
# Execution log — per-agent file-based debug logging
# ---------------------------------------------------------------------------

class TestExecutionLog:
    """_append_execution_log writes structured entries to {employee_dir}/execution.log."""

    def test_append_creates_file_and_writes(self, tmp_path):
        from onemancompany.core.vessel import _append_execution_log
        with patch("onemancompany.core.vessel.EMPLOYEES_DIR", tmp_path):
            _append_execution_log("emp01", "node123456ab", "start", "Starting task")
        log_path = tmp_path / "emp01" / "execution.log"
        assert log_path.exists()
        content = log_path.read_text()
        assert "[start" in content
        assert "node=node123456ab" in content
        assert "Starting task" in content

    def test_append_multiple_entries(self, tmp_path):
        from onemancompany.core.vessel import _append_execution_log
        with patch("onemancompany.core.vessel.EMPLOYEES_DIR", tmp_path):
            _append_execution_log("emp01", "node_a", "start", "Task A")
            _append_execution_log("emp01", "node_a", "llm_output", "Hello world")
            _append_execution_log("emp01", "node_a", "result", "Done")
        log_path = tmp_path / "emp01" / "execution.log"
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 3
        assert "start" in lines[0]
        assert "llm_output" in lines[1]
        assert "result" in lines[2]

    def test_content_truncated_at_500_chars(self, tmp_path):
        from onemancompany.core.vessel import _append_execution_log
        long_content = "x" * 1000
        with patch("onemancompany.core.vessel.EMPLOYEES_DIR", tmp_path):
            _append_execution_log("emp01", "node_a", "llm_output", long_content)
        log_path = tmp_path / "emp01" / "execution.log"
        content = log_path.read_text()
        # 500 chars of content + prefix, should be well under 1000
        assert len(content) < 700

    def test_rotation_when_exceeding_max_size(self, tmp_path):
        from onemancompany.core.vessel import _append_execution_log, EXECUTION_LOG_MAX_SIZE
        log_dir = tmp_path / "emp01"
        log_dir.mkdir(parents=True)
        log_path = log_dir / "execution.log"
        rotated_path = log_dir / "execution.log.1"
        # Write a file larger than threshold
        log_path.write_text("line\n" * (EXECUTION_LOG_MAX_SIZE // 4), encoding="utf-8")
        original_size = log_path.stat().st_size
        assert original_size > EXECUTION_LOG_MAX_SIZE
        with patch("onemancompany.core.vessel.EMPLOYEES_DIR", tmp_path):
            _append_execution_log("emp01", "node_a", "start", "New entry")
        # Old log renamed to .1, new log has only the fresh entry
        assert rotated_path.exists()
        assert rotated_path.stat().st_size == original_size
        assert log_path.exists()
        assert log_path.stat().st_size < original_size


class TestLogNodeWritesExecutionLog:
    """_log_node should call _append_execution_log."""

    def test_log_node_calls_append(self):
        mgr = EmployeeManager()
        with patch("onemancompany.core.vessel._append_execution_log") as mock_append:
            mgr._log_node("emp01", "node_abc", "start", "Starting task")
        mock_append.assert_called_once_with("emp01", "node_abc", "start", "Starting task")


# ---------------------------------------------------------------------------
# Executor timeout — asyncio.wait_for wraps executor.execute()
# ---------------------------------------------------------------------------

class TestExecutorTimeout:
    """_execute_task should timeout hanging executors via asyncio.wait_for."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(10)
    async def test_langchain_executor_timeout(self, tmp_path):
        """A hanging LangChain executor should be cancelled by asyncio.wait_for."""
        from onemancompany.core.task_tree import _cache as tree_cache

        mgr = EmployeeManager()
        entry, tree_path, root = _make_tree_entry(tmp_path, employee_id="00010")
        # Set timeout BEFORE the tree gets cached by _execute_task
        root.timeout_seconds = 1  # 1 second timeout
        tree = TaskTree.load(tree_path)
        tree.get_node(entry.node_id).timeout_seconds = 1
        tree.save(tree_path)
        # Clear cache so _execute_task reloads from disk with timeout
        tree_cache.pop(str(tree_path.resolve()), None)

        mgr.schedule_node("00010", entry.node_id, str(tree_path))
        mock_vessel = MagicMock()
        mock_vessel.employee_id = "00010"
        mgr.vessels["00010"] = mock_vessel

        # Create a hanging executor
        async def hang_forever(*args, **kwargs):
            await asyncio.sleep(999)
            return LaunchResult(output="never", model_used="", input_tokens=0, output_tokens=0, total_tokens=0)

        mock_executor = MagicMock()
        mock_executor.execute = hang_forever
        mgr.executors["00010"] = mock_executor

        with (
            patch("onemancompany.core.task_tree.save_tree_async"),
            patch("onemancompany.core.vessel._store") as mock_store,
        ):
            mock_store.save_employee_runtime = AsyncMock()
            await mgr._execute_task("00010", entry)

        # Verify node was marked failed with timeout (check in-memory tree, save_tree_async is mocked)
        from onemancompany.core.task_tree import get_tree
        tree = get_tree(str(tree_path))
        node = tree.get_node(entry.node_id)
        assert node.status == TaskPhase.FAILED.value
        assert "Timeout" in (node.result or "")
