"""Claude CLI ACP Backend — wraps run_claude_session for ACP subprocess execution.

Delegates to the persistent Claude daemon that manages its own OAuth auth,
MCP config, and session continuity.  The backend reads project/task context
from environment variables set by the ACP host process.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from loguru import logger

# Lazy import — heavy transitive deps (langchain, etc.) should not load at import time.
# Resolved inside execute() to avoid circular imports and speed up module load.
# from onemancompany.core.claude_session import run_claude_session


def _import_run_claude_session():
    """Lazily import run_claude_session to avoid heavy dep at module load."""
    from onemancompany.core.claude_session import run_claude_session  # noqa: PLC0415
    return run_claude_session


# Re-export for patching in tests (module-level name resolved at call time)
def run_claude_session(*args, **kwargs):  # type: ignore[misc]
    """Thin forwarder — replaced in tests via patch on this module."""
    return _import_run_claude_session()(*args, **kwargs)


_DAEMON_ERROR_PREFIX = "[claude-daemon error]"


class ClaudeCliAcpBackend:
    """ACP backend that delegates to the Claude CLI persistent daemon.

    The daemon manages its own authentication (Claude CLI OAuth), MCP config,
    and multi-turn session continuity.  This backend is intentionally thin —
    it just reads env-var context and calls :func:`run_claude_session`.
    """

    #: Identifies this backend for ACP agent_process.py dispatch.
    executor_type: str = "claude_cli"

    def __init__(self, employee_id: str, server_url: str) -> None:
        self._employee_id = employee_id
        self._server_url = server_url.rstrip("/")

        logger.debug(
            "ClaudeCliAcpBackend: constructed employee_id={eid} server_url={url}",
            eid=employee_id,
            url=server_url,
        )

    # ------------------------------------------------------------------
    # ACP Backend API
    # ------------------------------------------------------------------

    async def execute(
        self,
        task_description: str,
        client: Any,
        session_id: str,
        cancel_event: asyncio.Event,
    ) -> dict:
        """Send task_description to the Claude daemon and return a result dict.

        Context is read from environment variables injected by the ACP host:
          - ``OMC_PROJECT_ID`` — project identifier for session scoping
          - ``OMC_WORK_DIR``   — working directory for the session
          - ``OMC_TASK_ID``    — task identifier for MCP config / trace logging

        Args:
            task_description: The task prompt to send to Claude.
            client: ACP AgentSideConnection (unused by this backend).
            session_id: ACP session identifier (unused; Claude manages its own).
            cancel_event: Set to abort — not propagated to daemon yet.

        Returns:
            Dict with keys: ``output`` (str), ``error`` (str|None),
            ``model`` (str), ``tokens`` (dict with ``input``, ``output``,
            ``cost_usd``).
        """
        project_id = os.environ.get("OMC_PROJECT_ID", "")
        work_dir = os.environ.get("OMC_WORK_DIR", "")
        task_id = os.environ.get("OMC_TASK_ID", "")

        logger.debug(
            "ClaudeCliAcpBackend.execute: employee={eid} project={pid} task={tid} prompt={p:.80}",
            eid=self._employee_id,
            pid=project_id,
            tid=task_id,
            p=task_description,
        )

        raw = await run_claude_session(
            self._employee_id,
            project_id,
            prompt=task_description,
            work_dir=work_dir,
            task_id=task_id,
        )

        output: str = raw.get("output", "")
        model: str = raw.get("model", "")
        input_tokens: int = raw.get("input_tokens", 0)
        output_tokens: int = raw.get("output_tokens", 0)

        # Surface daemon errors as the error field so callers can distinguish
        error: str | None = None
        if output.startswith(_DAEMON_ERROR_PREFIX):
            error = output
            output = ""

        logger.debug(
            "ClaudeCliAcpBackend.execute: done employee={eid} model={model} "
            "input_tokens={it} output_tokens={ot} error={err}",
            eid=self._employee_id,
            model=model,
            it=input_tokens,
            ot=output_tokens,
            err=error,
        )

        return {
            "output": output,
            "error": error,
            "model": model,
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "cost_usd": None,
            },
        }

    def set_model(self, model_id: str) -> None:
        """No-op — Claude CLI manages its own model selection."""
        logger.debug(
            "ClaudeCliAcpBackend.set_model: ignored (Claude CLI manages own model) model_id={mid}",
            mid=model_id,
        )

    def set_config(self, key: str, value: Any) -> None:
        """No-op — configuration is managed via employee profile YAML."""
        logger.debug(
            "ClaudeCliAcpBackend.set_config: ignored key={k} value={v}",
            k=key,
            v=value,
        )
