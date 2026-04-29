"""LangChain ACP Backend — wraps BaseAgentRunner for ACP subprocess execution.

All tool calls route through HTTP ``/api/internal/tool-call`` because the
subprocess cannot access parent-process state (ContextVars, company_state).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
from loguru import logger

from onemancompany.acp.adapter import (
    start_tool_call,
    text_block,
    update_agent_message,
    update_agent_thought,
    update_agent_thought_text,
)

# Lazy import at call site to avoid heavy deps at module load
# from onemancompany.agents.base import EmployeeAgent
from onemancompany.agents.base import EmployeeAgent


class LangChainAcpBackend:
    """ACP backend that executes tasks via LangChain EmployeeAgent.

    Tool calls are proxied through HTTP to the OMC server so that
    company_state and ContextVars (which live in the parent process) are
    accessible without direct memory sharing.
    """

    #: Identifies this backend for ACP agent_process.py dispatch.
    executor_type: str = "langchain"

    def __init__(self, employee_id: str = "", server_url: str = "http://localhost:8000") -> None:
        self._employee_id = employee_id
        self._server_url = server_url.rstrip("/")
        self._agent_runner: Any | None = None  # cached EmployeeAgent, cleared on set_model
        self._model_id: str = ""
        self._config: dict[str, Any] = {}

        logger.debug(
            "LangChainAcpBackend: constructed employee_id={eid} server_url={url}",
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
        *,
        task_id: str = "",
        employee_id: str = "",
    ) -> dict:
        """Run LangChain agent, stream ACP updates to client, return result dict.

        Args:
            task_description: The task prompt to execute.
            client: ACP AgentSideConnection (has ``session_update`` coroutine).
            session_id: ACP session identifier.
            cancel_event: Set this event to abort the run early.
            task_id: Optional task ID for tool-call proxying.
            employee_id: Optional override for employee ID (falls back to ``self._employee_id``).

        Returns:
            Dict with keys: ``output`` (str), ``model`` (str),
            ``tokens`` (dict with ``input``, ``output``, ``cost_usd``).
        """
        eid = employee_id or self._employee_id
        tid = task_id or ""

        logger.debug(
            "LangChainAcpBackend.execute: employee_id={eid} task={task:.80}",
            eid=eid,
            task=task_description,
        )

        # Build or reuse cached agent
        if self._agent_runner is None:
            self._agent_runner = self._build_agent(task_id=tid, employee_id=eid)

        agent = self._agent_runner

        # Collect pending ACP update tasks so we can await them after run_streamed.
        # run_streamed calls on_log() synchronously (from within an async context),
        # so we schedule each update as a Task and gather them afterwards.
        _pending_updates: list[asyncio.Task] = []

        async def _send_update(update: Any) -> None:
            if client is not None:
                try:
                    await client.session_update(
                        session_id=session_id,
                        update=update,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("LangChainAcpBackend: session_update failed: {}", exc)

        def on_log(log_type: str, content: Any) -> None:
            """Convert LangChain streaming events to ACP session_update calls.

            ``run_streamed`` invokes this synchronously, so we cannot await here.
            Instead we create a Task (requires a running event loop) and collect
            it so ``execute()`` can await all pending updates before returning.
            """
            update = None

            if log_type == "llm_input":
                text = content if isinstance(content, str) else str(content)
                update = update_agent_thought_text(f"[thinking] {text}")

            elif log_type == "tool_call":
                if isinstance(content, dict):
                    tool_name = content.get("tool_name", "tool")
                    raw_input = content.get("tool_args", content.get("content", ""))
                else:
                    tool_name = "tool"
                    raw_input = str(content)
                call_id = str(uuid.uuid4())
                update = start_tool_call(
                    tool_call_id=call_id,
                    title=tool_name,
                    raw_input=raw_input,
                )

            elif log_type == "result":
                text = content if isinstance(content, str) else str(content)
                update = update_agent_message(text_block(text))

            if update is not None:
                try:
                    task = asyncio.ensure_future(_send_update(update))
                    _pending_updates.append(task)
                except RuntimeError:
                    # No running event loop (e.g. during sync tests) — skip
                    logger.debug("LangChainAcpBackend.on_log: no running event loop, skipping update")

        # Run the agent
        output: str = await agent.run_streamed(task_description, on_log=on_log)

        # Drain all pending ACP update tasks that were scheduled during run_streamed
        if _pending_updates:
            await asyncio.gather(*_pending_updates, return_exceptions=True)

        # Read usage from agent after run
        usage = getattr(agent, "_last_usage", {}) or {}
        model = usage.get("model", self._model_id or "")
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cost_usd = usage.get("cost_usd", None)

        logger.debug(
            "LangChainAcpBackend.execute: done employee_id={eid} model={model} tokens={tok}",
            eid=eid,
            model=model,
            tok=input_tokens + output_tokens,
        )

        return {
            "output": output,
            "model": model,
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "cost_usd": cost_usd,
            },
        }

    def set_model(self, model_id: str) -> None:
        """Clear cached agent so it rebuilds with the new model on next execute."""
        logger.debug("LangChainAcpBackend.set_model: model_id={mid}", mid=model_id)
        self._model_id = model_id
        self._agent_runner = None  # force rebuild

    def set_config(self, key: str, value: Any) -> None:
        """Store a config key-value pair; propagated on next agent build."""
        logger.debug("LangChainAcpBackend.set_config: key={k} value={v}", k=key, v=value)
        self._config[key] = value

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_agent(self, task_id: str = "", employee_id: str = "") -> Any:
        """Build EmployeeAgent with HTTP-proxied tools.

        The agent is constructed normally, then every tool's callable is
        replaced by an HTTP proxy that POST-s to the OMC server.  This is
        required because the subprocess cannot call tool functions directly
        (company_state and ContextVars live in the parent process).
        """
        eid = employee_id or self._employee_id
        logger.debug("LangChainAcpBackend._build_agent: employee_id={eid}", eid=eid)

        agent = EmployeeAgent(eid)
        self._replace_tools_with_http_proxies(agent, task_id=task_id, employee_id=eid)
        return agent

    def _replace_tools_with_http_proxies(
        self,
        agent: Any,
        *,
        task_id: str = "",
        employee_id: str = "",
    ) -> None:
        """Replace direct tool functions with HTTP POST /api/internal/tool-call proxies.

        CRITICAL: tools in a subprocess CANNOT call functions directly because
        ``company_state`` and ``ContextVars`` live in the parent process.  All
        tool calls must go through HTTP to the OMC server.

        Each tool's ``func`` (and ``coroutine``) is replaced with an async
        function that:
          1. POSTs to ``{server_url}/api/internal/tool-call``
          2. Body: ``{"employee_id": eid, "task_id": tid, "tool_name": name, "args": kwargs}``
          3. Returns the response text as the tool result.
        """
        eid = employee_id or self._employee_id
        tid = task_id
        server_url = self._server_url
        tools = getattr(agent, "_agent_tools", None)
        if not tools:
            logger.debug("LangChainAcpBackend._replace_tools_with_http_proxies: no tools on agent")
            return

        replaced_tools = []
        for tool in tools:
            name = getattr(tool, "name", "unknown")
            proxy = _make_http_proxy(name, eid, tid, server_url)

            # LangChain StructuredTool stores the callable in .func or .coroutine
            if hasattr(tool, "coroutine") and tool.coroutine is not None:
                tool.coroutine = proxy
            elif hasattr(tool, "func"):
                # Replace with an async proxy via coroutine attribute
                tool.coroutine = proxy
                tool.func = None  # type: ignore[attr-defined]
            replaced_tools.append(name)

        logger.debug(
            "LangChainAcpBackend: replaced {} tools with HTTP proxies: {}",
            len(replaced_tools),
            replaced_tools,
        )


# ---------------------------------------------------------------------------
# HTTP proxy factory
# ---------------------------------------------------------------------------


def _make_http_proxy(
    tool_name: str,
    employee_id: str,
    task_id: str,
    server_url: str,
) -> Any:
    """Return an async callable that proxies tool calls over HTTP."""

    async def _proxy(**kwargs: Any) -> str:
        payload = {
            "employee_id": employee_id,
            "task_id": task_id,
            "tool_name": tool_name,
            "args": kwargs,
        }
        logger.debug(
            "LangChainAcpBackend.http_proxy: tool={name} employee_id={eid}",
            name=tool_name,
            eid=employee_id,
        )
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{server_url}/api/internal/tool-call",
                    json=payload,
                )
                return response.text
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "LangChainAcpBackend.http_proxy: tool={name} failed: {exc}",
                name=tool_name,
                exc=exc,
            )
            return f"[tool error] {tool_name}: {exc}"

    _proxy.__name__ = f"proxy_{tool_name}"
    return _proxy
