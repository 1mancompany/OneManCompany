"""ACP Agent Process — OMCAgent subprocess entry point.

Each employee runs as a persistent subprocess implementing the ACP Agent
protocol.  The process reads configuration from environment variables and
delegates execution to one of three backend implementations (langchain,
claude_cli, or script — loaded lazily to avoid import errors when backends
are not yet installed).

Usage::

    python -m onemancompany.acp.agent_process

Or via the ACP runner::

    run_agent(OMCAgent())

Environment variables
---------------------
OMC_EMPLOYEE_ID   : Employee identifier (also used as the session_id).
OMC_EXECUTOR_TYPE : One of "langchain" | "claude_cli" | "script".
OMC_SERVER_URL    : Backend HTTP URL for LangChain / script backends.
OMC_EMPLOYEE_DIR  : Working directory for this employee (session state saved here).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from onemancompany.acp.adapter import (
    Agent,
    AgentCapabilities,
    CloseSessionResponse,
    ForkSessionResponse,
    Implementation,
    InitializeResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PromptResponse,
    ResumeSessionResponse,
    SessionNotification,
    SetSessionConfigOptionResponse,
    SetSessionModeResponse,
    SetSessionModelResponse,
    run_agent,
    session_notification,
    text_block,
    update_agent_message,
)
from onemancompany.acp.adapter import (
    CurrentModeUpdate,
    SessionCapabilities,
    SessionMode,
    SessionModeState,
    TextContentBlock,
)


# ---------------------------------------------------------------------------
# stdout protection — redirect print/logging to stderr so ACP framing is intact
# ---------------------------------------------------------------------------


class _StderrWriter:
    """Redirect all non-ACP writes to stderr."""

    def write(self, data: str) -> int:
        sys.stderr.write(data)
        return len(data)

    def flush(self) -> None:
        sys.stderr.flush()


# ---------------------------------------------------------------------------
# Supported modes
# ---------------------------------------------------------------------------

_MODES: list[dict[str, str]] = [
    {"id": "execute", "name": "Execute", "description": "Run tasks end-to-end"},
    {"id": "plan", "name": "Plan", "description": "Build a plan without executing"},
    {"id": "review", "name": "Review", "description": "Review existing work"},
]

_DEFAULT_MODE_ID = "execute"


# ---------------------------------------------------------------------------
# OMCAgent
# ---------------------------------------------------------------------------


class OMCAgent:
    """ACP Agent implementation for OneManCompany employees.

    Each instance corresponds to one employee subprocess identified by
    ``OMC_EMPLOYEE_ID``.  The agent reads env vars at construction time and
    initialises the appropriate backend lazily in ``_init_backend()``.
    """

    def __init__(self) -> None:
        self._employee_id: str = os.environ.get("OMC_EMPLOYEE_ID", "unknown")
        self._executor_type: str = os.environ.get("OMC_EXECUTOR_TYPE", "langchain")
        self._server_url: str = os.environ.get("OMC_SERVER_URL", "http://localhost:8000")
        self._employee_dir: Path = Path(os.environ.get("OMC_EMPLOYEE_DIR", f"/tmp/{self._employee_id}"))

        self._backend: Any = None  # Set by _init_backend() or tests
        self._client: Any = None  # ACP Client proxy, set by on_connect()
        self._current_mode_id: str = _DEFAULT_MODE_ID
        self._cancel_event: asyncio.Event = asyncio.Event()
        self._heartbeat_task: asyncio.Task[None] | None = None

        logger.debug(
            "OMCAgent: constructed employee_id={emp} executor_type={et}",
            emp=self._employee_id,
            et=self._executor_type,
        )

    # ------------------------------------------------------------------
    # ACP Agent protocol — lifecycle
    # ------------------------------------------------------------------

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Any = None,
        client_info: Any = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        """Return agent capabilities and info."""
        logger.debug("OMCAgent.initialize: protocol_version={pv}", pv=protocol_version)

        if self._backend is None:
            self._backend = await self._init_backend()

        capabilities = AgentCapabilities(
            load_session=True,
            session_capabilities=SessionCapabilities(
                resume=None,
                fork=None,
                close=None,
                list=None,
            ),
        )

        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=capabilities,
            agent_info=Implementation(
                name=self._employee_id,
                title=f"OMC Employee — {self._employee_id}",
                version="1.0.0",
            ),
        )

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        """Return a new session with session_id == employee_id."""
        logger.debug("OMCAgent.new_session: cwd={cwd}", cwd=cwd)

        self._cancel_event.clear()
        self._start_heartbeat()

        available_modes = [
            SessionMode(id=m["id"], name=m["name"], description=m["description"])
            for m in _MODES
        ]

        modes = SessionModeState(
            current_mode_id=self._current_mode_id,
            available_modes=available_modes,
        )

        return NewSessionResponse(
            session_id=self._employee_id,
            modes=modes,
        )

    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        """Reload session state for crash recovery."""
        logger.debug("OMCAgent.resume_session: session_id={sid}", sid=session_id)

        state_path = self._employee_dir / "session_state.json"
        if state_path.exists():
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
                logger.debug("OMCAgent.resume_session: loaded state from {path}", path=state_path)
                if "current_mode_id" in state_data:
                    self._current_mode_id = state_data["current_mode_id"]
            except Exception as exc:
                logger.warning("OMCAgent.resume_session: failed to load state — {exc}", exc=exc)

        self._cancel_event.clear()
        self._start_heartbeat()

        return ResumeSessionResponse()

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        """Hot-reload session preserving history."""
        logger.debug("OMCAgent.load_session: session_id={sid}", sid=session_id)

        state_path = self._employee_dir / "session_state.json"
        if state_path.exists():
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
                if "current_mode_id" in state_data:
                    self._current_mode_id = state_data["current_mode_id"]
            except Exception as exc:
                logger.warning("OMCAgent.load_session: failed to load state — {exc}", exc=exc)

        return LoadSessionResponse()

    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        """Return a forked session id."""
        forked_id = f"{self._employee_id}__fork"
        logger.debug(
            "OMCAgent.fork_session: session_id={sid} forked_id={fid}",
            sid=session_id,
            fid=forked_id,
        )
        return ForkSessionResponse(session_id=forked_id)

    async def list_sessions(
        self,
        cursor: str | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Return current sessions (single-session agent)."""
        from onemancompany.acp.adapter import ListSessionsResponse  # lazy

        return ListSessionsResponse(sessions=[self._employee_id])

    async def prompt(
        self,
        prompt: list[Any],
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        """Dispatch the prompt to the backend and stream updates to the client."""
        logger.debug(
            "OMCAgent.prompt: session_id={sid} blocks={n}",
            sid=session_id,
            n=len(prompt),
        )

        # Save intermediate state
        self._save_session_state({"current_mode_id": self._current_mode_id})
        self._cancel_event.clear()

        if self._backend is None:
            self._backend = await self._init_backend()

        # Extract text from prompt blocks
        prompt_text = " ".join(
            block.text
            for block in prompt
            if isinstance(block, TextContentBlock)
        )

        # Delegate to backend
        result = await self._backend.execute(
            employee_id=self._employee_id,
            prompt=prompt_text,
            mode=self._current_mode_id,
            cancel_event=self._cancel_event,
        )

        stop_reason = result.get("stop_reason", "end_turn") if isinstance(result, dict) else "end_turn"
        usage = result.get("usage") if isinstance(result, dict) else None

        # Stream any text output as an agent message update
        output_text = result.get("output") if isinstance(result, dict) else None
        if output_text and self._client is not None:
            chunk = update_agent_message(text_block(output_text))
            await self._client.session_update(
                session_id=session_id,
                update=chunk,
            )

        # Clear state file on clean completion
        self._clear_session_state()

        logger.debug(
            "OMCAgent.prompt: completed stop_reason={sr}",
            sr=stop_reason,
        )

        return PromptResponse(
            stop_reason=stop_reason,
            usage=usage,
        )

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        """Signal the running backend to stop."""
        logger.debug("OMCAgent.cancel: session_id={sid}", sid=session_id)
        self._cancel_event.set()

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse | None:
        """Stop heartbeat and clean up."""
        logger.debug("OMCAgent.close_session: session_id={sid}", sid=session_id)
        self._stop_heartbeat()
        self._clear_session_state()
        return CloseSessionResponse()

    async def set_session_mode(self, mode_id: str, session_id: str, **kwargs: Any) -> SetSessionModeResponse | None:
        """Update the current session mode and notify the client."""
        logger.debug(
            "OMCAgent.set_session_mode: session_id={sid} mode_id={mid}",
            sid=session_id,
            mid=mode_id,
        )
        self._current_mode_id = mode_id

        if self._client is not None:
            update = CurrentModeUpdate(current_mode_id=mode_id)
            await self._client.session_update(
                session_id=session_id,
                update=update,
            )

        return SetSessionModeResponse()

    async def set_session_model(
        self, model_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModelResponse | None:
        """Delegate model selection to backend."""
        logger.debug(
            "OMCAgent.set_session_model: session_id={sid} model_id={mid}",
            sid=session_id,
            mid=model_id,
        )
        if self._backend is not None and hasattr(self._backend, "set_model"):
            await self._backend.set_model(model_id)
        return SetSessionModelResponse()

    async def set_config_option(
        self, config_id: str, session_id: str, value: str | bool, **kwargs: Any
    ) -> SetSessionConfigOptionResponse | None:
        """Delegate config updates to backend."""
        logger.debug(
            "OMCAgent.set_config_option: session_id={sid} config_id={cid} value={val}",
            sid=session_id,
            cid=config_id,
            val=value,
        )
        if self._backend is not None and hasattr(self._backend, "set_config"):
            await self._backend.set_config(config_id, value)
        return SetSessionConfigOptionResponse(config_options=[])

    async def authenticate(self, method_id: str, **kwargs: Any) -> Any:
        """No-op — OMC employees don't require separate authentication."""
        from onemancompany.acp.adapter import AuthenticateResponse  # lazy

        return AuthenticateResponse()

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """No-op extension method handler."""
        logger.debug("OMCAgent.ext_method: method={m}", m=method)
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        """No-op extension notification handler."""
        logger.debug("OMCAgent.ext_notification: method={m}", m=method)

    def on_connect(self, conn: Any) -> None:
        """Store the client proxy for later use."""
        logger.debug("OMCAgent.on_connect: client connected")
        self._client = conn

    # ------------------------------------------------------------------
    # Backend initialisation (lazy imports)
    # ------------------------------------------------------------------

    async def _init_backend(self) -> Any:
        """Lazily import and return the appropriate backend."""
        et = self._executor_type
        logger.debug("OMCAgent._init_backend: executor_type={et}", et=et)

        if et == "langchain":
            from onemancompany.acp.backends.langchain_backend import LangChainAcpBackend  # type: ignore[import]

            return LangChainAcpBackend(server_url=self._server_url)

        if et == "claude_cli":
            from onemancompany.acp.backends.claude_cli_backend import ClaudeCliAcpBackend  # type: ignore[import]

            return ClaudeCliAcpBackend(employee_dir=self._employee_dir)

        if et == "script":
            from onemancompany.acp.backends.script_backend import ScriptAcpBackend  # type: ignore[import]

            return ScriptAcpBackend(server_url=self._server_url)

        raise ValueError(f"OMCAgent: unknown executor_type={et!r}")

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def _start_heartbeat(self) -> None:
        """Start the 30-second heartbeat background task."""
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            return
        self._heartbeat_task = asyncio.ensure_future(self._heartbeat_loop())

    def _stop_heartbeat(self) -> None:
        """Cancel the heartbeat task."""
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self._heartbeat_task = None

    async def _heartbeat_loop(self) -> None:
        """Send ext_notification("heartbeat") every 30 seconds."""
        try:
            while True:
                await asyncio.sleep(30)
                await self.ext_notification("heartbeat", {"employee_id": self._employee_id})
                logger.debug("OMCAgent: heartbeat sent")
        except asyncio.CancelledError:
            raise

    # ------------------------------------------------------------------
    # Session state persistence
    # ------------------------------------------------------------------

    def _save_session_state(self, state: dict[str, Any]) -> None:
        """Persist state to disk for crash recovery."""
        try:
            self._employee_dir.mkdir(parents=True, exist_ok=True)
            state_path = self._employee_dir / "session_state.json"
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning("OMCAgent._save_session_state: failed — {exc}", exc=exc)

    def _clear_session_state(self) -> None:
        """Remove the session state file after clean completion."""
        try:
            state_path = self._employee_dir / "session_state.json"
            if state_path.exists():
                state_path.unlink()
        except Exception as exc:
            logger.warning("OMCAgent._clear_session_state: failed — {exc}", exc=exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    """Entry point: redirect stdout and run the ACP agent loop."""
    # Protect ACP framing — non-ACP writes go to stderr
    sys.stdout = _StderrWriter()  # type: ignore[assignment]

    agent = OMCAgent()
    await run_agent(agent)


if __name__ == "__main__":
    asyncio.run(main())
