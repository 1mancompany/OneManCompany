"""OpenSandbox integration — container-based code execution for AI agents.

Provides LangChain @tool functions for executing code, running commands,
and managing files inside an isolated sandbox container.
The sandbox server is managed as a subprocess, controlled by config.yaml.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from typing import Any
from urllib.parse import urlparse

from langchain_core.tools import tool

from onemancompany.core.config import load_app_config

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_DEFAULTS = {
    "enabled": False,
    "server_url": "http://localhost:8080",
    "default_image": "opensandbox/code-interpreter:v1.0.1",
    "timeout_seconds": 120,
}


def load_sandbox_config() -> dict:
    """Read sandbox config from the project-level config.yaml (``tools.sandbox`` section).

    Missing keys fall back to ``_CONFIG_DEFAULTS`` so callers never need
    their own fallback values.
    """
    app_cfg = load_app_config()
    data = app_cfg.get("tools", {}).get("sandbox", {}) or {}
    defaults = dict(_CONFIG_DEFAULTS)
    defaults.update(data)
    return defaults


def is_sandbox_enabled() -> bool:
    """Check whether the sandbox module is enabled in config."""
    return bool(load_sandbox_config().get("enabled", False))


def _parse_server_url(server_url: str) -> tuple[str, str]:
    """Parse server_url into (protocol, domain) for ConnectionConfig."""
    parsed = urlparse(server_url)
    protocol = parsed.scheme or "http"
    domain = parsed.netloc or parsed.path  # e.g. "localhost:8080"
    return protocol, domain


# ---------------------------------------------------------------------------
# Server lifecycle (subprocess management)
# ---------------------------------------------------------------------------

_server_process: subprocess.Popen | None = None


def start_sandbox_server() -> None:
    """Start opensandbox-server as a subprocess if enabled.

    Only starts if ``enabled: true`` in config.yaml. Stores the process
    handle in a module-level variable for later cleanup.
    """
    global _server_process

    if not is_sandbox_enabled():
        return

    if _server_process is not None and _server_process.poll() is None:
        # Already running
        return

    cfg = load_sandbox_config()
    server_url = cfg["server_url"]

    # Auto-detect Docker socket on macOS (Docker Desktop uses a non-default path)
    env = os.environ.copy()
    if "DOCKER_HOST" not in env:
        home_socket = os.path.expanduser("~/.docker/run/docker.sock")
        if os.path.exists(home_socket):
            env["DOCKER_HOST"] = f"unix://{home_socket}"

    try:
        _server_process = subprocess.Popen(
            ["opensandbox-server"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        # Brief wait for server readiness
        time.sleep(2)
        if _server_process.poll() is not None:
            stderr_out = ""
            if _server_process.stderr:
                stderr_out = _server_process.stderr.read().decode(errors="replace")[-500:]
            print(f"[sandbox] Server exited immediately with code {_server_process.returncode}")
            if stderr_out:
                print(f"[sandbox] stderr: {stderr_out}")
            _server_process = None
        else:
            print(f"[sandbox] Server started at {server_url} (pid={_server_process.pid})")
    except FileNotFoundError:
        print("[sandbox] opensandbox-server not found in PATH. Is the package installed?")
        _server_process = None
    except Exception as e:
        print(f"[sandbox] Failed to start server: {e}")
        _server_process = None


def stop_sandbox_server() -> None:
    """Terminate the sandbox server subprocess gracefully."""
    global _server_process

    if _server_process is None:
        return

    if _server_process.poll() is not None:
        _server_process = None
        return

    pid = _server_process.pid
    try:
        _server_process.send_signal(signal.SIGTERM)
        try:
            _server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _server_process.kill()
            _server_process.wait(timeout=3)
        print(f"[sandbox] Server stopped (pid={pid})")
    except Exception as e:
        print(f"[sandbox] Error stopping server: {e}")
    finally:
        _server_process = None


# ---------------------------------------------------------------------------
# Sandbox instance management (client-side, async API)
# ---------------------------------------------------------------------------

_sandbox_instance: Any = None
_sandbox_lock = asyncio.Lock()


async def _get_sandbox() -> Any:
    """Lazy-create a Sandbox container on first tool use, reuse instance."""
    global _sandbox_instance

    async with _sandbox_lock:
        if _sandbox_instance is not None:
            return _sandbox_instance

        from opensandbox import Sandbox
        from opensandbox.config.connection import ConnectionConfig

        cfg = load_sandbox_config()
        protocol, domain = _parse_server_url(cfg["server_url"])

        conn = ConnectionConfig(protocol=protocol, domain=domain)
        _sandbox_instance = await Sandbox.create(
            cfg["default_image"],
            connection_config=conn,
        )
        return _sandbox_instance


async def cleanup_sandbox() -> None:
    """Destroy the active sandbox container (called per-task or at shutdown)."""
    global _sandbox_instance

    async with _sandbox_lock:
        if _sandbox_instance is None:
            return
        try:
            await _sandbox_instance.kill()
        except Exception as e:
            print(f"[sandbox] Cleanup error: {e}")
        finally:
            _sandbox_instance = None


def _collect_output(logs: Any) -> tuple[str, str]:
    """Extract stdout/stderr strings from Execution.logs."""
    stdout_parts = []
    stderr_parts = []
    if hasattr(logs, "stdout"):
        for msg in logs.stdout:
            stdout_parts.append(getattr(msg, "text", str(msg)))
    if hasattr(logs, "stderr"):
        for msg in logs.stderr:
            stderr_parts.append(getattr(msg, "text", str(msg)))
    return "\n".join(stdout_parts), "\n".join(stderr_parts)


# ---------------------------------------------------------------------------
# Disabled-guard response
# ---------------------------------------------------------------------------

_DISABLED_RESPONSE = {
    "status": "disabled",
    "message": "Sandbox module is not enabled. Set enabled: true in config.yaml",
}

_SERVER_ERROR_RESPONSE = {
    "status": "error",
    "message": "Sandbox server not available",
}


# ---------------------------------------------------------------------------
# LangChain @tool functions
# ---------------------------------------------------------------------------

@tool
async def sandbox_execute_code(code: str, language: str = "python") -> dict:
    """Execute code in a secure sandbox container.

    Runs code using the OpenSandbox CodeInterpreter. Supports Python, JavaScript,
    and shell scripts. Returns stdout, stderr, and execution status.

    Args:
        code: The source code to execute.
        language: Programming language — "python", "javascript", or "shell".

    Returns:
        A dict with status, stdout, stderr, and exit_code.
    """
    if not is_sandbox_enabled():
        return _DISABLED_RESPONSE

    try:
        sandbox = await _get_sandbox()
        # Write code to a temp file and execute it
        ext_map = {"python": "py", "javascript": "js", "shell": "sh"}
        ext = ext_map.get(language, "py")
        file_path = f"/tmp/code.{ext}"
        await sandbox.files.write_file(file_path, code)

        cmd_map = {"python": f"python3 {file_path}", "javascript": f"node {file_path}", "shell": f"bash {file_path}"}
        cmd = cmd_map.get(language, f"python3 {file_path}")

        result = await sandbox.commands.run(cmd)
        stdout, stderr = _collect_output(result.logs)
        exit_code = 0 if result.error is None else 1
        return {
            "status": "ok",
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
        }
    except ImportError:
        return {"status": "error", "message": "opensandbox package not installed"}
    except Exception as e:
        if "connect" in str(e).lower() or "refused" in str(e).lower():
            return _SERVER_ERROR_RESPONSE
        return {"status": "error", "message": str(e)}


@tool
async def sandbox_run_command(command: str) -> dict:
    """Run a shell command in the sandbox container.

    Executes an arbitrary shell command inside the isolated sandbox environment.
    Useful for installing packages, running build tools, or system operations.

    Args:
        command: The shell command to execute.

    Returns:
        A dict with status, stdout, stderr, and exit_code.
    """
    if not is_sandbox_enabled():
        return _DISABLED_RESPONSE

    try:
        sandbox = await _get_sandbox()
        result = await sandbox.commands.run(command)
        stdout, stderr = _collect_output(result.logs)
        exit_code = 0 if result.error is None else 1
        return {
            "status": "ok",
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
        }
    except Exception as e:
        if "connect" in str(e).lower() or "refused" in str(e).lower():
            return _SERVER_ERROR_RESPONSE
        return {"status": "error", "message": str(e)}


@tool
async def sandbox_write_file(file_path: str, content: str) -> dict:
    """Write a file inside the sandbox container.

    Creates or overwrites a file at the specified path within the sandbox
    filesystem. Useful for preparing input data or creating scripts.

    Args:
        file_path: Absolute path inside the sandbox (e.g. "/home/user/script.py").
        content: The text content to write.

    Returns:
        A dict with status and the file path written.
    """
    if not is_sandbox_enabled():
        return _DISABLED_RESPONSE

    try:
        sandbox = await _get_sandbox()
        await sandbox.files.write_file(file_path, content)
        return {"status": "ok", "path": file_path}
    except Exception as e:
        if "connect" in str(e).lower() or "refused" in str(e).lower():
            return _SERVER_ERROR_RESPONSE
        return {"status": "error", "message": str(e)}


@tool
async def sandbox_read_file(file_path: str) -> dict:
    """Read a file from the sandbox container.

    Retrieves the contents of a file at the specified path within the sandbox
    filesystem.

    Args:
        file_path: Absolute path inside the sandbox (e.g. "/home/user/output.txt").

    Returns:
        A dict with status, path, and content.
    """
    if not is_sandbox_enabled():
        return _DISABLED_RESPONSE

    try:
        sandbox = await _get_sandbox()
        content = await sandbox.files.read_file(file_path)
        return {
            "status": "ok",
            "path": file_path,
            "content": content,
        }
    except Exception as e:
        if "connect" in str(e).lower() or "refused" in str(e).lower():
            return _SERVER_ERROR_RESPONSE
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

SANDBOX_TOOLS = [
    sandbox_execute_code,
    sandbox_run_command,
    sandbox_write_file,
    sandbox_read_file,
]
