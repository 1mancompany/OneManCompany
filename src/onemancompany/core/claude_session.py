"""Claude Code daemon session management.

Each self-hosted employee gets a persistent Claude CLI process (daemon) that
stays alive across tasks.  Prompts are sent via stdin using stream-json format,
responses are read from stdout as NDJSON.

Lifecycle:
  1. First prompt  → spawn ``claude -p --input-format stream-json
     --output-format stream-json --session-id <uuid> ...``
  2. Send prompt   → write ``{"type":"user","message":{...}}`` to stdin
  3. Read response → collect NDJSON lines until ``result`` message
  4. Next task     → reuse the same process, send another prompt
  5. Process dies  → auto-restart with ``--resume <uuid>``

Data file: {employee_dir}/sessions.json
Format:    {"project_id": {"session_id": "uuid", "work_dir": "/path",
            "created": "iso", "used": true/false}, ...}
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from onemancompany.core.config import BLOCK_KEY_TEXT, BLOCK_KEY_TYPE, BLOCK_TYPE_TEXT, EMPLOYEES_DIR, ENCODING_UTF8

# Single-file constants
SESSIONS_FILENAME = "sessions.json"
_KEY_SESSION_ID = "session_id"
_KEY_WORK_DIR = "work_dir"
_KEY_CREATED = "created"
_KEY_USED = "used"
_KEY_RUNNING_PID = "running_pid"

# ---------------------------------------------------------------------------
# Per-employee locks — prevent concurrent sends on the same daemon
# ---------------------------------------------------------------------------
_session_locks: dict[str, asyncio.Lock] = {}


def _get_session_lock(employee_id: str, project_id: str) -> asyncio.Lock:
    key = f"{employee_id}:{project_id}"
    if key not in _session_locks:
        _session_locks[key] = asyncio.Lock()
    return _session_locks[key]


# ---------------------------------------------------------------------------
# Session persistence helpers (unchanged)
# ---------------------------------------------------------------------------

def _sessions_file(employee_id: str) -> Path:
    return EMPLOYEES_DIR / employee_id / SESSIONS_FILENAME


def _load_sessions(employee_id: str) -> dict:
    path = _sessions_file(employee_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding=ENCODING_UTF8))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_sessions(employee_id: str, data: dict) -> None:
    path = _sessions_file(employee_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding=ENCODING_UTF8)


def get_or_create_session(
    employee_id: str, project_id: str, work_dir: str = "",
) -> tuple[str, bool]:
    """Return (session_id, is_new)."""
    sessions = _load_sessions(employee_id)
    entry = sessions.get(project_id)
    if entry and entry.get(_KEY_SESSION_ID):
        if entry.get(_KEY_USED):
            return entry[_KEY_SESSION_ID], False
        return entry[_KEY_SESSION_ID], True

    session_id = str(uuid.uuid4())
    sessions[project_id] = {
        _KEY_SESSION_ID: session_id,
        _KEY_WORK_DIR: work_dir,
        _KEY_CREATED: datetime.now(timezone.utc).isoformat(),
        _KEY_USED: False,
    }
    _save_sessions(employee_id, sessions)
    return session_id, True


def _mark_session_used(employee_id: str, project_id: str) -> None:
    sessions = _load_sessions(employee_id)
    entry = sessions.get(project_id)
    if entry and not entry.get(_KEY_USED):
        entry[_KEY_USED] = True
        _save_sessions(employee_id, sessions)


def _save_running_pid(employee_id: str, project_id: str, pid: int) -> None:
    sessions = _load_sessions(employee_id)
    entry = sessions.get(project_id)
    if entry:
        entry[_KEY_RUNNING_PID] = pid
        _save_sessions(employee_id, sessions)


def _clear_running_pid(employee_id: str, project_id: str) -> None:
    sessions = _load_sessions(employee_id)
    entry = sessions.get(project_id)
    if entry and _KEY_RUNNING_PID in entry:
        del entry[_KEY_RUNNING_PID]
        _save_sessions(employee_id, sessions)


# ---------------------------------------------------------------------------
# ClaudeDaemon — persistent Claude CLI process per employee
# ---------------------------------------------------------------------------

# Registry of live daemons: key = "employee_id:project_id"
_daemons: dict[str, "ClaudeDaemon"] = {}


class ClaudeDaemon:
    """A persistent Claude CLI process that accepts prompts via stream-json stdin."""

    def __init__(
        self,
        employee_id: str,
        project_id: str,
        session_id: str,
        is_new: bool,
        mcp_config_path: str | None = None,
        work_dir: str = "",
        max_turns: int = 50,
    ) -> None:
        self.employee_id = employee_id
        self.project_id = project_id
        self.session_id = session_id
        self.is_new = is_new
        self.mcp_config_path = mcp_config_path
        self.work_dir = work_dir or str(EMPLOYEES_DIR / employee_id)
        self.max_turns = max_turns
        self.proc: asyncio.subprocess.Process | None = None
        self._started = False

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def _drain_stderr(self) -> None:
        """Read and log stderr to prevent pipe buffer from filling up."""
        try:
            while self.proc and self.proc.returncode is None:
                line = await self.proc.stderr.readline()
                if not line:
                    break
                text = line.decode(ENCODING_UTF8, errors="replace").strip()
                if text:
                    logger.debug(f"[claude-daemon:stderr] {self.employee_id}: {text[:300]}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[claude-daemon:stderr] drain failed for {self.employee_id}: {e}")

    async def start(self) -> None:
        """Spawn the persistent claude process."""
        cmd = [
            "claude", "--print", "--verbose",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
            "--max-turns", str(self.max_turns),
        ]
        if self.mcp_config_path:
            cmd += ["--mcp-config", self.mcp_config_path]
        if self.is_new:
            cmd += ["--session-id", self.session_id]
        else:
            cmd += ["--resume", self.session_id]

        # Exclude ANTHROPIC_API_KEY so Claude CLI uses its own OAuth auth
        # instead of picking up the company-level token (which is for
        # company-hosted LangChain employees, not self-hosted CLI).
        _exclude_env = {"CLAUDECODE", "ANTHROPIC_API_KEY"}
        env = {k: v for k, v in os.environ.items() if k not in _exclude_env}

        mode = "NEW" if self.is_new else "RESUME"
        logger.info(
            f"[claude-daemon] [{mode}] employee={self.employee_id} "
            f"project={self.project_id} session={self.session_id[:8]}… "
            f"cwd={self.work_dir}"
        )

        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.work_dir,
            env=env,
        )
        _save_running_pid(self.employee_id, self.project_id, self.proc.pid)
        self._started = True
        # Drain stderr in background to prevent pipe buffer deadlock
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        # Drain initial stdout to discard any stale result from --resume
        await self._drain_initial_stdout()
        # After first successful start, future restarts should use --resume
        self.is_new = False

    async def _drain_initial_stdout(self) -> None:
        """Drain any initial stdout output after process start.

        When resuming a session (--resume), Claude CLI may emit messages from
        the previous session (including a stale ``result`` message). If we
        don't consume these before sending the first prompt, ``send_prompt``
        will read the stale result and return immediately.

        Strategy: read lines with a short timeout. Once no more data arrives
        within the timeout window, we assume the initial burst is over.
        """
        if not self.proc or not self.proc.stdout:
            return
        drained = 0
        try:
            while True:
                line = await asyncio.wait_for(
                    self.proc.stdout.readline(), timeout=3.0,
                )
                if not line:
                    # EOF — process exited during drain
                    break
                drained += 1
                line_str = line.decode(ENCODING_UTF8, errors="replace").strip()
                if line_str:
                    try:
                        msg = json.loads(line_str)
                        msg_type = msg.get("type", "")
                        logger.debug(
                            f"[claude-daemon] Drained initial {msg_type} "
                            f"message for employee={self.employee_id}"
                        )
                    except json.JSONDecodeError:
                        logger.debug("[claude-daemon] Drained non-JSON line: {}", line_str[:100])
        except (asyncio.TimeoutError, TimeoutError):
            logger.debug("[claude-daemon] Drain timeout reached — initial drain complete")
        if drained:
            logger.info(
                f"[claude-daemon] Drained {drained} initial message(s) "
                f"for employee={self.employee_id} (stale --resume output)"
            )

    async def send_prompt(self, prompt: str, timeout: int = 600) -> dict:
        """Send a prompt and collect the full response.

        Reads NDJSON lines from stdout until a ``result`` message appears.
        Returns dict with keys: output, model, input_tokens, output_tokens.
        """
        if not self.alive:
            raise RuntimeError("Daemon process is not running")

        # Send user message via stdin
        msg = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": prompt},
        })
        self.proc.stdin.write(msg.encode(ENCODING_UTF8) + b"\n")
        await self.proc.stdin.drain()

        # Collect response
        text_parts: list[str] = []
        result_text = ""
        total_input_tokens = 0
        total_output_tokens = 0
        model_used = ""

        try:
            async with asyncio.timeout(timeout):
                while True:
                    line = await self.proc.stdout.readline()
                    if not line:
                        # Process exited
                        break
                    line_str = line.decode(ENCODING_UTF8, errors="replace").strip()
                    if not line_str:
                        continue
                    try:
                        msg_data = json.loads(line_str)
                    except json.JSONDecodeError:
                        logger.debug(f"[claude-daemon] non-JSON line: {line_str[:200]}")
                        continue

                    msg_type = msg_data.get("type", "")

                    if msg_type == "stream_event":
                        # Extract text deltas for streaming
                        event = msg_data.get("event", {})
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text_parts.append(delta.get("text", ""))
                        # Extract usage from message_delta events
                        elif event.get("type") == "message_delta":
                            usage = event.get("usage", {})
                            if usage.get("output_tokens"):
                                total_output_tokens = usage["output_tokens"]

                    elif msg_type == "assistant":
                        # Complete assistant message — extract text and usage
                        message = msg_data.get("message", {})
                        content = message.get("content", [])
                        for block in content:
                            if isinstance(block, dict) and block.get(BLOCK_KEY_TYPE) == BLOCK_TYPE_TEXT:
                                text_parts.append(block.get(BLOCK_KEY_TEXT, ""))
                        # Extract usage
                        usage = message.get("usage", {})
                        if usage.get("input_tokens"):
                            total_input_tokens += usage["input_tokens"]
                        if usage.get("output_tokens"):
                            total_output_tokens = max(total_output_tokens, usage["output_tokens"])
                        if message.get("model"):
                            model_used = message["model"]

                    elif msg_type == "result":
                        # Final result — response complete
                        result_text = msg_data.get("result", "")
                        # result message may also carry usage/cost info
                        if msg_data.get("input_tokens"):
                            total_input_tokens = max(total_input_tokens, msg_data["input_tokens"])
                        if msg_data.get("output_tokens"):
                            total_output_tokens = max(total_output_tokens, msg_data["output_tokens"])
                        if msg_data.get("model"):
                            model_used = msg_data["model"]
                        _mark_session_used(self.employee_id, self.project_id)
                        break

        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(
                f"[claude-daemon] Timeout after {timeout}s for "
                f"employee={self.employee_id}"
            )
            return {"output": f"[claude-daemon timeout] {timeout}s exceeded",
                    "model": "", "input_tokens": 0, "output_tokens": 0}

        # Prefer result text, fall back to accumulated text deltas
        output = result_text or "".join(text_parts)
        return {
            "output": output.strip(),
            "model": model_used,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
        }

    async def stop(self) -> None:
        """Terminate the daemon process gracefully."""
        if hasattr(self, "_stderr_task") and not self._stderr_task.done():
            self._stderr_task.cancel()
        if self.proc and self.proc.returncode is None:
            logger.info(
                f"[claude-daemon] Stopping employee={self.employee_id} "
                f"pid={self.proc.pid}"
            )
            try:
                self.proc.terminate()
                try:
                    await asyncio.wait_for(self.proc.wait(), timeout=5)
                except (asyncio.TimeoutError, TimeoutError):
                    self.proc.kill()
            except ProcessLookupError:
                logger.debug("Process already exited for employee={} project={}", self.employee_id, self.project_id)
        _clear_running_pid(self.employee_id, self.project_id)
        self.proc = None


async def _get_or_start_daemon(
    employee_id: str,
    project_id: str,
    work_dir: str = "",
    max_turns: int = 50,
    task_id: str = "",
) -> ClaudeDaemon:
    """Get an existing daemon or start a new one for this employee+project."""
    key = f"{employee_id}:{project_id}"

    daemon = _daemons.get(key)
    if daemon and daemon.alive:
        return daemon

    # Clean up dead daemon
    if daemon:
        await daemon.stop()

    # Generate MCP config
    mcp_config_path = None
    try:
        from onemancompany.tools.mcp.config_builder import write_mcp_config
        mcp_config_path = str(write_mcp_config(
            employee_id,
            task_id=task_id,
            project_id=project_id,
            project_dir=work_dir,
        ))
    except Exception as e:
        logger.warning(f"Failed to generate MCP config: {e}")

    # Try to start daemon (may resume existing session)
    session_id, is_new = get_or_create_session(employee_id, project_id, work_dir=work_dir)

    daemon = ClaudeDaemon(
        employee_id=employee_id,
        project_id=project_id,
        session_id=session_id,
        is_new=is_new,
        mcp_config_path=mcp_config_path,
        work_dir=work_dir,
        max_turns=max_turns,
    )
    await daemon.start()

    # If process died during startup/drain (common with --resume: CLI outputs
    # the old result then exits), restart with a fresh session.
    if not daemon.alive:
        logger.warning(
            f"[claude-daemon] Process died after start (--resume output "
            f"consumed). Restarting with new session for employee={employee_id}"
        )
        await daemon.stop()
        new_session_id = str(uuid.uuid4())
        sessions = _load_sessions(employee_id)
        sessions[project_id] = {
            _KEY_SESSION_ID: new_session_id,
            _KEY_WORK_DIR: work_dir,
            _KEY_CREATED: datetime.now(timezone.utc).isoformat(),
            _KEY_USED: False,
        }
        _save_sessions(employee_id, sessions)

        daemon = ClaudeDaemon(
            employee_id=employee_id,
            project_id=project_id,
            session_id=new_session_id,
            is_new=True,
            mcp_config_path=mcp_config_path,
            work_dir=work_dir,
            max_turns=max_turns,
        )
        await daemon.start()

    _daemons[key] = daemon
    return daemon


# ---------------------------------------------------------------------------
# Public API — drop-in replacement for the old run_claude_session
# ---------------------------------------------------------------------------

async def run_claude_session(
    employee_id: str,
    project_id: str,
    prompt: str,
    work_dir: str = "",
    max_turns: int = 50,
    timeout: int = 600,
    task_id: str = "",
) -> dict:
    """Send a prompt to the employee's persistent Claude daemon.

    If no daemon is running, one is started automatically.
    If the daemon died, it is restarted with --resume.

    Returns dict: {output, model, input_tokens, output_tokens}
    """
    _empty = {"output": "", "model": "", "input_tokens": 0, "output_tokens": 0}
    lock = _get_session_lock(employee_id, project_id)

    async with lock:
        try:
            daemon = await _get_or_start_daemon(
                employee_id, project_id, work_dir, max_turns, task_id,
            )
            result = await daemon.send_prompt(prompt, timeout=timeout)

            # If daemon died during execution, try once more with restart
            if not daemon.alive and not result.get("output"):
                logger.warning(
                    f"[claude-daemon] Process died during execution, "
                    f"restarting for employee={employee_id}"
                )
                daemon = await _get_or_start_daemon(
                    employee_id, project_id, work_dir, max_turns, task_id,
                )
                result = await daemon.send_prompt(prompt, timeout=timeout)

            return result
        except FileNotFoundError:
            return {**_empty, "output": "[claude-daemon error] `claude` CLI not found on PATH"}
        except Exception as e:
            logger.error(f"[claude-daemon] Error for employee={employee_id}: {e}")
            return {**_empty, "output": f"[claude-daemon error] {e}"}


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

async def stop_all_daemons() -> int:
    """Stop all running daemon processes. Called on server shutdown."""
    count = 0
    for key, daemon in list(_daemons.items()):
        await daemon.stop()
        count += 1
    _daemons.clear()
    return count


def cleanup_orphan_sessions() -> int:
    """Kill orphaned claude session processes from a previous server run."""
    import signal

    killed = 0
    if not EMPLOYEES_DIR.exists():
        return killed

    for emp_dir in sorted(EMPLOYEES_DIR.iterdir()):
        if not emp_dir.is_dir():
            continue
        employee_id = emp_dir.name
        sessions = _load_sessions(employee_id)
        dirty = False
        for project_id, entry in sessions.items():
            pid = entry.get(_KEY_RUNNING_PID)
            if pid is None:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                killed += 1
                logger.info(
                    f"[session-cleanup] Killed orphan PID {pid} "
                    f"(employee={employee_id} project={project_id}) "
                    f"— session preserved for --resume"
                )
            except ProcessLookupError:
                logger.debug(
                    f"[session-cleanup] PID {pid} already gone "
                    f"(employee={employee_id})"
                )
            except PermissionError:
                logger.warning(
                    f"[session-cleanup] No permission to kill PID {pid} "
                    f"(employee={employee_id})"
                )
            del entry[_KEY_RUNNING_PID]
            dirty = True
        if dirty:
            _save_sessions(employee_id, sessions)

    return killed


# ---------------------------------------------------------------------------
# Query helpers (unchanged)
# ---------------------------------------------------------------------------

def list_sessions(employee_id: str) -> list[dict]:
    """Return all sessions for an employee."""
    sessions = _load_sessions(employee_id)
    result = []
    for pid, entry in sessions.items():
        result.append({
            "project_id": pid,
            _KEY_SESSION_ID: entry.get(_KEY_SESSION_ID, ""),
            _KEY_WORK_DIR: entry.get(_KEY_WORK_DIR, ""),
            _KEY_CREATED: entry.get(_KEY_CREATED, ""),
            _KEY_USED: entry.get(_KEY_USED, False),
        })
    return result


def cleanup_session(employee_id: str, project_id: str) -> None:
    """Remove a session record (does not delete Claude's session files)."""
    sessions = _load_sessions(employee_id)
    if project_id in sessions:
        del sessions[project_id]
        _save_sessions(employee_id, sessions)


def get_daemon_status() -> list[dict]:
    """Return status of all active daemons (for monitoring)."""
    result = []
    for daemon_key, daemon in _daemons.items():
        result.append({
            "key": daemon_key,
            "employee_id": daemon.employee_id,
            "project_id": daemon.project_id,
            "session_id": daemon.session_id[:8] + "…",
            "alive": daemon.alive,
            "pid": daemon.proc.pid if daemon.proc else None,
        })
    return result
