"""Tests for SubprocessExecutor — subprocess-based task execution with two-stage kill."""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.vessel import LaunchResult, TaskContext


class TestSubprocessExecutor:
    @pytest.mark.asyncio
    async def test_execute_happy_path(self):
        """Execute runs launch.sh and captures JSON output."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (
            b'{"output":"done","model":"test","input_tokens":10,"output_tokens":5}',
            b"",
        )
        mock_proc.returncode = 0
        mock_proc.pid = 12345

        ctx = TaskContext(project_id="p1", work_dir="/tmp", employee_id="00010", task_id="t1")

        with patch("onemancompany.core.subprocess_executor.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await exe.execute("do work", ctx)

        assert result.output == "done"
        assert result.model_used == "test"
        assert result.input_tokens == 10
        assert result.output_tokens == 5

    @pytest.mark.asyncio
    async def test_execute_plain_text_output(self):
        """Non-JSON stdout is returned as plain text."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"plain text result", b"")
        mock_proc.returncode = 0
        mock_proc.pid = 12345

        ctx = TaskContext(project_id="p1", work_dir="/tmp", employee_id="00010", task_id="t1")

        with patch("onemancompany.core.subprocess_executor.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await exe.execute("do work", ctx)

        assert result.output == "plain text result"

    @pytest.mark.asyncio
    async def test_execute_nonzero_exit(self):
        """Non-zero exit returns error in output."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"something failed")
        mock_proc.returncode = 1
        mock_proc.pid = 12345

        ctx = TaskContext(project_id="p1", work_dir="/tmp", employee_id="00010", task_id="t1")

        with patch("onemancompany.core.subprocess_executor.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await exe.execute("do work", ctx)

        assert "Error" in result.output
        assert "exit 1" in result.output

    @pytest.mark.asyncio
    async def test_execute_timeout_raises(self):
        """Timeout triggers cancel and raises TimeoutError."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh", timeout_seconds=1)

        mock_proc = AsyncMock()
        mock_proc.communicate.side_effect = asyncio.TimeoutError()
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.pid = 12345
        mock_proc.returncode = None

        ctx = TaskContext(project_id="p1", work_dir="/tmp", employee_id="00010", task_id="t1")

        with patch("onemancompany.core.subprocess_executor.asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(TimeoutError, match="Timeout"):
                await exe.execute("do work", ctx)

        mock_proc.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_prompt_written_to_temp_file(self):
        """Task description is written to a temp file and passed via env var."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b'{"output":"ok"}', b"")
        mock_proc.returncode = 0
        mock_proc.pid = 12345

        ctx = TaskContext(project_id="p1", work_dir="/tmp", employee_id="00010", task_id="t1")
        captured_env = {}

        async def capture_exec(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            # Verify the prompt file exists and has correct content
            prompt_path = kwargs["env"]["OMC_TASK_DESCRIPTION_FILE"]
            with open(prompt_path) as f:
                assert f.read() == "hello from CEO"
            return mock_proc

        with patch("onemancompany.core.subprocess_executor.asyncio.create_subprocess_exec", side_effect=capture_exec):
            await exe.execute("hello from CEO", ctx)

        assert "OMC_TASK_DESCRIPTION_FILE" in captured_env
        # Temp file should be cleaned up after execution
        assert not os.path.exists(captured_env["OMC_TASK_DESCRIPTION_FILE"])

    @pytest.mark.asyncio
    async def test_prompt_file_cleaned_up_on_error(self):
        """Temp prompt file is cleaned up even when execution fails."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")

        mock_proc = AsyncMock()
        mock_proc.communicate.side_effect = asyncio.TimeoutError()
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.pid = 12345
        mock_proc.returncode = None

        ctx = TaskContext(project_id="p1", work_dir="/tmp", employee_id="00010", task_id="t1")
        captured_path = {}

        async def capture_exec(*args, **kwargs):
            captured_path["file"] = kwargs["env"]["OMC_TASK_DESCRIPTION_FILE"]
            return mock_proc

        with patch("onemancompany.core.subprocess_executor.asyncio.create_subprocess_exec", side_effect=capture_exec):
            with pytest.raises(TimeoutError):
                await exe.execute("test prompt", ctx)

        # File should still be cleaned up
        assert not os.path.exists(captured_path["file"])

    @pytest.mark.asyncio
    async def test_cancel_graceful(self):
        """cancel() exits after SIGTERM if process dies within grace period."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")

        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.pid = 999
        # First wait succeeds (process exits)
        mock_proc.wait = AsyncMock(return_value=0)
        exe._process = mock_proc

        await exe.cancel()

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_force_kill(self):
        """cancel() sends SIGKILL after grace period if process won't die."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor, _KILL_POLL_INTERVAL

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")

        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.pid = 999
        # wait always times out until kill
        call_count = 0

        async def mock_wait():
            nonlocal call_count
            call_count += 1
            if call_count <= 6:  # 6 * 5s = 30s grace period
                raise asyncio.TimeoutError()
            return -9

        mock_proc.wait = mock_wait
        exe._process = mock_proc

        await exe.cancel()

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_already_exited(self):
        """cancel() is a no-op if process already exited."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")

        mock_proc = MagicMock()
        mock_proc.returncode = 0  # already exited
        mock_proc.terminate = MagicMock()
        exe._process = mock_proc

        await exe.cancel()

        mock_proc.terminate.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_no_process(self):
        """cancel() is a no-op if no process was started."""
        from onemancompany.core.subprocess_executor import SubprocessExecutor

        exe = SubprocessExecutor(employee_id="00010", script_path="/tmp/test.sh")
        await exe.cancel()  # should not raise


class TestSubprocessAdapterRegistration:
    def test_subprocess_adapter_registered(self):
        """SubprocessAdapter is registered and resolvable."""
        from onemancompany.core.conversation_adapters import (
            EXECUTOR_TYPE_SUBPROCESS,
            get_adapter,
        )

        adapter_cls = get_adapter(EXECUTOR_TYPE_SUBPROCESS)
        assert adapter_cls.__name__ == "SubprocessAdapter"

    def test_executor_class_map_contains_subprocess(self):
        """SubprocessExecutor maps to subprocess type."""
        from onemancompany.core.conversation_adapters import (
            EXECUTOR_TYPE_SUBPROCESS,
            _EXECUTOR_CLASS_MAP,
        )

        assert _EXECUTOR_CLASS_MAP["SubprocessExecutor"] == EXECUTOR_TYPE_SUBPROCESS

    @pytest.mark.asyncio
    async def test_subprocess_adapter_injects_company_context(self):
        """SubprocessAdapter prepends company context to conversation prompt."""
        from onemancompany.core.conversation_adapters import SubprocessAdapter
        from onemancompany.core.conversation import Conversation, Message
        from onemancompany.core.models import ConversationType

        adapter = SubprocessAdapter()

        conv = Conversation(
            id="test-conv",
            employee_id="00010",
            type=ConversationType.ONE_ON_ONE,
            phase="active",
            tools_enabled=False,
            metadata={},
        )
        messages = []
        new_msg = Message(sender="00001", role="ceo", text="Hello")

        mock_executor = AsyncMock()
        mock_executor.execute.return_value = LaunchResult(output="reply from agent")

        captured_prompt = {}

        async def capture_execute(prompt, ctx):
            captured_prompt["value"] = prompt
            return LaunchResult(output="reply from agent")

        mock_executor.execute = capture_execute

        mock_em = MagicMock()
        mock_em.executors = {"00010": mock_executor}
        mock_em._build_company_context_block.return_value = "[Company Context]\n## Your Persona\nI am OpenClaw\n[/Company Context]"

        with patch("onemancompany.core.conversation_adapters._get_employee_executor", return_value=mock_executor), \
             patch("onemancompany.core.conversation_adapters.employee_manager", mock_em, create=True), \
             patch("onemancompany.core.vessel.employee_manager", mock_em), \
             patch("onemancompany.core.conversation_adapters._resolve_conversation_work_dir", return_value="/tmp"):
            result = await adapter.send(conv, messages, new_msg)

        assert result == "reply from agent"
        assert "[Company Context]" in captured_prompt["value"]
        assert "I am OpenClaw" in captured_prompt["value"]
        assert "Hello" in captured_prompt["value"]
