"""Unit tests for tools/sandbox/__init__.py — sandbox server lifecycle and tool functions."""

from __future__ import annotations

import signal
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from onemancompany.tools import sandbox as sandbox_mod


# ---------------------------------------------------------------------------
# load_sandbox_config
# ---------------------------------------------------------------------------


class TestLoadSandboxConfig:
    def test_defaults_when_no_config(self):
        with patch.object(sandbox_mod, "load_app_config", return_value={}):
            cfg = sandbox_mod.load_sandbox_config()
        assert cfg["enabled"] is False
        assert cfg["server_url"] == "http://localhost:8080"
        assert cfg["default_image"] == "opensandbox/code-interpreter:v1.0.1"
        assert cfg["timeout_seconds"] == 120

    def test_overrides_from_config(self):
        app_cfg = {"tools": {"sandbox": {"enabled": True, "server_url": "http://myhost:9090"}}}
        with patch.object(sandbox_mod, "load_app_config", return_value=app_cfg):
            cfg = sandbox_mod.load_sandbox_config()
        assert cfg["enabled"] is True
        assert cfg["server_url"] == "http://myhost:9090"
        assert cfg["default_image"] == "opensandbox/code-interpreter:v1.0.1"

    def test_none_sandbox_section_uses_defaults(self):
        app_cfg = {"tools": {"sandbox": None}}
        with patch.object(sandbox_mod, "load_app_config", return_value=app_cfg):
            cfg = sandbox_mod.load_sandbox_config()
        assert cfg["enabled"] is False

    def test_missing_tools_section(self):
        app_cfg = {"other": "stuff"}
        with patch.object(sandbox_mod, "load_app_config", return_value=app_cfg):
            cfg = sandbox_mod.load_sandbox_config()
        assert cfg["enabled"] is False


# ---------------------------------------------------------------------------
# is_sandbox_enabled
# ---------------------------------------------------------------------------


class TestIsSandboxEnabled:
    def test_enabled_true(self):
        with patch.object(sandbox_mod, "load_sandbox_config", return_value={"enabled": True}):
            assert sandbox_mod.is_sandbox_enabled() is True

    def test_enabled_false(self):
        with patch.object(sandbox_mod, "load_sandbox_config", return_value={"enabled": False}):
            assert sandbox_mod.is_sandbox_enabled() is False

    def test_enabled_missing(self):
        with patch.object(sandbox_mod, "load_sandbox_config", return_value={}):
            assert sandbox_mod.is_sandbox_enabled() is False


# ---------------------------------------------------------------------------
# _parse_server_url
# ---------------------------------------------------------------------------


class TestParseServerUrl:
    def test_http_url(self):
        protocol, domain = sandbox_mod._parse_server_url("http://localhost:8080")
        assert protocol == "http"
        assert domain == "localhost:8080"

    def test_https_url(self):
        protocol, domain = sandbox_mod._parse_server_url("https://sandbox.example.com:443")
        assert protocol == "https"
        assert domain == "sandbox.example.com:443"

    def test_bare_url_no_scheme(self):
        # urlparse("localhost:8080") treats "localhost" as scheme
        protocol, domain = sandbox_mod._parse_server_url("localhost:8080")
        assert protocol == "localhost"
        assert domain == "8080"

    def test_double_slash_url_defaults_to_http(self):
        # urlparse("//localhost:8080") has empty scheme, code defaults to "http"
        protocol, domain = sandbox_mod._parse_server_url("//localhost:8080")
        assert protocol == "http"  # `or "http"` fallback
        assert domain == "localhost:8080"


# ---------------------------------------------------------------------------
# start_sandbox_server
# ---------------------------------------------------------------------------


