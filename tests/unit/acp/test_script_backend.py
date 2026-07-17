"""Unit tests for acp/backends/script_backend.py."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestScriptBackendExecute:
    @pytest.mark.asyncio
    async def test_script_backend_spawns_subprocess(self, tmp_path):
        """execute() spawns bash subprocess with launch.sh and returns result dict."""
        # Create a fake launch.sh
        launch_sh = tmp_path / "launch.sh"
        launch_sh.write_text("#!/bin/bash\necho done")
        launch_sh.chmod(0o755)

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b'{"output":"done","model":"script","input_tokens":0,"output_tokens":0}', b"")
        )
        mock_proc.returncode = 0

        mock_create = AsyncMock(return_value=mock_proc)

        with (
            patch(
                "onemancompany.acp.backends.script_backend.EMPLOYEES_DIR",
                tmp_path.parent,
            ),
            patch("asyncio.create_subprocess_exec", mock_create),
        ):
            # Write an employee launch.sh under EMPLOYEES_DIR/employee_id/
            emp_dir = tmp_path.parent / "00010"
            emp_dir.mkdir(parents=True, exist_ok=True)
            (emp_dir / "launch.sh").write_text("#!/bin/bash\necho done")

            from onemancompany.acp.backends.script_backend import ScriptAcpBackend  # noqa: PLC0415

            backend = ScriptAcpBackend(
                employee_id="00010",
                server_url="http://localhost:8000",
            )
            cancel_event = asyncio.Event()
            result = await backend.execute(
                task_description="Run a script",
                client=None,
                session_id="sess-1",
                cancel_event=cancel_event,
            )

        mock_create.assert_called_once()
        call_args = mock_create.call_args
        assert "bash" in call_args[0]

        assert isinstance(result, dict)
        assert result["output"] == "done"
        assert result["model"] == "script"
        assert result["error"] is None
        assert result["tokens"]["input"] == 0
        assert result["tokens"]["output"] == 0

    @pytest.mark.asyncio
    async def test_script_backend_handles_plain_stdout(self, tmp_path):
        """execute() falls back to raw stdout string when JSON parse fails."""
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"plain output text", b""))
        mock_proc.returncode = 0

        mock_create = AsyncMock(return_value=mock_proc)

        with (
            patch("onemancompany.acp.backends.script_backend.EMPLOYEES_DIR", tmp_path.parent),
            patch("asyncio.create_subprocess_exec", mock_create),
        ):
            emp_dir = tmp_path.parent / "00011"
            emp_dir.mkdir(parents=True, exist_ok=True)
            (emp_dir / "launch.sh").write_text("#!/bin/bash\necho plain output text")

            from onemancompany.acp.backends.script_backend import ScriptAcpBackend  # noqa: PLC0415

            backend = ScriptAcpBackend(employee_id="00011", server_url="http://localhost:8000")
            result = await backend.execute(
                task_description="Do something",
                client=None,
                session_id="sess-2",
                cancel_event=asyncio.Event(),
            )

        assert result["output"] == "plain output text"
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_script_backend_nonzero_exit_returns_error(self, tmp_path):
        """execute() returns error when subprocess exits non-zero."""
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"script failed"))
        mock_proc.returncode = 1

        mock_create = AsyncMock(return_value=mock_proc)

        with (
            patch("onemancompany.acp.backends.script_backend.EMPLOYEES_DIR", tmp_path.parent),
            patch("asyncio.create_subprocess_exec", mock_create),
        ):
            emp_dir = tmp_path.parent / "00012"
            emp_dir.mkdir(parents=True, exist_ok=True)
            (emp_dir / "launch.sh").write_text("#!/bin/bash\nexit 1")

            from onemancompany.acp.backends.script_backend import ScriptAcpBackend  # noqa: PLC0415

            backend = ScriptAcpBackend(employee_id="00012", server_url="http://localhost:8000")
            result = await backend.execute(
                task_description="Fail",
                client=None,
                session_id="sess-3",
                cancel_event=asyncio.Event(),
            )

        assert result["error"] is not None
        assert "script failed" in result["error"] or "exit" in result["error"]

    def test_set_model_is_noop(self):
        """set_model() is a no-op for script backend."""
        from onemancompany.acp.backends.script_backend import ScriptAcpBackend

        backend = ScriptAcpBackend(employee_id="00010", server_url="http://localhost:8000")
        backend.set_model("any-model")  # should not raise

    def test_set_config_is_noop(self):
        """set_config() is a no-op for script backend."""
        from onemancompany.acp.backends.script_backend import ScriptAcpBackend

        backend = ScriptAcpBackend(employee_id="00010", server_url="http://localhost:8000")
        backend.set_config("key", "value")  # should not raise
