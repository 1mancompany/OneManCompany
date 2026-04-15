"""Coverage tests for core/claude_session.py — missing lines."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# write_llm_trace (lines 49-57)
# ---------------------------------------------------------------------------

class TestWriteLlmTrace:
    def test_skips_when_not_debug(self, monkeypatch):
        import onemancompany.core.config as config_mod
        import onemancompany.core.claude_session as cs_mod
        monkeypatch.setattr(config_mod, "IS_DEBUG", False)
        cs_mod.write_llm_trace("proj1", {"key": "val"})  # should not raise

    def test_skips_empty_project_id(self, monkeypatch):
        import onemancompany.core.config as config_mod
        import onemancompany.core.claude_session as cs_mod
        monkeypatch.setattr(config_mod, "IS_DEBUG", True)
        cs_mod.write_llm_trace("", {"key": "val"})  # no-op

    def test_skips_default_project_id(self, monkeypatch):
        import onemancompany.core.config as config_mod
        import onemancompany.core.claude_session as cs_mod
        monkeypatch.setattr(config_mod, "IS_DEBUG", True)
        cs_mod.write_llm_trace("default", {"key": "val"})  # no-op

    def test_writes_trace_line(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        import onemancompany.core.claude_session as cs_mod
        monkeypatch.setattr(config_mod, "IS_DEBUG", True)
        monkeypatch.setattr(cs_mod, "PROJECTS_DIR", tmp_path)
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()
        cs_mod.write_llm_trace("proj1", {"event": "test"})
        trace_path = proj_dir / "llm_traces.jsonl"
        assert trace_path.exists()
        lines = trace_path.read_text().strip().split("\n")
        assert json.loads(lines[0])["event"] == "test"


# ---------------------------------------------------------------------------
# Session persistence helpers (lines 81-82, 140-152)
# ---------------------------------------------------------------------------

class TestSessionPersistence:
    def test_get_and_remove_lock(self):
        import onemancompany.core.claude_session as cs_mod
        lock = cs_mod._get_session_lock("emp1", "proj1")
        assert isinstance(lock, asyncio.Lock)
        cs_mod._remove_session_lock("emp1", "proj1")
        assert "emp1:proj1" not in cs_mod._session_locks

    def test_save_and_clear_running_pid(self, tmp_path, monkeypatch):
        import onemancompany.core.claude_session as cs_mod
        monkeypatch.setattr(cs_mod, "EMPLOYEES_DIR", tmp_path)
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()

        # First create a session
        sid, is_new = cs_mod.get_or_create_session("00010", "proj1", "/tmp/work")
        assert is_new is True

        # Save PID
        cs_mod._save_running_pid("00010", "proj1", 12345)
        sessions = cs_mod._load_sessions("00010")
        assert sessions["proj1"]["running_pid"] == 12345

        # Clear PID
        cs_mod._clear_running_pid("00010", "proj1")
        sessions = cs_mod._load_sessions("00010")
        assert "running_pid" not in sessions["proj1"]


# ---------------------------------------------------------------------------
# get_or_create_session (lines 140-144, 148-152)
# ---------------------------------------------------------------------------

class TestGetOrCreateSession:
    def test_returns_existing_used(self, tmp_path, monkeypatch):
        import onemancompany.core.claude_session as cs_mod
        monkeypatch.setattr(cs_mod, "EMPLOYEES_DIR", tmp_path)
        (tmp_path / "00010").mkdir()

        sid, is_new = cs_mod.get_or_create_session("00010", "proj1")
        assert is_new is True
        cs_mod._mark_session_used("00010", "proj1")

        sid2, is_new2 = cs_mod.get_or_create_session("00010", "proj1")
        assert sid2 == sid
        assert is_new2 is False  # used=True → not new


# ---------------------------------------------------------------------------
# ClaudeDaemon (lines 173-337, 350-662)
# ---------------------------------------------------------------------------

class TestClaudeDaemon:
    def test_alive_property(self):
        from onemancompany.core.claude_session import ClaudeDaemon
        daemon = ClaudeDaemon("emp1", "proj1", "sid1", True)
        assert daemon.alive is False
        daemon.proc = MagicMock()
        daemon.proc.returncode = None
        assert daemon.alive is True
        daemon.proc.returncode = 0
        assert daemon.alive is False

    @pytest.mark.asyncio
    async def test_drain_stderr(self):
        from onemancompany.core.claude_session import ClaudeDaemon
        daemon = ClaudeDaemon("emp1", "proj1", "sid1", True)
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stderr = MagicMock()

        # Return one line then empty
        mock_proc.stderr.readline = AsyncMock(side_effect=[
            b"some warning\n",
            b"",
        ])
        daemon.proc = mock_proc
        # Simulate process exiting after readline returns empty
        def set_return_code():
            mock_proc.returncode = 0
        mock_proc.stderr.readline.side_effect = [b"line\n", b""]
        await daemon._drain_stderr()

    def test_trace_assistant_message(self, tmp_path, monkeypatch):
        import onemancompany.core.claude_session as cs_mod
        from onemancompany.core.claude_session import ClaudeDaemon
        monkeypatch.setattr("onemancompany.core.config.IS_DEBUG", False)

        daemon = ClaudeDaemon("emp1", "proj1", "sid1", True)
        message = {
            "model": "claude-3",
            "usage": {"input_tokens": 100},
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "tool_use", "name": "bash", "id": "t1", "input": {}},
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
                {"type": "thinking", "text": "hmm..."},
            ],
        }
        # Should not raise (traces are no-ops when not debug)
        daemon._trace_assistant_message(message)

    def test_accumulate_debug_assistant(self):
        from onemancompany.core.claude_session import ClaudeDaemon
        messages: list[dict] = []
        msg = {
            "content": [
                {"type": "text", "text": "Hello world"},
                {"type": "tool_use", "id": "t1", "name": "bash", "input": {"cmd": "ls"}},
                {"type": "tool_result", "tool_use_id": "t1", "content": "file.txt"},
            ],
        }
        ClaudeDaemon._accumulate_debug_assistant(messages, msg)
        # Should produce tool result entry and assistant entry
        assert any(m.get("role") == "tool" for m in messages)
        assert any(m.get("role") == "assistant" for m in messages)
        assistant = [m for m in messages if m["role"] == "assistant"][0]
        assert "tool_calls" in assistant
        assert assistant["content"] == "Hello world"

    def test_accumulate_debug_assistant_tool_result_dict_content(self):
        from onemancompany.core.claude_session import ClaudeDaemon
        messages: list[dict] = []
        msg = {
            "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": {"key": "value"}},
            ],
        }
        ClaudeDaemon._accumulate_debug_assistant(messages, msg)
        tool_msg = [m for m in messages if m.get("role") == "tool"][0]
        assert '"key"' in tool_msg["content"]

    def test_accumulate_debug_assistant_empty(self):
        from onemancompany.core.claude_session import ClaudeDaemon
        messages: list[dict] = []
        msg = {"content": [{"type": "unknown"}]}
        ClaudeDaemon._accumulate_debug_assistant(messages, msg)
        assert len(messages) == 0


# ---------------------------------------------------------------------------
# send_prompt (lines 511-632)
# ---------------------------------------------------------------------------

class TestSendPrompt:
    @pytest.mark.asyncio
    async def test_send_prompt_not_alive(self):
        from onemancompany.core.claude_session import ClaudeDaemon
        daemon = ClaudeDaemon("emp1", "proj1", "sid1", True)
        with pytest.raises(RuntimeError, match="not running"):
            await daemon.send_prompt("hello")

    @pytest.mark.asyncio
    async def test_send_prompt_result_message(self, monkeypatch):
        import onemancompany.core.claude_session as cs_mod
        from onemancompany.core.claude_session import ClaudeDaemon
        monkeypatch.setattr("onemancompany.core.config.IS_DEBUG", False)

        daemon = ClaudeDaemon("emp1", "proj1", "sid1", True)
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()

        # Simulate stdout returning result message
        result_msg = json.dumps({"type": "result", "result": "Done!",
                                 "input_tokens": 50, "output_tokens": 100,
                                 "model": "claude-3"})
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = AsyncMock(return_value=result_msg.encode() + b"\n")
        daemon.proc = mock_proc

        with patch("onemancompany.core.claude_session._mark_session_used"):
            resp = await daemon.send_prompt("hello", timeout=5)
        assert resp["output"] == "Done!"
        assert resp["model"] == "claude-3"

    @pytest.mark.asyncio
    async def test_send_prompt_timeout(self, monkeypatch):
        import onemancompany.core.claude_session as cs_mod
        from onemancompany.core.claude_session import ClaudeDaemon
        monkeypatch.setattr("onemancompany.core.config.IS_DEBUG", False)

        daemon = ClaudeDaemon("emp1", "proj1", "sid1", True)
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()

        # Simulate readline never completing
        async def slow_readline():
            await asyncio.sleep(100)
            return b""

        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = slow_readline
        daemon.proc = mock_proc

        resp = await daemon.send_prompt("hello", timeout=0)
        assert "timeout" in resp["output"].lower()

    @pytest.mark.asyncio
    async def test_send_prompt_stream_event(self, monkeypatch):
        import onemancompany.core.claude_session as cs_mod
        from onemancompany.core.claude_session import ClaudeDaemon
        monkeypatch.setattr("onemancompany.core.config.IS_DEBUG", False)

        daemon = ClaudeDaemon("emp1", "proj1", "sid1", True)
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()

        stream_msg = json.dumps({
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}},
        })
        message_delta = json.dumps({
            "type": "stream_event",
            "event": {"type": "message_delta", "usage": {"output_tokens": 10}},
        })
        result_msg = json.dumps({"type": "result", "result": ""})

        lines = [
            stream_msg.encode() + b"\n",
            message_delta.encode() + b"\n",
            result_msg.encode() + b"\n",
        ]
        call_idx = [0]

        async def mock_readline():
            if call_idx[0] < len(lines):
                line = lines[call_idx[0]]
                call_idx[0] += 1
                return line
            return b""

        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = mock_readline
        daemon.proc = mock_proc

        with patch("onemancompany.core.claude_session._mark_session_used"):
            resp = await daemon.send_prompt("hello", timeout=5)
        assert resp["output"] == "Hi"


# ---------------------------------------------------------------------------
# stop (lines 641-662)
# ---------------------------------------------------------------------------

class TestDaemonStop:
    @pytest.mark.asyncio
    async def test_stop_kills_on_timeout(self, tmp_path, monkeypatch):
        import onemancompany.core.claude_session as cs_mod
        from onemancompany.core.claude_session import ClaudeDaemon
        monkeypatch.setattr(cs_mod, "EMPLOYEES_DIR", tmp_path)
        (tmp_path / "emp1").mkdir()

        daemon = ClaudeDaemon("emp1", "proj1", "sid1", True)
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 12345
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()

        async def slow_wait():
            await asyncio.sleep(100)

        mock_proc.wait = slow_wait
        daemon.proc = mock_proc
        daemon._stderr_task = MagicMock()
        daemon._stderr_task.done.return_value = True

        # wait_for will timeout, then kill
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            await daemon.stop()
        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_process_lookup_error(self, tmp_path, monkeypatch):
        import onemancompany.core.claude_session as cs_mod
        from onemancompany.core.claude_session import ClaudeDaemon
        monkeypatch.setattr(cs_mod, "EMPLOYEES_DIR", tmp_path)
        (tmp_path / "emp1").mkdir()

        daemon = ClaudeDaemon("emp1", "proj1", "sid1", True)
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.terminate.side_effect = ProcessLookupError
        daemon.proc = mock_proc
        daemon._stderr_task = MagicMock()
        daemon._stderr_task.done.return_value = True

        await daemon.stop()  # should not raise


# ---------------------------------------------------------------------------
# stop_all_daemons (lines 809-814)
# ---------------------------------------------------------------------------

class TestStopAllDaemons:
    @pytest.mark.asyncio
    async def test_stop_all(self, monkeypatch):
        import onemancompany.core.claude_session as cs_mod
        daemon = MagicMock()
        daemon.stop = AsyncMock()
        cs_mod._daemons["emp1:proj1"] = daemon
        count = await cs_mod.stop_all_daemons()
        assert count == 1
        daemon.stop.assert_called_once()
        assert len(cs_mod._daemons) == 0


# ---------------------------------------------------------------------------
# cleanup_orphan_sessions (lines 819-858)
# ---------------------------------------------------------------------------

class TestCleanupOrphanSessions:
    def test_cleanup_orphans(self, tmp_path, monkeypatch):
        import onemancompany.core.claude_session as cs_mod
        import os
        monkeypatch.setattr(cs_mod, "EMPLOYEES_DIR", tmp_path)
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        cs_mod._save_sessions("00010", {
            "proj1": {"session_id": "sid1", "running_pid": os.getpid()},
            "proj2": {"session_id": "sid2", "running_pid": 9999999},
        })

        with patch("os.kill") as mock_kill:
            mock_kill.side_effect = [None, ProcessLookupError]
            count = cs_mod.cleanup_orphan_sessions()
        assert count == 1  # Only the first one was killed

    def test_cleanup_no_employees(self, tmp_path, monkeypatch):
        import onemancompany.core.claude_session as cs_mod
        monkeypatch.setattr(cs_mod, "EMPLOYEES_DIR", tmp_path / "nonexistent")
        assert cs_mod.cleanup_orphan_sessions() == 0

    def test_cleanup_permission_error(self, tmp_path, monkeypatch):
        import onemancompany.core.claude_session as cs_mod
        monkeypatch.setattr(cs_mod, "EMPLOYEES_DIR", tmp_path)
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        cs_mod._save_sessions("00010", {
            "proj1": {"session_id": "sid1", "running_pid": 12345},
        })
        with patch("os.kill", side_effect=PermissionError):
            count = cs_mod.cleanup_orphan_sessions()
        assert count == 0


# ---------------------------------------------------------------------------
# list_sessions / cleanup_session / get_daemon_status (lines 890-900)
# ---------------------------------------------------------------------------

class TestQueryHelpers:
    def test_list_sessions(self, tmp_path, monkeypatch):
        import onemancompany.core.claude_session as cs_mod
        monkeypatch.setattr(cs_mod, "EMPLOYEES_DIR", tmp_path)
        (tmp_path / "00010").mkdir()
        cs_mod.get_or_create_session("00010", "proj1")

        result = cs_mod.list_sessions("00010")
        assert len(result) == 1
        assert result[0]["project_id"] == "proj1"

    def test_cleanup_session(self, tmp_path, monkeypatch):
        import onemancompany.core.claude_session as cs_mod
        monkeypatch.setattr(cs_mod, "EMPLOYEES_DIR", tmp_path)
        (tmp_path / "00010").mkdir()
        cs_mod.get_or_create_session("00010", "proj1")
        cs_mod.cleanup_session("00010", "proj1")
        sessions = cs_mod._load_sessions("00010")
        assert "proj1" not in sessions

    def test_get_daemon_status(self, monkeypatch):
        import onemancompany.core.claude_session as cs_mod
        daemon = MagicMock()
        daemon.employee_id = "emp1"
        daemon.project_id = "proj1"
        daemon.session_id = "abcdef1234567890"
        daemon.alive = True
        daemon.proc = MagicMock()
        daemon.proc.pid = 12345
        cs_mod._daemons["emp1:proj1"] = daemon

        result = cs_mod.get_daemon_status()
        assert len(result) == 1
        assert result[0]["alive"] is True
        # Cleanup
        cs_mod._daemons.pop("emp1:proj1", None)


# ---------------------------------------------------------------------------
# _run_claude_cmd (lines 173-193)
# ---------------------------------------------------------------------------

class TestRunClaudeCmd:
    @pytest.mark.asyncio
    async def test_successful_command(self):
        import onemancompany.core.claude_session as cs_mod
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("asyncio.wait_for", return_value=(b"ok", b"")):
            result = await cs_mod._run_claude_cmd(["echo"], "test", {})
        assert result is True

    @pytest.mark.asyncio
    async def test_failed_command(self):
        import onemancompany.core.claude_session as cs_mod
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"err", b"msg"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("asyncio.wait_for", return_value=(b"err", b"msg")):
            result = await cs_mod._run_claude_cmd(["bad"], "test", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_timeout(self):
        import onemancompany.core.claude_session as cs_mod
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            result = await cs_mod._run_claude_cmd(["slow"], "test", {})
        assert result is False


# ---------------------------------------------------------------------------
# _ensure_plugins (lines 202-238)
# ---------------------------------------------------------------------------

class TestEnsurePlugins:
    @pytest.mark.asyncio
    async def test_ensures_plugins_with_marketplace(self, monkeypatch):
        import onemancompany.core.claude_session as cs_mod
        monkeypatch.setattr(cs_mod, "_ensured_plugins", set())

        calls = []
        async def mock_run(cmd, label, env):
            calls.append((cmd, label))
            return True

        with patch.object(cs_mod, "_run_claude_cmd", side_effect=mock_run):
            await cs_mod._ensure_plugins(["superpowers@superpowers-marketplace"])

        assert len(calls) == 3  # marketplace add + install + enable
        assert "superpowers@superpowers-marketplace" in cs_mod._ensured_plugins
        cs_mod._ensured_plugins.discard("superpowers@superpowers-marketplace")

    @pytest.mark.asyncio
    async def test_skips_already_ensured(self, monkeypatch):
        import onemancompany.core.claude_session as cs_mod
        monkeypatch.setattr(cs_mod, "_ensured_plugins", {"already-done"})
        # Should be no-op
        await cs_mod._ensure_plugins(["already-done"])