class TestStartSandboxServer:
    def setup_method(self):
        # Reset module-level state
        sandbox_mod._server_process = None

    def test_noop_when_disabled(self):
        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=False):
            sandbox_mod.start_sandbox_server()
        assert sandbox_mod._server_process is None

    def test_noop_when_already_running(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        sandbox_mod._server_process = mock_proc

        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            sandbox_mod.start_sandbox_server()

        # Should not create new process
        assert sandbox_mod._server_process is mock_proc

    def test_starts_server_successfully(self, monkeypatch):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running after sleep
        mock_proc.pid = 12345

        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "load_sandbox_config", return_value={
                "enabled": True,
                "server_url": "http://localhost:8080",
            }):
                with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
                    with patch("time.sleep"):
                        with patch("os.path.exists", return_value=False):
                            sandbox_mod._server_process = None
                            sandbox_mod.start_sandbox_server()

        assert sandbox_mod._server_process is mock_proc

    def test_server_exits_immediately(self, monkeypatch, capsys):
        mock_proc = MagicMock()
        # First poll after Popen: None (during already-running check), then returncode (exited)
        mock_proc.poll.return_value = 1
        mock_proc.returncode = 1
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.read.return_value = b"error: port in use"
        mock_proc.pid = 99

        sandbox_mod._server_process = None

        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "load_sandbox_config", return_value={
                "enabled": True,
                "server_url": "http://localhost:8080",
            }):
                with patch("subprocess.Popen", return_value=mock_proc):
                    with patch("time.sleep"):
                        with patch("os.path.exists", return_value=False):
                            sandbox_mod.start_sandbox_server()

        assert sandbox_mod._server_process is None
        captured = capsys.readouterr()
        assert "Server exited immediately" in captured.out
        assert "port in use" in captured.out

    def test_server_exits_no_stderr(self, monkeypatch, capsys):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        mock_proc.returncode = 1
        mock_proc.stderr = None
        mock_proc.pid = 99

        sandbox_mod._server_process = None

        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "load_sandbox_config", return_value={
                "enabled": True,
                "server_url": "http://localhost:8080",
            }):
                with patch("subprocess.Popen", return_value=mock_proc):
                    with patch("time.sleep"):
                        with patch("os.path.exists", return_value=False):
                            sandbox_mod.start_sandbox_server()

        assert sandbox_mod._server_process is None
        captured = capsys.readouterr()
        assert "Server exited immediately" in captured.out

    def test_file_not_found_error(self, monkeypatch, capsys):
        sandbox_mod._server_process = None

        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "load_sandbox_config", return_value={
                "enabled": True,
                "server_url": "http://localhost:8080",
            }):
                with patch("subprocess.Popen", side_effect=FileNotFoundError("not found")):
                    with patch("os.path.exists", return_value=False):
                        sandbox_mod.start_sandbox_server()

        assert sandbox_mod._server_process is None
        captured = capsys.readouterr()
        assert "not found in PATH" in captured.out

    def test_generic_exception(self, monkeypatch, capsys):
        sandbox_mod._server_process = None

        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "load_sandbox_config", return_value={
                "enabled": True,
                "server_url": "http://localhost:8080",
            }):
                with patch("subprocess.Popen", side_effect=RuntimeError("oops")):
                    with patch("os.path.exists", return_value=False):
                        sandbox_mod.start_sandbox_server()

        assert sandbox_mod._server_process is None
        captured = capsys.readouterr()
        assert "Failed to start server" in captured.out

    def test_auto_detect_docker_socket(self, monkeypatch):
        """When DOCKER_HOST not set and ~/.docker/run/docker.sock exists."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 100

        sandbox_mod._server_process = None

        env_without_docker = {k: v for k, v in __import__("os").environ.items() if k != "DOCKER_HOST"}
        monkeypatch.delenv("DOCKER_HOST", raising=False)

        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "load_sandbox_config", return_value={
                "enabled": True,
                "server_url": "http://localhost:8080",
            }):
                with patch("os.path.exists", return_value=True):
                    with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
                        with patch("time.sleep"):
                            sandbox_mod.start_sandbox_server()

        # Check that DOCKER_HOST was set in the env passed to Popen
        call_kwargs = mock_popen.call_args
        env_arg = call_kwargs[1]["env"]
        assert "DOCKER_HOST" in env_arg

    def teardown_method(self):
        sandbox_mod._server_process = None


# ---------------------------------------------------------------------------
# stop_sandbox_server
# ---------------------------------------------------------------------------


class TestStopSandboxServer:
    def setup_method(self):
        sandbox_mod._server_process = None

    def test_noop_when_none(self):
        sandbox_mod.stop_sandbox_server()
        assert sandbox_mod._server_process is None

    def test_noop_when_already_exited(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # already exited
        sandbox_mod._server_process = mock_proc

        sandbox_mod.stop_sandbox_server()
        assert sandbox_mod._server_process is None

    def test_graceful_stop(self, capsys):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        mock_proc.pid = 42
        mock_proc.wait.return_value = 0
        sandbox_mod._server_process = mock_proc

        sandbox_mod.stop_sandbox_server()

        mock_proc.send_signal.assert_called_once_with(signal.SIGTERM)
        mock_proc.wait.assert_called_once_with(timeout=5)
        assert sandbox_mod._server_process is None
        captured = capsys.readouterr()
        assert "Server stopped" in captured.out

    def test_force_kill_on_timeout(self, capsys):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 42
        mock_proc.wait.side_effect = [subprocess.TimeoutExpired("cmd", 5), None]
        sandbox_mod._server_process = mock_proc

        sandbox_mod.stop_sandbox_server()

        mock_proc.kill.assert_called_once()
        assert sandbox_mod._server_process is None

    def test_exception_during_stop(self, capsys):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 42
        mock_proc.send_signal.side_effect = OSError("permission denied")
        sandbox_mod._server_process = mock_proc

        sandbox_mod.stop_sandbox_server()

        assert sandbox_mod._server_process is None
        captured = capsys.readouterr()
        assert "Error stopping server" in captured.out

    def teardown_method(self):
        sandbox_mod._server_process = None


# ---------------------------------------------------------------------------
# _get_sandbox
# ---------------------------------------------------------------------------


class TestGetSandbox:
    def setup_method(self):
        sandbox_mod._sandbox_instance = None

    @pytest.mark.asyncio
    async def test_creates_sandbox_on_first_call(self):
        mock_sandbox = MagicMock()

        mock_sandbox_cls = MagicMock()
        mock_sandbox_cls.create = AsyncMock(return_value=mock_sandbox)

        with patch("onemancompany.tools.sandbox.Sandbox", mock_sandbox_cls, create=True):
            with patch("onemancompany.tools.sandbox.ConnectionConfig", MagicMock(), create=True):
                with patch("onemancompany.tools.sandbox.Volume", MagicMock(), create=True):
                    with patch("onemancompany.tools.sandbox.Host", MagicMock(), create=True):
                        with patch.object(sandbox_mod, "load_sandbox_config", return_value={
                            "server_url": "http://localhost:8080",
                            "default_image": "test-image",
                        }):
                            with patch("onemancompany.core.config.PROJECTS_DIR", MagicMock(resolve=MagicMock(return_value="/fake/projects"))):
                                # We need to mock the imports inside _get_sandbox
                                import sys
                                mock_opensandbox = MagicMock()
                                mock_opensandbox.Sandbox = mock_sandbox_cls
                                mock_config_mod = MagicMock()
                                mock_models_mod = MagicMock()

                                with patch.dict(sys.modules, {
                                    "opensandbox": mock_opensandbox,
                                    "opensandbox.config": MagicMock(),
                                    "opensandbox.config.connection": mock_config_mod,
                                    "opensandbox.models": MagicMock(),
                                    "opensandbox.models.sandboxes": mock_models_mod,
                                }):
                                    result = await sandbox_mod._get_sandbox()

        assert result is mock_sandbox
        sandbox_mod._sandbox_instance = None

    @pytest.mark.asyncio
    async def test_reuses_existing_instance(self):
        existing = MagicMock()
        sandbox_mod._sandbox_instance = existing

        result = await sandbox_mod._get_sandbox()
        assert result is existing
        sandbox_mod._sandbox_instance = None

    def teardown_method(self):
        sandbox_mod._sandbox_instance = None


# ---------------------------------------------------------------------------
# cleanup_sandbox
# ---------------------------------------------------------------------------


class TestCleanupSandbox:
    def setup_method(self):
        sandbox_mod._sandbox_instance = None

    @pytest.mark.asyncio
    async def test_noop_when_none(self):
        await sandbox_mod.cleanup_sandbox()
        assert sandbox_mod._sandbox_instance is None

    @pytest.mark.asyncio
    async def test_kills_and_clears(self):
        mock_sb = MagicMock()
        mock_sb.kill = AsyncMock()
        sandbox_mod._sandbox_instance = mock_sb

        await sandbox_mod.cleanup_sandbox()

        mock_sb.kill.assert_awaited_once()
        assert sandbox_mod._sandbox_instance is None

    @pytest.mark.asyncio
    async def test_handles_kill_exception(self, capsys):
        mock_sb = MagicMock()
        mock_sb.kill = AsyncMock(side_effect=RuntimeError("kill failed"))
        sandbox_mod._sandbox_instance = mock_sb

        await sandbox_mod.cleanup_sandbox()

        assert sandbox_mod._sandbox_instance is None
        captured = capsys.readouterr()
        assert "Cleanup error" in captured.out

    def teardown_method(self):
        sandbox_mod._sandbox_instance = None


# ---------------------------------------------------------------------------
# _collect_output
# ---------------------------------------------------------------------------


class TestCollectOutput:
    def test_with_stdout_and_stderr(self):
        logs = MagicMock()
        msg1 = MagicMock()
        msg1.text = "hello"
        msg2 = MagicMock()
        msg2.text = "world"
        err1 = MagicMock()
        err1.text = "error line"

        logs.stdout = [msg1, msg2]
        logs.stderr = [err1]

        stdout, stderr = sandbox_mod._collect_output(logs)
        assert stdout == "hello\nworld"
        assert stderr == "error line"

    def test_with_no_text_attr(self):
        logs = MagicMock()
        logs.stdout = ["raw string"]
        logs.stderr = []

        stdout, stderr = sandbox_mod._collect_output(logs)
        assert stdout == "raw string"
        assert stderr == ""

    def test_with_no_stdout_stderr_attrs(self):
        logs = MagicMock(spec=[])  # no attributes
        stdout, stderr = sandbox_mod._collect_output(logs)
        assert stdout == ""
        assert stderr == ""

    def test_empty_lists(self):
        logs = MagicMock()
        logs.stdout = []
        logs.stderr = []
        stdout, stderr = sandbox_mod._collect_output(logs)
        assert stdout == ""
        assert stderr == ""


# ---------------------------------------------------------------------------
# sandbox_execute_code
# ---------------------------------------------------------------------------


class TestSandboxExecuteCode:
    @pytest.mark.asyncio
    async def test_disabled_returns_disabled_response(self):
        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=False):
            result = await sandbox_mod.sandbox_execute_code.ainvoke(
                {"code": "print('hello')", "language": "python"}
            )
        assert result["status"] == "disabled"

    @pytest.mark.asyncio
    async def test_execute_python_code(self):
        mock_sandbox = MagicMock()
        mock_sandbox.files.write_file = AsyncMock()
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.logs = MagicMock()
        mock_result.logs.stdout = []
        mock_result.logs.stderr = []
        mock_sandbox.commands.run = AsyncMock(return_value=mock_result)

        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", return_value=mock_sandbox):
                result = await sandbox_mod.sandbox_execute_code.ainvoke(
                    {"code": "print('hello')", "language": "python"}
                )

        assert result["status"] == "ok"
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_execute_javascript_code(self):
        mock_sandbox = MagicMock()
        mock_sandbox.files.write_file = AsyncMock()
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.logs = MagicMock()
        mock_result.logs.stdout = []
        mock_result.logs.stderr = []
        mock_sandbox.commands.run = AsyncMock(return_value=mock_result)

        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", return_value=mock_sandbox):
                result = await sandbox_mod.sandbox_execute_code.ainvoke(
                    {"code": "console.log('hi')", "language": "javascript"}
                )

        assert result["status"] == "ok"
        # Verify the js extension was used
        mock_sandbox.files.write_file.assert_awaited_once()
        file_path_arg = mock_sandbox.files.write_file.call_args[0][0]
        assert file_path_arg.endswith(".js")

    @pytest.mark.asyncio
    async def test_execute_shell_code(self):
        mock_sandbox = MagicMock()
        mock_sandbox.files.write_file = AsyncMock()
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.logs = MagicMock()
        mock_result.logs.stdout = []
        mock_result.logs.stderr = []
        mock_sandbox.commands.run = AsyncMock(return_value=mock_result)

        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", return_value=mock_sandbox):
                result = await sandbox_mod.sandbox_execute_code.ainvoke(
                    {"code": "echo hi", "language": "shell"}
                )

        assert result["status"] == "ok"
        file_path_arg = mock_sandbox.files.write_file.call_args[0][0]
        assert file_path_arg.endswith(".sh")

    @pytest.mark.asyncio
    async def test_execute_unknown_language_defaults_to_python(self):
        mock_sandbox = MagicMock()
        mock_sandbox.files.write_file = AsyncMock()
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.logs = MagicMock()
        mock_result.logs.stdout = []
        mock_result.logs.stderr = []
        mock_sandbox.commands.run = AsyncMock(return_value=mock_result)

        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", return_value=mock_sandbox):
                result = await sandbox_mod.sandbox_execute_code.ainvoke(
                    {"code": "code here", "language": "rust"}
                )

        assert result["status"] == "ok"
        file_path_arg = mock_sandbox.files.write_file.call_args[0][0]
        assert file_path_arg.endswith(".py")

    @pytest.mark.asyncio
    async def test_execute_with_error(self):
        mock_sandbox = MagicMock()
        mock_sandbox.files.write_file = AsyncMock()
        mock_result = MagicMock()
        mock_result.error = "SyntaxError"
        mock_result.logs = MagicMock()
        mock_result.logs.stdout = []
        mock_result.logs.stderr = []
        mock_sandbox.commands.run = AsyncMock(return_value=mock_result)

        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", return_value=mock_sandbox):
                result = await sandbox_mod.sandbox_execute_code.ainvoke(
                    {"code": "bad code", "language": "python"}
                )

        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_import_error(self):
        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", side_effect=ImportError("no opensandbox")):
                result = await sandbox_mod.sandbox_execute_code.ainvoke(
                    {"code": "print(1)", "language": "python"}
                )

        assert result["status"] == "error"
        assert "not installed" in result["message"]

    @pytest.mark.asyncio
    async def test_connection_error(self):
        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", side_effect=Exception("connection refused")):
                result = await sandbox_mod.sandbox_execute_code.ainvoke(
                    {"code": "print(1)", "language": "python"}
                )

        assert result["status"] == "error"
        assert result["message"] == "Sandbox server not available"

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", side_effect=RuntimeError("unknown")):
                result = await sandbox_mod.sandbox_execute_code.ainvoke(
                    {"code": "print(1)", "language": "python"}
                )

        assert result["status"] == "error"
        assert result["message"] == "unknown"


# ---------------------------------------------------------------------------
# sandbox_run_command
# ---------------------------------------------------------------------------


class TestSandboxRunCommand:
    @pytest.mark.asyncio
    async def test_disabled_returns_disabled_response(self):
        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=False):
            result = await sandbox_mod.sandbox_run_command.ainvoke({"command": "ls"})
        assert result["status"] == "disabled"

    @pytest.mark.asyncio
    async def test_run_command_success(self):
        mock_sandbox = MagicMock()
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.logs = MagicMock()
        mock_result.logs.stdout = []
        mock_result.logs.stderr = []
        mock_sandbox.commands.run = AsyncMock(return_value=mock_result)

        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", return_value=mock_sandbox):
                result = await sandbox_mod.sandbox_run_command.ainvoke({"command": "ls -la"})

        assert result["status"] == "ok"
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_run_command_with_error(self):
        mock_sandbox = MagicMock()
        mock_result = MagicMock()
        mock_result.error = "command not found"
        mock_result.logs = MagicMock()
        mock_result.logs.stdout = []
        mock_result.logs.stderr = []
        mock_sandbox.commands.run = AsyncMock(return_value=mock_result)

        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", return_value=mock_sandbox):
                result = await sandbox_mod.sandbox_run_command.ainvoke({"command": "nonexistent"})

        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_connection_error(self):
        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", side_effect=Exception("connect error")):
                result = await sandbox_mod.sandbox_run_command.ainvoke({"command": "ls"})

        assert result["status"] == "error"
        assert result["message"] == "Sandbox server not available"

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", side_effect=RuntimeError("boom")):
                result = await sandbox_mod.sandbox_run_command.ainvoke({"command": "ls"})

        assert result["status"] == "error"
        assert result["message"] == "boom"


# ---------------------------------------------------------------------------
# sandbox_write_file
# ---------------------------------------------------------------------------


class TestSandboxWriteFile:
    @pytest.mark.asyncio
    async def test_disabled_returns_disabled_response(self):
        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=False):
            result = await sandbox_mod.sandbox_write_file.ainvoke(
                {"file_path": "/tmp/test.txt", "content": "hello"}
            )
        assert result["status"] == "disabled"

    @pytest.mark.asyncio
    async def test_write_file_success(self):
        mock_sandbox = MagicMock()
        mock_sandbox.files.write_file = AsyncMock()

        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", return_value=mock_sandbox):
                result = await sandbox_mod.sandbox_write_file.ainvoke(
                    {"file_path": "/tmp/test.txt", "content": "hello world"}
                )

        assert result["status"] == "ok"
        assert result["path"] == "/tmp/test.txt"
        mock_sandbox.files.write_file.assert_awaited_once_with("/tmp/test.txt", "hello world")

    @pytest.mark.asyncio
    async def test_connection_error(self):
        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", side_effect=Exception("refused")):
                result = await sandbox_mod.sandbox_write_file.ainvoke(
                    {"file_path": "/tmp/test.txt", "content": "hello"}
                )

        assert result["status"] == "error"
        assert result["message"] == "Sandbox server not available"

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", side_effect=RuntimeError("disk full")):
                result = await sandbox_mod.sandbox_write_file.ainvoke(
                    {"file_path": "/tmp/test.txt", "content": "hello"}
                )

        assert result["status"] == "error"
        assert result["message"] == "disk full"


# ---------------------------------------------------------------------------
# sandbox_read_file
# ---------------------------------------------------------------------------


class TestSandboxReadFile:
    @pytest.mark.asyncio
    async def test_disabled_returns_disabled_response(self):
        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=False):
            result = await sandbox_mod.sandbox_read_file.ainvoke(
                {"file_path": "/tmp/test.txt"}
            )
        assert result["status"] == "disabled"

    @pytest.mark.asyncio
    async def test_read_file_success(self):
        mock_sandbox = MagicMock()
        mock_sandbox.files.read_file = AsyncMock(return_value="file contents")

        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", return_value=mock_sandbox):
                result = await sandbox_mod.sandbox_read_file.ainvoke(
                    {"file_path": "/tmp/test.txt"}
                )

        assert result["status"] == "ok"
        assert result["path"] == "/tmp/test.txt"
        assert result["content"] == "file contents"

    @pytest.mark.asyncio
    async def test_connection_error(self):
        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", side_effect=Exception("connection refused")):
                result = await sandbox_mod.sandbox_read_file.ainvoke(
                    {"file_path": "/tmp/test.txt"}
                )

        assert result["status"] == "error"
        assert result["message"] == "Sandbox server not available"

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        with patch.object(sandbox_mod, "is_sandbox_enabled", return_value=True):
            with patch.object(sandbox_mod, "_get_sandbox", side_effect=RuntimeError("not found")):
                result = await sandbox_mod.sandbox_read_file.ainvoke(
                    {"file_path": "/tmp/test.txt"}
                )

        assert result["status"] == "error"
        assert result["message"] == "not found"


# ---------------------------------------------------------------------------
# SANDBOX_TOOLS export
# ---------------------------------------------------------------------------


class TestSandboxToolsExport:
    def test_all_tools_exported(self):
        assert len(sandbox_mod.SANDBOX_TOOLS) == 4
        names = {t.name for t in sandbox_mod.SANDBOX_TOOLS}
        assert names == {
            "sandbox_execute_code",
            "sandbox_run_command",
            "sandbox_write_file",
            "sandbox_read_file",
        }
