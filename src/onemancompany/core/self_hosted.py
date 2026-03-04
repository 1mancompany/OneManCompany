"""Self-hosted employee utilities (on-demand Claude Code sessions).

Self-hosted employees no longer run as long-lived worker processes.
Sessions are started on demand via ``claude_session.run_claude_session()``
whenever a task is dispatched or a 1-1 chat message arrives.

This module provides a thin compatibility layer used by the API routes.
"""

from __future__ import annotations

from onemancompany.core.claude_session import list_sessions


def is_self_hosted_ready(employee_id: str) -> bool:
    """Return True — self-hosted employees are always ready (on-demand)."""
    return True


def get_session_summary(employee_id: str) -> dict:
    """Return a summary of active sessions for the employee."""
    sessions = list_sessions(employee_id)
    return {
        "employee_id": employee_id,
        "session_count": len(sessions),
        "sessions": sessions,
    }
