"""ACP Connection Manager — client-side of the ACP integration.

Manages persistent ACP agent subprocess connections, bridges session updates
to the EventBus, and provides the OMCAcpClient callback implementation.

Architecture
------------
- ``AcpConnectionManager`` — central singleton managing all employee subprocess
  connections.  Spawns subprocesses, handles handshakes, watchdog respawn.
- ``OMCAcpClient`` — implements the ACP Client protocol, receiving callbacks
  from agent subprocesses and publishing to the EventBus.
- ``PendingResult`` — accumulates streaming results until usage_final arrives.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from onemancompany.acp.adapter import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AvailableCommandsUpdate,
    ClientSideConnection,
    ConfigOptionUpdate,
    CurrentModeUpdate,
    RequestPermissionResponse,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
    connect_to_agent,
    text_block,
)
from onemancompany.acp.events_bridge import acp_update_to_event
from onemancompany.acp.permission import PolicyEngine
from onemancompany.core.events import event_bus
from onemancompany.core.vessel import LaunchResult

# ---------------------------------------------------------------------------
# Permission policy engine (lazy singleton)
# ---------------------------------------------------------------------------

_policy_engine: PolicyEngine | None = None


def _get_policy_engine() -> PolicyEngine:
    global _policy_engine
    if _policy_engine is None:
        rules_path = Path("company_rules/permissions.yaml")
        _policy_engine = PolicyEngine(rules_path)
    return _policy_engine


# ---------------------------------------------------------------------------
# PendingResult — accumulates streaming output until usage_final
# ---------------------------------------------------------------------------


@dataclass
class PendingResult:
    """Accumulates streaming output from an active prompt call."""

    output_chunks: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    model_used: str = ""
    usage_final_event: asyncio.Event = field(default_factory=asyncio.Event)


# ---------------------------------------------------------------------------
# OMCAcpClient — implements the ACP Client protocol callbacks
# ---------------------------------------------------------------------------


class OMCAcpClient:
    """Receives callbacks from an ACP agent subprocess.

    One instance is created per employee connection.  All session updates are
    published to the global EventBus and accumulated into a PendingResult if
    one is active for that employee.

    Parameters
    ----------
    employee_id:
        The employee this client represents.
    manager:
        Back-reference to ``AcpConnectionManager`` for heartbeat / IDE updates.
    """

    def __init__(self, employee_id: str, manager: AcpConnectionManager) -> None:
        self._employee_id = employee_id
        self._manager = manager

    # ------------------------------------------------------------------
    # ACP Client protocol
    # ------------------------------------------------------------------

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        """Classify the update, publish to EventBus, accumulate into PendingResult."""
        logger.debug(
            "OMCAcpClient.session_update: employee_id={emp} type={t}",
            emp=self._employee_id,
            t=type(update).__name__,
        )

        kind, data = self._classify_update(update)

        event = acp_update_to_event(self._employee_id, kind, data)
        await event_bus.publish(event)

        # Accumulate text output into pending result
        pending = self._manager._pending_results.get(self._employee_id)
        if pending is not None and kind == "message":
            text = data.get("text", "")
            if text:
                pending.output_chunks.append(text)
            if kind == "usage":
                self._accumulate_usage(pending, update)

    async def request_permission(
        self,
        options: list[Any],
        session_id: str,
        tool_call: Any,
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        """Evaluate permission policy and return allow/reject instantly."""
        logger.debug(
            "OMCAcpClient.request_permission: employee_id={emp} session={sid}",
            emp=self._employee_id,
            sid=session_id,
        )

        engine = _get_policy_engine()
        tool_name = getattr(tool_call, "tool", "") if tool_call is not None else ""
        decision = engine.decide(tool=tool_name, args={}, context={})

        if decision.allowed:
            from acp.schema import AllowedOutcome  # lazy

            option_id = options[0].id if options else "allow"
            return RequestPermissionResponse(
                outcome=AllowedOutcome(option_id=option_id, outcome="selected")
            )
        else:
            from acp.schema import DeniedOutcome  # lazy

            return RequestPermissionResponse(
                outcome=DeniedOutcome(outcome="cancelled")
            )

    async def read_text_file(
        self, path: str, session_id: str, **kwargs: Any
    ) -> Any:
        """Read a file with a basic safety check."""
        from onemancompany.acp.adapter import ReadTextFileResponse  # lazy

        logger.debug(
            "OMCAcpClient.read_text_file: employee_id={emp} path={path!r}",
            emp=self._employee_id,
            path=path,
        )

        try:
            content = Path(path).read_text(encoding="utf-8")
            return ReadTextFileResponse(content=content)
        except Exception as exc:
            logger.warning(
                "OMCAcpClient.read_text_file: failed to read {path!r} — {exc}",
                path=path,
                exc=exc,
            )
            return ReadTextFileResponse(content="")

    async def write_text_file(
        self, content: str, path: str, session_id: str, **kwargs: Any
    ) -> Any:
        """Write content to a file."""
        from onemancompany.acp.adapter import WriteTextFileResponse  # lazy

        logger.debug(
            "OMCAcpClient.write_text_file: employee_id={emp} path={path!r}",
            emp=self._employee_id,
            path=path,
        )

        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return WriteTextFileResponse()
        except Exception as exc:
            logger.warning(
                "OMCAcpClient.write_text_file: failed to write {path!r} — {exc}",
                path=path,
                exc=exc,
            )
            return WriteTextFileResponse()

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        """Handle extension notifications from the agent subprocess."""
        logger.debug(
            "OMCAcpClient.ext_notification: employee_id={emp} method={m}",
            emp=self._employee_id,
            m=method,
        )

        if method == "heartbeat":
            self._manager.record_heartbeat(self._employee_id)
            logger.debug(
                "OMCAcpClient: heartbeat from employee_id={emp}",
                emp=self._employee_id,
            )

        elif method == "http_endpoint":
            port = params.get("port")
            if port is not None:
                self._manager.record_ide_endpoint(self._employee_id, int(port))
                logger.debug(
                    "OMCAcpClient: IDE endpoint port={port} for employee_id={emp}",
                    port=port,
                    emp=self._employee_id,
                )

        elif method == "usage_final":
            pending = self._manager._pending_results.get(self._employee_id)
            if pending is not None:
                pending.input_tokens = params.get("input_tokens", 0)
                pending.output_tokens = params.get("output_tokens", 0)
                cost = params.get("cost_usd")
                if cost is not None:
                    pending.cost_usd = float(cost)
                model = params.get("model", "")
                if model:
                    pending.model_used = model
                pending.usage_final_event.set()
                logger.debug(
                    "OMCAcpClient: usage_final for employee_id={emp} in={i} out={o}",
                    emp=self._employee_id,
                    i=pending.input_tokens,
                    o=pending.output_tokens,
                )

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """No-op extension method handler."""
        logger.debug(
            "OMCAcpClient.ext_method: employee_id={emp} method={m}",
            emp=self._employee_id,
            m=method,
        )
        return {}

    def on_connect(self, conn: Any) -> None:
        """Called when the ACP connection is established."""
        logger.debug(
            "OMCAcpClient.on_connect: employee_id={emp} connected",
            emp=self._employee_id,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _classify_update(self, update: Any) -> tuple[str, dict[str, Any]]:
        """Return (kind, data) for a session update object."""
        if isinstance(update, AgentMessageChunk):
            text = ""
            if update.content:
                for block in update.content:
                    if hasattr(block, "text"):
                        text += block.text
            return "message", {"text": text, "message_id": update.message_id}

        if isinstance(update, AgentThoughtChunk):
            text = ""
            if update.content:
                for block in update.content:
                    if hasattr(block, "text"):
                        text += block.text
            return "thought", {"text": text}

        if isinstance(update, ToolCallStart):
            return "tool_call_start", {
                "tool_call_id": getattr(update, "tool_call_id", ""),
                "tool_name": getattr(update, "tool_name", ""),
            }

        if isinstance(update, ToolCallProgress):
            return "tool_call_progress", {
                "tool_call_id": getattr(update, "tool_call_id", ""),
            }

        if isinstance(update, AgentPlanUpdate):
            return "plan", {"entries": []}

        if isinstance(update, UsageUpdate):
            usage = update.used
            data: dict[str, Any] = {}
            if usage is not None:
                data["input_tokens"] = getattr(usage, "input_tokens", 0) or 0
                data["output_tokens"] = getattr(usage, "output_tokens", 0) or 0
            return "usage", data

        if isinstance(update, AvailableCommandsUpdate):
            return "commands", {}

        if isinstance(update, CurrentModeUpdate):
            return "mode", {"mode_id": getattr(update, "current_mode_id", "")}

        if isinstance(update, ConfigOptionUpdate):
            return "config", {}

        return "unknown", {"type": type(update).__name__}

    def _accumulate_usage(self, pending: PendingResult, update: UsageUpdate) -> None:
        """Accumulate token counts from a UsageUpdate into PendingResult."""
        usage = update.used
        if usage is not None:
            pending.input_tokens += getattr(usage, "input_tokens", 0) or 0
            pending.output_tokens += getattr(usage, "output_tokens", 0) or 0


# ---------------------------------------------------------------------------
# AcpConnectionManager — central manager for all ACP connections
# ---------------------------------------------------------------------------


class AcpConnectionManager:
    """Central manager for ACP agent subprocess connections.

    One instance is shared across the application.  Each employee maps to one
    subprocess + one ACP connection.  A background watchdog detects crashes and
    heartbeat timeouts, respawning as needed.
    """

    #: Seconds between watchdog ticks.
    WATCHDOG_INTERVAL: float = 10.0
    #: Max seconds since last heartbeat before respawn.
    HEARTBEAT_TIMEOUT: float = 60.0

    def __init__(self) -> None:
        self._connections: dict[str, ClientSideConnection] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._sessions: dict[str, str] = {}
        self._ide_endpoints: dict[str, int] = {}
        self._heartbeat_timestamps: dict[str, float] = {}
        self._pending_results: dict[str, PendingResult] = {}
        self._executor_types: dict[str, str] = {}
        self._extra_envs: dict[str, dict[str, str]] = {}

        self._watchdog_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def register_employee(
        self,
        employee_id: str,
        executor_type: str = "langchain",
        extra_env: dict[str, str] | None = None,
    ) -> None:
        """Spawn subprocess, perform ACP handshake, start watchdog.

        Parameters
        ----------
        employee_id:
            Unique employee identifier.
        executor_type:
            One of "langchain" | "claude_cli" | "script".
        extra_env:
            Additional environment variables to pass to the subprocess.
        """
        logger.debug(
            "AcpConnectionManager.register_employee: employee_id={emp} executor_type={et}",
            emp=employee_id,
            et=executor_type,
        )

        self._executor_types[employee_id] = executor_type
        self._extra_envs[employee_id] = extra_env or {}

        process, conn = await self._spawn_subprocess(employee_id, executor_type, extra_env or {})
        self._processes[employee_id] = process
        self._connections[employee_id] = conn

        # ACP handshake — initialize
        from onemancompany.acp.adapter import PROTOCOL_VERSION  # avoid circular at top

        await conn.initialize(protocol_version=PROTOCOL_VERSION)
        logger.debug(
            "AcpConnectionManager: initialized ACP for employee_id={emp}",
            emp=employee_id,
        )

        # ACP handshake — new_session
        resp = await conn.new_session(cwd=str(Path.cwd()))
        session_id: str = resp.session_id if hasattr(resp, "session_id") else employee_id
        self._sessions[employee_id] = session_id
        self._heartbeat_timestamps[employee_id] = time.monotonic()

        logger.debug(
            "AcpConnectionManager: new_session for employee_id={emp} session_id={sid}",
            emp=employee_id,
            sid=session_id,
        )

        self._ensure_watchdog()

    async def unregister_employee(self, employee_id: str) -> None:
        """Close session, terminate subprocess, clean up all state."""
        logger.debug(
            "AcpConnectionManager.unregister_employee: employee_id={emp}",
            emp=employee_id,
        )

        conn = self._connections.get(employee_id)
        session_id = self._sessions.get(employee_id, employee_id)

        if conn is not None:
            try:
                await conn.close_session(session_id=session_id)
            except Exception as exc:
                logger.warning(
                    "AcpConnectionManager.unregister_employee: close_session failed — {exc}",
                    exc=exc,
                )

        proc = self._processes.get(employee_id)
        if proc is not None:
            try:
                proc.terminate()
            except Exception as exc:
                logger.warning(
                    "AcpConnectionManager.unregister_employee: terminate failed — {exc}",
                    exc=exc,
                )

        self._connections.pop(employee_id, None)
        self._processes.pop(employee_id, None)
        self._sessions.pop(employee_id, None)
        self._heartbeat_timestamps.pop(employee_id, None)
        self._pending_results.pop(employee_id, None)
        self._executor_types.pop(employee_id, None)
        self._extra_envs.pop(employee_id, None)
        self._ide_endpoints.pop(employee_id, None)

    async def send_prompt(self, employee_id: str, task_text: str) -> None:
        """Send a prompt to the agent subprocess.

        Creates a PendingResult that ``collect_result`` will wait on.
        """
        logger.debug(
            "AcpConnectionManager.send_prompt: employee_id={emp} text_len={n}",
            emp=employee_id,
            n=len(task_text),
        )

        conn = self._connections[employee_id]
        session_id = self._sessions.get(employee_id, employee_id)

        pending = PendingResult()
        self._pending_results[employee_id] = pending

        prompt_blocks = [text_block(task_text)]
        await conn.prompt(prompt=prompt_blocks, session_id=session_id)

    async def collect_result(
        self, employee_id: str, timeout: float = 5.0
    ) -> LaunchResult:
        """Wait for usage_final and assemble a LaunchResult.

        Parameters
        ----------
        employee_id:
            The employee whose result to collect.
        timeout:
            Max seconds to wait for usage_final.
        """
        pending = self._pending_results.get(employee_id)
        if pending is None:
            logger.warning(
                "AcpConnectionManager.collect_result: no pending result for employee_id={emp}",
                emp=employee_id,
            )
            return LaunchResult(error="No pending result")

        try:
            await asyncio.wait_for(pending.usage_final_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "AcpConnectionManager.collect_result: timeout waiting for usage_final employee_id={emp}",
                emp=employee_id,
            )

        output = "".join(pending.output_chunks)
        result = LaunchResult(
            output=output,
            input_tokens=pending.input_tokens,
            output_tokens=pending.output_tokens,
            total_tokens=pending.input_tokens + pending.output_tokens,
            cost_usd=pending.cost_usd,
            model_used=pending.model_used,
        )

        self._pending_results.pop(employee_id, None)

        logger.debug(
            "AcpConnectionManager.collect_result: employee_id={emp} output_len={n} tokens={t}",
            emp=employee_id,
            n=len(output),
            t=result.total_tokens,
        )
        return result

    async def cancel_prompt(self, employee_id: str) -> None:
        """Send a cancel signal to the agent subprocess."""
        conn = self._connections.get(employee_id)
        session_id = self._sessions.get(employee_id, employee_id)
        if conn is not None:
            logger.debug(
                "AcpConnectionManager.cancel_prompt: employee_id={emp}", emp=employee_id
            )
            await conn.cancel(session_id=session_id)

    async def set_mode(self, employee_id: str, mode_id: str) -> None:
        """Set the session mode on the agent subprocess."""
        conn = self._connections[employee_id]
        session_id = self._sessions.get(employee_id, employee_id)
        logger.debug(
            "AcpConnectionManager.set_mode: employee_id={emp} mode={m}",
            emp=employee_id,
            m=mode_id,
        )
        await conn.set_session_mode(mode_id=mode_id, session_id=session_id)

    async def set_model(self, employee_id: str, model_id: str) -> None:
        """Set the LLM model on the agent subprocess."""
        conn = self._connections[employee_id]
        session_id = self._sessions.get(employee_id, employee_id)
        logger.debug(
            "AcpConnectionManager.set_model: employee_id={emp} model={m}",
            emp=employee_id,
            m=model_id,
        )
        await conn.set_session_model(model_id=model_id, session_id=session_id)

    async def set_config(self, employee_id: str, key: str, value: Any) -> None:
        """Set a config option on the agent subprocess."""
        conn = self._connections[employee_id]
        session_id = self._sessions.get(employee_id, employee_id)
        logger.debug(
            "AcpConnectionManager.set_config: employee_id={emp} key={k}",
            emp=employee_id,
            k=key,
        )
        await conn.set_config_option(config_id=key, value=value, session_id=session_id)

    async def fork_session(self, employee_id: str) -> str:
        """Fork the current session and return the forked session_id."""
        conn = self._connections[employee_id]
        session_id = self._sessions.get(employee_id, employee_id)
        logger.debug(
            "AcpConnectionManager.fork_session: employee_id={emp}", emp=employee_id
        )
        resp = await conn.fork_session(session_id=session_id, cwd=str(Path.cwd()))
        forked_id: str = resp.session_id if hasattr(resp, "session_id") else f"{employee_id}__fork"
        return forked_id

    async def hot_reload_employee(self, employee_id: str) -> None:
        """Hot-reload: close session → kill process → respawn → load_session."""
        logger.debug(
            "AcpConnectionManager.hot_reload_employee: employee_id={emp}", emp=employee_id
        )

        conn = self._connections.get(employee_id)
        session_id = self._sessions.get(employee_id, employee_id)

        if conn is not None:
            try:
                await conn.close_session(session_id=session_id)
            except Exception as exc:
                logger.warning(
                    "AcpConnectionManager.hot_reload_employee: close_session error — {exc}",
                    exc=exc,
                )

        proc = self._processes.get(employee_id)
        if proc is not None:
            try:
                proc.kill()
            except Exception as exc:
                logger.warning(
                    "AcpConnectionManager.hot_reload_employee: kill error — {exc}",
                    exc=exc,
                )

        executor_type = self._executor_types.get(employee_id, "langchain")
        extra_env = self._extra_envs.get(employee_id, {})

        process, conn = await self._spawn_subprocess(employee_id, executor_type, extra_env)
        self._processes[employee_id] = process
        self._connections[employee_id] = conn

        from onemancompany.acp.adapter import PROTOCOL_VERSION  # lazy

        await conn.initialize(protocol_version=PROTOCOL_VERSION)
        await conn.load_session(session_id=session_id, cwd=str(Path.cwd()))
        self._sessions[employee_id] = session_id
        self._heartbeat_timestamps[employee_id] = time.monotonic()

        logger.debug(
            "AcpConnectionManager.hot_reload_employee: reloaded employee_id={emp}",
            emp=employee_id,
        )

    def record_heartbeat(self, employee_id: str) -> None:
        """Update the last heartbeat timestamp for an employee."""
        self._heartbeat_timestamps[employee_id] = time.monotonic()
        logger.debug(
            "AcpConnectionManager.record_heartbeat: employee_id={emp}", emp=employee_id
        )

    def record_ide_endpoint(self, employee_id: str, port: int) -> None:
        """Store the IDE HTTP port for an employee."""
        self._ide_endpoints[employee_id] = port
        logger.debug(
            "AcpConnectionManager.record_ide_endpoint: employee_id={emp} port={port}",
            emp=employee_id,
            port=port,
        )

    def get_ide_endpoint(self, employee_id: str) -> int | None:
        """Return the IDE HTTP port for an employee, or None if not set."""
        return self._ide_endpoints.get(employee_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _spawn_subprocess(
        self,
        employee_id: str,
        executor_type: str,
        extra_env: dict[str, str],
    ) -> tuple[asyncio.subprocess.Process, ClientSideConnection]:
        """Spawn the OMC agent subprocess and connect via ACP stdio transport."""
        env = {**os.environ}
        env["OMC_EMPLOYEE_ID"] = employee_id
        env["OMC_EXECUTOR_TYPE"] = executor_type
        env.update(extra_env)

        logger.debug(
            "AcpConnectionManager._spawn_subprocess: employee_id={emp} executor_type={et}",
            emp=employee_id,
            et=executor_type,
        )

        process = await asyncio.create_subprocess_exec(
            *["python", "-m", "onemancompany.acp.agent_process"],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        client = OMCAcpClient(employee_id=employee_id, manager=self)

        # connect_to_agent wraps the stdio streams in an ACP connection
        conn = connect_to_agent(
            client,
            process.stdout,
            process.stdin,
        )

        return process, conn

    async def _respawn_employee(self, employee_id: str) -> None:
        """Respawn subprocess and resume the existing session."""
        logger.debug(
            "AcpConnectionManager._respawn_employee: employee_id={emp}", emp=employee_id
        )

        executor_type = self._executor_types.get(employee_id, "langchain")
        extra_env = self._extra_envs.get(employee_id, {})
        session_id = self._sessions.get(employee_id, employee_id)

        process, conn = await self._spawn_subprocess(employee_id, executor_type, extra_env)
        self._processes[employee_id] = process
        self._connections[employee_id] = conn

        from onemancompany.acp.adapter import PROTOCOL_VERSION  # lazy

        await conn.initialize(protocol_version=PROTOCOL_VERSION)
        await conn.resume_session(session_id=session_id, cwd=str(Path.cwd()))
        self._heartbeat_timestamps[employee_id] = time.monotonic()

        logger.debug(
            "AcpConnectionManager._respawn_employee: respawned employee_id={emp}",
            emp=employee_id,
        )

    def _ensure_watchdog(self) -> None:
        """Start the watchdog task if not already running."""
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.ensure_future(self._watchdog_loop())

    async def _watchdog_loop(self) -> None:
        """Every 10s: detect process crashes or heartbeat timeouts and respawn."""
        try:
            while True:
                await asyncio.sleep(self.WATCHDOG_INTERVAL)

                now = time.monotonic()
                for employee_id in list(self._processes.keys()):
                    proc = self._processes.get(employee_id)
                    if proc is None:
                        continue

                    # Check for process crash
                    if proc.returncode is not None:
                        logger.warning(
                            "AcpConnectionManager._watchdog_loop: process crashed employee_id={emp} returncode={rc}",
                            emp=employee_id,
                            rc=proc.returncode,
                        )
                        try:
                            await self._respawn_employee(employee_id)
                        except Exception as exc:
                            logger.error(
                                "AcpConnectionManager._watchdog_loop: respawn failed employee_id={emp} — {exc}",
                                emp=employee_id,
                                exc=exc,
                            )
                        continue

                    # Check for heartbeat timeout
                    last_hb = self._heartbeat_timestamps.get(employee_id, now)
                    if (now - last_hb) > self.HEARTBEAT_TIMEOUT:
                        logger.warning(
                            "AcpConnectionManager._watchdog_loop: heartbeat timeout employee_id={emp}",
                            emp=employee_id,
                        )
                        try:
                            proc.terminate()
                        except Exception as term_exc:
                            logger.debug(
                                "AcpConnectionManager._watchdog_loop: terminate failed (process may already be dead) — {exc}",
                                exc=term_exc,
                            )
                        try:
                            await self._respawn_employee(employee_id)
                        except Exception as exc:
                            logger.error(
                                "AcpConnectionManager._watchdog_loop: respawn after timeout failed employee_id={emp} — {exc}",
                                emp=employee_id,
                                exc=exc,
                            )

        except asyncio.CancelledError:
            raise


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

connection_manager = AcpConnectionManager()
