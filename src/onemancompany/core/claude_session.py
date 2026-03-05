"""On-demand Claude Code session management.

Each self-hosted employee gets per-project sessions.  A session is simply a
UUID that is passed to ``claude --print`` so that the Claude CLI can persist /
resume conversational context across invocations.

- First call:  ``claude --print --session-id <uuid> <prompt>``  (create)
- Subsequent:  ``claude --print --resume <uuid> <prompt>``      (resume)

Data file: {employee_dir}/sessions.json
Format:    {"project_id": {"session_id": "uuid", "work_dir": "/path",
            "created": "iso", "used": true/false}, ...}
"""

from __future__ import annotations

import asyncio
from loguru import logger
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from onemancompany.core.config import EMPLOYEES_DIR


# ---------------------------------------------------------------------------
# Per-session locks — prevent concurrent `claude` processes on the same session
# ---------------------------------------------------------------------------
_session_locks: dict[str, asyncio.Lock] = {}


def _get_session_lock(employee_id: str, project_id: str) -> asyncio.Lock:
    key = f"{employee_id}:{project_id}"
    if key not in _session_locks:
        _session_locks[key] = asyncio.Lock()
    return _session_locks[key]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sessions_file(employee_id: str) -> Path:
    return EMPLOYEES_DIR / employee_id / "sessions.json"


def _load_sessions(employee_id: str) -> dict:
    path = _sessions_file(employee_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_sessions(employee_id: str, data: dict) -> None:
    path = _sessions_file(employee_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_or_create_session(employee_id: str, project_id: str, work_dir: str = "") -> tuple[str, bool]:
    """Return (session_id, is_new).

    If the project already has a session that has been used at least once,
    ``is_new`` is ``False`` and the caller should use ``--resume``.
    Otherwise a fresh UUID is created (``is_new=True``, use ``--session-id``).
    """
    sessions = _load_sessions(employee_id)
    entry = sessions.get(project_id)
    if entry and entry.get("session_id"):
        if entry.get("used"):
            return entry["session_id"], False  # existing, resume
        # Created but never successfully used — treat as new
        return entry["session_id"], True

    session_id = str(uuid.uuid4())
    sessions[project_id] = {
        "session_id": session_id,
        "work_dir": work_dir,
        "created": datetime.now(timezone.utc).isoformat(),
        "used": False,
    }
    _save_sessions(employee_id, sessions)
    return session_id, True


def _mark_session_used(employee_id: str, project_id: str) -> None:
    """Mark a session as successfully used (so future calls use --resume)."""
    sessions = _load_sessions(employee_id)
    entry = sessions.get(project_id)
    if entry and not entry.get("used"):
        entry["used"] = True
        _save_sessions(employee_id, sessions)


async def run_claude_session(
    employee_id: str,
    project_id: str,
    prompt: str,
    work_dir: str = "",
    max_turns: int = 50,
    timeout: int = 600,
) -> str:
    """Execute a Claude CLI call and return stdout.

    - First call for a project: ``claude --print --session-id <uuid> <prompt>``
    - Subsequent calls:         ``claude --print --resume <uuid> <prompt>``
    """
    lock = _get_session_lock(employee_id, project_id)

    async with lock:
        session_id, is_new = get_or_create_session(employee_id, project_id, work_dir=work_dir)
        cwd = work_dir or str(EMPLOYEES_DIR / employee_id)

        base = [
            "claude", "--print",
            "--dangerously-skip-permissions",
            "--max-turns", str(max_turns),
        ]
        if is_new:
            cmd = base + ["--session-id", session_id, prompt]
        else:
            cmd = base + ["--resume", session_id, prompt]

        # Strip CLAUDECODE env var so the child process doesn't think it's nested
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        mode = "NEW" if is_new else "RESUME"
        print(f"[claude-session] [{mode}] employee={employee_id} project={project_id} "
              f"session={session_id[:8]}… cwd={cwd}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode("utf-8", errors="replace").strip()
            if proc.returncode != 0 and not output:
                err = stderr.decode("utf-8", errors="replace").strip()
                output = f"[claude-session error] exit={proc.returncode}\n{err[:2000]}"
            else:
                # Success — mark session as used for future --resume calls
                _mark_session_used(employee_id, project_id)
            return output
        except asyncio.TimeoutError:
            try:
                proc.terminate()  # type: ignore[possibly-undefined]
            except Exception as _e:
                logger.warning("Failed to terminate timed-out process: %s", _e)
            return f"[claude-session timeout] Session {session_id[:8]}… timed out after {timeout}s"
        except FileNotFoundError:
            return "[claude-session error] `claude` CLI not found on PATH"
        except Exception as e:
            return f"[claude-session error] {e}"


def list_sessions(employee_id: str) -> list[dict]:
    """Return all sessions for an employee."""
    sessions = _load_sessions(employee_id)
    result = []
    for pid, entry in sessions.items():
        result.append({
            "project_id": pid,
            "session_id": entry.get("session_id", ""),
            "work_dir": entry.get("work_dir", ""),
            "created": entry.get("created", ""),
            "used": entry.get("used", False),
        })
    return result


def cleanup_session(employee_id: str, project_id: str) -> None:
    """Remove a session record (does not delete Claude's session files)."""
    sessions = _load_sessions(employee_id)
    if project_id in sessions:
        del sessions[project_id]
        _save_sessions(employee_id, sessions)
